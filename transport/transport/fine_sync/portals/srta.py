# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Sharjah Roads & Transport Authority — the one portal that can be queried
unattended.

Confirmed by capture on 2026-08-07: the Fine Enquiry page is public, carries no
CAPTCHA, and does not redirect to a login. Its form takes exactly the three
plate parts we store. Every other portal captured so far ends at a sign-in wall
(MOI on UAE Pass + reCAPTCHA, RAKTA and RTA on a login form).

**Scope, and it matters:** SRTA covers transport and toll violations only. It
is NOT the police traffic-fine registry, so a clean result here says nothing
about a vehicle's police fines. Callers must present that honestly - the whole
point of recording `scope` on the portal.

**Selectors below are the real ones read off the live page**, not guesses:

    ctl00$ContentPlaceHolder1$grpFines      search-mode radio group
    ctl00$ContentPlaceHolder1$lstPlate_city plate emirate
    lstPlate_code                           plate code (note: no ASP.NET prefix)
    ctl00$ContentPlaceHolder1$plate_nb      plate number
    ctl00$ContentPlaceHolder1$btnSubmit     submit

**What is still unverified:** the result page. No query has been submitted,
because doing so with invented plate data would be querying somebody else's
records, and we have no real fleet plates yet. `parse_results` is therefore
written defensively - it raises when it cannot recognise the response rather
than returning an empty list, so an unverified assumption can never surface as
"this vehicle has no fines".
"""

import re

from transport.transport.fine_sync.base import (
	FetchedFine,
	FetchResult,
	FineFetchError,
)
from transport.transport.fine_sync.browser_fetcher import BrowserFetcher

ENQUIRY_URL = "https://eforms.srta.gov.ae/eforms/FineEnq.aspx"

PLATE_CITY = "select[name='ctl00$ContentPlaceHolder1$lstPlate_city']"
PLATE_CODE = "select[id='lstPlate_code'], select[name='lstPlate_code']"
PLATE_NUMBER = "input[name='ctl00$ContentPlaceHolder1$plate_nb']"
SUBMIT = "input[name='ctl00$ContentPlaceHolder1$btnSubmit']"
SEARCH_MODE = "input[name='ctl00$ContentPlaceHolder1$grpFines']"

# Wording seen on enquiry portals when a search legitimately returns nothing.
# Only an explicit "no records" statement may be treated as a clean result.
NO_RESULTS_PATTERNS = (
	r"no\s+(fine|record|result|violation)s?\s+(found|available)",
	r"لا\s+توجد\s+مخالفات",
)


class SrtaFetcher(BrowserFetcher):
	def fetch_for_vehicle(self, plate_parts):
		page = self.start()
		page.goto(ENQUIRY_URL, wait_until="domcontentloaded")
		self._settle(page)
		self.assert_no_captcha()

		# Choose "by car" - the third search mode on the captured form.
		modes = page.query_selector_all(SEARCH_MODE)
		if len(modes) < 3:
			raise FineFetchError(
				f"SRTA's enquiry form did not look as expected "
				f"({len(modes)} search modes, expected at least 3). The page has probably "
				f"changed; re-run a capture before trusting this fetcher."
			)
		modes[2].check()
		self._settle(page)

		self._select_by_label(page, PLATE_CITY, plate_parts.get("plate_emirate"))
		self._select_by_label(page, PLATE_CODE, plate_parts.get("plate_code"))
		page.fill(PLATE_NUMBER, str(plate_parts.get("plate_number") or ""))

		page.click(SUBMIT)
		self._settle(page)
		self.assert_no_captcha()

		return self.parse_results(page.content() or "")

	def _select_by_label(self, page, selector, value):
		"""Pick an option by its visible text, falling back to its value.

		The emirate and code lists are the portal's own wording, which will not
		always match ours exactly; an unmatched value must fail loudly rather
		than silently leaving the default selected and querying the wrong car.
		"""
		if not value:
			raise FineFetchError("Vehicle is missing a plate part; it cannot be looked up.")

		el = page.query_selector(selector)
		if not el:
			raise FineFetchError(f"SRTA form control not found: {selector}")

		for attempt in ("label", "value"):
			try:
				el.select_option(**{attempt: str(value)})
				return
			except Exception:
				continue
		raise FineFetchError(
			f"'{value}' is not one of the options SRTA offers for {selector}. "
			f"Check how the plate part is recorded against the portal's own list."
		)

	def parse_results(self, html):
		"""Turn the response into fines, or refuse to guess.

		Deliberately conservative: an explicit "no records" message is the only
		thing accepted as a genuine empty result. Anything unrecognised raises,
		because a silent empty list here would be indistinguishable from a
		vehicle with a clean record.
		"""
		lowered = html.lower()

		for pattern in NO_RESULTS_PATTERNS:
			if re.search(pattern, lowered):
				return FetchResult(fines=[], message="SRTA reported no transport/toll fines.")

		rows = self._extract_rows(html)
		if rows is None:
			raise FineFetchError(
				"SRTA returned a page this fetcher does not recognise - neither a results "
				"table nor a 'no records' message. Not treating it as 'no fines'. Capture "
				"the response and complete parse_results before relying on this portal."
			)

		return FetchResult(
			fines=[self._row_to_fine(r) for r in rows],
			message=f"SRTA returned {len(rows)} row(s).",
		)

	def _extract_rows(self, html):
		"""Locate result rows. Returns None when the shape is unrecognised.

		Left intentionally unimplemented against a real response: no query has
		been submitted yet, so the table's markup is unknown. Returning None
		routes the caller to the explicit failure above.
		"""
		return None

	def _row_to_fine(self, row):
		return FetchedFine(
			ticket_number=str(row.get("ticket_number") or "").strip(),
			amount=row.get("amount") or 0,
			fine_datetime=row.get("fine_datetime"),
			fine_type=row.get("fine_type"),
			fine_location=row.get("fine_location"),
			plate=row.get("plate"),
			raw=row,
		)
