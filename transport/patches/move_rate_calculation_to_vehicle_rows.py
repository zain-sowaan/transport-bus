import frappe

PARENT = "Transport Rate Calculation"
CHILD = "Transport Rate Calculation Vehicle"

# The per-vehicle fields as they were on the parent, before the cost build-up
# moved into a child table.
MOVED_FIELDS = (
	"vehicle_category",
	"seating_capacity",
	"with_driver",
	"with_fuel",
	"estimated_km",
	"estimated_trips_per_month",
	"vehicle_rental_cost",
	"fuel_cost",
	"salik_toll_parking_cost",
	"driver_salary_cost",
	"room_rent_cost",
	"maintenance_cost",
	"total_estimated_cost",
	"cost_per_trip",
	"minimum_margin_percent",
	"minimum_price",
	"proposed_price",
	"discount_percent",
	"final_price",
)


def execute():
	"""Turn each existing single-vehicle calculation into one with one vehicle row.

	The costing used to live on the parent, one vehicle per document. Removing
	those fields from the doctype does not drop their columns - Frappe leaves
	them behind - so the old values are still readable by raw SQL here, which
	is the only way to reach them now that they are gone from the meta.

	Totals are recomputed rather than copied. `total_estimated_cost` exists on
	both the old and new shape but means different things (one vehicle versus
	all of them), and the Project P&L report reads it, so it has to be right.
	"""
	if not frappe.db.table_exists(PARENT) or not frappe.db.table_exists(CHILD):
		return

	columns = {c.get("Field") or c.get("column_name") for c in frappe.db.sql(f"desc `tab{PARENT}`", as_dict=True)}
	available = [f for f in MOVED_FIELDS if f in columns]
	if not available:
		# Nothing to carry over - a fresh site, or this already ran.
		return

	selected = ", ".join(f"`{f}`" for f in available)
	rows = frappe.db.sql(f"select name, {selected} from `tab{PARENT}`", as_dict=True)

	migrated = 0
	for old in rows:
		if frappe.db.exists(CHILD, {"parent": old.name, "parenttype": PARENT}):
			continue
		if not any(old.get(f) for f in available):
			# Never filled in - do not manufacture an empty vehicle row.
			continue

		doc = frappe.get_doc(PARENT, old.name)
		doc.append("vehicles", {f: old.get(f) for f in available})
		# A pre-existing record may predate fields that are now mandatory on the
		# row; fall back rather than fail the migration on old data.
		row = doc.vehicles[-1]
		row.vehicle_category = row.vehicle_category or "Passenger"
		row.with_driver = row.with_driver or "With Driver"
		row.with_fuel = row.with_fuel or "Without Fuel"

		doc.flags.ignore_permissions = True
		doc.flags.ignore_validate_update_after_submit = True
		# The minimum-margin check is a control on new pricing decisions, not a
		# reason to refuse to carry historical data forward.
		doc.flags.ignore_mandatory = True
		original = frappe.db.get_single_value("Transport Settings", "enforce_minimum_margin")
		if original:
			frappe.db.set_single_value("Transport Settings", "enforce_minimum_margin", 0)
		try:
			doc.save()
		finally:
			if original:
				frappe.db.set_single_value("Transport Settings", "enforce_minimum_margin", original)
		migrated += 1

	if migrated:
		frappe.db.commit()
		print(f"Moved {migrated} rate calculation(s) onto vehicle rows.")
