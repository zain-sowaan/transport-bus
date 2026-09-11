# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Roads & Transport Authority (RTA), Dubai.

**Registered, and deliberately cannot fetch yet.** Read this before adding
selectors, because the missing piece is not code.

The portal record carried a settled decision, dated 2026-08-22: RTA is the only
one of the thirteen portals with a conventional username/password form, which
briefly made it the one place a stored credential could have helped - until an
OTP was confirmed to follow the password. That ended unattended sync, and the
portal was parked at "Operator Assisted is the correct and final state".

That decision is not being overturned; it is being answered on a different axis.
It rules out *unattended* sync, and every word of it still holds for the
scheduler. The browser extension is the attended path: a person signs into RTA
in their own Chrome, answers their own OTP and their own CAPTCHA (RTA is one of
the two portals `browser_fetcher.assert_no_captcha` names by name), and the
extension reads the page that results. An OTP is not an obstacle to someone who
is already sitting there.

So the route is sound. What is missing is the one thing that cannot be reasoned
out: **nobody has seen RTA's fines table.** Every capture under
`private/portal-captures/` is TAMM. `portal_url` on the record is empty. The
table is behind the login, so `capture_portal_page` reaches the sign-in form and
stops there.

`fetch_implemented` and `client_reader` are therefore both left at their refusing
defaults, and this class exists to make the refusal say *why*. Writing plausible
selectors would produce a run reporting `fines_found = 0, status = Completed`,
which every operator would read as "this fleet has no fines in Dubai". That
sentence is the most expensive thing this module can produce and it must never
be produced by guessing.

## Observed 2026-09-10, from an operator's signed-in session

A first look at the live portal, by a person with the account. It moves the
blocker but does not clear it, and it corrects one thing this module assumed.

**Three hosts, not one.** They do different jobs and only the third matters:

* `www.rta.ae` - IBM WebSphere portal shell. Its home page carries a "Check
  Your Fines" widget: a radio pair (by traffic file / by plate), an input
  `#trafficFileNo`, and a Search button. The traffic file number is
  **eight digits** - `maxlength="8" pattern="\\d{8}"` - which is worth
  validating before a fetch rather than after the portal refuses one.
* `ums.rta.ae` - a React app (`#root`) serving `/violations/public-fines/
  fines-search`. Four search modes as a slick carousel, switched by
  `div.loginIconsView`, not by anything with `role="tab"`. Its inputs carry
  stable ids (`#Id_trafficFileNumber`, `#Id_plateNumber`, ...). This is a
  search front end; it is not where the itemised list lives.
* `traffic.rta.ae` - the legacy Struts application, and **the strongest lead
  for where the fines table lives**. Two paths point into it and they are not
  the same one, which is exactly why neither is treated as settled here:

      https://traffic.rta.ae/trfesrv/public_resources/ffu/fines-payment.do
          - a footer link on www.rta.ae, labelled "Fines Inquiry and Payment"

      https://traffic.rta.ae/trfesrv/public_resources/.../public-fines-payment
          - read off the status bar while hovering "View more details" on the
            summary widget, so the middle of the path was never visible

  Neither page has been loaded. What the host suggests is a `.do` Struts app
  with CSRF tokens in its URLs, which if it holds the rows would mean real
  server-rendered markup and none of TAMM's single-page-app timing problems -
  but that is an inference from the URL shape, not an observation. Do not write
  selectors against it until someone has opened it.

**The widget answers a cheaper question than the table does.** A traffic file
search on the home page returns a summary only - a fine count and a total, with
a "View more details" link out to `traffic.rta.ae`. That is not a substitute
for the itemised list, but it is a genuine reachability probe: it says whether
this traffic file has anything worth a full fetch, in one request and without
the deep page.

**The login premise stands, with a correction.** The `ums.rta.ae` search *form*
renders while the header still offers "Login", which briefly suggested this
portal was SRTA-shaped - a public form needing no session. It is not. The
operator reports being sent to sign in, and only then reaching the gated pages.
Read the rendered form as a public front door to an authenticated service, not
as public access. `supports_unattended = False` is unchanged and unchallenged.

**reCAPTCHA is confirmed, on a sibling service.** RTA's public-transport fines
page (`Pay Fines Related to Public Transport`) calls `grecaptcha.getResponse()`
in its own search handler. That is a different service from traffic fines, so
it does not prove the traffic file route challenges - but it does confirm the
mechanism is deployed on this estate, which is what `browser_fetcher.
assert_no_captcha` already assumed for RTA.

**Still missing: the fines table itself.** Four pages were captured from the
signed-in session - the home page, the dashboard, the linking/subscription
page, and public-transport fines. None contains a fines table; all four have
zero `<tr>` elements, because each is the portal shell rather than the deep
page. The one page that matters, `ffu/fines-payment.do` rendered for a traffic
file, has still never been seen.

So the gap has narrowed from "somewhere behind the login" to a single named
URL and a single click. It has not closed.

## Read 2026-09-11, and the reader is written from what was seen

The observation above arrived. The list was read end to end against the live
portal, and each of the three extraction functions was run there before being
written here - none of them is inferred.

What the page turned out to be:

* **The itemised list is on `ums.rta.ae`, not `traffic.rta.ae`.** The earlier
  note guessed the legacy Struts app from a footer link and a status-bar hover.
  Wrong: submitting the traffic-file search navigates to
  `/violations/public-fines/customer-violations`, a PrimeReact table, and that
  is where the fines are.
* **The route is public.** The search was run, and the full list read, while the
  page header still offered "Login". The sign-in the operator did buys the
  dashboard and the linking pages, not this inquiry.
* **Three result tabs**, `#Id_FinesTab` (Payable), `#Id_ViolationsTab`
  (Non-Payable) and `#Id_BlackpointsTab`. Only the first is read today, which is
  a real coverage gap and is recorded as one - see below.
* **The list identifies the vehicle but not the fine.** Each row shows a vehicle
  description, date, amount, source and black points. The Fine Number and the
  plate appear only in the detail panel, which is why details are mandatory here
  and merely enriching on TAMM.
* **The row key is not the fine number.** RTA tags each row's checkbox
  `aria-label="Row Selected CTCK_<digits>"` for parking fines and `TCK_<digits>`
  for police ones. That is an internal key in a different space from the Fine
  Number, and staging it would mint a fine no operator can find on the portal.

## What is still open

* **Two of the three lists have never been seen with rows in them.** The reader
  now visits all three and tags every row with the list it came from, so a
  non-payable fine is no longer recorded as payable, and a list that could not
  be read is named in the run's own message rather than folded into a total.
  But the fleet this was written against had nothing under Non-Payable or Black
  Points, so their row markup is *assumed* to be the same component, not
  observed to be. If that assumption is wrong those lists report zero -
  visibly, which is why coverage is reported per list, but still wrongly.
  Confirm against a traffic file that actually has some.
* **RTA expires the results page.** Observed directly: the tab ended up on
  `/session-expired` partway through exploring it. The reader watches for that
  path and reports it as its own outcome instead of reading the empty page as a
  clean fleet - but nobody has measured how large a traffic file can be before
  one sitting is not enough.
* **Whether a server can run this repeatedly.** See `SERVER_FETCH_REASON`.
"""

import re

from transport.transport.fine_sync.base import FetchedFine, FineFetchError
from transport.transport.fine_sync.operator_fetcher import OperatorAssistedFetcher

SEARCH_URL = "https://ums.rta.ae/violations/public-fines/fines-search"
RESULTS_PATH = "/violations/public-fines/customer-violations"

# "AED 200" - RTA prints whole dirhams here, unlike TAMM's two decimals, so this
# must not require a decimal part or every amount reads as zero.
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d{1,2})?)")

# RTA writes its own row key as CTCK_<digits> for parking fines and TCK_<digits>
# for police ones. It is NOT the Fine Number an operator would recognise - that
# only appears in the detail panel - so it is kept for traceability and never
# used as the ticket number.
ROW_KEY_RE = re.compile(r"\b(C?TCK)_(\d+)\b")

# RTA's three lists, mapped onto what the staging doctype's portal_status Select
# will actually accept: "", "Payable", "Unpayable".
#
# This mapping is not cosmetic - writing RTA's own wording straight through is a
# ValidationError inside _stage_fine, and because that raises mid-loop it fails
# the WHOLE run after some fines have already staged. The run then reads Failed
# while carrying real rows, which is the worst of both answers.
#
# "Non-Payable" maps to "Unpayable" because they mean the same thing and TAMM
# got there first: a fine that cannot be settled on the portal and has to go
# through the issuing authority. RTA's own subtitle for the payable list says
# exactly that - "fines that can be cleared without referring to the source
# authority". Black points sit under a tab RTA labels "Payable Black Points",
# so they are payable.
PORTAL_STATUS_BY_LIST = {
	"Payable": "Payable",
	"Non-Payable": "Unpayable",
	"Black Points": "Payable",
}


# Each row is a stack of <div><span>Label</span>Value</div>, except the first,
# which carries the vehicle description and has no label at all. Reading the
# span as the key beats mapping column positions: RTA's own sort control
# reorders rows, and a future column added in the middle would silently shift
# every positional index by one.
EXTRACT_ROWS_JS = """
() => [...document.querySelectorAll('div.finesRowList')].map(list => {
  const row = {};
  const cells = [...list.children];

  // First cell, no label: "MAKE MODEL, YEAR, COLOUR". Kept whole rather than
  // split - the make can itself contain a comma, and nothing downstream needs
  // the pieces.
  if (cells.length) row.vehicle = (cells[0].innerText || '').trim();

  cells.slice(1).forEach(cell => {
    const label = cell.querySelector('span');
    if (!label) return;
    const key = (label.textContent || '').trim().replace(/:$/, '');
    // The value is everything the label is not. Subtracting the label's own
    // text is what keeps "Amount" out of "Amount AED 200".
    const value = (cell.innerText || '').replace(label.textContent || '', '').trim();
    if (key) row[key] = value;
  });

  // RTA's internal row key, on the selection checkbox. Recorded because it is
  // the only identifier present before a detail panel is opened, which makes it
  // the one way to tell "this row was read" from "this row was skipped".
  const tr = list.closest('tr');
  const box = tr && tr.querySelector('input.p-checkbox-input');
  const aria = (box && box.getAttribute('aria-label')) || '';
  const key = aria.match(/\\b(C?TCK)_(\\d+)\\b/);
  if (key) { row._rowKind = key[1]; row._rowId = key[2]; }

  return row;
}).filter(row => Object.keys(row).length > 1)
"""

# PrimeReact's paginator, which marks its own dead ends: the control keeps its
# class and gains p-disabled rather than disappearing. Checking both that class
# and the disabled property means a walk stops on the last page instead of
# clicking a control that does nothing and re-reading page one forever.
NEXT_PAGE_JS = """
() => {
  const next = document.querySelector('.p-paginator-next');
  if (!next) return null;
  if (next.disabled || next.classList.contains('p-disabled')) return null;
  next.click();
  return true;
}
"""

# The detail panel, which is where the Fine Number and the plate actually live.
# Label/value pairs are <span>/<p>; Details and Dispute are a <span> followed by
# a sibling <ul>, so both shapes are read rather than only the first.
READ_PANEL_JS = """
() => {
  const list = document.querySelector('div.dataList');
  if (!list) return null;
  const out = {};

  [...list.children].forEach(cell => {
    if (cell.tagName !== 'DIV') return;
    const label = cell.querySelector('span');
    if (!label) return;
    const key = (label.textContent || '').trim().replace(/:$/, '');
    if (!key) return;
    const value = cell.querySelector('p');
    if (value) { out[key] = (value.textContent || '').trim(); return; }
    // No <p>: the value is the <ul> that follows this div.
    const after = cell.nextElementSibling;
    if (after && after.tagName === 'UL') {
      out[key] = [...after.querySelectorAll('li')]
        .map(li => (li.innerText || '').trim()).filter(Boolean).join('; ');
    }
  });

  // The plate is structured markup, so the code and the number come out as
  // separate values instead of a string that has to be split back apart.
  const info = document.querySelector('div.vInfo');
  if (info) {
    const heading = info.querySelector('h4');
    if (heading) out._vehicle = (heading.textContent || '').trim();
    const plate = info.querySelector('[data-testid="PlateComponent"]');
    if (plate) {
      const code = plate.querySelector('[data-testid="PlateCharacter"]');
      out._code = code ? (code.textContent || '').trim() : null;
      const number = [...plate.children].find(child => child !== code);
      out._number = number ? (number.textContent || '').trim() : null;
    }
  }

  return out;
}
"""

SERVER_FETCH_REASON = (
	"RTA's fines are readable, but only in a person's own browser so far. The "
	"public traffic-file inquiry has been read end to end and the extension has a "
	"reader for it; what has not been established is whether the same search "
	"survives being run repeatedly from a server address without a reCAPTCHA - and "
	"RTA is one of the two portals browser_fetcher.assert_no_captcha names. Use "
	"Fetch Fines In This Browser until that is measured."
)


class RtaFetcher(OperatorAssistedFetcher):
	"""Registered so RTA refuses with a reason instead of looking unrecognised.

	Before this existed, RTA answered with "No fetcher is implemented for
	Roads & Transport Authority (RTA) - Dubai (Independent route)", which reads
	as though the portal were unknown. It is not unknown; it is understood in
	detail and blocked on a single observation.
	"""

	# Unchanged by the reader landing, and deliberately so. The traffic-file
	# inquiry turned out to be public - it was read while the page header still
	# offered "Login" - which removes the OTP from THIS path but not the reason
	# for this flag: nobody has measured whether repeating that search from one
	# server address draws a reCAPTCHA. Until somebody has, RTA is attended only.
	supports_unattended = False

	# No UAE Pass, so no code to put on anyone's screen.
	supports_relay = False

	# These two are now deliberately different, where before they were both False
	# for one reason. The extension can read RTA; the server still cannot be
	# trusted to. `fetch_implemented` gates run_sync and the sweep, and it stays
	# down until the CAPTCHA question above is answered with a measurement.
	fetch_implemented = False
	client_reader = "rta"
	client_origin = "https://ums.rta.ae"

	def fetch_for_vehicle(self, plate_parts):
		raise FineFetchError(f"{self.portal.name}: {SERVER_FETCH_REASON}")

	def fetch_for_traffic_file(self, traffic_file_number, **kwargs):
		raise FineFetchError(f"{self.portal.name}: {SERVER_FETCH_REASON}")

	def client_fetch_target(self, traffic_file_number=None):
		"""The search page, plus the one field the reader has to fill.

		**This deviates from TAMM's shape, and the deviation is forced.** TAMM
		carries the fleet in a query parameter, so the server hands over a URL and
		the extension never holds an identifier. RTA's results live at
		`/violations/public-fines/customer-violations` with **no query string at
		all** - the list is client-side state produced by submitting the form. A
		GET to that path renders an empty page, so there is no URL that can carry
		the fleet, and the reader has to type the number into the form itself.

		So `prefill` exists, and it is the only route on which the extension is
		given a traffic file number as a value. It is held for one form fill and
		is never stored, never logged and never posted back - `ingest_client_fetch`
		still refuses to accept it, which is the guarantee that actually matters:
		a browser cannot name the fleet a batch of fines is attached to.
		"""
		if not traffic_file_number:
			raise FineFetchError(
				f"{self.portal.name} is queried by traffic file, and no active credential "
				"carries one."
			)
		return {
			"url": SEARCH_URL,
			"origin": self.client_origin,
			# Both the search and the results live under this prefix, so the
			# extension's "has the tab landed" test passes on either - which it
			# must, because the reader arrives before the search is submitted.
			"path_prefix": "/violations/public-fines",
			"reader": self.client_reader,
			"prefill": {
				"tab": "Traffic Code Number",
				"selector": "#Id_trafficFileNumber",
				"value": str(traffic_file_number),
			},
		}

	def _to_fine(self, row):
		"""One list row, plus its detail panel, as a FetchedFine.

		The detail panel is **not optional here**, which is the one way this
		portal differs from TAMM at the contract level. TAMM's list carries the
		fine number and the plate, and the modal only enriches. RTA's list
		carries neither - it shows the vehicle description and nothing that
		identifies the fine - so a row read without its panel has no ticket
		number to dedup on and no plate to match a vehicle by. Such a row is
		dropped rather than staged half-formed.
		"""
		details = row.get("_details") or {}

		ticket = (details.get("Fine Number") or "").strip()
		# RTA's own row key is deliberately not a fallback. It is a different
		# number in a different space, and staging it as a ticket number would
		# mint a fine that no operator can find on the portal.
		if not ticket.isdigit():
			return None

		code = (details.get("_code") or "").strip()
		number = (details.get("_number") or "").strip()

		points = self._black_points(row.get("Black points") or details.get("Payable Black Points"))

		return FetchedFine(
			ticket_number=ticket,
			amount=self._amount(row.get("Amount") or details.get("Amount")),
			fine_datetime=self._datetime(
				row.get("Date and Time of Issuing The Fine")
				or details.get("Date and Time of Issuing The Fine")
			),
			# RTA prints the offence in the Details list, which is the closest
			# thing it has to a type. Location is its own labelled field.
			fine_type=(details.get("Details") or "").strip() or None,
			fine_location=self._present(details.get("Location")),
			# Dubai, not Abu Dhabi. RTA is the Dubai authority and its plate
			# markup carries no emirate, so it is supplied here - see the module
			# docstring on why _vehicle_for_plate cannot match these yet.
			plate=" ".join(part for part in ("Dubai", code, number) if part),
			black_points=points,
			raw={
				"issuing_authority": self._present(row.get("Source") or details.get("Source")),
				"face_amount": self._amount(row.get("Amount") or details.get("Amount")),
				# RTA shows no discounted figure on this route. Recorded as absent
				# rather than equal to the face amount, which would read as a
				# discount that was offered and declined.
				"discounted_amount": None,
				"plate_emirate": "Dubai",
				"plate_code": code,
				"plate_number": number,
				"vehicle_description": self._present(row.get("vehicle") or details.get("_vehicle")),
				"description": (details.get("Details") or "").strip() or None,
				# Which of RTA's three lists this row came from. "Payable" is not
				# assumed: a non-payable fine has to go through the issuing
				# authority, and recording it as payable would put it in front of
				# somebody as though it could be settled from the portal.
				#
				# Anything unrecognised becomes "", never a guess and never the
				# portal's own wording - an invalid Select value fails the insert
				# and takes the rest of the run with it.
				"portal_status": PORTAL_STATUS_BY_LIST.get(row.get("_tab"), "")
				if row.get("_tab")
				else "Payable",
				# RTA's own label for the list, kept because the mapping above is
				# lossy: Payable and Black Points both land on "Payable", and this
				# is the only place that difference survives.
				"rta_list": row.get("_tab") or None,
				"online_declaration": self._present(details.get("Online declaration")),
				# RTA's internal key, kept for traceability only.
				"rta_row_kind": row.get("_rowKind"),
				"rta_row_id": row.get("_rowId"),
				"row": row,
			},
		)

	@staticmethod
	def _present(value):
		"""RTA writes an absent value as "-", which is not a value."""
		text = (value or "").strip()
		return None if text in ("", "-") else text

	@classmethod
	def _amount(cls, text):
		found = AMOUNT_RE.findall((text or "").replace("AED", " "))
		return float(found[0].replace(",", "")) if found else 0.0

	@classmethod
	def _black_points(cls, text):
		value = cls._present(text)
		if not value:
			return 0
		digits = re.search(r"(\d+)", value)
		return int(digits.group(1)) if digits else 0

	@staticmethod
	def _datetime(text):
		"""RTA's "08 Sep 2026, 8:28 am" into a Frappe datetime string.

		Returned as None rather than guessed at when the format moves: a fine
		with no date stages and can be corrected, where a fine dated today
		because a parse failed is a liability with a false date on it.
		"""
		from datetime import datetime

		value = (text or "").strip()
		if not value:
			return None
		for fmt in ("%d %b %Y, %I:%M %p", "%d %b %Y, %H:%M", "%d %b %Y"):
			try:
				return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
			except ValueError:
				continue
		return None
