# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""TAMM - Abu Dhabi's government services portal, fetched with an operator present.

**Why this one cannot be unattended, settled by UAE Pass's own documentation.**
TAMM signs in through UAE Pass, whose web integration is an OAuth2 authorization
code flow that ends with the user confirming a push notification on their
registered phone. There is no server-to-server path to an authorization code,
and the client credentials are issued per use case only after an onboarding
assessment. Being a UAE Pass relying party would also not help: it authenticates
users into *our* application, and grants no access whatsoever to TAMM's data.

So the login stays human, and this fetcher is built around that rather than
against it: a real browser window opens, **the operator signs in themselves**,
and automation resumes the moment the session is live. This module never types,
stores or reads a credential, and it never touches a CAPTCHA - if UAE Pass shows
one, the person sitting in front of the window answers it, which is not the same
thing as a program solving one.

**What the login buys.** A company traffic profile lists the whole fleet's fines
from Abu Dhabi Police, Dubai and the Integrated Transport Center on one page -
the aggregated multi-emirate view no per-emirate scraper can produce. One
sign-in therefore covers every vehicle under the traffic file, which is why the
entry point here is `fetch_for_traffic_file` rather than a per-plate lookup.
"""

import re

from transport.transport.fine_sync.base import (
	AuthenticationRequired,
	FetchedFine,
	FetchResult,
)
from transport.transport.fine_sync.browser_fetcher import BrowserFetcher

FINES_URL = "https://www.tamm.abudhabi/wb/adp/pay-traffic-fines/companies?lang=en&companyTcf={tcf}"

# The fines table is the one carrying this header; matching on it rather than on
# a generated class name keeps the parse working when the markup is re-themed.
TABLE_MARKER = "Fine Number"

# TAMM's own session identifier. Its expiry is what decides whether a banked
# sign-in is still usable - roughly 90 minutes from login, in practice.
SESSION_COOKIE = "jt-prod_sid"

# While the browser sits on any of these, it is mid-authentication and must be
# left completely alone - navigating would abandon a half-finished sign-in.
AUTH_URL_MARKERS = (
	"uaepass",
	"/login",
	"signin",
	"sign-in",
	"sso",
	"openid",
	"authorize",
	"oauth",
)

MONTHS = {
	"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
	"jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

AMOUNT_RE = re.compile(r"([\d,]+\.\d{2})")
DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\D+(\d{1,2}):(\d{2})\s*(am|pm)", re.I)
POINTS_RE = re.compile(r"(\d+)\s*Black\s*Point", re.I)

# Every cell carries a data-id naming its column, and the plate is structured
# markup rather than text. Reading those beats mapping header positions: it
# survives re-ordered columns, and it yields the plate code and number as
# separate values instead of something that has to be split back apart.
EXTRACT_ROWS_JS = """
() => [...document.querySelectorAll('tr.ui-lib-table-row')]
  // The header carries the same row class as the data rows and differs only by
  // this flag. Without it the header parses into a fine whose ticket number is
  // the literal text "Fine Number" - which then stages as a real liability.
  .filter(tr => !tr.querySelector('td[data-is-header="true"]'))
  .map(tr => {
  const row = {};
  tr.querySelectorAll('td[data-id]').forEach(td => {
    row[td.getAttribute('data-id')] = (td.innerText || '').trim();
  });
  const plate = tr.querySelector('.ui-lib-number-plate');
  if (plate) {
    const text = sel => {
      const el = plate.querySelector(sel);
      return el ? (el.innerText || '').trim() : null;
    };
    row._code = text('.ui-lib-number-plate__serial-text-item');
    row._emirate = text('.ui-lib-number-plate__serial-area-item-ar');
    row._number = text('.ui-lib-number-plate__register-text');
  }
  return row;
}).filter(row => Object.keys(row).length > 1)
"""

# Pagination is client-side and fires no network request, so later pages simply
# do not exist in the DOM until their control is clicked. The control itself was
# not where a first guess put it, so this tries the plausible shapes in order
# rather than betting on one: the numbered page after the active one, then a
# next-arrow, then anything labelled next.
NEXT_PAGE_JS = """
() => {
  const root = document.querySelector('.ui-lib-pagination') || document;
  const leaves = [...root.querySelectorAll('*')].filter(el =>
    el.children.length === 0 && /^\\d{1,3}$/.test((el.textContent || '').trim()));

  const isActive = el => {
    for (let n = el; n && n !== root; n = n.parentElement) {
      const cls = (n.getAttribute && (n.getAttribute('class') || '')) || '';
      if (/active|selected|current/i.test(cls)) return true;
      if (n.getAttribute && n.getAttribute('aria-current')) return true;
    }
    return false;
  };

  const numbered = leaves.map(el => ({el, n: parseInt((el.textContent || '').trim(), 10)}));
  const active = numbered.find(x => isActive(x.el));
  if (active) {
    const target = numbered.find(x => x.n === active.n + 1);
    if (target) {
      (target.el.closest('button, a, li') || target.el).click();
      return active.n + 1;
    }
  }

  const next = [...root.querySelectorAll('button, a')].find(b => {
    const label = ((b.getAttribute('aria-label') || '') + ' ' + (b.className || '')).toLowerCase();
    return /next/.test(label) && !b.hasAttribute('disabled');
  });
  if (next) { next.click(); return 'next'; }
  return null;
}
"""


# The detail modal is a plain label/value list. Reading it as consecutive lines
# avoids depending on its internal markup, which is generated and unstable.
READ_PANEL_JS = """
() => {
  const labels = ['Ticket Number', 'Plate Number', 'Description',
                  'Fine Location', 'Status', 'Ticket Type'];
  const panel = [...document.querySelectorAll('div, section, aside')]
    .filter(el => {
      const t = el.innerText || '';
      return t.includes('Ticket Number') && t.includes('Description');
    })
    .sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)[0];
  if (!panel) return null;
  const lines = (panel.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const out = {};
  for (let i = 0; i < lines.length - 1; i++) {
    if (labels.includes(lines[i]) && !labels.includes(lines[i + 1])) {
      out[lines[i]] = lines[i + 1];
    }
  }
  return out;
}
"""


class TammFetcher(BrowserFetcher):
	"""Operator-assisted. Opens a real window and waits for a human to sign in."""

	# The whole point: the operator must be able to see and use this window.
	headless = False

	# UAE Pass involves a push notification to a phone, so the wait has to be
	# measured in minutes. It ends the moment the session is live, not on a timer.
	login_timeout_ms = 2700000
	poll_interval_ms = 3000
	# Long enough that a person is never interrupted mid-flow, short enough that
	# a completed login is picked up without them having to do anything else.
	renavigate_after_ms = 30000
	# Comfortably more pages than a fleet's fine list should ever run to; the
	# walk stops on "nothing new" long before this, and this only bounds a
	# pager that would otherwise cycle.
	max_pages = 25

	def __init__(self, portal, credential=None):
		super().__init__(portal, credential)
		self._context = None

	# -- session -----------------------------------------------------------
	def session_path(self):
		"""Where the signed-in session is kept between runs.

		A Chrome *profile directory* is not enough. TAMM and UAE Pass hand out
		**session cookies**, which a browser discards on close by design, so
		every run started from a profile alone demanded a fresh UAE Pass push -
		which is exactly the thing that makes an operator stop using this.
		Playwright's storage_state captures session cookies too, so restoring it
		into a new context resumes the session properly.

		It lives under the site's *private* files, never `public/files`, which
		is served over HTTP to anyone holding the path. This file is a live
		credential for a government portal; treat it as one.
		"""
		import os

		import frappe

		directory = frappe.get_site_path("private", "portal-sessions")
		os.makedirs(directory, exist_ok=True)
		return os.path.join(directory, "tamm.json")

	def start(self):
		if self._page:
			return self._page

		import os

		from transport.transport.fine_sync.browser_fetcher import get_playwright

		sync_playwright = get_playwright()
		self._pw = sync_playwright().start()
		self._browser = self._pw.chromium.launch(headless=self.headless)

		saved = self.session_path()
		options = {"viewport": {"width": 1500, "height": 950}}
		if os.path.exists(saved):
			options["storage_state"] = saved

		self._context = self._browser.new_context(**options)
		self._page = self._context.new_page()
		self._page.set_default_timeout(self.timeout_ms)
		return self._page

	def save_session(self):
		"""Persist the signed-in session. Called as soon as login succeeds.

		Saved at that moment rather than at the end of the run, so a failure
		anywhere later still leaves the operator's sign-in banked instead of
		asking them to do it all again.
		"""
		if not self._context:
			return
		try:
			self._context.storage_state(path=self.session_path())
		except Exception:
			# Losing the session cache is a nuisance, never a reason to fail a
			# run that has already fetched real data.
			pass

	def close(self):
		for closer in (
			lambda: self._context and self._context.close(),
			lambda: self._browser and self._browser.close(),
			lambda: self._pw and self._pw.stop(),
		):
			try:
				closer()
			except Exception:
				pass
		self._page = self._context = self._browser = self._pw = None

	def fetch_for_vehicle(self, plate_parts):
		raise NotImplementedError(
			"TAMM is queried per company traffic file, not per plate - one sign-in "
			"returns the whole fleet. Use fetch_for_traffic_file()."
		)

	def has_saved_session(self):
		"""Whether a banked sign-in is still worth using. No browser involved.

		Checks the session cookie's expiry, not merely that the file is on disk.
		A file always outlives the session it holds - TAMM's runs about 90
		minutes - so an existence check alone means every scheduled fire opens a
		browser, fails to find the table, and records a Failed run. Overnight
		that is a Failed run and an error log every half hour, all of them noise
		describing the same expected condition: nobody has signed in lately.
		"""
		import json
		import os
		import time

		path = self.session_path()
		if not os.path.exists(path):
			return False
		try:
			with open(path) as fh:
				state = json.load(fh)
		except Exception:
			return False

		for cookie in state.get("cookies", []):
			if cookie.get("name") != SESSION_COOKIE:
				continue
			expires = cookie.get("expires")
			if expires is None or expires < 0:
				# A true session cookie carries no expiry; it may or may not
				# still be good, and only the portal can say. Worth one attempt.
				return True
			# A minute of headroom, so a session about to lapse mid-fetch does
			# not get started at all.
			return expires > time.time() + 60
		return False

	def fetch_for_traffic_file(self, traffic_file_number, include_details=False, wait_for_login=True):
		"""Fetch the whole traffic file.

		`wait_for_login=False` is the unattended mode: it uses whatever session
		was saved and gives up at once if that session is dead. A scheduled run
		must never sit waiting for a push nobody is going to approve - at a
		30-minute cadence that would leave hung browsers stacking up.
		"""
		page = self.start()
		page.goto(FINES_URL.format(tcf=traffic_file_number), wait_until="domcontentloaded")
		self._settle(page)

		if wait_for_login:
			self._await_operator_login(page, traffic_file_number)
		elif not self._table_present(page):
			raise AuthenticationRequired(
				"No live TAMM session. A person has to sign in through UAE Pass before "
				"an unattended run can read anything."
			)

		rows = self._collect_all_pages(page, include_details=include_details)
		fines = [f for f in (self._to_fine(row) for row in rows) if f]

		return FetchResult(
			fines=fines,
			message=(
				f"TAMM reported {len(fines)} fine(s) for traffic file {traffic_file_number}, "
				"covering Abu Dhabi Police, Dubai and Integrated Transport Center."
			),
		)

	# -- login -------------------------------------------------------------
	def _await_operator_login(self, page, traffic_file_number):
		"""Block until the fines table is on screen, or give up saying why.

		Never enters a credential and never answers a challenge - it only
		watches. The one thing it must not do is navigate while the operator is
		mid-login: re-issuing the deep link every few seconds yanks the page out
		from under a half-finished UAE Pass flow, and the sign-in can then never
		complete. So the deep link is retried sparingly, and never at all while
		the browser is sitting on an authentication host.
		"""
		waited = 0
		since_nav = 0
		while waited < self.login_timeout_ms:
			if self._table_present(page):
				# Bank the sign-in the instant it works, so no later failure
				# can cost the operator another UAE Pass push.
				self.save_session()
				return

			since_nav += self.poll_interval_ms
			if since_nav >= self.renavigate_after_ms and not self._on_auth_page(page):
				# Post-login TAMM lands on its own dashboard rather than the deep
				# link, so the link does need re-issuing - just rarely, and only
				# once the operator is clearly off the sign-in screens.
				since_nav = 0
				try:
					page.goto(
						FINES_URL.format(tcf=traffic_file_number), wait_until="domcontentloaded"
					)
					self._settle(page)
					if self._table_present(page):
						self.save_session()
						return
				except Exception:
					pass

			page.wait_for_timeout(self.poll_interval_ms)
			waited += self.poll_interval_ms

		raise AuthenticationRequired(
			"Timed out waiting for the operator to complete UAE Pass sign-in and reach the "
			f"fines page for traffic file {traffic_file_number}. Nothing was fetched."
		)

	def _on_auth_page(self, page):
		"""True while the browser is on a sign-in host we must not interrupt."""
		url = (page.url or "").lower()
		return any(marker in url for marker in AUTH_URL_MARKERS)

	def _table_present(self, page):
		try:
			return bool(page.evaluate(EXTRACT_ROWS_JS))
		except Exception:
			return False

	# -- listing -----------------------------------------------------------
	def _collect_all_pages(self, page, include_details=False):
		"""Walk every page, keyed on fine number so repeats merge harmlessly.

		Details are read page by page rather than at the end, because only the
		ten rows currently rendered can be opened - once the walk finishes the
		browser is sitting on the last page and the other forty-three rows are
		no longer in the DOM to click.

		Stops when a click yields nothing new rather than when it runs out of
		buttons: that terminates correctly whether the control is a numbered
		pager, a next-arrow, or something that silently does nothing.
		"""
		collected = {}
		rows = page.evaluate(EXTRACT_ROWS_JS)
		if not rows:
			raise AuthenticationRequired("Signed in, but the fines table never appeared.")
		self._absorb(collected, rows)
		if include_details:
			self._details_for_visible(page, rows)

		for _ in range(self.max_pages):
			before = len(collected)
			try:
				if not page.evaluate(NEXT_PAGE_JS):
					break
			except Exception:
				break
			page.wait_for_timeout(1500)
			try:
				rows = page.evaluate(EXTRACT_ROWS_JS) or []
			except Exception:
				break
			self._absorb(collected, rows)
			if include_details and rows:
				self._details_for_visible(page, rows)
			if len(collected) == before:
				break

		return list(collected.values())

	def _details_for_visible(self, page, rows):
		"""Open each rendered row's detail modal and attach what it says.

		Best effort per row: a fine whose modal will not open keeps its
		list-view values instead of failing the run. The list simply does not
		carry the violation description, so without this they stay empty.
		"""
		for row in rows:
			ticket = (row.get("fineNumber") or "").strip()
			if not ticket.isdigit():
				continue
			try:
				row["_details"] = self._read_detail_panel(page, ticket) or {}
			except Exception:
				row["_details"] = {}

	@staticmethod
	def _absorb(collected, rows):
		for row in rows:
			ticket = (row.get("fineNumber") or row.get("ticketNumber") or "").strip()
			if ticket:
				collected[ticket] = row

	# -- parsing -----------------------------------------------------------
	def _to_fine(self, row):
		def value(*names):
			for name in names:
				if row.get(name):
					return row[name]
			return ""

		ticket = value("fineNumber", "ticketNumber").strip()
		# Second guard, independent of the markup: a real ticket number is
		# digits. Anything else is a header, a total, or a placeholder row.
		if not ticket.isdigit():
			return None

		face, discounted = self._amounts(value("amount"))
		code = (row.get("_code") or "").strip()
		number = (row.get("_number") or "").strip()
		if not (code and number):
			code, number = self._plate(value("plateNumber"))
		types = value("types", "typesBlackPoints", "blackPoints", "status")
		points = POINTS_RE.search(types)
		details = row.get("_details") or {}

		return FetchedFine(
			ticket_number=ticket,
			amount=face,
			fine_datetime=self._datetime(value("dateTime", "date", "fineDate")),
			# Both come from the detail modal; the list view carries neither.
			# Left unset rather than filled with the issuing authority, which is
			# a different thing entirely from where the offence happened.
			fine_type=None,
			fine_location=details.get("Fine Location") or None,
			plate=" ".join(part for part in ("Abu Dhabi", code, number) if part),
			black_points=int(points.group(1)) if points else 0,
			raw={
				"issuing_authority": value("source", "issuedBy").strip() or None,
				"face_amount": face,
				"discounted_amount": discounted,
				"tamm_status": "Unpayable" if "unpayable" in types.lower() else "Payable",
				"plate_code": code,
				"plate_number": number,
				# Why the fine was issued, in the authority's own words.
				"description": details.get("Description"),
				"ticket_type": details.get("Ticket Type"),
				"detail_status": details.get("Status"),
				# The row exactly as read. Column ids are the portal's, not ours,
				# so keeping the whole thing means a renamed or newly-added
				# column is recoverable without re-querying.
				"row": row,
			},
		)

	@staticmethod
	def _amounts(text):
		"""Face amount and the discounted one, in TAMM's order.

		The cell shows the discounted figure first and the original struck
		through after it. The face amount is what gets recorded, because the
		discount expires and the assessment does not.
		"""
		found = [float(m.replace(",", "")) for m in AMOUNT_RE.findall(text or "")]
		if not found:
			return 0.0, None
		if len(found) == 1:
			return found[0], None
		return found[1], found[0]

	@staticmethod
	def _plate(text):
		tokens = [t.strip() for t in (text or "").split("\n") if t.strip()]
		digits = [t for t in tokens if t.isdigit()]
		if not digits:
			return "", ""
		# First numeric token is the plate code, last is the plate number.
		return digits[0], digits[-1]

	@staticmethod
	def _datetime(text):
		match = DATE_RE.search(text or "")
		if not match:
			return None
		day, month, year, hour, minute, meridiem = match.groups()
		hour = int(hour) % 12
		if meridiem.lower() == "pm":
			hour += 12
		return "%04d-%02d-%02d %02d:%02d:00" % (
			int(year), MONTHS.get(month.lower(), 1), int(day), hour, int(minute)
		)

	# -- detail panel ------------------------------------------------------
	def _read_detail_panel(self, page, ticket):
		"""Open one fine's details via its View Details button, read, then close.

		The row's chevron looked like the opener but is hidden until hover and
		never resolves to a click. The real control is a button in the `link`
		column, rendered twice - a labelled one for wide screens and an
		icon-only one for narrow - with only one visible at any width, hence
		`:visible` rather than picking an index.
		"""
		# Any panel still open from the previous row covers this one's button, so
		# start from a known-closed state rather than assuming the last close
		# worked. Skipping this left detail coverage scattered rather than
		# simply stopping - some rows happened to land between modals.
		self._dismiss_panel(page)

		row = page.locator("tr.ui-lib-table-row").filter(has_text=ticket).first
		button = row.locator('td[data-id="link"] button:visible').first
		try:
			button.click(timeout=8000)
		except Exception:
			button.click(timeout=8000, force=True)

		details = None
		for _ in range(12):  # up to ~6s for the panel to render
			details = page.evaluate(READ_PANEL_JS)
			if details:
				break
			page.wait_for_timeout(500)

		self._dismiss_panel(page)
		return details

	def _dismiss_panel(self, page):
		"""Close the detail panel and wait until it is really gone.

		Returning before the panel has actually left the DOM is what makes the
		*next* row's button unclickable, so this confirms rather than assumes.
		"""
		for _ in range(6):
			if not page.evaluate(READ_PANEL_JS):
				return True
			try:
				closed = page.evaluate(
					"""
					() => {
					  const buttons = [...document.querySelectorAll(
					    '[role="dialog"] button, .ui-lib-modal button, .ui-lib-drawer button')];
					  const x = buttons.find(b => /close/i.test(
					    (b.getAttribute('aria-label') || '') + ' ' + (b.className || '')));
					  if (x) { x.click(); return true; }
					  return false;
					}
					"""
				)
				if not closed:
					page.keyboard.press("Escape")
			except Exception:
				pass
			page.wait_for_timeout(600)
		return False
