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

import time
from dataclasses import dataclass, field


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
