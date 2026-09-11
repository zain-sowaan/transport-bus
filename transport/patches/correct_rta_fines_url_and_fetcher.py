# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Point RTA at the page its fines are actually on, and wire up its fetcher.

seed_traffic_fine_portals is create-only by design - it must never blanket-
overwrite a portal, because authorization and capture results are earned per
site. Changing shipped reference data afterwards is done here, deliberately,
one field at a time. Same shape as update_evg_and_moi_portal_access.

**The URL was wrong, and wrong in the way that costs an operator time.**
`public_form_url` held
`traffic.rta.ae/trfesrv/public_resources/ffu/fines-payment.do`. That was never
observed - it was inferred from a footer link on www.rta.ae and from a
status-bar hover, and it reads plausibly enough that nobody questioned it. It
drives the Capture Public Page button, so pressing that captured a page with
none of the fleet's fines on it, and the capture would have looked like a
success.

The fines are on `ums.rta.ae`. Submitting the traffic-file search navigates to
`/violations/public-fines/customer-violations`, which is where the list renders.
The search form itself - the thing `public_form_url` is meant to name - is
`/violations/public-fines/fines-search`, and that is what this writes.

**`fetcher_key` was never set**, so RtaFetcher was reachable only by an
operator typing "rta" into the field by hand. It is the portal-specific key the
registry looks up first, and shipping it means a new site gets a working RTA
without anybody knowing that string.

Only rows still holding the old inferred URL are touched. A site that has
already corrected this by hand is left exactly as it is - the point of a
targeted patch is that it cannot undo somebody's deliberate edit.
"""

import frappe

DOCTYPE = "Traffic Fine Portal"

# The value being replaced, matched loosely: the shipped string carried a
# ?switchLanguage=en that a person editing the field may well have dropped.
WRONG_HOST_FRAGMENT = "traffic.rta.ae"

SEARCH_URL = "https://ums.rta.ae/violations/public-fines/fines-search"


def execute():
	portals = frappe.get_all(
		DOCTYPE,
		filters={"authority": ["like", "%Roads & Transport Authority%"], "emirate": "Dubai"},
		fields=["name", "public_form_url", "fetcher_key"],
	)

	for portal in portals:
		updates = {}

		# Only replace the inferred traffic.rta.ae URL. A site pointing somewhere
		# else has been edited on purpose and is not this patch's business.
		if WRONG_HOST_FRAGMENT in (portal.public_form_url or ""):
			updates["public_form_url"] = SEARCH_URL

		# Blank only. A site that put something else here chose it.
		if not (portal.fetcher_key or "").strip():
			updates["fetcher_key"] = "rta"

		if not updates:
			continue

		frappe.db.set_value(DOCTYPE, portal.name, updates, update_modified=False)

	frappe.db.commit()
