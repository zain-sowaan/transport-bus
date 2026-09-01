# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Which fetcher serves which portal.

Keyed on `access_route` rather than portal name, because six of the thirteen
portals are reached through the single MOI route - one fetcher serves all of
them, and adding another northern-emirate police force needs no new code.
"""

import frappe
from frappe import _

from transport.transport.fine_sync.portals.moi import MoiFetcher
from transport.transport.fine_sync.portals.srta import SrtaFetcher
from transport.transport.fine_sync.portals.tamm import TammFetcher

# Route-level fetchers: one implementation serving every portal reached the
# same way. This is what makes a single MOI fetcher cover six portals.
FETCHERS_BY_ROUTE = {
	"MOI Federated": MoiFetcher,
}

# Portal-specific fetchers, checked first. The three Side Registry portals do
# NOT share an implementation - SRTA is a public ASP.NET form while RAKTA sits
# behind a login - so they cannot be keyed on the route.
FETCHERS_BY_KEY = {
	"srta": SrtaFetcher,
	# Operator-assisted: opens a window and waits for a person to sign in. It
	# is keyed here like any other fetcher so the same run/staging machinery
	# applies, but it can never be scheduled - see TammFetcher's module docs.
	"tamm": TammFetcher,
}


def fetcher_class_for(portal):
	"""The fetcher class serving a portal, or None. Instantiates nothing.

	Split out because callers increasingly want to ask what a portal *can* do -
	whether its sign-in is relayable, say - before committing to opening a
	browser. Doing that by constructing a fetcher meant the lookup was written
	out three times and could disagree with itself.
	"""
	return (
		FETCHERS_BY_KEY.get((portal.get("fetcher_key") or "").strip().lower())
		or FETCHERS_BY_ROUTE.get(portal.access_route)
	)


def get_fetcher(portal, credential=None):
	"""Instantiate the fetcher for a portal, or explain why there isn't one."""
	fetcher_class = fetcher_class_for(portal)
	if not fetcher_class:
		frappe.throw(
			_("No fetcher is implemented for {0} ({1} route). "
			  "MOI Federated is the only route built so far - it covers six of the "
			  "thirteen portals.").format(portal.name, portal.access_route),
			title=_("Portal Not Supported"),
		)
	return fetcher_class(portal, credential)


def is_supported(portal):
	return bool(fetcher_class_for(portal))
