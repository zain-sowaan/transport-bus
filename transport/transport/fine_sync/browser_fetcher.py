# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Headless-browser fetching, plus the capture mode that has to come first.

Playwright is imported lazily and is NOT a dependency of the app. The browser
belongs in a sidecar, not in the ERP runtime, and the app must keep working on
sites that will never run a fine sync. To enable it:

    ./env/bin/pip install playwright
    ./env/bin/playwright install chromium

**On the current state of every portal:** no backend request has been captured
for any of the 13, so no scraper can be written that would actually parse a
result page - the response shape is unknown. What can be done, and what
`capture_page()` below is for, is the authorized capture exercise itself:
drive to the portal, record the page and its form fields, and use that to
write the real selectors. Anything else would be invented.
"""

import frappe

from transport.transport.fine_sync.base import CaptchaEncountered, FineFetcher

# Text that indicates a CAPTCHA is present. We detect these to STOP - never to
# solve or work around them. A vehicle whose lookup hits one is reported as
# Failed so a human can complete it.
CAPTCHA_MARKERS = (
	"recaptcha",
	"g-recaptcha",
	"hcaptcha",
	"captcha",
)


def get_playwright():
	try:
		from playwright.sync_api import sync_playwright
	except ImportError:
		frappe.throw(
			frappe._(
				"Playwright is not installed in this bench. Install it with:<br>"
				"<code>./env/bin/pip install playwright</code><br>"
				"<code>./env/bin/playwright install chromium</code>"
			)
		)
	return sync_playwright


class BrowserFetcher(FineFetcher):
	"""Shared browser plumbing. Portal-specific subclasses supply the steps."""

	headless = True
	timeout_ms = 45000

	def __init__(self, portal, credential=None):
		super().__init__(portal, credential)
		self._pw = None
		self._browser = None
		self._page = None

	# -- lifecycle ---------------------------------------------------------
	def start(self):
		if self._page:
			return self._page

		sync_playwright = get_playwright()
		self._pw = sync_playwright().start()
		self._browser = self._pw.chromium.launch(headless=self.headless)
		context = self._browser.new_context()
		self._page = context.new_page()
		self._page.set_default_timeout(self.timeout_ms)
		return self._page

	def close(self):
		for closer in (
			lambda: self._page and self._page.context.close(),
			lambda: self._browser and self._browser.close(),
			lambda: self._pw and self._pw.stop(),
		):
			try:
				closer()
			except Exception:
				# Never let cleanup mask the real error from a fetch.
				pass
		self._page = self._browser = self._pw = None

	# -- guards ------------------------------------------------------------
	def assert_no_captcha(self):
		"""Stop the moment a CAPTCHA appears.

		Policy, not a limitation to engineer around: we do not solve, bypass or
		outsource CAPTCHAs. Two portals are already known to use one (Dubai RTA
		and the Abu Dhabi Police legacy sign-in) and eight more are unverified.
		"""
		content = (self._page.content() or "").lower()
		if any(marker in content for marker in CAPTCHA_MARKERS):
			raise CaptchaEncountered(
				f"{self.portal.name} presented a CAPTCHA. This lookup has to be completed by a person."
			)

	def _settle(self, page):
		"""Wait for the page to stop moving, tolerating a slow or chatty portal.

		Neither wait is guaranteed to fire - a portal that keeps a connection
		open never reaches networkidle - so both are best-effort and the
		caller proceeds regardless.
		"""
		for state in ("load", "networkidle"):
			try:
				page.wait_for_load_state(state, timeout=15000)
			except Exception:
				pass
		page.wait_for_timeout(1500)

	# -- capture -----------------------------------------------------------
	def capture_page(self, url=None):
		"""Record a portal page so its real contract can be written down.

		This is the A2 capture step. It reads the page and reports what is on
		it - form fields, whether a CAPTCHA is present, where it redirected -
		and writes nothing to the portal. Run it with written authorization
		and lawfully controlled test data, then use what it returns to
		implement the portal's fetch methods for real.
		"""
		page = self.start()
		target = url or self.portal.public_form_url
		if not target:
			frappe.throw(frappe._("{0} has no public form URL recorded.").format(self.portal.name))

		page.goto(target, wait_until="domcontentloaded")

		# These portals redirect client-side after the initial load - MOI's
		# direct service route bounces to its unified sign-in. Reading the DOM
		# while that is in flight throws "Execution context was destroyed", so
		# let the page settle first and retry once if it moves under us.
		self._settle(page)

		content, inputs = "", []
		for attempt in (1, 2):
			try:
				content = page.content() or ""
				inputs = page.eval_on_selector_all(
					"input, select, textarea",
					"""els => els.map(e => ({
						tag: e.tagName.toLowerCase(),
						type: e.getAttribute('type'),
						name: e.getAttribute('name'),
						id: e.getAttribute('id'),
						placeholder: e.getAttribute('placeholder')
					}))""",
				)
				break
			except Exception:
				if attempt == 2:
					raise
				self._settle(page)

		lowered = content.lower()

		return {
			"requested_url": target,
			"final_url": page.url,
			"redirected": page.url.rstrip("/") != target.rstrip("/"),
			"title": page.title(),
			"captcha_detected": any(m in lowered for m in CAPTCHA_MARKERS),
			"form_fields": inputs,
			"html_length": len(content),
			"html": content,
		}
