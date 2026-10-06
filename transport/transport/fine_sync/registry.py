# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Which fetcher serves which portal.

Keyed on `access_route` rather than portal name, because six of the thirteen
portals are reached through the single MOI route - one fetcher serves all of
them, and adding another northern-emirate police force needs no new code.
"""

import frappe
from frappe import _

from transport.transport.fine_sync.portals.darb import DarbFetcher
from transport.transport.fine_sync.portals.moi import MoiFetcher
from transport.transport.fine_sync.portals.rakta import RaktaFetcher
from transport.transport.fine_sync.portals.rta import RtaFetcher
from transport.transport.fine_sync.portals.srta import SrtaFetcher
from transport.transport.fine_sync.portals.tamm import TammFetcher

# Route-level fetchers: one implementation serving every portal reached the
# same way. This is what makes a single MOI fetcher cover six portals.
FETCHERS_BY_ROUTE = {
	"MOI Federated": MoiFetcher,
}

# Portal-specific fetchers, checked first. The three Side Registry portals do
# NOT share an implementation - SRTA is a public ASP.NET form and RAKTA is a
# public Angular one that encrypts its request body - so they cannot be keyed
# on the route.
#
# RAKTA was recorded here as sitting "behind a login" until 2026-09-18, when
# the fines route was measured and found to be public. The page is *called*
# /home/login and offers a Sign in button for the account features, which is
# where that reading came from. Corrected rather than left, because a portal
# wrongly marked login-gated is one nobody tries again.
FETCHERS_BY_KEY = {
	"srta": SrtaFetcher,
	# Registered without a reader, like RtaFetcher was. DARB's fines sit behind
	# a sign-in carrying its own reCAPTCHA, behind a bot-management layer that
	# refuses anything that is not a browser, and nobody has seen the signed-in
	# table yet. Keyed here so a caller hears that, instead of "no fetcher is
	# implemented for the Side Registry route" - which reads as though the
	# portal were unrecognised rather than blocked for a stateable reason.
	"darb": DarbFetcher,
	# Public, and read only by the extension: RAKTA encrypts its search
	# parameters in the browser, so the server cannot reproduce the request
	# however many selectors it knows. Registered so that refusal is what a
	# caller hears, rather than "no fetcher is implemented".
	"rakta": RaktaFetcher,
	# Operator-assisted: opens a window and waits for a person to sign in. It
	# is keyed here like any other fetcher so the same run/staging machinery
	# applies, but it can never be scheduled - see TammFetcher's module docs.
	"tamm": TammFetcher,
	# Registered without being able to fetch. RtaFetcher's whole job is to
	# refuse with the actual reason - the fines page has never been captured -
	# rather than let RTA fall through to "no fetcher is implemented", which
	# reads as though the portal were unrecognised. See portals/rta.py.
	"rta": RtaFetcher,
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
		# Names what IS built rather than quoting a fixed count. The previous
		# wording - "MOI Federated is the only route built so far, it covers six
		# of the thirteen portals" - was true when one route existed and went
		# quietly out of date as each fetcher landed. It is now the message a
		# client sees whenever they press Fetch on a portal nobody has written
		# yet, because the registry list is no longer narrowed to the portals
		# that work, so it has to be right without anyone remembering to edit it.
		built = ", ".join(sorted(FETCHERS_BY_KEY)) or "none"
		frappe.throw(
			_("No fetcher is implemented for {0} ({1} route). "
			  "Built so far: {2}, plus every portal reached through the MOI Federated "
			  "route. This portal is recorded so its details are not lost, but nothing "
			  "can read it yet.").format(portal.name, portal.access_route, built),
			title=_("Portal Not Supported"),
		)
	return fetcher_class(portal, credential)


def is_supported(portal):
	return bool(fetcher_class_for(portal))
