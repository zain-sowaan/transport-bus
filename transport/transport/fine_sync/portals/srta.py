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

**Verified against a live query on 2026-08-24** with a real fleet plate. What
that run established, and why the code below looks the way it does:

* The plate controls ship `disabled`. They are enabled by picking the search
  mode, and the radio's own `<label>` sits over it - a direct click on the
  input is intercepted, so the label is what gets clicked.
* The plate-code list is **not** in the page. Choosing an emirate calls
  `PageMethods.GetPlateCodes(cityCode, ...)` over AJAX and rewrites the list,
  so the code has to be selected only after that call lands.
* The submitted value is carried by the hidden `hdnPlateCode` field, which the
  select's own `onchange` populates.
* Emirate and code labels are the portal's wording, not ours: `Abudhabi` for
  Abu Dhabi, `Umm al qwain` for Umm Al Quwain, and Sharjah's colour codes are
  **Arabic** (`ابيض` for a white plate). Hence the alias tables.
* An empty result renders "No Fines Found." inside `GridView1`.

**Still unverified: the populated grid.** The vehicle queried was clean, so the
result table's columns have never been seen. `_extract_rows` therefore parses
the grid structurally and `_row_to_fine` refuses to invent a mapping - it
raises and names the headers it actually found, so the first vehicle with a
fine tells us exactly what to map rather than silently producing blank fines.
"""

import re
from html import unescape

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
# The input is overlaid by its own label, which swallows the click.
BY_CAR_LABEL = "label[for='ctl00_ContentPlaceHolder1_rdnbycar']"
PLATE_CODE_HIDDEN = "#hdnPlateCode"
RESULT_GRID_ID = "ctl00_ContentPlaceHolder1_GridView1"
ERROR_LABEL = "#ctl00_ContentPlaceHolder1_Mainerrorlbl"

# SRTA spells some emirates its own way. Ours is the Select option on Rental
# Vehicle; theirs is the visible option text on the enquiry form.
EMIRATE_ALIASES = {
	"Abu Dhabi": ("Abudhabi",),
	"Umm Al Quwain": ("Umm al qwain", "Umm Al Qaiwain"),
}

# Sharjah issues colour-coded plates alongside numbered ones, and SRTA lists
# those colours in Arabic. normalize_plate() upper-cases what we store, so the
# keys here are upper-case. Observed on the Sharjah list; other emirates may
# label their colours differently, which will surface as a loud failure in
# _select_by_label rather than a wrong lookup.
PLATE_CODE_ALIASES = {
	"WHITE": ("ابيض",),
	"WHITE+BLUE": ("ابيض+ازرق",),
	"GREEN": ("اخضر",),
	"BLUE": ("ازرق",),
	"ORANGE": ("برتقالي",),
	"BROWN": ("بني",),
}

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

		modes = page.query_selector_all(SEARCH_MODE)
		if len(modes) < 3:
			raise FineFetchError(
				f"SRTA's enquiry form did not look as expected "
				f"({len(modes)} search modes, expected at least 3). The page has probably "
				f"changed; re-run a capture before trusting this fetcher."
			)

		# Clicking the label, not the radio: the label overlays the input and
		# intercepts the pointer event. This is also what enables the plate
		# controls, which are disabled until a search mode is chosen.
		page.click(BY_CAR_LABEL)
		self._settle(page)

		self._select_by_label(page, PLATE_CITY, plate_parts.get("plate_emirate"), EMIRATE_ALIASES)
		self._await_plate_codes(page)
		self._select_by_label(page, PLATE_CODE, plate_parts.get("plate_code"), PLATE_CODE_ALIASES)

		# The select is only the visible half; the form posts this hidden field.
		# If its onchange did not fire we would silently query the wrong plate.
		if not (page.eval_on_selector(PLATE_CODE_HIDDEN, "e => e.value") or "").strip():
			raise FineFetchError(
				"SRTA's hidden plate-code field stayed empty after choosing the code, "
				"so the search would have run against the wrong plate. Aborted."
			)

		page.fill(PLATE_NUMBER, str(plate_parts.get("plate_number") or ""))

		page.click(SUBMIT)
		self._settle(page)
		self.assert_no_captcha()

		self._raise_portal_error(page)
		return self.parse_results(page.content() or "")

	def _await_plate_codes(self, page):
		"""Wait for GetPlateCodes to refill the list after the emirate changes.

		Without this the code list still holds only its "Plate Code" placeholder
		- or the transient "Loading..." entry - and the selection below fails
		for every vehicle.
		"""
		try:
			page.wait_for_function(
				"() => { const e = document.getElementById('lstPlate_code');"
				" return e && e.options.length > 1 && e.options[0].text !== 'Loading...'; }",
				timeout=30000,
			)
		except Exception:
			raise FineFetchError(
				"SRTA did not return any plate codes for this emirate. Its GetPlateCodes "
				"call either failed or the form has changed."
			)

	def _raise_portal_error(self, page):
		"""Surface the portal's own error text instead of parsing around it."""
		el = page.query_selector(ERROR_LABEL)
		message = (el.inner_text().strip() if el else "")
		if message:
			raise FineFetchError(f"SRTA reported: {message}")

	def _select_by_label(self, page, selector, value, aliases=None):
		"""Pick an option by its visible text, falling back to aliases and value.

		The emirate and code lists are the portal's own wording, which will not
		always match ours exactly; an unmatched value must fail loudly rather
		than silently leaving the default selected and querying the wrong car.
		"""
		if not value:
			raise FineFetchError("Vehicle is missing a plate part; it cannot be looked up.")

		el = page.query_selector(selector)
		if not el:
			raise FineFetchError(f"SRTA form control not found: {selector}")

		candidates = [str(value), *(aliases or {}).get(str(value), ())]
		for candidate in candidates:
			for attempt in ("label", "value"):
				try:
					el.select_option(**{attempt: candidate})
					return
				except Exception:
					continue

		offered = page.eval_on_selector(
			selector, "e => Array.from(e.options).map(o => o.text.trim())"
		)
		raise FineFetchError(
			f"'{value}' is not one of the options SRTA offers for {selector}. "
			f"It lists: {offered}. Record the plate part the way the portal spells it, "
			f"or add an alias."
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

	def _extract_rows(self, html_text):
		"""Pull the result grid apart into one dict per fine, keyed by header.

		Structure confirmed on a live response: the results live in an ASP.NET
		GridView (`GridView1`). When it is empty it renders a single cell with
		the "No Fines Found." message, which parse_results has already matched
		before reaching here - so a grid with no header row at this point is a
		shape we do not recognise, and None sends the caller to a hard failure.
		"""
		grid = re.search(
			rf'<table[^>]*id="{RESULT_GRID_ID}"[^>]*>(.*?)</table>', html_text, re.S | re.I
		)
		if not grid:
			return None

		rows = re.findall(r"<tr[^>]*>(.*?)</tr>", grid.group(1), re.S | re.I)
		if not rows:
			return None

		headers = [self._cell_text(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", rows[0], re.S | re.I)]
		if not headers:
			return None

		out = []
		for row in rows[1:]:
			cells = [self._cell_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
			# A GridView pager renders as a row with its own cell count; skip
			# anything that does not line up with the header rather than
			# zipping it into a misaligned fine.
			if len(cells) != len(headers):
				continue
			out.append(dict(zip(headers, cells)))
		return out

	@staticmethod
	def _cell_text(cell):
		return unescape(re.sub(r"<[^>]+>", " ", cell)).replace("\xa0", " ").strip()

	def _row_to_fine(self, row):
		"""Map one grid row onto a fine, or refuse.

		The columns have never been observed - the vehicle used to verify this
		fetcher had no fines - so nothing here guesses. If the ticket number
		cannot be identified the row is rejected with the headers that were
		actually returned, which is what tells us how to map it. Returning a
		blank fine instead would post a zero-amount liability against a
		vehicle, which is worse than failing.
		"""
		lookup = {k.lower(): v for k, v in row.items()}

		def pick(*names):
			for n in names:
				for key, value in lookup.items():
					if n in key:
						return value
			return None

		ticket = pick("fine no", "fine number", "ticket", "رقم المخالفة")
		if not ticket:
			raise FineFetchError(
				"SRTA returned fines but this fetcher cannot tell which column is the "
				f"fine number. Columns returned: {list(row)}. Map them in _row_to_fine "
				"before relying on this portal - nothing is being recorded meanwhile."
			)

		return FetchedFine(
			ticket_number=str(ticket).strip(),
			amount=pick("amount", "fine amount", "المبلغ") or 0,
			fine_datetime=pick("date", "time", "التاريخ"),
			fine_type=pick("type", "violation", "description", "المخالفة"),
			fine_location=pick("location", "place", "المكان"),
			plate=pick("plate", "رقم اللوحة"),
			raw=row,
		)
