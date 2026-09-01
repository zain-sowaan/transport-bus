# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Portals a person signs into, and the session banking that makes that bearable.

Two of the thirteen portals - TAMM and MOI - sit behind UAE Pass, whose web
flow ends with the user approving a push notification on a registered phone.
There is no server-to-server path to that, so the sign-in stays human and these
fetchers are built around it rather than against it: a real window opens, the
operator signs in themselves, and automation resumes once the session is live.

Nothing here types a credential, and nothing here answers a challenge. If a
CAPTCHA appears - MOI's login page is confirmed to carry one - the person in
front of the window answers it, which is not the same thing as a program
solving one. `assert_no_captcha()` is deliberately never called on these paths;
inheriting it into the login wait would abort the run the moment the operator's
own challenge rendered.

The session banking below is shared because getting it wrong is expensive in a
way that is easy to miss: a failure to persist costs a UAE Pass push that
cannot be re-requested on demand, and it fails silently.
"""

import os

import frappe

from transport.transport.fine_sync.base import SignInWindowClosed
from transport.transport.fine_sync.browser_fetcher import BrowserFetcher

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


class OperatorAssistedFetcher(BrowserFetcher):
	"""Shared machinery for the sign-in-and-bank-the-session portals."""

	# The whole point: the operator must be able to see and use this window.
	headless = False

	# UAE Pass involves a push notification to a phone, so the wait has to be
	# measured in minutes. It ends the moment the session is live, not on a timer.
	login_timeout_ms = 2700000
	poll_interval_ms = 3000
	# Long enough that a person is never interrupted mid-flow, short enough that
	# a completed login is picked up without them having to do anything else.
	renavigate_after_ms = 30000
	# The result table is rendered by a SPA some seconds after the navigation
	# resolves, so an unattended run that checks once the moment the page loads
	# reports "no session" for a session that is perfectly alive. Bounded, so a
	# genuinely dead session still fails fast rather than hanging a worker.
	unattended_grace_ms = 90000

	# Subclasses name the file their session is banked in.
	session_filename = None

	def __init__(self, portal, credential=None):
		super().__init__(portal, credential)
		self._context = None

	# -- session -----------------------------------------------------------
	def session_path(self):
		"""Where the signed-in session is kept between runs.

		A Chrome *profile directory* is not enough. These portals hand out
		**session cookies**, which a browser discards on close by design, so
		every run started from a profile alone demanded a fresh UAE Pass push -
		which is exactly the thing that makes an operator stop using this.
		Playwright's storage_state captures session cookies too, so restoring it
		into a new context resumes the session properly.

		It lives under the site's *private* files, never `public/files`, which
		is served over HTTP to anyone holding the path. This file is a live
		credential for a government portal; treat it as one.
		"""
		if not self.session_filename:
			return None

		directory = frappe.get_site_path("private", "portal-sessions")
		os.makedirs(directory, exist_ok=True)
		# makedirs' mode argument is filtered through the process umask, which on a
		# normal bench leaves this group- and world-readable. Set it outright.
		os.chmod(directory, 0o700)
		return os.path.join(directory, self.session_filename)

	def start(self):
		if self._page:
			return self._page

		from transport.transport.fine_sync.browser_fetcher import get_playwright, launch_chromium

		sync_playwright = get_playwright()
		self._pw = sync_playwright().start()
		self._browser = launch_chromium(self._pw, headless=self.headless)

		saved = self.session_path()
		options = {"viewport": {"width": 1500, "height": 950}}
		if saved and os.path.exists(saved):
			options["storage_state"] = saved

		# Kept on the instance because save_session() has nothing to persist
		# without it - and would return quietly rather than say so.
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
		if not (self._context and self.session_path()):
			return

		try:
			path = self.session_path()
			self._context.storage_state(path=path)
			# This file is a live government-portal session. Owner-only, always -
			# Playwright writes it with the default umask otherwise.
			os.chmod(path, 0o600)
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

	# -- login -------------------------------------------------------------
	def poll_wait(self, page, milliseconds=None):
		"""Sleep between login polls, and name it when the window disappears.

		Every operator-assisted wait sits in a poll loop for up to 45 minutes,
		so a closed window almost always surfaces here first. Playwright raises
		TargetClosedError from whichever call was in flight, and untranslated
		that reached the operator as a traceback ending in
		`Page.wait_for_timeout` - which says nothing about a window having been
		shut, and nothing about what to do next.

		Use this instead of `page.wait_for_timeout` anywhere a person is being
		waited on.
		"""
		try:
			page.wait_for_timeout(milliseconds or self.poll_interval_ms)
		except Exception as exc:
			if not self._window_gone(exc):
				raise
			raise SignInWindowClosed(
				"The sign-in window was closed before the sign-in finished, so nothing "
				"was fetched. Press Fetch Fines Now to open a fresh one - it waits up to "
				"45 minutes, and it looks idle the whole time it is waiting."
			) from exc

	@staticmethod
	def _window_gone(exc):
		"""True when Playwright is telling us the browser is simply not there.

		Matched on the message rather than the class so this holds if Playwright
		reorganises its exception hierarchy - the strings have been stable far
		longer than the module paths.
		"""
		detail = str(exc)
		return (
			"Target page, context or browser has been closed" in detail
			or "TargetClosedError" in type(exc).__name__
			or "Browser closed" in detail
		)

	def _on_auth_page(self, page):
		"""True while the browser is on a sign-in host we must not interrupt."""
		url = (page.url or "").lower()
		return any(marker in url for marker in AUTH_URL_MARKERS)
