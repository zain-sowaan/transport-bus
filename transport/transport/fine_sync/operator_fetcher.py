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
from transport.transport.fine_sync.uae_pass import (
	LoginFrameRecorder,
	captcha_challenge_visible,
	on_uae_pass,
	page_has_captcha,
	page_is_blocked,
	read_frame,
)

HEADLESS_USER_AGENT = (
	"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
	"Chrome/131.0.0.0 Safari/537.36"
)

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

	# Whether this portal's sign-in can be relayed - the number typed headlessly
	# and the confirmation shown to the operator to approve on their phone.
	# False by default and stated per portal, because a portal that challenges
	# on the *search* rather than only at sign-in can never be relayed, and a
	# global switch would silently hang it waiting for a window nobody can see.
	supports_relay = False

	# Set by the caller when a person pressed a button and is watching a screen.
	# `relay_announce` receives one dict per poll and is responsible for getting
	# it in front of them.
	use_relay = False
	relay_announce = None

	# Minutes, not the forty-five the windowed path allows. Nobody is being
	# waited on to walk to a machine - the operator is already looking at the
	# screen, and the UAE Pass push expires well inside this.
	relay_timeout_ms = 300000

	# Whether start() restores the banked sign-in. The reach probe turns this
	# off: it is asking whether the sign-in FORM is reachable, so a live session
	# is irrelevant to its answer and actively harmful to have. Two browsers on
	# one TAMM session is what the portal answers by invalidating it, costing
	# the operator a fresh UAE Pass push - which is the exact defect the portal
	# lock exists to prevent, and a probe restoring the session would have
	# reintroduced it through a path that never takes that lock. With a live
	# session TAMM could also short-circuit the sign-in hop entirely, and the
	# probe would then report "did not reach UAE Pass" for a healthy setup.
	use_saved_session = True

	def __init__(self, portal, credential=None):
		super().__init__(portal, credential)
		self._context = None
		self._recorder = None

	# -- capture -----------------------------------------------------------
	def recorder(self):
		"""The sign-in screen recorder, made on first use.

		Every selector written for the UAE Pass *confirmation* screen is an
		inference, because that screen only renders after a real push and has
		never been recorded. This is how that stops being true, at the cost of
		a JSON file per distinct screen during a sign-in that was happening
		anyway. Made lazily so a run that never reaches a login writes nothing.
		"""
		if self._recorder is None:
			self._recorder = LoginFrameRecorder(self.portal.name)
		return self._recorder

	def record_login_frame(self, page, note=None):
		self.recorder().record(page, note=note)

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

		saved = self.session_path() if self.use_saved_session else None
		options = {"viewport": {"width": 1500, "height": 950}}
		if saved and os.path.exists(saved):
			options["storage_state"] = saved
		if self.headless:
			# Playwright's default headless user agent contains the literal
			# string "HeadlessChrome", which is the first thing a WAF filters
			# on. Stating an ordinary one is not evasion of a challenge - no
			# challenge is being answered here, and a CAPTCHA still stops the
			# run outright. It is so that a portal the operator is entitled to
			# use serves the same page it serves their own browser.
			options["user_agent"] = HEADLESS_USER_AGENT

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

	# -- relay -------------------------------------------------------------
	def announce(self, payload):
		"""Push one progress update towards whoever is watching. Never fatal."""
		if not self.relay_announce:
			return
		try:
			self.relay_announce(payload)
		except Exception:
			# Losing a progress message must not fail a sign-in that is working.
			pass

	def relay_entry_url(self, traffic_file_number=None):
		"""The URL that starts the sign-in without a human clicking through.

		Subclasses that support the relay must override this. The default
		refuses rather than guessing, because guessing here means a headless
		browser wandering a portal's SPA looking for a button.
		"""
		raise NotImplementedError

	def probe_headless_reach(self, traffic_file_number=None):
		"""Find out whether headless can even reach the sign-in form. Types nothing.

		This exists because the relay rests on an assumption that had never been
		tested: that a headless Chromium gets the same page a windowed one does.
		Government portals sit behind WAFs, this one has already served a block
		page for a mistaken URL shape, and a headless browser is the classic
		thing such a filter turns away. Finding that out during a demo is how
		this went wrong once already.

		Deliberately stops before the form is filled. It needs no operator, no
		push and no phone, so it can be run any time - which is the point.
		"""
		self.headless = True
		# Never on the operator's banked session - see use_saved_session.
		self.use_saved_session = False
		page = self.start()
		try:
			page.set_default_navigation_timeout(120000)
			page.goto(self.relay_entry_url(traffic_file_number), wait_until="domcontentloaded")
			self._settle(page)
			self.record_login_frame(page, note="headless-reach-probe")

			frame = read_frame(page)
			body = frame.get("body") or ""
			html = ""
			try:
				html = page.content() or ""
			except Exception:
				pass

			reached = on_uae_pass(page)
			field = None
			if reached:
				from transport.transport.fine_sync.uae_pass import IDENTIFIER_JS

				try:
					# Marks the field with an attribute and reports it. Nothing is
					# typed - this is the probe, and it stops here by design.
					field = page.evaluate(IDENTIFIER_JS)
				except Exception:
					field = None

			return {
				"probed_with_saved_session": False,
				"reached_uae_pass": reached,
				"final_host": frame.get("url", "").split("/")[2] if "//" in frame.get("url", "") else None,
				"blocked": page_is_blocked(body),
				# Reported as two separate readings because they mean opposite
				# things for whether the relay can run. UAE Pass loads invisible
				# reCAPTCHA on every sign-in - script present, nothing asked -
				# and treating that as a challenge stops a run that was fine.
				"captcha_script_present": page_has_captcha(html),
				"captcha_challenge_on_screen": captcha_challenge_visible(page),
				"identifier_field": field,
				"title": frame.get("title"),
				"capture_dir": self.recorder().directory,
				"frames": self.recorder().count,
			}
		finally:
			self.close()
