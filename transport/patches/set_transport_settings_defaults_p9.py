import frappe

DOCTYPE = "Transport Settings"

DEFAULTS = {
	"imported_fine_responsibility": "Company",
	# enable_scheduled_fine_sync stays 0 - deliberately not seeded here, so it
	# can only ever be switched on by a person.
}


def execute():
	"""Fine-sync settings. As established in p7/p8, "has this been configured?"
	needs a direct tabSingles lookup - both doc.get() and get_single_value()
	return a typed zero when the row is absent, which makes "never set"
	indistinguishable from "deliberately set to 0/off"."""
	for fieldname, value in DEFAULTS.items():
		already_set = frappe.db.sql(
			"select 1 from tabSingles where doctype = %s and field = %s limit 1",
			(DOCTYPE, fieldname),
		)
		if not already_set:
			frappe.db.set_single_value(DOCTYPE, fieldname, value)
