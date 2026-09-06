# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""The fetcher contract.

Deliberately pluggable: an HTTP fetcher and a browser fetcher implement the
same interface, so moving a portal from one approach to the other is a
configuration change rather than a rewrite. That shape was agreed and put in
writing before any code existed, and it matters here because no portal's
backend request has been captured yet - today every portal needs a browser,
but any of them may become a direct HTTP call once its contract is known.
"""

import hashlib
import re
import time
from dataclasses import dataclass, field


def safe_slug(name, prefix="", length=40):
	"""A filesystem-safe, collision-free token for a portal name.

	Here rather than beside either caller because the same defect has now been
	found twice, in two unrelated places, from the same cause: portal names
	contain slashes ("Abu Dhabi Police / TAMM" is a real one), and anything that
	turns a name into a path silently gets a nested directory instead of a file.
	It cost a lock that excluded nothing, and then a capture directory that
	scattered its frames one level down.

	`frappe.scrub` is not a substitute - it lowercases and swaps spaces for
	underscores but passes slashes straight through, which is exactly how the
	second one happened.

	The digest is the part that matters, not the tidiness: slugging alone maps
	"A / B" and "A - B" onto one token, and two portals sharing a name is the
	same defect wearing the opposite sign.
	"""
	name = str(name or "")
	slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:length] or "portal"
	digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
	return f"{prefix}{slug}_{digest}"


class FineFetchError(Exception):
	"""A lookup could not be completed.

	Raised rather than returning an empty list, because "we could not ask" and
	"there are no fines" must never collapse into the same result.
	"""


class CaptchaEncountered(FineFetchError):
	"""The portal presented a CAPTCHA.

	This is a terminal outcome for automated fetching, by policy: we do not
	solve, bypass or outsource CAPTCHAs. The vehicle is recorded as Failed with
	this reason so an operator can complete it by hand.
	"""


class AuthenticationRequired(FineFetchError):
	"""The portal needs an interactive login we cannot complete unattended."""


class ConfirmationNotApproved(AuthenticationRequired):
	"""A UAE Pass push went out and nobody approved it inside the window.

	Split from its parent because the two need opposite remedies and the old
	shared message gave the wrong one. `AuthenticationRequired` means the portal
	would not take the saved sign-in, and the answer is to sign in again.
	This means the sign-in was fine and a person did not reach their phone in
	time; the answer is to press the button again, and telling them to re-sign-in
	sends them round a loop they were never in.

	**The class name is load-bearing.** `_apply_portal_verdict` decides whether
	a portal refused a session by testing the Sync Run's traceback for the
	string "AuthenticationRequired", and a missed push is not a refusal. Because
	this name does not contain that substring, a timeout stops flipping a
	perfectly good banked session's banner to "rejected". Renaming it to
	anything containing the parent's name silently restores that bug.
	"""


class SignInWindowClosed(FineFetchError):
	"""The sign-in window was closed before anyone finished signing in.

	Its own class because it is the one failure here that is nobody's fault and
	needs no diagnosis - somebody shut a window. Playwright reports it as
	TargetClosedError from whatever call happened to be in flight, which reached
	the operator as a forty-line traceback about `Page.wait_for_timeout`. That
	is what a demo failure looked like on 2026-09-01: twice, identically, with
	nothing on screen to suggest the remedy was "press the button again".
	"""


@dataclass
class FetchedFine:
	"""One fine exactly as the portal reported it."""

	ticket_number: str
	amount: float = 0.0
	fine_datetime: str | None = None
	fine_type: str | None = None
	fine_location: str | None = None
	plate: str | None = None
	black_points: int = 0
	raw: dict = field(default_factory=dict)


@dataclass
class FetchResult:
	fines: list[FetchedFine] = field(default_factory=list)
	message: str | None = None
	# True when a time limit stopped the read before the portal ran out of
	# results. Carried as a flag rather than left to the message, because a
	# caller has to downgrade the run's status on it - a partial read that
	# reports "Completed" is the same lie as a failed lookup reporting zero.
	truncated: bool = False


class FineFetcher:
	"""Base class for portal fetchers.

	`portal` is a Traffic Fine Portal document; `credential` is an optional
	Traffic Fine Portal Credential document. Neither is stored on the instance
	beyond the life of a run.
	"""

	# Whether this portal can be queried with nobody present. False where a
	# person must answer something on every search, not merely at sign-in.
	supports_unattended = True

	# Whether this fetcher can actually parse a result page yet. False means the
	# portal's post-login structure has never been captured, so callers must not
	# offer a fetch - a scraper written against imagined selectors would appear
	# to work, return nothing, and be read as "this vehicle has no fines".
	fetch_implemented = True

	# The reader the browser extension runs against this portal's page, or None
	# where no reader exists. This is the CLIENT-side twin of fetch_implemented,
	# and it is separate on purpose: a portal can be readable by a browser on
	# the server and have no extension reader, or the reverse.
	#
	# None means the extension must refuse the portal by name. It must never
	# mean "run the nearest reader and see" - a reader written against another
	# portal's markup finds no rows, and no rows is reported as a clean, empty
	# result. "This fleet has no fines in Dubai" is the single most expensive
	# sentence this module can produce, and it must never be produced by
	# guessing.
	client_reader = None

	# The origin the reader runs on, e.g. "https://www.tamm.abudhabi". Read at
	# package time to grant the extension access to it: Chrome only prompts for
	# an optional permission on a user gesture, and the button's gesture is spent
	# on the desk page long before the service worker could ask. So the origins
	# of whichever readers ship are granted in the manifest instead.
	client_origin = None

	# Wall-clock budget for the fetching itself, in seconds; None is unbounded.
	# The caller sets it, but the fetcher decides when to start the clock, so
	# that waiting for a person to sign in never spends time meant for reading
	# fines.
	time_budget_seconds = None

	def __init__(self, portal, credential=None):
		self.portal = portal
		self.credential = credential
		self._deadline = None

	def arm_deadline(self):
		"""Start the clock. Call when real work begins, not when the run does."""
		self._deadline = (
			time.monotonic() + self.time_budget_seconds if self.time_budget_seconds else None
		)

	def out_of_time(self):
		return self._deadline is not None and time.monotonic() >= self._deadline

	def client_fetch_target(self, traffic_file_number=None):
		"""Where the operator's browser should go, and which reader to run.

		Returns `{"url", "origin", "path_prefix", "reader"}`. The URL is built
		here rather than in the extension because the portal's entry point is
		portal knowledge - TAMM carries the fleet in a query parameter, a
		login-based portal carries it in the session and takes no parameter at
		all. An extension that built its own URLs would have to know that
		difference, and would be wrong about it the first time a portal moved.

		Raises by default. A fetcher that declares a `client_reader` must
		override this; one that does not should never be asked.
		"""
		raise NotImplementedError(
			f"{type(self).__name__} declares no client fetch target."
		)

	def fetch_for_vehicle(self, plate_parts) -> FetchResult:
		"""Return the fines for one vehicle.

		`plate_parts` is a dict with plate_emirate / plate_code / plate_number.
		Implementations must raise FineFetchError (or a subclass) when the
		lookup cannot be completed, and return an empty FetchResult only when
		the portal genuinely reported no fines.
		"""
		raise NotImplementedError

	def fetch_for_traffic_file(self, traffic_file_number) -> FetchResult:
		"""Return every fine under one traffic file, if the portal supports it.

		Several portals accept a traffic file number, and a corporate file may
		cover the whole fleet in a single query - far cheaper than one lookup
		per vehicle. Whether the response identifies the plate per row is not
		yet known for any portal, so callers must handle rows without a plate.
		"""
		raise NotImplementedError

	def close(self):
		"""Release any browser/session resources."""
