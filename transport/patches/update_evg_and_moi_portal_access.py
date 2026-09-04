# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Correct two portals whose recorded access details were wrong.

seed_traffic_fine_portals is create-only by design - it must never blanket-
overwrite a portal, because authorization and capture results are earned per
site. Changing shipped reference data afterwards is done here, deliberately,
one field at a time. Same shape as update_srta_verified_reference.

Both corrections come from live checks on 2026-09-02 through a UAE exit IP,
which is the detail that made them findable at all: **both hosts refuse
requests from outside the UAE**, so every earlier capture recorded a timeout or
a reset and concluded "unreachable". Unreachable from Karachi and Frankfurt is
not unreachable.

**EVG.** Recorded as `www.evg.ae`, Not Supported, never observed. Three things
were wrong:

* The host rejects the `www.` prefix outright - `Invalid Host header`, its
  allow-list holds only the bare `evg.ae`. So part of "it times out" was our
  own URL.
* It has a **Quick Search that needs no login at all**: traffic file number,
  plate, or licence number. A corporate traffic file returns the whole fleet
  in one query. Verified live - an Abu Dhabi traffic file resolved on this
  federal lookup and returned that fleet's whole outstanding set, cross-checked
  plate-for-plate against TAMM. Counts and amounts deliberately not recorded
  here: this repository is public, and a client's liability position is not
  reference data.
* It is nevertheless **not automatable**, for a reason that is not the login:
  a visible reCAPTCHA v2 checkbox guards every search. Policy is to stop at
  one. Hence Operator Assisted, not Automated.

The second blocker matters more than the CAPTCHA and is recorded on the row so
nobody re-litigates it: **the Fine No. column is masked pre-login**
(`************` on every row). Staging keys on (portal, ticket_number), and a
synthetic date+plate+amount key cannot stand in - that same result set carried
three byte-identical rows (one plate, one date, one location, 300 AED each),
which any such key would collapse into one and under-report by two fines.

**MOI.** Six portals carried `scode=486`. moi.py's own docstring already
recorded that 486 resolves to "Payment of Vehicle Impound Period" rather than
fines; the records were simply never corrected. The official Start Service link
on MOI's own service page is `scode=10715`.

Deliberately untouched here as everywhere: is_enabled, has_written_authorization,
authorization_reference and authorization_expires_on. Those are the site's.
"""

import frappe

DOCTYPE = "Traffic Fine Portal"

# MOI's fines service. 486 is the vehicle-impound service, which is why a
# capture through it never found a fines form.
MOI_WRONG_SCODE = "scode=486"
MOI_RIGHT_SCODE = "scode=10715"

EVG_UPDATES = {
	# The bare host, and the fines page rather than the site root - a capture
	# aimed at the root learns nothing about the search form.
	"public_form_url": "https://evg.ae/_layouts/evg/finepayment0.aspx?language=en",
	"access_route": "Independent",
	# Police Traffic Fines, not the Unified Channel it was filed under. `scope`
	# is not a description in this codebase, it is an honesty gate: the fleet
	# summary reads it to decide whether to print "Police traffic fines were NOT
	# included". EVG returned the fleet's police fines, cross-checked plate-for-
	# plate against TAMM, so filing it as a generic channel would hide real cover.
	# Safe because that summary ALSO requires fetch_mode == "Automated", which
	# this is not - so it cannot make an unattended sweep claim police cover it
	# does not have. What it does do is put EVG in the "pending operator" list
	# with its CAPTCHA named as the reason, which is exactly right.
	"scope": "Police Traffic Fines",
	# Operator Assisted, not Automated: a person must answer a CAPTCHA on every
	# single search. Not Supported would be wrong too - the lookup works fine,
	# it just needs somebody present.
	"fetch_mode": "Operator Assisted",
	"authentication": (
		"NO LOGIN REQUIRED for lookup. VERIFIED live 2026-09-02: the Pay Traffic Fines "
		"page offers a Quick Search - by traffic code (file) number, by plate "
		"(source + colour + kind + number), or by licence number - alongside the "
		"signed-in service. A corporate traffic file returned the whole fleet in one "
		"query. Reachable ONLY from a UAE IP; from outside it times out, which is what "
		"earlier captures recorded. The host also rejects the www. prefix with "
		"'Invalid Host header' - use the bare evg.ae."
	),
	"captcha_type": (
		"Visible Google reCAPTCHA v2 'I'm not a robot' checkbox on the Quick Search "
		"form itself, CONFIRMED by sight 2026-09-02. Not the invisible kind. It guards "
		"every search, so a banked session would not help even if there were one to "
		"bank. Never solved or bypassed - the operator answers their own."
	),
	"required_inputs": (
		"Any one of: traffic code (file) number; plate number + plate source + plate "
		"colour + plate kind; or licence number + issuing place. Note the plate model "
		"differs from ours - EVG wants source/colour/kind where we store "
		"emirate/code/number, so plate_code does not map directly."
	),
	"scope_note": (
		"Federal MOI channel. Returns POLICE traffic fines across emirates, unlike the "
		"side registries. Verified against a real fleet: every plate it returned was "
		"also present in TAMM's staging with matching per-plate fine counts."
	),
	"notes": (
		"Best use is human reconciliation, not sync: one CAPTCHA returns the whole "
		"fleet with no login, no UAE Pass push and no session to keep alive, which "
		"makes it a cheap independent check that the TAMM fetch is complete. "
		"CANNOT be a fetcher, and the CAPTCHA is only half the reason: the Fine No. "
		"column is MASKED pre-login ('************' on every row). Staging keys on "
		"(portal, ticket_number) and a synthetic date+plate+amount key cannot "
		"substitute - the verified result set held three byte-identical rows (same "
		"plate, same date, same location, 300 AED each) that such a key would collapse "
		"into one. Columns available: Fine No., Date & Time, Location (street level), "
		"Plate No., Total Amount, Discount %, Amount after Discount, Late Charges, "
		"Black Points, Law, Fine Type. Payment is refused without login "
		"('This ticket can not be paid from this website'). "
		"The site also enforces an anti-forgery token: a scripted click produced "
		"'Invalid anti-forgery token.', so any future automation must drive real "
		"events rather than synthetic ones."
	),
	"confidence": "High - Quick Search, CAPTCHA and a full result set all seen live 2026-09-02",
	"last_verified": "2026-09-02",
}


def execute():
	evg = frappe.db.get_value(DOCTYPE, {"portal_name": ["like", "%Emirates Vehicle Gate%"]}, "name")
	if evg:
		frappe.db.set_value(DOCTYPE, evg, EVG_UPDATES)

	# Matched on the URL rather than on a list of portal names: the same wrong
	# service code is shared by MOI Federal and the five police forces reached
	# through it, and keying on the defect fixes exactly the rows that have it.
	for name, url in frappe.get_all(
		DOCTYPE,
		filters={"public_form_url": ["like", f"%{MOI_WRONG_SCODE}%"]},
		fields=["name", "public_form_url"],
		as_list=True,
	):
		frappe.db.set_value(
			DOCTYPE, name, "public_form_url", url.replace(MOI_WRONG_SCODE, MOI_RIGHT_SCODE)
		)
