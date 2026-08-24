# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Refresh the reference data on the portal registry after SRTA was verified live.

seed_traffic_fine_portals is create-only by design: it must never blanket-
overwrite a portal, because authorization and capture results are earned per
site. Its docstring names this as the way to change shipped reference data
afterwards - a patch that updates specific fields deliberately.

Two changes, both reference data only:

1. Every portal shipped `last_verified` as a raw spreadsheet serial ("46237"),
   which a Data field renders literally. Corrected to the date it stood for.
2. SRTA's access notes were written from an indexed page, before any query had
   been submitted. A live submit on 2026-08-24 replaced the guesses with the
   real mechanics.

Deliberately untouched: is_enabled, has_written_authorization,
authorization_reference and authorization_expires_on. Those are the site's,
not ours.
"""

import frappe

DOCTYPE = "Traffic Fine Portal"
SERIAL_PLACEHOLDER = "46237"
SERIAL_ACTUAL_DATE = "2026-08-03"

SRTA_UPDATES = {
	"last_verified": "2026-08-24",
	"confidence": "High - public form and a live submit both verified",
	"authentication": (
		"Public Fine Enquiry form, no login wall. VERIFIED by a live submit on "
		"2026-08-24: search-by-car returns a per-plate result. The plate controls "
		"ship disabled and are enabled by choosing the search mode; the mode radio "
		"is overlaid by its own label, so the label is what must be clicked."
	),
	"captcha_type": (
		"None. CONFIRMED by capture 2026-08-07 and again by a completed live query "
		"on 2026-08-24 - no CAPTCHA on the form or on the result page."
	),
	"notes": (
		"Covers SRTA transport/toll fines, NOT the Sharjah Police road-traffic "
		"registry - a clean result here says nothing about police fines. "
		"Form mechanics verified 2026-08-24: the plate-code list is not in the page, "
		"it is fetched per emirate by PageMethods.GetPlateCodes over AJAX; the value "
		"actually posted is the hidden hdnPlateCode field; Sharjah issues colour-coded "
		"plates whose options are labelled in Arabic; results render in GridView1, "
		"which shows 'No Fines Found.' when empty."
	),
	"tos_note": (
		"No published ToS or automated-access policy has been located - that is "
		"unchanged and still needs review. The enquiry form itself is public and "
		"takes no login. Use is limited to plates the operator owns; do not query "
		"third-party plates, and re-check the portal footer before widening use."
	),
}


def execute():
	# db_set is avoided in favour of db.set_value: these are plain reference
	# fields with no derived state, and the registry has no validate() logic
	# that needs to run.
	for name in frappe.get_all(
		DOCTYPE, filters={"last_verified": SERIAL_PLACEHOLDER}, pluck="name"
	):
		frappe.db.set_value(DOCTYPE, name, "last_verified", SERIAL_ACTUAL_DATE)

	srta = frappe.db.get_value(DOCTYPE, {"fetcher_key": "srta"}, "name")
	if srta:
		frappe.db.set_value(DOCTYPE, srta, SRTA_UPDATES)
