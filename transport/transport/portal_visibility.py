# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Temporarily narrow the portal registry to the portals something can fetch.

**This is a deliberate, reversible narrowing, not a permission model.** Thirteen
portals are seeded and not all of them have a fetcher. The ones that do not
carry the research that says why - scope, authentication, CAPTCHA type, terms -
so deleting them to tidy the list would throw away the expensive part and leave
the list looking complete when it is not.

**The rule is derived from the registry, never written down twice.** It used to
be a hardcoded tuple of fetcher keys, and that was a bug with a long fuse: a
portal added later was invisible until somebody remembered to edit this file,
and nothing failed loudly when they did not. It disappeared from the list
instead, which reads as a record that was never saved. Asking `registry.py`
means a portal becomes visible the moment its fetcher is registered, and a
portal nobody has written a fetcher for stays hidden without anyone deciding
that separately.

**A `fetcher_key` on the record is not a fetcher.** Typing `itc` into the field
does nothing on its own; `registry.py` has to map that key to a class. Until it
does, the portal has no fetcher and this hides it - which is the intended
answer, not a malfunction.

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
user setting. It is also the ONLY mechanism, on purpose: a list-view default
filter was tried alongside it and worked once the stale user setting was
cleared, but the two together put a "Filters 1" chip on a list whose hidden
rows do not come back when you clear it, which is a worse lie than showing
thirteen.

**To undo it, delete the one line in hooks.py that names this module.** Nothing
here changes data: every portal keeps its record, stays editable by name, and
comes straight back.

**What it also hides, which is the cost.** This is not list-view dressing - it
is a query condition, so a filtered portal will not appear in a Link field's
search or in a report's rows either, and it applies to Administrator too. Sync
Runs already recorded against a hidden portal still open and still show it;
what changes is that somebody searching for "Sharjah" in a portal Link field
will not find it while this is on.
"""

import frappe

DOCTYPE = "Traffic Fine Portal"


def _fetchable():
	"""The fetcher keys and access routes the registry can serve.

	Imported inside the function rather than at module scope. Hooks modules are
	loaded early and `registry.py` pulls in every fetcher, so importing it at
	the top of a file named by `permission_query_conditions` risks a circular
	import on boot for no benefit - this is called per query, not per request.
	"""
	from transport.transport.fine_sync.registry import FETCHERS_BY_KEY, FETCHERS_BY_ROUTE

	return sorted(FETCHERS_BY_KEY), sorted(FETCHERS_BY_ROUTE)


def portal_query_conditions(user=None):
	"""SQL condition limiting the registry to portals a fetcher can serve.

	Both halves are needed because `registry.py` resolves a fetcher two ways
	and the records record them in different columns: most portals name a
	`fetcher_key`, while the six MOI police portals carry no key at all and are
	served by route. Matching only on the key would hide every one of them.

	A portal with neither a known key nor a served route matches neither half
	and stays hidden. NULL is handled for free: `NULL IN (...)` is NULL, which
	is not true, so an empty `fetcher_key` fails the test rather than passing it.
	"""
	keys, routes = _fetchable()

	# Values are class-registry keys, not caller input, so there is nothing
	# here a user could inject - but each is escaped individually rather than
	# pasted in as one blob, so that stays true if the source ever changes.
	clauses = []
	if keys:
		quoted = ", ".join(frappe.db.escape(key) for key in keys)
		clauses.append(f"`tab{DOCTYPE}`.fetcher_key in ({quoted})")
	if routes:
		quoted = ", ".join(frappe.db.escape(route) for route in routes)
		clauses.append(f"`tab{DOCTYPE}`.access_route in ({quoted})")

	if not clauses:
		# No fetchers registered at all. Showing everything is the safe failure:
		# hiding the whole registry would read as data loss, and this hook exists
		# to reduce noise, never to deny access.
		return ""

	return "(" + " or ".join(clauses) + ")"
