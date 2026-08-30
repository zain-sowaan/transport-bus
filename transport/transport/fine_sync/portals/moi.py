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

**What is still missing, and why there is no parse below.** The post-login pages
have never been seen. Which identifier MOI accepts (traffic file, plate,
driving licence), where the results live, how that table is marked up, how it
paginates, and what its session cookie is called are all unknown. None of it is
guessable, and guessing has a specific failure mode this design exists to
prevent: a scraper written against imagined selectors does not crash, it returns
zero rows, and zero rows reads as "this fleet has no fines".

So `fetch_implemented` is False and both fetch methods refuse. The next step is
`capture_signed_in()` below, run once with the operator signed in - it records
what the pages actually are, and the parse gets written from that.
"""

import json
import os

import frappe

from transport.transport.fine_sync.base import FineFetchError
from transport.transport.fine_sync.operator_fetcher import OperatorAssistedFetcher

ENTRY_URL = "https://portal.moi.gov.ae/eservices/direct?scode=486"

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


class MoiFetcher(OperatorAssistedFetcher):
	"""Operator-assisted, and capture-first: it records before it reads."""

	session_filename = "moi.json"

	# Unknown until a capture reports MOI's cookie names. Left None deliberately
	# so session_status() answers "no datable session" instead of inventing a
	# reading the portal banner would then present to an operator as fact.
	session_cookie_name = None

	# No parse exists. This gates the Fetch Fines button so it never offers
	# something that cannot happen - delete this line in the same edit that
	# writes the real fetch.
	fetch_implemented = False

	# A capture is a person driving the portal by hand: entering an identifier,
	# opening a result, paging through it. Slower than a scripted read, and it
	# ends when the operator closes the window.
	capture_window_ms = 1500000

	def fetch_for_vehicle(self, plate_parts):
		raise FineFetchError(NOT_YET_CAPTURED)

	def fetch_for_traffic_file(self, traffic_file_number, **kwargs):
		raise FineFetchError(NOT_YET_CAPTURED)

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

			waited, last_url = 0, None
			while waited < self.capture_window_ms:
				url = page.url or ""
				if url != last_url and not self._on_auth_page(page):
					last_url = url
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
