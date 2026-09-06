import frappe

DOCTYPE = "Transport Rate Calculation"
CHILD = "Transport Rate Calculation Vehicle"

# Everything calculate() derives. Written back explicitly rather than through
# save(), so the repair cannot be stopped by a validation rule about the
# document's *content* - see execute().
PARENT_FIELDS = (
	"total_estimated_cost",
	"total_minimum_price",
	"total_proposed_price",
	"total_final_price",
	"total_margin",
	"total_margin_percent",
	"requires_discount_approval",
)
CHILD_FIELDS = (
	"estimated_km",
	"estimated_trips_per_month",
	"working_days_per_month",
	"qty",
	"total_estimated_cost",
	"cost_per_trip",
	"minimum_margin_percent",
	"minimum_price",
	"final_price",
	"line_total_cost",
	"line_minimum_price",
	"line_proposed_price",
	"line_final_price",
	"line_margin",
	"margin_percent",
)


def execute():
	"""Refresh the derived figures on rate calculations saved before the fix.

	Two defects wrote wrong numbers into the table, and both are invisible from
	the desk because these fields are only ever computed during a save:

	* `margin_percent` was assigned to `line_margin_percent`, a name no field
	  has ever had, so every row stored 0.
	* `total_final_price` summed `line_margin` instead of `line_final_price`,
	  which left the document reporting a large negative margin on a profitable
	  quote - one real record showed -83.57% where the true figure was +16.43%.

	Re-saving from the browser does not fix them: Frappe only runs validate()
	on a document that is dirty, and opening one and pressing Save changes
	nothing. So the recalculation has to be driven from here.

	**Written field by field rather than through `save()`.** A save would re-run
	`enforce_minimum_margin` and `check_supply_terms`, and a document that was
	legitimately saved while those were off - or before a row's supply terms
	were tightened - would throw and take the whole migrate down with it. This
	is a data repair, not a re-approval: it recomputes exactly what a save would
	have computed and writes it, without re-judging whether the document should
	have been allowed in the first place.

	`update_modified=False` for the same reason. These values were always meant
	to be there; stamping every historical quote as edited today would destroy
	the audit trail for a correction nobody made by hand.
	"""
	names = frappe.get_all(DOCTYPE, pluck="name")
	if not names:
		return

	repaired, failed = 0, []
	for name in names:
		try:
			doc = frappe.get_doc(DOCTYPE, name)
			doc.calculate()
		except Exception as exc:
			# calculate() runs check_supply_terms, which throws on a row whose
			# costs contradict its supply terms. Such a document is already
			# wrong in a way this patch is not meant to decide about - record it
			# and leave it exactly as it is.
			failed.append(f"{name}: {exc}")
			continue

		frappe.db.set_value(
			DOCTYPE, name, {f: doc.get(f) for f in PARENT_FIELDS}, update_modified=False
		)
		for row in doc.vehicles:
			frappe.db.set_value(
				CHILD, row.name, {f: row.get(f) for f in CHILD_FIELDS}, update_modified=False
			)
		repaired += 1

	frappe.db.commit()
	print(f"Transport: recalculated {repaired} of {len(names)} rate calculation(s).")
	if failed:
		# Printed, not thrown. One unrepairable document must not block a
		# migrate, and the names are what somebody needs in order to open them.
		print("  Left unchanged because calculate() rejected them:")
		for line in failed:
			print(f"    {line}")
