# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Temporarily narrow the portal registry to the portals that can be read.

**This is a deliberate, reversible narrowing, not a permission model.** Thirteen
portals are seeded; two of them have a browser reader today. The other eleven
carry the research that says why they cannot be read - scope, authentication,
CAPTCHA type, terms - so deleting them to tidy the list would throw away the
expensive part and leave the list looking complete when it is not.

Two client-side attempts came first and neither worked, which is why this is
server-side:

* adding the filter in `listview_settings.onload` - the settings object loaded
  and the guards passed, but the list applied its own filter state afterwards
  and the screen still showed 13 of 13;
* the supported `listview_settings.filters` key - registered correctly, visible
  in `frappe.listview_settings`, and still never applied. Saved user settings
  are one reason a default is skipped, and clearing those changed nothing.

A `permission_query_conditions` hook is applied by the query builder itself, so
it holds wherever the list is reached from and cannot be undone by a stale
user setting.

It is also the ONLY mechanism, on purpose. A list-view default filter was tried
alongside it and worked once the stale user setting was cleared - but the two
together put a "Filters 1" chip on a list whose hidden rows do not come back
when you clear it, which is a worse lie than showing thirteen. One mechanism, in
one place, that behaves the same however the list is reached.

**To undo it, delete the one line in hooks.py that names this module.** Nothing
here changes data: every portal keeps its record, stays editable by name, and
comes straight back.

**What it also hides, which is the cost.** This is not list-view dressing - it
is a query condition, so a filtered portal will not appear in a Link field's
search or in a report's rows either. Sync Runs already recorded against SRTA
still open and still show their portal; what changes is that somebody searching
for "Sharjah" in a portal Link field will not find it while this is on.
"""

import frappe

DOCTYPE = "Traffic Fine Portal"

# The readers that exist in extension/content/readers/. Add a key here when a
# third reader ships. Short on purpose: a portal appearing in the registry is a
# claim that somebody can actually read it.
READABLE_IN_BROWSER = ("tamm", "rta")


def portal_query_conditions(user=None):
	"""SQL condition limiting the registry to portals with a browser reader."""
	# Built from a module constant rather than anything a caller supplies, so
	# there is no interpolation of user input here - but quoted per value anyway
	# rather than pasted as one blob, so adding a key later cannot change that.
	keys = ", ".join(frappe.db.escape(key) for key in READABLE_IN_BROWSER)
	return f"`tab{DOCTYPE}`.fetcher_key in ({keys})"
