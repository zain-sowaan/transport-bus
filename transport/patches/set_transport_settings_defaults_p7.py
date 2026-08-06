import frappe

DOCTYPE = "Transport Settings"
FIELDNAME = "enforce_sales_order_for_trip"


def execute():
	"""Mandatory-controls addition to Transport Settings. Same reasoning as
	set_transport_settings_defaults_p5: earlier patches have already run on
	existing sites and never fire again, so each new round of Single-doctype
	fields needs its own patch (see reference_frappe_single_doctype_defaults).

	Detecting "never configured" for a Check field needs a direct tabSingles
	lookup. Two things that look like they'd work but don't:
	  - `doc.get(fieldname)` -> a Document initialises an unset Check to 0.
	  - `frappe.db.get_single_value(...)` -> also returns 0, not None, when
	    the tabSingles row is absent entirely (verified on this site: 22 rows
	    existed for this doctype, none of them this field, and it still
	    returned int 0).
	Both make "unset" indistinguishable from "deliberately switched off", so
	the patch would silently no-op and ship the control disabled.
	"""
	already_set = frappe.db.sql(
		"select 1 from tabSingles where doctype = %s and field = %s limit 1",
		(DOCTYPE, FIELDNAME),
	)

	if not already_set:
		frappe.db.set_single_value(DOCTYPE, FIELDNAME, 1)
