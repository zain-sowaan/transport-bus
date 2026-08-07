# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Ministry of Interior — the first portal to integrate.

MOI was chosen over EVG because the same route serves six of the thirteen
registered portals (MOI itself plus the Sharjah, Ajman, Umm Al Quwain, RAK and
Fujairah police forces), so one working integration covers nearly half the
inventory. EVG, the earlier choice, is the inventory's weakest row.

**What is known** (from the reviewed inventory, and only that):

  Entry point   https://portal.moi.gov.ae/eservices/direct?scode=486
  Auth          Login-gated; the direct service route redirects to MOI's
                unified/OIDC sign-in. Guest access is not confirmed.
  Inputs        Traffic profile/file number, or driving licence number, or
                plate details (emirate + code + number).
  CAPTCHA       Not verified either way.
  Backend API   Not observed. No request or response has ever been captured.

**What is not known, and therefore not written here:** the post-login page
structure, the field selectors, the result table's shape, pagination, and the
response format. `fetch_for_vehicle` and `fetch_for_traffic_file` below raise
rather than guess. A scraper written against imagined selectors would appear
to work, silently return nothing, and be read as "this vehicle has no fines" -
the exact failure the whole design is built to prevent.

**The next step is capture, not coding.** Run `capture_page()` from the parent
class against the entry point with authorization in place; it reports the real
form fields and whether a CAPTCHA appears. Implement the two methods from what
it returns.
"""

from transport.transport.fine_sync.base import AuthenticationRequired
from transport.transport.fine_sync.browser_fetcher import BrowserFetcher

ENTRY_URL = "https://portal.moi.gov.ae/eservices/direct?scode=486"

NOT_YET_CAPTURED = (
	"The MOI lookup flow has not been captured yet, so there is nothing to drive. "
	"No backend request or page structure has been recorded for this portal. "
	"Run a capture against {url} first (Traffic Fine Portal > Capture Page), then "
	"implement the selectors from what it reports. Returning an empty result here "
	"instead would be indistinguishable from 'this vehicle has no fines'."
)


class MoiFetcher(BrowserFetcher):
	"""Serves MOI and the five police forces that route through it."""

	def fetch_for_vehicle(self, plate_parts):
		raise AuthenticationRequired(NOT_YET_CAPTURED.format(url=ENTRY_URL))

	def fetch_for_traffic_file(self, traffic_file_number):
		# Worth capturing first when the time comes: MOI accepts a traffic file
		# number, and a corporate file may return the whole fleet in one query.
		# Whether each returned row identifies its plate is unknown, so the
		# mapping back to a vehicle is unresolved until capture answers it.
		raise AuthenticationRequired(NOT_YET_CAPTURED.format(url=ENTRY_URL))
