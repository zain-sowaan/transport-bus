# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Roads & Transport Authority (RTA), Dubai.

**Registered, and deliberately cannot fetch yet.** Read this before adding
selectors, because the missing piece is not code.

The portal record carried a settled decision, dated 2026-08-22: RTA is the only
one of the thirteen portals with a conventional username/password form, which
briefly made it the one place a stored credential could have helped - until an
OTP was confirmed to follow the password. That ended unattended sync, and the
portal was parked at "Operator Assisted is the correct and final state".

That decision is not being overturned; it is being answered on a different axis.
It rules out *unattended* sync, and every word of it still holds for the
scheduler. The browser extension is the attended path: a person signs into RTA
in their own Chrome, answers their own OTP and their own CAPTCHA (RTA is one of
the two portals `browser_fetcher.assert_no_captcha` names by name), and the
extension reads the page that results. An OTP is not an obstacle to someone who
is already sitting there.

So the route is sound. What is missing is the one thing that cannot be reasoned
out: **nobody has seen RTA's fines table.** Every capture under
`private/portal-captures/` is TAMM. `portal_url` on the record is empty. The
table is behind the login, so `capture_portal_page` reaches the sign-in form and
stops there.

`fetch_implemented` and `client_reader` are therefore both left at their refusing
defaults, and this class exists to make the refusal say *why*. Writing plausible
selectors would produce a run reporting `fines_found = 0, status = Completed`,
which every operator would read as "this fleet has no fines in Dubai". That
sentence is the most expensive thing this module can produce and it must never
be produced by guessing.

## What unblocks it

One sign-in, by a person authorized to use the account, and the DOM of the
fines table. From that:

* three extraction functions - rows, next page, detail panel - modelled on the
  constants at the top of `tamm.py`,
* `_to_fine()`, mapping those rows onto FetchedFine,
* `client_reader = "rta"` and a `content/readers/rta.js` generated from them,
* `client_fetch_target()` returning the fines URL, its origin and path prefix.

None of it is large. All of it depends on that one observation.
"""

from transport.transport.fine_sync.base import FineFetchError
from transport.transport.fine_sync.operator_fetcher import OperatorAssistedFetcher

UNREAD_REASON = (
	"RTA's fines page has never been captured, so there is nothing to read it with. "
	"The sign-in is username, password and an OTP to a person's phone, and the fines "
	"table sits behind it - which is why an automated capture reaches the login form "
	"and stops. An operator has to sign in once and hand over the fines table markup "
	"before either the server-side fetch or the browser extension can work on Dubai."
)


class RtaFetcher(OperatorAssistedFetcher):
	"""Registered so RTA refuses with a reason instead of looking unrecognised.

	Before this existed, RTA answered with "No fetcher is implemented for
	Roads & Transport Authority (RTA) - Dubai (Independent route)", which reads
	as though the portal were unknown. It is not unknown; it is understood in
	detail and blocked on a single observation.
	"""

	# A person answers a password, an OTP and often a CAPTCHA. None of that can
	# happen with nobody present, and no engineering changes it.
	supports_unattended = False

	# The sign-in is a form, not UAE Pass, so there is no code to relay.
	supports_relay = False

	# Both refusing, and for the same reason: the page has never been seen.
	# `fetch_implemented` gates the server-side fetch; `client_reader` gates the
	# extension. Set them together, when the markup arrives.
	fetch_implemented = False
	client_reader = None

	def fetch_for_vehicle(self, plate_parts):
		raise FineFetchError(f"{self.portal.name}: {UNREAD_REASON}")

	def fetch_for_traffic_file(self, traffic_file_number, **kwargs):
		raise FineFetchError(f"{self.portal.name}: {UNREAD_REASON}")

	def client_fetch_target(self, traffic_file_number=None):
		raise FineFetchError(f"{self.portal.name}: {UNREAD_REASON}")
