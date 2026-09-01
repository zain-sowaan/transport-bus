# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""The UAE Pass sign-in, driven without a window, with the approval left to a person.

**What this is and is not.** UAE Pass's own documentation settles the shape of
this: the web flow is an OAuth2 authorization code flow that ends with the user
confirming on a registered phone, there is no `client_credentials` grant for
identity, and no refresh token is documented on the standard web-application
token response. The one delegation-shaped feature - Data Sharing Authorization -
routes every request through a live person approving in the mobile app, and
states that consent "applies solely to the specific authorization request".
Their FAQ is explicit that "Authorization of individuals to represent an
organization is to be managed by the Service Provider post authentication."

So there is no sanctioned way to obtain a session without a human, and this
module does not pretend otherwise. What it removes is the *window*, not the
person: the browser runs headless on the server, this code types the operator's
own registered mobile number into the identity provider's own form, and then
**stops** and shows the operator what the screen says so they can confirm it on
their phone. The approval - the only part that is actually a security decision -
stays entirely with them.

Their Standard Implementation Guidelines say an SP "is recommended not to
initiate UAE Pass authentication on behalf of user". That is a recommendation
rather than a prohibition, it addresses relying-party integrations rather than
an operator automating their own sign-in, and it is flagged in the README for a
human to decide on. It is recorded here because the next person to read this
file deserves to know it exists.

**Three guards, each of which exists because of a specific near-miss.**

* **The number is typed only when the hostname *is* the identity provider's.**
  An earlier probe matched the substring "uaepass" against the whole URL, which
  also matches TAMM's own initiator link (`?provider=uaepass`) on
  `www.tamm.abudhabi` - a page whose first visible text input is the site-wide
  public search box. Matching on `urlparse().hostname` closes that.
* **The identifier field must be identified positively.** No "first visible text
  input" fallback. If nothing matches by id, name or type, this raises and dumps
  the page rather than guessing, for the same reason.
* **A CAPTCHA is a full stop.** Never solved, never outsourced. Headless has
  nobody in front of it to answer one, so it ends the run and says to use the
  window instead.

**The screen is shown as a picture, not as scraped text.** The confirmation
screen's markup has never been captured, so any selector for it would be a
guess - and a guess that renders the *wrong* two-digit code is worse than no
code at all, because the operator would then tap the wrong option on their
phone. A screenshot cannot be wrong about what the screen says. The text scrape
runs too, but is labelled provisional and shown beside the image, and the
instruction to the operator is that the image wins.
"""

import base64
import json
import os
import re
from urllib.parse import urlparse

import frappe

from transport.transport.fine_sync.base import (
	AuthenticationRequired,
	CaptchaEncountered,
	FineFetchError,
	safe_slug,
)

# Hostnames this module will type into. Exact matches, plus anything under the
# real domain, and never a substring test against a full URL.
UAE_PASS_HOSTS = {
	"id.uaepass.ae",
	"ids.uaepass.ae",
	"stg-id.uaepass.ae",
	"stg-ids.uaepass.ae",
}
UAE_PASS_DOMAIN = ".uaepass.ae"

# Markers that mean a WAF or bot check answered instead of the portal. Headless
# Chromium is the case these actually fire on, which is the whole reason the
# reachability probe exists.
BLOCK_MARKERS = (
	"access denied",
	"request blocked",
	"you have been blocked",
	"attention required",
	"incapsula",
	"cloudflare",
	"akamai",
	"reference #",
)

CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "g-recaptcha", "captcha", "turnstile")


class RelayNotPossible(FineFetchError):
	"""The headless relay cannot be used for this portal or in this state."""


# -- the number ------------------------------------------------------------
def normalize_mobile(raw):
	"""Return the number in the local `05...` form the sign-in actually wants.

	The field on the portal shows a `971500000000` placeholder, and an earlier
	version of this work inferred a requirement from it. That was wrong: the
	operator's successful sign-ins all used the local form. Placeholders
	describe a format loosely; they are not a contract.

	Accepts what a person would reasonably type - spaces, dashes, a leading
	`+971`, `00971` or a bare `5...` - and normalises rather than rejecting,
	because the alternative is a run that fails forty minutes in over a hyphen.
	"""
	digits = re.sub(r"\D", "", str(raw or ""))
	if not digits:
		return None

	if digits.startswith("00971"):
		digits = digits[5:]
	elif digits.startswith("971"):
		digits = digits[3:]

	if digits.startswith("0"):
		digits = digits[1:]

	# What is left should be a nine-digit local mobile starting with 5.
	if len(digits) == 9 and digits.startswith("5"):
		return "0" + digits
	return None


def get_relay_mobile():
	"""Read the registered number off Transport Settings.

	Read here rather than passed in as a job argument on purpose: RQ keeps job
	kwargs in Redis and Frappe renders them in the RQ Job list, which would put
	a personal phone number on a screen any System Manager can open.
	"""
	from transport.transport.fine_sync.service import _setting

	raw = _setting("uae_pass_mobile")
	if not raw:
		frappe.throw(
			frappe._(
				"No UAE Pass mobile number is set. Put the registered number on Transport "
				"Settings before using the code relay - it is the number the confirmation "
				"is pushed to, so the sign-in cannot start without it."
			),
			title=frappe._("UAE Pass Mobile Number Missing"),
		)

	mobile = normalize_mobile(raw)
	if not mobile:
		frappe.throw(
			frappe._(
				"The UAE Pass mobile number on Transport Settings is not a UAE mobile "
				"number. It should be the local ten-digit form starting 05."
			),
			title=frappe._("UAE Pass Mobile Number Invalid"),
		)
	return mobile


# -- where are we ----------------------------------------------------------
def on_uae_pass(page):
	"""True only when the *hostname* is the identity provider's.

	Deliberately not a substring test. `provider=uaepass` appears in TAMM's own
	sign-in link, so a substring match against the URL says yes while the
	browser is still sitting on a TAMM page - the page whose first text input is
	a public search box.
	"""
	host = (urlparse(page.url or "").hostname or "").lower()
	return host in UAE_PASS_HOSTS or host.endswith(UAE_PASS_DOMAIN)


def page_is_blocked(text):
	low = (text or "").lower()
	return any(marker in low for marker in BLOCK_MARKERS)


def page_has_captcha(html):
	"""Whether CAPTCHA machinery is present in the markup at all.

	Diagnostic only. Do NOT gate a run on this - see the note on
	`captcha_challenge_visible`, which is what a run should stop for.
	"""
	low = (html or "").lower()
	return any(marker in low for marker in CAPTCHA_MARKERS)


# What "a CAPTCHA is challenging" actually looks like in the DOM, established by
# reading the real page rather than by guessing at it twice:
#
#   iframe .../recaptcha/enterprise/anchor   256x60    inside .grecaptcha-badge
#   iframe .../recaptcha/enterprise/bframe   1498x150  the challenge modal
#   input.btn-login.g-recaptcha              420x58    the sign-in button itself
#
# All three are present on every load, and none of them is a challenge. The
# bframe IS the challenge modal, and it is always in the DOM - reCAPTCHA hides
# it by setting `visibility: hidden` on an ancestor, which neither
# `offsetParent` nor a size test detects. So the test has to be computed
# visibility up the ancestor chain, and the widget-class test has to exclude
# form controls, because invisible reCAPTCHA binds its class to the button.
CAPTCHA_CHALLENGE_JS = """
() => {
  const shown = el => {
    for (let n = el; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) {
        return false;
      }
    }
    const r = el.getBoundingClientRect();
    return r.width > 40 && r.height > 40;
  };

  // The challenge modal itself. The anchor iframe is the badge, not a challenge.
  // Plain string tests rather than a regex: the URLs contain slashes, and a
  // slash inside a JS regex literal has to be escaped, which does not survive
  // being written from Python without one backslash too many or too few.
  const isChallengeFrame = f => {
    const src = (f.getAttribute('src') || '').toLowerCase();
    if (!src) return false;
    return (src.includes('recaptcha') && src.includes('bframe'))
        || (src.includes('hcaptcha') && (src.includes('challenge') || src.includes('checkbox')))
        || src.includes('turnstile');
  };
  if ([...document.querySelectorAll('iframe')].some(f => isChallengeFrame(f) && shown(f))) {
    return true;
  }

  // A rendered checkbox widget, which is a container element. Never a button:
  // invisible reCAPTCHA puts `g-recaptcha` on the submit control, and that is a
  // button being clicked, not a person being asked something.
  return [...document.querySelectorAll('.g-recaptcha, .h-captcha, .cf-turnstile')]
    .filter(el => !['input', 'button'].includes(el.tagName.toLowerCase()))
    .filter(el => !el.classList.contains('grecaptcha-badge'))
    .some(shown);
}
"""


def captcha_challenge_visible(page):
	"""True only when a CAPTCHA is actually asking a person something.

	The distinction matters, and getting it wrong stops a run that had nothing
	wrong with it. UAE Pass's sign-in carries `class="btn-login g-recaptcha"` on
	its submit button - **invisible** reCAPTCHA Enterprise, which scores the
	session silently and renders no challenge. A substring test for "g-recaptcha"
	in the markup is therefore true on every single load, and gating on it
	aborted the relay before it typed anything, on a page that in a week of real
	sign-ins has never once challenged. A size-and-visibility test was no better
	on its own: it called the 420x58 login button a challenge.

	Policy is unchanged and is not what this loosens: if a challenge is put on
	screen, the run stops. Nothing here solves, answers or outsources one - and
	headless has nobody in front of it who could. This only stops calling the
	scorer's presence a challenge.
	"""
	try:
		return bool(page.evaluate(CAPTCHA_CHALLENGE_JS))
	except Exception:
		# Cannot tell. Say no rather than aborting a healthy run on a page that
		# happened to be mid-navigation; a real challenge blocks the sign-in
		# anyway and surfaces as a timeout with the screen recorded.
		return False


# -- the page --------------------------------------------------------------
# Positively identified, in confidence order: the observed id, then a named
# field, then a telephone input. There is no "first visible text input" branch,
# and there must never be one.
IDENTIFIER_JS = """
() => {
  const vis = el => el.offsetParent !== null && el.type !== 'hidden' && el.type !== 'password';
  const els = [...document.querySelectorAll('input')].filter(vis);
  const named = re => els.find(e =>
    re.test(e.id || '') || re.test(e.getAttribute('name') || ''));

  const hit = named(/^username$/i)
           || named(/^(mobile|msisdn|mobileNumber|emiratesId|eid)$/i)
           || els.find(e => e.type === 'tel')
           || named(/(mobile|msisdn|username|emirates)/i);
  if (!hit) return null;
  hit.setAttribute('data-relay-target', '1');
  return {
    id: hit.id || '', name: hit.getAttribute('name') || '', type: hit.type || '',
    placeholder: hit.getAttribute('placeholder') || '',
  };
}
"""

SUBMIT_JS = """
() => {
  const vis = el => el.offsetParent !== null && !el.disabled;
  const forms = [...document.querySelectorAll('button[type=submit], input[type=submit]')].filter(vis);
  let hit = forms[0];
  if (!hit) {
    hit = [...document.querySelectorAll('button, a[role=button]')].filter(vis)
      .find(b => /^(login|log in|sign in|continue|next|submit)$/i.test((b.innerText || '').trim()));
  }
  if (!hit) return null;
  hit.setAttribute('data-relay-submit', '1');
  return (hit.innerText || hit.value || '').trim().slice(0, 40) || '(unlabelled)';
}
"""

# The confirmation screen has never been captured, so this looks for the SHAPE
# of a match code - a short run of digits standing alone in a leaf element -
# and reports every candidate with enough context to judge it, rather than
# asserting one answer. Ranking is by whether the surrounding text talks about
# matching a code; unranked candidates are still returned.
MATCH_CODE_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (el.children.length) continue;
    const text = (el.innerText || '').trim();
    if (!/^\\d{1,3}$/.test(text)) continue;
    if (el.offsetParent === null) continue;
    let context = '';
    for (let n = el.parentElement, hops = 0; n && hops < 4; n = n.parentElement, hops++) {
      context = (n.innerText || '').trim();
      if (context.length > 20) break;
    }
    out.push({
      value: text,
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      cls: (el.getAttribute('class') || '').slice(0, 70),
      context: context.replace(/\\s+/g, ' ').slice(0, 160),
      likely: /match|code|number|confirm|approve|app|select/i.test(context),
    });
  }
  out.sort((a, b) => (b.likely ? 1 : 0) - (a.likely ? 1 : 0));
  return out.slice(0, 12);
}
"""

# Structure only. No input VALUES are ever read - the one thing on this page
# worth protecting is what was typed into it.
FRAME_JS = """
() => {
  const describe = el => ({
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    id: el.id || '',
    name: el.getAttribute('name') || '',
    cls: (el.getAttribute('class') || '').slice(0, 90),
    placeholder: el.getAttribute('placeholder') || '',
    aria: el.getAttribute('aria-label') || '',
    text: (el.innerText || '').trim().slice(0, 60),
    visible: el.offsetParent !== null,
  });
  return {
    url: document.location.href,
    title: document.title,
    inputs: [...document.querySelectorAll('input, select')].map(describe),
    buttons: [...document.querySelectorAll('button, a[role=button], input[type=submit]')]
             .map(describe).filter(b => b.text || b.id || b.name),
    headings: [...document.querySelectorAll('h1, h2, h3, label, p')]
              .map(e => (e.innerText || '').trim()).filter(t => t && t.length < 200).slice(0, 40),
    captcha: /recaptcha|hcaptcha|g-recaptcha|captcha|turnstile/i.test(document.documentElement.innerHTML),
    body: (document.body ? document.body.innerText : '').replace(/\\s+/g, ' ').slice(0, 1200),
  };
}
"""


def read_frame(page):
	"""Structure of whatever is on screen, or an error record saying why not."""
	try:
		return page.evaluate(FRAME_JS)
	except Exception as exc:
		return {"url": page.url or "", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def read_match_code(page):
	"""Candidate confirmation codes, best guess first. May legitimately be empty."""
	try:
		return page.evaluate(MATCH_CODE_JS) or []
	except Exception:
		return []


def screenshot_data_uri(page, quality=60):
	"""The screen as a picture, for showing the operator what to confirm.

	Returned as a data URI and never written to disk. It is the one artefact
	here that may carry the typed number as rendered pixels, so it goes to the
	person who started the run and nowhere else.

	Size was measured rather than assumed, because this is re-sent every few
	seconds and a payload over the socket buffer would deliver *nothing* - the
	dialog would sit on "Starting..." and read exactly like the hang this was
	built to avoid. The UAE Pass sign-in at 1500x950 comes to 36 KB as a data
	URI at quality 60 (30 KB at 40, 26 KB at 25), comfortably inside the limit,
	so there is no quality ladder here. Re-measure before raising the viewport.
	"""
	try:
		raw = page.screenshot(type="jpeg", quality=quality, full_page=False)
	except Exception:
		return None
	return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


# -- capture ---------------------------------------------------------------
class LoginFrameRecorder:
	"""Writes the sign-in screens to disk as they change, so the next run is exact.

	Every selector in this module for the *confirmation* screen is inferred,
	because that screen only renders after a real push and has therefore never
	been recorded. This recorder is how that stops being true: it costs a JSON
	file per distinct screen during a sign-in somebody was doing anyway.

	Owner-only under the site's private files, alongside the existing portal
	captures - never the repository, never a scratch directory.
	"""

	max_frames = 40

	def __init__(self, portal_name, tag="login"):
		self.portal_name = portal_name
		self.tag = tag
		self.directory = None
		self.count = 0
		self._last_signature = None

	def _open(self):
		if self.directory:
			return self.directory
		stamp = frappe.utils.now().replace(":", "").replace(" ", "-").replace(".", "-")
		# safe_slug, not frappe.scrub: scrub passes a slash straight through, and
		# "Abu Dhabi Police / TAMM" then became a nested directory whose frames
		# landed a level below where anything looked for them.
		directory = frappe.get_site_path(
			"private", "portal-captures", f"{self.tag}-{safe_slug(self.portal_name)}-{stamp}"
		)
		os.makedirs(directory, exist_ok=True)
		# makedirs' mode is filtered through the umask, which on a normal bench
		# leaves this readable by everyone. Set both levels outright.
		os.chmod(os.path.dirname(directory), 0o700)
		os.chmod(directory, 0o700)
		self.directory = directory
		return directory

	def record(self, page, note=None):
		"""Write this screen if it differs from the last one. Never fatal.

		Signature is coarse on purpose: these pages run countdown timers, and a
		signature tracking every character would write a file every poll for
		forty-five minutes.
		"""
		# Checked before the evaluate, not after it. The headed sign-in polls
		# every three seconds for up to forty-five minutes; testing the cap
		# afterwards left it running a full DOM walk on every one of those polls
		# long after it had stopped writing anything.
		if self.count >= self.max_frames:
			return False
		try:
			frame = read_frame(page)
			signature = "{}|{}|{}".format(
				frame.get("url", ""),
				len(frame.get("inputs") or []),
				len(frame.get("body") or "") // 200,
			)
			if signature == self._last_signature:
				return False
			self._last_signature = signature

			frame["_note"] = note
			frame["_recorded_at"] = frappe.utils.now()
			frame["_match_code_candidates"] = read_match_code(page)

			path = os.path.join(self._open(), f"frame-{self.count + 1:03d}.json")
			with open(path, "w") as fh:
				json.dump(frame, fh, indent=1)
			os.chmod(path, 0o600)
			self.count += 1
			return True
		except Exception:
			# A capture is a diagnostic. It must never be the reason a sign-in
			# that was working fails.
			return False


# -- the relay -------------------------------------------------------------
def relay_sign_in(page, announce, recorder=None, timeout_ms=300000, poll_ms=4000):
	"""Type the number, submit, then show the operator the screen until it clears.

	Returns when the browser leaves the identity provider - which is what
	completing the sign-in looks like from here. Raises with a readable reason
	otherwise; the caller turns that into a Failed run.

	`announce(payload)` is called on every poll rather than once, so a person
	who reloads their browser gets the current screen back within a few seconds
	instead of losing the code entirely. Nothing about the screen is persisted
	to make that work, which is the point: the picture is live-only.

	The timeout is minutes, not the forty-five the windowed path allows. Nobody
	is being waited on to walk to a machine here - the operator is watching the
	screen already, and the push itself expires long before three quarters of an
	hour.
	"""
	if not on_uae_pass(page):
		raise RelayNotPossible(
			"The browser never reached UAE Pass, so there was no sign-in form to fill. "
			f"It stopped on {urlparse(page.url or '').hostname or 'nowhere'} instead. "
			"Run Test Headless Reach on the portal to see what that page was."
		)

	if recorder:
		recorder.record(page, note="uae-pass-login-form")

	if captcha_challenge_visible(page):
		raise CaptchaEncountered(
			"UAE Pass put a CAPTCHA challenge on screen. Nothing here will answer one, and "
			"headless has nobody in front of it who could. Switch Sign-In Mode to 'Operator "
			"signs in at the server' and complete it in the window."
		)

	target = page.evaluate(IDENTIFIER_JS)
	if not target:
		if recorder:
			recorder.record(page, note="no-identifier-field")
		raise RelayNotPossible(
			"UAE Pass loaded but no sign-in field could be identified with confidence, so "
			"nothing was typed. The screen has been recorded under private/portal-captures "
			"so the field can be named exactly rather than guessed at."
		)

	# Only ever reached on the identity provider's own host, with a field that
	# was positively identified. Both conditions, every time.
	page.fill("[data-relay-target='1']", get_relay_mobile())

	announce({
		"stage": "submitting",
		"message": frappe._("Signing in to UAE Pass..."),
	})

	clicked = page.evaluate(SUBMIT_JS)
	if not clicked:
		if recorder:
			recorder.record(page, note="no-submit-control")
		raise RelayNotPossible(
			"The number was entered but no sign-in button could be found to submit it. "
			"The screen has been recorded under private/portal-captures."
		)
	# Clicked through Playwright rather than from inside the page. UAE Pass binds
	# invisible reCAPTCHA to this very button (`class="btn-login g-recaptcha"`),
	# and a real click dispatches the full event sequence its handler expects
	# where a scripted `.click()` dispatches a bare one.
	page.click("[data-relay-submit='1']")

	waited = 0
	while waited < timeout_ms:
		page.wait_for_timeout(poll_ms)
		waited += poll_ms

		if not on_uae_pass(page):
			# Off the identity provider means the confirmation landed and the
			# redirect back has happened.
			announce({
				"stage": "signed-in",
				"message": frappe._("Confirmed. Reading fines now."),
			})
			if recorder:
				recorder.record(page, note="post-login")
			return True

		if recorder:
			recorder.record(page, note="awaiting-confirmation")

		candidates = read_match_code(page)
		announce({
			"stage": "awaiting-confirmation",
			"message": frappe._(
				"Open UAE Pass on your phone and confirm the request. Match what your "
				"phone shows against the screen below."
			),
			"screen": screenshot_data_uri(page),
			# Labelled provisional wherever it is shown. The picture is the
			# thing that cannot be wrong about what the screen says.
			"code_guess": candidates[0]["value"] if candidates else None,
			"seconds_left": max(0, (timeout_ms - waited) // 1000),
		})

	raise AuthenticationRequired(
		"UAE Pass was not confirmed in time, so nothing was fetched. The push expires on "
		"its own - press Fetch Fines Now to start a fresh one."
	)
