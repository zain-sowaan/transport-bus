import frappe

DOCTYPE = "Transport Settings"

# Only fields with a meaningful non-zero default belong here. The two charge
# Item links are deliberately absent - they are real per-site master data for
# Accounts to pick, not something to invent.
DEFAULTS = {
	"ot_basis": "Hours",
	"daily_working_hours": 10,
	"auto_bill_weekend_holiday": 1,
	"weekend_holiday_charge_basis": "Per Day",
}


def execute():
	"""Overtime-basis and weekend/holiday-billing settings.

	As with p5/p7, each new round of Single-doctype fields needs its own patch
	because earlier patches never re-run. And as established in p7, "has this
	been configured yet?" must be a direct tabSingles lookup - both
	`doc.get(fieldname)` and `frappe.db.get_single_value()` return a typed zero
	rather than None when the row is absent, which makes "never set" look
	identical to "deliberately set to 0/off".
	"""
	for fieldname, value in DEFAULTS.items():
		already_set = frappe.db.sql(
			"select 1 from tabSingles where doctype = %s and field = %s limit 1",
			(DOCTYPE, fieldname),
		)
		if not already_set:
			frappe.db.set_single_value(DOCTYPE, fieldname, value)
