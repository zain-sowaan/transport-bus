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

FETCHERS_BY_ROUTE = {
	"MOI Federated": MoiFetcher,
}


def get_fetcher(portal, credential=None):
	"""Instantiate the fetcher for a portal, or explain why there isn't one."""
	fetcher_class = FETCHERS_BY_ROUTE.get(portal.access_route)
	if not fetcher_class:
		frappe.throw(
			_("No fetcher is implemented for {0} ({1} route). "
			  "MOI Federated is the only route built so far - it covers six of the "
			  "thirteen portals.").format(portal.name, portal.access_route),
			title=_("Portal Not Supported"),
		)
	return fetcher_class(portal, credential)


def is_supported(portal):
	return portal.access_route in FETCHERS_BY_ROUTE
