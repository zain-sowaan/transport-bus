import frappe

DOCTYPE = "Transport Settings"

DEFAULTS = {
	# The window is the default sign-in, and stays so. The relay is the newer
	# path and the one that types a number into a form, so it is chosen
	# deliberately rather than inherited by anyone who upgrades.
	"fine_sync_login_mode": "Operator Signs In At The Server",
	"relay_wait_minutes": 5,
	# use_relay_for_scheduled_sync stays 0, and is deliberately not seeded: a
	# scheduled relay pushes a confirmation to somebody's phone at whatever
	# hour the cron fires and publishes the code to a screen nobody is
	# watching. Only a person should ever switch that on.
}


def execute():
	"""Seed the relay settings, for the reason p7-p9 each ran into again.

	A `default` in a DocType's JSON never reaches `tabSingles` on its own - it
	is applied when the document is *saved*, and a Single that nobody has opened
	has never been saved. So the field reads back as an empty string, and a
	Select renders blank with no option chosen, which looks like a broken form
	rather than a default.

	The tabSingles lookup rather than get_single_value is the same point p9
	makes: both `doc.get()` and `get_single_value()` return a typed zero when
	the row is absent, so "never configured" and "deliberately set to off" are
	indistinguishable through them. Only the row's presence separates the two,
	and overwriting a deliberate choice on every migrate would be worse than
	leaving a blank.
	"""
	for fieldname, value in DEFAULTS.items():
		already_set = frappe.db.sql(
			"select 1 from tabSingles where doctype = %s and field = %s limit 1",
			(DOCTYPE, fieldname),
		)
		if not already_set:
			frappe.db.set_single_value(DOCTYPE, fieldname, value)
