# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""MOI - the federal portal that fronts six of the thirteen emirate forces.

**Why this one is worth the most.** Ajman, Sharjah, Fujairah, RAK and Umm Al
Quwain police, plus the federal service itself, all sit behind the Ministry of
Interior's unified portal, so one authorization and one sign-in reach six of the
thirteen. The registry keys it on `access_route` rather than portal name for
exactly that reason.

**Why it can only ever be operator-assisted.** A capture on 2026-08-07 confirmed
Google reCAPTCHA on the login page, which settles it: no unattended run gets
past sign-in, and we do not solve, bypass or outsource a CAPTCHA. That is a
policy, not an engineering gap. What it does *not* rule out is a person signing
in themselves - answering their own challenge in a window we opened is not a
program solving one - which is the shape TAMM already works in.

**What the 2026-08-30 capture established.** The portal is ASP.NET WebForms
(`__VIEWSTATE`, `__EVENTTARGET`, `ctl00$...` naming). The fines service accepts
five alternative identifiers, chosen by one radio group
(`ctl00$ContentPlaceHolder1$1`):

    rdbTcf          -> txtByTcf            traffic profile number
    rdbPlate        -> txtPlateNo          + ddlPLateSource/ddlPlateKind/ddlPlateColor
    rdbLicense      -> txtLicenseNo        + ddlLicenseSource
    rdbEmirateId    -> txtByEmirateId      + ddlEmirates/ddlYear
    rdbTicketNumber -> txtByTicketNumber

submitted with `ctl00$ContentPlaceHolder1$btnAdvSearch`. Traffic profile number
is the one worth using: like TAMM's traffic file it covers a whole fleet in one
query rather than a plate at a time.

There is also a company selector, `ctl00$UCCompanySelector$ddlCompanies`, which
scopes the whole session to one company - the same shape as TAMM's "Change
Traffic Profile", and the same trap: whatever it is left on decides whose fines
come back. Its options are the operator's own companies and are never recorded
here.

**Why this still cannot be unattended, now settled twice over.** The login page
carries reCAPTCHA, confirmed 2026-08-07. The *search form* carries its own
`g-recaptcha-response` field, confirmed 2026-08-30 - so a banked session does
not buy an unattended query either. We do not solve, bypass or outsource a
CAPTCHA. The person signs in, picks the company, enters the identifier and
answers the challenge; automation resumes at the results table. That moves the
handoff later than TAMM's but it is the same arrangement.

**What is still missing, and why there is no parse below.** The results table
has not been seen: a search posts back to the same URL, so the first capture
recorded five service pages and not one row of data. Until its markup is on
record, `fetch_implemented` stays False. A scraper written against imagined
selectors does not crash - it returns zero rows, and zero rows reads as "this
fleet has no fines".
"""

import json
import os
import re

import frappe

from frappe import _

from transport.transport.fine_sync.base import (
	AuthenticationRequired,
	FetchedFine,
	FetchResult,
)
from transport.transport.fine_sync.operator_fetcher import OperatorAssistedFetcher

# Captured 2026-08-30: scode=486 resolves to "Payment of Vehicle Impound
# Period", not fines. The fines service is its own page, reached from the
# Traffic Services menu.
ENTRY_URL = "https://portal.moi.gov.ae/eservices/TrafficServices/Fines/TrafficFinesPayment.aspx"
PROFILE_URL = "https://portal.moi.gov.ae/eservices/TrafficServices/TrafficProfile/TrafficProfile.aspx"

NOT_YET_CAPTURED = (
	"MOI's pages behind the sign-in have not been captured yet, so there is nothing to "
	"read them with. Open the MOI portal record and use Capture Portal Pages with an "
	"operator signed in; the fetch can be written once the real structure is on record."
)

# What the capture records off each page. Deliberately structural: field names,
# table shapes and pagination controls are what a parse is written against.
INSPECT_JS = """
() => {
  const attrs = el => Object.fromEntries(
    [...el.attributes].map(a => [a.name, a.value]).filter(([k]) => k !== 'style')
  );
  const cls = el => (typeof el.className === 'string' && el.className.trim())
    ? '.' + el.className.trim().split(/\\s+/).join('.') : '';
  return {
    forms: [...document.querySelectorAll('input, select, textarea')].map(e => ({
      tag: e.tagName.toLowerCase(), type: e.getAttribute('type'),
      name: e.getAttribute('name'), id: e.getAttribute('id'),
      placeholder: e.getAttribute('placeholder'),
      label: ((e.labels && e.labels[0] && e.labels[0].innerText) || '').trim().slice(0, 80),
    })),
    tables: [...document.querySelectorAll('table, [role=table]')].slice(0, 10).map(t => ({
      selector: t.tagName.toLowerCase() + cls(t),
      rows: t.querySelectorAll('tr, [role=row]').length,
      headers: [...t.querySelectorAll('th, [role=columnheader]')].map(h => h.innerText.trim()).slice(0, 25),
      // The first data row's cell attributes are what a parse keys on. TAMM
      // turned out to carry a data-id per column, far more durable than
      // counting header positions - worth knowing before writing anything.
      firstRowCells: [...((t.querySelectorAll('tr, [role=row]')[1] || {}).children || [])]
        .map(c => ({ text: c.innerText.trim().slice(0, 60), attrs: attrs(c) })),
    })),
    pagination: [...document.querySelectorAll('[class*=pagin], [class*=pager], nav')]
      .slice(0, 5).map(p => ({ selector: p.tagName.toLowerCase() + cls(p),
                               text: p.innerText.trim().slice(0, 200) })),
    buttons: [...document.querySelectorAll('button, a[role=button], input[type=submit]')]
      .slice(0, 40).map(b => (b.innerText || b.value || '').trim()).filter(Boolean),
  };
}
"""


# The results table. Columns are mapped off the header row rather than by fixed
# position: MOI's cells carry no identifying attributes at all - unlike TAMM's
# data-id per column - so position is the only handle there is, and reading it
# from the header at least survives a re-ordered table.
EXTRACT_ROWS_JS = """
() => {
  const table = document.querySelector('#TicketsTable');
  if (!table) return [];
  const heads = [...table.querySelectorAll('th')].map(h => h.innerText.trim().toLowerCase());
  const idx = name => heads.findIndex(h => h.includes(name));
  const cell = td => {
    const c = td.cloneNode(true);
    // Every cell repeats its own column name in a hidden responsive label.
    // Left in, the emirate name comes back with its own column heading glued
    // in front of it, and every value in the table is wrong the same quiet way.
    c.querySelectorAll('.mobileLbl').forEach(n => n.remove());
    return c.innerText.trim();
  };
  // The plate is structured markup, not text. Reading innerText glues the
  // category, the Arabic and English country wordmarks and the number into one
  // unbroken run. Every plate carries data-plate-js holding the portal's own
  // parse, which is what the fleet match needs.
  const plateOf = td => {
    const holder = td.querySelector('[data-plate-js]');
    if (!holder) return { code: '', number: '', emirate: '', category: '' };
    let info = {};
    try { info = JSON.parse(holder.getAttribute('data-plate-js')) || {}; } catch (e) { info = {}; }
    const panels = [...holder.querySelectorAll('.plate-holder > div')].map(d => d.innerText.trim());
    return {
      // Three panels: category, the country wordmark, then the number. The
      // code is what a person reads as the plate's first group.
      code: panels.length ? panels[0] : '',
      number: info.PlateNo || holder.getAttribute('data-ticketplate-number') || '',
      emirate: info.PlateSourceEnglishDesc || '',
      category: info.PlateColorEnglishDesc || '',
    };
  };
  const i = {
    plate: idx('plate'), amount: idx('amount'), ticket: idx('fine number'),
    when: idx('date'), source: idx('source'), points: idx('black'), status: idx('fine type'),
  };
  return [...table.querySelectorAll('tr')]
    .filter(tr => tr.querySelectorAll('td').length >= 8)
    .map(tr => {
      const tds = [...tr.querySelectorAll('td')];
      const at = k => (i[k] >= 0 && tds[i[k]]) ? cell(tds[i[k]]) : '';
      return {
        plate: (i.plate >= 0 && tds[i.plate]) ? plateOf(tds[i.plate]) : {},
        amount: at('amount'), ticket: at('ticket'),
        when: at('when'), source: at('source'), points: at('points'), status: at('status'),
      };
    })
    .filter(r => r.ticket);
}
"""

# DataTables here is client-side - no serverSide, no ajax - so paging is a DOM
# swap and the next page can simply be clicked. Driven off the active page
# number rather than a "next" arrow, because the arrow stays in the DOM at the
# end of the list and clicking it silently does nothing.
NEXT_PAGE_JS = """
() => {
  const pager = document.querySelector('#TicketsTable_paginate');
  if (!pager) return false;
  const active = pager.querySelector('.active a, .active, .current');
  const current = active ? parseInt(active.innerText.trim(), 10) : 1;
  if (!current) return false;
  const target = [...pager.querySelectorAll('a')]
    .find(a => parseInt(a.innerText.trim(), 10) === current + 1);
  if (!target) return false;
  target.click();
  return true;
}
"""

AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)")
# DD/MM/YYYY HH:MM. Read off the data, not assumed: across the captured page the
# first component reaches 21 while the second never exceeds 8.
DATETIME_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})\D+(\d{1,2}):(\d{2})")


class MoiFetcher(OperatorAssistedFetcher):
	"""Operator-assisted, and capture-first: it records before it reads."""

	session_filename = "moi.json"

	# Captured 2026-08-30. MOI issues this with no expiry - a true session
	# cookie - so session_status() reports "unknown" rather than a countdown:
	# whether it still works is something only the portal can answer. That is
	# the honest reading, and it is why the page carries an "Extend session"
	# button: the timeout is server-side and idle-based.
	session_cookie_name = ".MOI.SSO.SS"

	# The parse is written against the markup captured on 2026-08-30, not
	# against imagined selectors. What it still cannot do is *reach* the table
	# unattended - the search carries its own CAPTCHA - so this fetcher is
	# offered only where a person is present, and run_scheduled_operator_syncs
	# will never queue it while fetch_for_traffic_file demands an operator.
	fetch_implemented = True

	# The search form carries its own CAPTCHA, so no banked session makes this
	# unattended. Declared rather than discovered: without it the scheduled
	# sweep would queue a job every 45 minutes that is guaranteed to fail, and
	# fill the log with a failure that is really a design fact.
	supports_unattended = False

	# How long to wait for the operator to sign in, pick the company, enter the
	# profile number and answer the CAPTCHA. Measured in minutes, like TAMM's.
	results_timeout_ms = 2700000

	# Two pages at 40 rows covered a 52-fine profile; this only bounds a pager
	# that would otherwise cycle.
	max_pages = 25

	# A capture is a person driving the portal by hand: entering an identifier,
	# opening a result, paging through it. Slower than a scripted read, and it
	# ends when the operator closes the window.
	capture_window_ms = 1500000

	# MOI is ASP.NET WebForms: a search posts back to the SAME URL, so the
	# results table renders without the address changing. A capture watching
	# only the URL recorded five pages of forms and not one row of data - it
	# saw the operator arrive at each service and never saw them search.
	# Watching a content signature instead is what makes the results visible.
	SIGNATURE_JS = """() => {
	  const rows = document.querySelectorAll('table tr, [role=row]').length;
	  // Coarse, because the session countdown rewrites text constantly and a
	  // signature that tracked every character would snapshot on every poll.
	  const bulk = Math.floor((document.body ? document.body.innerText.length : 0) / 2000);
	  return document.location.href + '|' + rows + '|' + bulk;
	}"""

	def fetch_for_vehicle(self, plate_parts):
		raise NotImplementedError(
			"MOI is queried by traffic profile number, which returns the whole fleet in "
			"one search. A plate-at-a-time walk would mean one CAPTCHA per vehicle."
		)

	def fetch_for_traffic_file(self, traffic_file_number, include_details=False, wait_for_login=True):
		"""Read the fines the operator has searched for.

		**This never performs the search itself, and that is not an omission.**
		MOI puts a reCAPTCHA on the search form, not merely on sign-in, so there
		is no arrangement in which a program submits this query. The person
		signs in, chooses the company, enters the profile number and answers the
		challenge; automation starts at the table. `traffic_file_number` is
		therefore what the operator is asked *for*, and what the result is
		labelled with - it is never typed into the page by this code.

		Unattended callers get a refusal rather than an empty result. A run that
		quietly returned nothing here would be recorded as "this fleet has no
		fines", which is the one outcome that must never be inferred.
		"""
		if not wait_for_login:
			raise AuthenticationRequired(
				"MOI cannot be fetched unattended: its search form carries a CAPTCHA, so "
				"a person has to run the query. Nothing was read, and this must not be "
				"taken to mean the fleet has no fines."
			)

		page = self.start()
		page.set_default_navigation_timeout(120000)
		page.goto(ENTRY_URL, wait_until="domcontentloaded")
		self._settle(page)

		if not self._await_results(page):
			raise AuthenticationRequired(
				"No results table appeared. The operator has to sign in, select the "
				"company, enter the traffic profile number and answer the CAPTCHA before "
				"there is anything to read."
			)
		self.save_session()

		# Waiting on a person is not reading, and must not spend the budget.
		self.arm_deadline()

		rows, truncated = self._collect_all_pages(page)
		fines = [f for f in (self._to_fine(r) for r in rows) if f]

		message = _(
			"MOI reported {0} fine(s) for traffic profile {1}."
		).format(len(fines), traffic_file_number)
		if truncated:
			message += _(
				" The read stopped at its time limit before the list ran out, so this is a "
				"PARTIAL picture - fines beyond this point were never seen, and their "
				"absence here does not mean they do not exist."
			)
		return FetchResult(fines=fines, message=message, truncated=truncated)

	def _await_results(self, page):
		"""Wait for the operator to produce a results table. Never touches the form."""
		waited = 0
		while waited < self.results_timeout_ms:
			try:
				if page.evaluate("() => !!document.querySelector('#TicketsTable tbody tr td')"):
					return True
			except Exception:
				pass
			page.wait_for_timeout(self.poll_interval_ms)
			waited += self.poll_interval_ms
		return False

	def _collect_all_pages(self, page):
		"""Walk the pager, keyed on fine number so a repeated page merges harmlessly."""
		collected, truncated = {}, False
		for _ in range(self.max_pages):
			try:
				rows = page.evaluate(EXTRACT_ROWS_JS) or []
			except Exception:
				break
			for row in rows:
				collected[row["ticket"]] = row
			if self.out_of_time():
				# Out of budget with pages possibly unread. Reported rather than
				# treated as the end of the list.
				truncated = True
				break
			before = len(collected)
			try:
				if not page.evaluate(NEXT_PAGE_JS):
					break
			except Exception:
				break
			page.wait_for_timeout(1200)
			if len(collected) == before and not rows:
				break
		return list(collected.values()), truncated

	def _to_fine(self, row):
		ticket = (row.get("ticket") or "").strip()
		if not ticket or not ticket.isdigit():
			return None

		return FetchedFine(
			ticket_number=ticket,
			amount=self._amount(row.get("amount")),
			fine_datetime=self._datetime(row.get("when")),
			plate=self._plate_label(row.get("plate") or {}),
			black_points=self._points(row.get("points")),
			raw={
				"plate_code": (row.get("plate") or {}).get("code") or None,
				"plate_number": (row.get("plate") or {}).get("number") or None,
				"plate_emirate": (row.get("plate") or {}).get("emirate") or None,
				# MOI labels this column "Fine Type", but its values are Payable
				# and Unpayable - a status, not a type. Recorded as what it is.
				"portal_status": (row.get("status") or "").strip() or None,
				# The issuing authority, deliberately NOT mapped to
				# fine_location: it is not a location, and enrichment only fills
				# empty fields, so putting it there would block the real address.
				"source": (row.get("source") or "").strip() or None,
			},
		)

	@staticmethod
	def _amount(text):
		match = AMOUNT_RE.search(text or "")
		return float(match.group(1).replace(",", "")) if match else 0.0

	@staticmethod
	def _points(text):
		digits = re.sub(r"[^\d]", "", text or "")
		return int(digits) if digits else 0

	@staticmethod
	def _plate_label(plate):
		"""How the plate reads to a person: category then number."""
		parts = [plate.get("code"), plate.get("number")]
		return " ".join(p for p in parts if p) or None

	@staticmethod
	def _datetime(text):
		match = DATETIME_RE.search(text or "")
		if not match:
			return None
		day, month, year, hour, minute = (int(g) for g in match.groups())
		return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"

	# -- capture -----------------------------------------------------------
	def capture_signed_in(self):
		"""Record MOI's real pages while an operator is signed in.

		Snapshots on **every URL change** rather than trying to detect the
		moment login completes. Detecting that is precisely the part that
		cannot be got right without already knowing the pages, and getting it
		wrong means the capture sits waiting through the one sign-in we are
		allowed to ask for. Recording everything and sorting it out afterwards
		costs disk and nothing else.

		**Every page is written to disk the moment it is read.** A capture runs
		up to twenty-five minutes and ends when the operator closes the window -
		which reaches this code as an exception from Playwright, not a clean
		return. Holding the pages in memory until the end meant that ordinary
		ending threw away the entire capture, and a client sign-in cannot be
		asked for again on demand. Anything already seen survives now, whatever
		happens next.

		The session is banked the instant the browser is on an MOI page and off
		the sign-in hosts, so a later failure never costs a second UAE Pass push.

		Records cookie **names only, never values** - the names are what
		`session_cookie_name` needs; the values are the session itself.
		"""
		report_dir = self._open_report()
		seen_urls, banked, pages = [], False, 0

		try:
			page = self.start()
			page.set_default_navigation_timeout(120000)
			try:
				page.goto(ENTRY_URL, wait_until="domcontentloaded")
			except Exception:
				# A slow first load is not a failed capture - the operator is
				# about to drive this window by hand anyway.
				pass

			waited, last_signature = 0, None
			while waited < self.capture_window_ms:
				url = page.url or ""
				try:
					signature = page.evaluate(self.SIGNATURE_JS)
				except Exception:
					# Mid-navigation the context is gone; the next poll catches it.
					signature = last_signature
				if signature != last_signature and not self._on_auth_page(page):
					last_signature = signature
					self._settle(page)
					# Counted only once it is actually on disk. Incrementing
					# first meant a capture that threw while reading a page
					# reported having captured it.
					snapshot = self._snapshot(page, url)
					self._record_page(report_dir, pages + 1, snapshot)
					pages += 1
					seen_urls.append(url)
					if "moi.gov.ae" in url.lower():
						self.save_session()
						banked = True

				page.wait_for_timeout(self.poll_interval_ms)
				waited += self.poll_interval_ms
		except Exception as e:
			# The window closing is the normal ending, and it arrives here.
			# Recorded rather than raised: pages already on disk are the point.
			# Caught rather than returned from a `finally`, which would swallow
			# a KeyboardInterrupt and a worker shutdown along with it.
			self._note = str(e)[:300]

		return self._finish_report(report_dir, seen_urls, banked, pages)

	def _open_report(self):
		"""Make the directory pages are written into as they are read.

		Private files, owner-only: these pages carry a fleet's fine data, and
		this app's repository is public. Never the scratchpad, never the repo.
		"""
		stamp = frappe.utils.now().replace(":", "").replace(" ", "-").replace(".", "-")
		directory = frappe.get_site_path("private", "portal-captures", f"moi-{stamp}")
		os.makedirs(directory, exist_ok=True)
		os.chmod(os.path.dirname(directory), 0o700)
		os.chmod(directory, 0o700)
		return directory

	def _record_page(self, directory, index, snapshot):
		path = os.path.join(directory, f"page-{index:03d}.json")
		try:
			with open(path, "w") as fh:
				json.dump(snapshot, fh, indent=1)
			os.chmod(path, 0o600)
		except Exception:
			# One unwritable page must not end a capture that is still
			# collecting the others.
			pass

	def _cookie_names(self):
		"""Names only. The values are the live session and are never recorded."""
		try:
			return sorted({c.get("name") for c in self._context.cookies() if c.get("name")})
		except Exception:
			return []

	def _finish_report(self, directory, seen_urls, banked, pages):
		cookie_names = self._cookie_names()
		index = {
			"portal": self.portal.name,
			"captured_on": frappe.utils.now(),
			"entry_url": ENTRY_URL,
			"cookie_names": cookie_names,
			"session_banked": banked,
			"pages_captured": pages,
			"urls": seen_urls,
			"ended_with": getattr(self, "_note", None),
		}
		try:
			path = os.path.join(directory, "index.json")
			with open(path, "w") as fh:
				json.dump(index, fh, indent=1)
			os.chmod(path, 0o600)
		except Exception:
			pass

		return {
			"capture_file": directory,
			"pages_captured": pages,
			"urls": seen_urls,
			"cookie_names": cookie_names,
			"session_banked": banked,
		}

	def _snapshot(self, page, url):
		try:
			structure = page.evaluate(INSPECT_JS)
		except Exception as e:
			structure = {"error": str(e)}
		try:
			html = page.content() or ""
		except Exception:
			html = ""
		try:
			title = (page.title() or "")[:200]
		except Exception:
			title = ""
		return {"url": url, "title": title, "structure": structure, "html": html}
