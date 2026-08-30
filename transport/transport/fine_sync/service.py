# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Running a fine sync, and turning what it finds into real fines.

Two rules shape everything here:

1. **A failed lookup is never a clean result.** Every vehicle's outcome is
   recorded individually, and any failure downgrades the run's status. A
   vehicle that could not be queried must never read as a vehicle with no
   fines.
2. **Nothing imported applies black points by itself.** Fines arrive from a
   portal already marked Paid; without the hold, importing a driver's history
   would blacklist them retroactively for fines settled long ago.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import format_duration, now_datetime

from transport.transport.fine_sync.base import FineFetchError
from transport.transport.fine_sync.browser_fetcher import BrowserFetcher
from transport.transport.fine_sync.registry import get_fetcher, is_supported
from transport.transport.vehicle_plate import PLATE_FIELDS

SYNC_ROLES = ("Transport Accounts", "Transport Operations", "System Manager")

# Used when Transport Settings has no figure of its own. A Single's JSON default
# never reaches tabSingles until the doc is saved once, so reading the setting
# alone would give None on every site that has not opened the form.
DEFAULT_SWEEP_BUDGET_MINUTES = 30


def _sweep_budget_seconds():
	"""How long a sync may spend fetching. None means no limit.

	0 means "not configured", not "unlimited". `get_single_value` casts an Int
	field to 0 when the Single has never been saved, so an unset site and a site
	deliberately holding 0 are indistinguishable - and reading that as no limit
	would leave every site that has not opened Transport Settings running the
	unbounded sweep this exists to stop. Removing the ceiling is therefore an
	explicit act: a negative value.
	"""
	minutes = int(frappe.db.get_single_value(
		"Transport Settings", "fine_sync_time_budget_minutes"
	) or 0)
	if minutes == 0:
		minutes = DEFAULT_SWEEP_BUDGET_MINUTES
	return minutes * 60 if minutes > 0 else None


def _checkpoint():
	"""Commit what the run has recorded so far.

	A fetch is minutes of browser work, and Frappe holds one transaction open for
	a whole background job. Without these checkpoints the Sync Run and every fine
	it stages sit inside that single transaction, which has three consequences:

	* The naming-series rows for Traffic Fine Sync Run and Traffic Fine Staging
	  stay locked for the entire fetch, so anything else trying to create one -
	  the "Fetch Fines Now" button, the next scheduled fire - blocks and then
	  dies with "Lock wait timeout exceeded".
	* Nothing is visible while it happens. The run stays uncommitted, so an
	  operator watching Traffic Fine Sync Run sees no sign it is running at all,
	  which is exactly the doubt this module exists to settle.
	* A worker killed mid-fetch loses every fine it had already read.

	Committing means a later failure no longer rolls back earlier progress, and
	that is deliberate: a run that read forty vehicles and then broke should keep
	those forty and be marked failed, not discard them.
	"""
	frappe.db.commit()


def _check_permission():
	if not any(role in frappe.get_roles() for role in SYNC_ROLES):
		frappe.throw(_("Not permitted to run a traffic-fine sync."), frappe.PermissionError)


def get_fleet_for_sync():
	"""Vehicles that can actually be queried, and those that cannot.

	A vehicle without all three plate parts is unqueryable. It is returned
	separately rather than skipped quietly, so the run records it as Skipped
	with a reason instead of leaving a silent hole in the fleet's coverage.
	"""
	vehicles = frappe.get_all(
		"Rental Vehicle",
		fields=["name", "license_plate", *PLATE_FIELDS],
		order_by="license_plate",
	)
	queryable, unqueryable = [], []
	for v in vehicles:
		(queryable if all(v.get(f) for f in PLATE_FIELDS) else unqueryable).append(v)
	return queryable, unqueryable


@frappe.whitelist()
def fetch_fines_for_vehicles(vehicles=None):
	"""The "Fetch Fines" action, for a chosen set of vehicles.

	Runs every portal that can genuinely be queried unattended, and reports -
	rather than hides - everything it could not cover. Today that means one
	portal: SRTA, which carries transport and toll violations only. Police
	fines sit behind sign-in walls (MOI on UAE Pass and reCAPTCHA, RAKTA and
	RTA on login forms) and need a person.

	The coverage summary is the point of this function. Fetching some fines and
	presenting it as "your fines" is how a fleet ends up believing it is clear
	when it is not.
	"""
	_check_permission()

	if isinstance(vehicles, str):
		vehicles = json.loads(vehicles)
	if not vehicles:
		frappe.throw(_("Select at least one vehicle."))

	from transport.transport.doctype.traffic_fine_portal.traffic_fine_portal import get_syncable_portals

	automated = [p for p in get_syncable_portals() if p.get("fetch_mode") == "Automated"]

	# Operator-assisted portals are queried separately and deliberately NOT via
	# get_syncable_portals(): "enabled for sync" means enabled for UNATTENDED
	# sync, which one of these can never be. Requiring it here would have made
	# the pending list permanently empty - which is precisely the silence this
	# summary exists to break.
	assisted = frappe.get_all(
		"Traffic Fine Portal",
		filters={"fetch_mode": "Operator Assisted", "has_written_authorization": 1},
		fields=["name", "scope", "captcha_type"],
	)

	runs, staged_total = [], 0
	for portal in automated:
		result = run_sync(portal.name, vehicles=vehicles)
		runs.append(result)
		staged_total += result.get("fines_new") or 0

	# Anything not automated is real, uncovered work - name it.
	pending = [
		{"portal": p.name, "reason": p.captcha_type or _("Requires an operator to sign in.")}
		for p in assisted
	]

	police_covered = any(
		frappe.db.get_value("Traffic Fine Portal", p.name, "scope") == "Police Traffic Fines"
		for p in automated
	)

	return {
		"vehicles": len(vehicles),
		"runs": runs,
		"fines_staged": staged_total,
		"portals_queried": [p.name for p in automated],
		"pending_operator": pending,
		"police_fines_covered": police_covered,
		"coverage_note": (
			_("Police traffic fines were NOT included - no police portal can be queried "
			  "unattended. This covers transport and toll violations only.")
			if not police_covered
			else None
		),
	}


@frappe.whitelist()
def run_sync(portal, limit=None, vehicles=None):
	"""Fetch fines for the fleet from one portal.

	Refuses outright unless the portal is enabled AND holds an unexpired
	written authorization - the gate lives in the data, not in a caller's
	discipline.
	"""
	_check_permission()

	from transport.transport.doctype.traffic_fine_portal.traffic_fine_portal import get_syncable_portals

	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	if portal_doc.name not in [p.name for p in get_syncable_portals()]:
		frappe.throw(
			_("{0} is not available for sync. It must be enabled and hold a valid, "
			  "unexpired written authorization.").format(portal_doc.name),
			title=_("Portal Not Available"),
		)

	credential = frappe.db.get_value(
		"Traffic Fine Portal Credential", {"portal": portal_doc.name, "is_active": 1}, "name"
	)
	credential_doc = frappe.get_doc("Traffic Fine Portal Credential", credential) if credential else None

	run = frappe.get_doc({
		"doctype": "Traffic Fine Sync Run",
		"portal": portal_doc.name,
		"status": "Running",
		"started_on": now_datetime(),
		"triggered_by": frappe.session.user,
	})
	run.insert(ignore_permissions=True)
	# Release the naming-series lock before any browser work starts.
	_checkpoint()

	queryable, unqueryable = get_fleet_for_sync()

	if vehicles:
		if isinstance(vehicles, str):
			vehicles = json.loads(vehicles)
		wanted = set(vehicles)
		queryable = [v for v in queryable if v.name in wanted]
		# Keep the unqueryable ones that were actually selected, so a chosen
		# vehicle missing its plate parts is still reported rather than
		# disappearing from the run.
		unqueryable = [v for v in unqueryable if v.name in wanted]

	if limit:
		queryable = queryable[: int(limit)]

	for v in unqueryable:
		missing = [f for f in PLATE_FIELDS if not v.get(f)]
		run.add_vehicle_result(
			v.name, v.license_plate, "Skipped",
			_("Cannot be queried: missing {0}. Its fines are unknown, not zero.").format(
				", ".join(frappe.unscrub(m) for m in missing)
			),
		)

	fetcher = None
	error_log = None
	budget = _sweep_budget_seconds()
	deadline = time.monotonic() + budget if budget else None
	try:
		fetcher = get_fetcher(portal_doc, credential_doc)
		fetcher.time_budget_seconds = budget
		fetcher.arm_deadline()
		for index, v in enumerate(queryable):
			# Checked between vehicles, which is where a sweep actually spends
			# its time: one browser page load each, so 42 vehicles can run past
			# half an hour. Stopping here leaves the fleet partly covered, and
			# the rest of this block exists to make sure that is never mistaken
			# for a clean result.
			if deadline and time.monotonic() >= deadline:
				for skipped in queryable[index:]:
					run.add_vehicle_result(
						skipped.name, skipped.license_plate, "Skipped",
						_("The sync reached its {0}-minute time limit before this vehicle. "
						  "Its fines are unknown, not zero.").format(budget // 60),
					)
				run.status = "Completed with Errors"
				break
			_sync_one_vehicle(run, fetcher, portal_doc, v)
			# Per vehicle, not per run: a fleet sweep is a browser page load each,
			# so holding every staged fine back to the end means holding the
			# staging series lock for the whole sweep.
			_checkpoint()
	except Exception:
		# A failure setting up (or an unsupported portal) fails the whole run
		# loudly rather than leaving it looking merely empty.
		run.status = "Failed"
		error_log = frappe.get_traceback()
		frappe.log_error(title=f"Traffic fine sync failed: {portal_doc.name}")
	finally:
		if fetcher:
			fetcher.close()
		# Before finalize, not only after: finalize() saves, and a save that
		# throws would otherwise roll back the Failed status along with it,
		# leaving no record at all of a run that broke.
		_checkpoint()
		run.finalize(error_log=error_log)
		_checkpoint()

	return {
		"sync_run": run.name,
		"status": run.status,
		"vehicles_queried": run.vehicles_queried,
		"vehicles_failed": run.vehicles_failed,
		"fines_found": run.fines_found,
		"fines_new": run.fines_new,
	}


def _sync_one_vehicle(run, fetcher, portal_doc, vehicle):
	plate_parts = {f: vehicle.get(f) for f in PLATE_FIELDS}
	try:
		result = fetcher.fetch_for_vehicle(plate_parts)
	except FineFetchError as e:
		# Expected, explainable failures: CAPTCHA, login needed, portal not
		# captured yet. Recorded per vehicle, never swallowed.
		run.add_vehicle_result(vehicle.name, vehicle.license_plate, "Failed", str(e))
		return
	except Exception as e:
		run.add_vehicle_result(vehicle.name, vehicle.license_plate, "Failed", f"{type(e).__name__}: {e}")
		return

	staged = 0
	for fine in result.fines:
		if _stage_fine(run, portal_doc, vehicle, fine):
			staged += 1

	run.fines_new = (run.fines_new or 0) + staged
	run.add_vehicle_result(
		vehicle.name, vehicle.license_plate, "Success", result.message, len(result.fines)
	)


def _stage_fine(run, portal_doc, vehicle, fine):
	"""Write one fetched fine to staging. Returns True if it was new.

	A row we already hold is enriched rather than skipped outright. A later run
	can legitimately know more than an earlier one - the first pass may have read
	only the portal's list view while a second opened each fine's detail panel -
	and dropping that would make re-fetching pointless. Only empty fields are
	filled, and only while the row is still New, so nothing already reviewed or
	corrected by a person is overwritten.
	"""
	existing = frappe.db.get_value(
		"Traffic Fine Staging",
		{"portal": portal_doc.name, "ticket_number": fine.ticket_number},
		["name", "status"],
		as_dict=True,
	)
	if existing:
		# Stamp every run, on every row, whatever its status, and let it move
		# `modified` with it. Two reasons the timestamp must not be suppressed:
		# the list view's own "Last Updated On" is what an operator actually
		# reads, and a row still showing "1 d" after a fetch a minute ago reads
		# as a sync that never ran. Recording that we looked IS a write, so
		# `modified` moving is accurate rather than cosmetic - and unlike
		# re-writing the amount to force the same effect, it destroys nothing an
		# accountant may have corrected.
		frappe.db.set_value(
			"Traffic Fine Staging", existing.name, "last_fetched_on", now_datetime()
		)
		if existing.status == "New":
			_enrich_staging_row(existing.name, vehicle, fine)
		return False

	doc = frappe.get_doc({
		"doctype": "Traffic Fine Staging",
		"sync_run": run.name,
		"portal": portal_doc.name,
		"ticket_number": fine.ticket_number,
		"vehicle": vehicle.name if vehicle else None,
		"plate": fine.plate or (vehicle.license_plate if vehicle else None),
		"fine_datetime": fine.fine_datetime,
		"amount": fine.amount,
		"fine_type": fine.fine_type,
		"fine_location": fine.fine_location,
		"black_points": fine.black_points,
		# Optional detail a portal may or may not report. Read from raw so a
		# fetcher that has these does not need its own staging code, and one
		# that doesn't simply leaves them empty.
		"discounted_amount": fine.raw.get("discounted_amount"),
		"portal_status": fine.raw.get("tamm_status") or fine.raw.get("portal_status"),
		"description": fine.raw.get("description"),
		"ticket_type": fine.raw.get("ticket_type"),
		"raw_payload": json.dumps(fine.raw, indent=1, default=str)[:10000],
		"status": "New",
		"last_fetched_on": now_datetime(),
	})
	doc.insert(ignore_permissions=True)
	return doc.status == "New"


@frappe.whitelist()
def run_operator_assisted_sync(
	portal, traffic_file_number, include_details=0, wait_for_login=1, skip_if_busy=0
):
	"""Fetch a whole company traffic file, one run at a time.

	The lock is the point of this wrapper. Two runs against one portal means two
	browsers restoring the same banked session, and the portal responds by
	invalidating it - which costs the operator a fresh UAE Pass push and makes
	the *next* scheduled sweep fail too. It has happened here: a manual fetch
	and a scheduled one started fourteen seconds apart, both on the same TAMM
	session. Guarding only the scheduled path left the button free to collide
	with it, so the lock lives here, where every caller must pass through it.

	`skip_if_busy` distinguishes the two callers. A person who pressed a button
	deserves to be told why nothing happened; a 45-minute cron finding a run
	already going is ordinary, and should say so without filing an error.
	"""
	from frappe.utils.synchronization import LockTimeoutError, filelock

	_check_permission()

	try:
		with filelock(f"fine_sync_{portal}", timeout=1):
			return _operator_assisted_sync(
				portal, traffic_file_number, include_details, wait_for_login
			)
	except LockTimeoutError:
		if int(skip_if_busy or 0):
			return {"skipped": True, "reason": _("A fetch for {0} is already running.").format(portal)}
		frappe.throw(
			_("A fetch from {0} is already running. Wait for it to finish - starting a second "
			  "one would sign the first out of the portal and lose its results.").format(portal),
			title=_("Fetch Already Running"),
		)


def _operator_assisted_sync(portal, traffic_file_number, include_details=0, wait_for_login=1):
	"""The fetch itself. Always reached through run_operator_assisted_sync's lock.

	Deliberately NOT routed through `get_syncable_portals()`. That gate exists to
	stop *unattended* access to a portal nobody authorized, and it turns
	`is_enabled` into a licence to run without supervision. This path is the
	opposite: a named operator signs in with their own credentials, watches the
	window, and answers any challenge themselves. Requiring `is_enabled` here
	would have meant switching on the very flag that permits scheduled runs, in
	order to do something a human is standing over.

	Written authorization is still required, because that is about permission to
	take the data at all - which a person being present does not change.

	Cannot be scheduled: `run_scheduled_syncs` only walks syncable portals, and
	this one is not among them.
	"""
	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	if not portal_doc.has_written_authorization:
		frappe.throw(
			_("Record the written authorization for {0} before fetching from it.").format(
				portal_doc.name
			),
			title=_("Authorization Required"),
		)

	run = frappe.get_doc({
		"doctype": "Traffic Fine Sync Run",
		"portal": portal_doc.name,
		"status": "Running",
		"started_on": now_datetime(),
		"triggered_by": frappe.session.user,
	})
	run.insert(ignore_permissions=True)
	# The sign-in wait alone runs to forty-five minutes. Nothing may sit in an
	# open transaction across that.
	_checkpoint()

	fetcher, error_log, staged = None, None, 0
	try:
		fetcher = get_fetcher(portal_doc)
		# The fetcher arms this itself once the sign-in is done, so an operator
		# taking ten minutes to approve a UAE Pass push does not spend the time
		# meant for reading fines.
		fetcher.time_budget_seconds = _sweep_budget_seconds()
		attended = bool(int(wait_for_login))
		if not attended:
			# Nobody is watching, so there is nothing for a visible window to
			# show - and a scheduler worker has no display to open one on.
			fetcher.headless = True
		result = fetcher.fetch_for_traffic_file(
			traffic_file_number,
			include_details=bool(int(include_details)),
			wait_for_login=attended,
		)
		for fine in result.fines:
			vehicle = _vehicle_for_plate(fine.raw.get("plate_code"), fine.raw.get("plate_number"))
			if _stage_fine(run, portal_doc, vehicle, fine):
				staged += 1
			run.add_vehicle_result(
				vehicle.name if vehicle else None,
				fine.plate,
				"Success",
				# A fine we cannot tie to a vehicle is still a real liability.
				# Saying so per row keeps it from reading as a clean import.
				None if vehicle else _("No Rental Vehicle matches this plate - fine is unlinked."),
				1,
			)
			_checkpoint()
		run.fines_found = len(result.fines)
		run.fines_new = staged
		if result.truncated:
			# A time-limited read covered part of the portal's list. Saying
			# "Completed" here would present that as the whole liability.
			run.status = "Completed with Errors"
			# The status alone says something went wrong without saying what.
			# The fetcher's message is the only place that records the list was
			# cut short rather than exhausted, so it has to land on the run.
			error_log = result.message
	except Exception:
		run.status = "Failed"
		error_log = frappe.get_traceback()
		frappe.log_error(title=f"Operator-assisted fine sync failed: {portal_doc.name}")
	finally:
		if fetcher:
			fetcher.close()
		# Before finalize, not only after: finalize() saves, and a save that
		# throws would otherwise roll back the Failed status along with it,
		# leaving no record at all of a run that broke.
		_checkpoint()
		run.finalize(error_log=error_log)
		_checkpoint()

	return {
		"sync_run": run.name,
		"status": run.status,
		"fines_found": run.fines_found,
		"fines_new": run.fines_new,
	}


@frappe.whitelist()
def enqueue_operator_assisted_sync(portal, include_details=1):
	"""The "Fetch Fines Now" button. Queues the run and returns immediately.

	Not run inline: a fetch opens a browser, may wait for a UAE Pass push, and
	walks every page - minutes of work that would time out a web request long
	before it finished. The caller gets the Sync Run's name and watches that
	instead, which is the same record the scheduled path writes.
	"""
	_check_permission()

	traffic_file_number = frappe.db.get_value(
		"Traffic Fine Portal Credential",
		{"portal": portal, "is_active": 1},
		"traffic_file_number",
	)
	if not traffic_file_number:
		frappe.throw(
			_("{0} has no active credential carrying a traffic file number. Add one before "
			  "fetching - the whole fleet is queried by traffic file, not plate by plate.").format(portal),
			title=_("Traffic File Number Missing"),
		)

	frappe.enqueue(
		"transport.transport.fine_sync.service.run_operator_assisted_sync",
		queue="long",
		timeout=3600,
		portal=portal,
		traffic_file_number=traffic_file_number,
		include_details=include_details,
		wait_for_login=1,
	)
	return {
		"queued": True,
		"portal": portal,
		"message": _("Fetch queued. A browser window will open for sign-in; watch "
		             "Traffic Fine Sync Run for the result."),
	}


def describe_session_status(status):
	"""Turn a session reading into the sentence a person gets told.

	One wording, two audiences: the banner on the portal form and the note the
	scheduled sweep leaves behind. They were drifting apart - the form said "the
	session expired 31m ago" while the sweep recorded a flat "no usable
	session" - and an operator comparing the two had no way to know they
	described the same thing.
	"""
	left = status.get("seconds_left")
	span = format_duration(abs(left), hide_days=True) if left is not None else None

	if status.get("state") == "live":
		status["indicator"] = "green"
		status["message"] = _(
			"Signed-in session is live for about another {0}. Scheduled syncs can run "
			"unattended until it lapses."
		).format(span)
	elif status.get("state") == "expired":
		status["indicator"] = "red"
		status["message"] = _(
			"The signed-in session expired {0} ago. Scheduled syncs are doing nothing "
			"until someone signs in again - use Fetch Fines Now."
		).format(span)
	elif status.get("state") == "none":
		status["indicator"] = "orange"
		status["message"] = _(
			"No sign-in is banked for this portal. Nothing can be fetched, on a schedule "
			"or otherwise, until someone signs in - use Fetch Fines Now."
		)
	elif status.get("state") == "unknown":
		status["indicator"] = "blue"
		status["message"] = _(
			"A session is banked, but the portal does not date it, so whether it still "
			"works can only be found out by trying."
		)
	else:
		# Anything else means the portal banks no session this code can date -
		# today because no fetcher does, later because a portal's session cookie
		# has not been named yet. Falling through left the banner blank and the
		# sweep saying "no signed-in session" about a portal that may well have
		# one, which is the silent dead end this whole path exists to remove.
		status["indicator"] = "gray"
		status["message"] = _(
			"This portal does not bank a datable sign-in, so how much life is left in "
			"one cannot be reported here."
		)
	return status


@frappe.whitelist()
def get_portal_session_status(portal):
	"""How much life is left in an operator's banked sign-in, for the desk.

	Exists because a lapsed session is otherwise invisible on the form. The
	scheduled entry point skips a portal with no live session on purpose - a
	browser opened every 45 minutes to fail would bury the log in noise - so
	without this an operator has no way to know whether the automation can
	currently do anything at all.

	Opens no browser and contacts no portal: it dates the banked session cookie
	on disk, and never reads its value.
	"""
	if not frappe.has_permission("Traffic Fine Portal", "read", doc=portal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	try:
		fetcher = get_fetcher(portal_doc)
	except Exception:
		# No fetcher at all for this portal. Reported rather than hidden,
		# because the Fetch Fines button used to appear here and fail with a
		# message about a missing traffic file number - which sent the operator
		# off to add a credential that would not have helped.
		return {
			"state": "unsupported",
			"usable": False,
			"fetch_implemented": False,
			"can_capture": False,
			"indicator": "orange",
			"message": _(
				"No fetcher is implemented for {0}, so fines cannot be collected from it "
				"yet - by hand or on a schedule. This is not a credential or "
				"authorization problem."
			).format(portal_doc.name),
		}

	reader = getattr(fetcher, "session_status", None)
	status = describe_session_status(
		reader() if reader else {"state": "unsupported", "usable": False}
	)
	status["fetch_implemented"] = bool(getattr(fetcher, "fetch_implemented", True))
	status["can_capture"] = hasattr(fetcher, "capture_signed_in")

	if not status["fetch_implemented"]:
		# Say what is actually missing. "No live session" would be true and
		# useless here - signing in would not help, because nothing can read
		# the pages a sign-in reaches.
		status["indicator"] = "orange"
		status["message"] = _(
			"{0} cannot be fetched yet: its pages behind the sign-in have never been "
			"captured, so there is nothing to read them with. Signing in will not change "
			"that - run Capture Portal Pages with an operator signed in, and the fetch "
			"can be written from what it records."
		).format(portal_doc.name)

	return status


@frappe.whitelist()
def enqueue_portal_capture(portal):
	"""Queue the capture step for a portal whose pages have never been read.

	Separate from a fetch on purpose. A fetch claims to collect liabilities; a
	capture claims only to record what the portal looks like, and it is the
	honest thing to offer for a portal we cannot yet parse. Offering "Fetch
	Fines Now" there instead produced a run that failed for reasons the
	operator could do nothing about.

	Queued rather than run inline for the same reason a fetch is: it opens a
	browser and waits for a person.
	"""
	_check_permission()

	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	if not portal_doc.has_written_authorization:
		frappe.throw(
			_("Record the written authorization for {0} before opening it at all - a "
			  "capture reads the portal just as a fetch does.").format(portal_doc.name),
			title=_("Authorization Required"),
		)

	fetcher = get_fetcher(portal_doc)
	if not hasattr(fetcher, "capture_signed_in"):
		frappe.throw(
			_("{0} has no capture step - its pages are already implemented.").format(portal_doc.name),
			title=_("Nothing to Capture"),
		)

	frappe.enqueue(
		"transport.transport.fine_sync.service.run_portal_capture",
		queue="long",
		timeout=3600,
		portal=portal_doc.name,
	)
	return {
		"queued": True,
		"message": _(
			"Capture queued. A browser window will open - sign in, then walk to the fines "
			"list and page through it. Every page you visit is recorded. Close the window "
			"when you are done."
		),
	}


def run_portal_capture(portal):
	"""Run the capture and write what it found onto the portal record."""
	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	fetcher = get_fetcher(portal_doc)

	# Nothing may sit in an open transaction across a capture: it runs to
	# twenty-five minutes waiting on a person, and a worker holding a
	# transaction that long is what locked the naming series and took a
	# Rental Vehicle read down with it.
	frappe.db.commit()

	report = None
	try:
		report = fetcher.capture_signed_in()
	finally:
		fetcher.close()
		if report is None:
			# The capture threw rather than returning. Say so on the record, so
			# a client sign-in that produced nothing is not silently ascribed to
			# a portal that simply had no pages.
			frappe.db.set_value(
				"Traffic Fine Portal",
				portal_doc.name,
				"last_capture_summary",
				_("Capture ended unexpectedly and reported nothing. Check the Error Log, "
				  "and look under private/portal-captures for any pages written before it "
				  "stopped."),
				update_modified=False,
			)
			frappe.db.commit()

	frappe.db.set_value(
		"Traffic Fine Portal",
		portal_doc.name,
		"last_capture_summary",
		_("{0} page(s) recorded to {1}. Session cookies seen: {2}. Session banked: {3}.").format(
			report["pages_captured"],
			report["capture_file"],
			", ".join(report["cookie_names"]) or _("none"),
			_("yes") if report["session_banked"] else _("no"),
		),
		update_modified=False,
	)
	frappe.db.commit()
	return report


def run_scheduled_operator_syncs():
	"""Pick up new fines automatically, but only while an operator's session lives.

	Deliberately does nothing far more often than it does something, and that is
	the design rather than a shortcoming:

	* **Off unless switched on.** Same Transport Settings flag as the unattended
	  syncs, which ships off.
	* **Never waits for a login.** A sign-in needs a UAE Pass push approved on a
	  phone. Waiting for one on a schedule would leave a browser hung until the
	  next fire, and at a 45-minute cadence those stack up until the box dies.
	  No live session simply means no run.
	* **Never overlaps itself.** `run_operator_assisted_sync` holds the portal
	  lock; this passes `skip_if_busy` so a collision is reported, not filed as
	  an error.

	**It hands the fetch to the `long` queue rather than doing it here.** Frappe
	picks a job's queue from its frequency alone - `get_queue_name()` returns
	`long` only for "Daily Long" and maintenance jobs - so this Cron job runs on
	`default`, shoulder to shoulder with the */5 trip reminders and the
	driver-confirmation escalation. Fetching inline held a default worker for
	the whole browser run, up to the 30-minute ceiling: long enough to swallow
	six consecutive escalation cycles without a trace. Enqueueing is what keeps
	a slow portal from becoming a late escalation.

	Whatever it decides, it writes down why. Skipping quietly was costing more
	than it saved: the Scheduled Job Log records "Complete" for a sweep that
	fetched the whole fleet and for one that found no session, no credential, or
	a disabled flag, and nothing anywhere distinguished them.
	"""
	notes = []

	if not frappe.db.get_single_value("Transport Settings", "enable_scheduled_fine_sync"):
		_record_sweep_note(_("Skipped: Traffic Fine Sync is switched off in Transport Settings."))
		return

	portals = frappe.get_all(
		"Traffic Fine Portal",
		filters={"fetch_mode": "Operator Assisted", "has_written_authorization": 1},
		fields=["name", "fetcher_key"],
	)
	if not portals:
		_record_sweep_note(
			_("Nothing to do: no operator-assisted portal has its written authorization on record.")
		)
		return

	for portal in portals:
		# Whether anything can read this portal is checked before whether a
		# credential exists, because otherwise the note blames the credential:
		# it sent an operator off to add a traffic file number to a portal that
		# has no fetcher, which would not have helped and did not.
		portal_doc = frappe.get_doc("Traffic Fine Portal", portal.name)
		try:
			fetcher = get_fetcher(portal_doc)
		except Exception:
			notes.append(_("{0}: no fetcher is implemented for this portal.").format(portal.name))
			continue

		if not getattr(fetcher, "fetch_implemented", True):
			notes.append(
				_("{0}: its pages behind the sign-in have not been captured yet, so nothing "
				  "can read them. Run Capture Portal Pages on the portal record.").format(portal.name)
			)
			continue

		traffic_file_number = frappe.db.get_value(
			"Traffic Fine Portal Credential",
			{"portal": portal.name, "is_active": 1},
			"traffic_file_number",
		)
		if not traffic_file_number:
			# Without a traffic file there is nothing to query; a per-vehicle
			# fallback would be a different (and much slower) thing entirely.
			notes.append(_("{0}: no active credential carrying a traffic file number.").format(portal.name))
			continue

		# Check for a banked session before opening anything at all.
		status = describe_session_status(
			fetcher.session_status() if hasattr(fetcher, "session_status") else {}
		)
		if not status.get("usable"):
			notes.append(
				_("{0}: not fetched - {1}").format(
					portal.name,
					status.get("message")
					or _("this portal banks no signed-in session, so nothing can be fetched unattended."),
				)
			)
			continue

		frappe.enqueue(
			"transport.transport.fine_sync.service.run_operator_assisted_sync",
			queue="long",
			timeout=3600,
			portal=portal.name,
			traffic_file_number=traffic_file_number,
			include_details=1,
			wait_for_login=0,
			skip_if_busy=1,
		)
		notes.append(_("{0}: session live, fetch queued on the long worker.").format(portal.name))

	_record_sweep_note("\n".join(notes))


def _record_sweep_note(note):
	"""Leave the sweep's reasoning somewhere a person can read it.

	Onto Transport Settings rather than the Error Log, because none of these
	outcomes is an error - "nobody has signed in lately" is the normal state of
	an operator-assisted portal - and filing them as errors every 45 minutes is
	the noise this whole path was built to avoid. It is a status, so it lives
	where the switch that controls it lives.
	"""
	try:
		frappe.db.set_value(
			"Transport Settings",
			"Transport Settings",
			{
				"last_operator_sweep_note": note,
				"last_operator_sweep_on": now_datetime(),
			},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		# A note that cannot be written must never take the sweep down with it.
		frappe.log_error(title="Could not record operator sweep note")


def _enrich_staging_row(name, vehicle, fine):
	"""Fill only what the row is still missing, never overwrite."""
	row = frappe.get_doc("Traffic Fine Staging", name)
	candidates = {
		"vehicle": vehicle.name if vehicle else None,
		"fine_type": fine.fine_type,
		"fine_location": fine.fine_location,
		"black_points": fine.black_points or None,
		"discounted_amount": fine.raw.get("discounted_amount"),
		"portal_status": fine.raw.get("tamm_status") or fine.raw.get("portal_status"),
		"description": fine.raw.get("description"),
		"ticket_type": fine.raw.get("ticket_type"),
	}
	updates = {
		field: value
		for field, value in candidates.items()
		if value not in (None, "", 0) and not row.get(field)
	}
	if updates:
		row.db_set(updates, update_modified=False)


def _vehicle_for_plate(plate_code, plate_number):
	"""Match a reported plate to a fleet vehicle, or return None.

	Returns a shape `_stage_fine` and `add_vehicle_result` can both use.
	"""
	if not (plate_code and plate_number):
		return None
	name = frappe.db.get_value(
		"Rental Vehicle",
		{"plate_emirate": "Abu Dhabi", "plate_code": plate_code, "plate_number": plate_number},
		"name",
	)
	if not name:
		return None
	return frappe._dict({"name": name, "license_plate": name})


@frappe.whitelist()
def promote_staging_rows(rows):
	"""Create real fines from staged rows.

	Imported fines are deliberately conservative: black points are held, and
	responsibility defaults to Company. Both are human decisions - assigning a
	driver automatically would feed the blacklisting chain off portal data
	nobody has reviewed.

	VAT is NOT added. A portal reports the authority's face amount, and a
	traffic fine is a statutory penalty rather than a taxable supply, so
	defaulting these to the doctype's usual 5% would overstate every imported
	fine. Flagged for accounting sign-off; change it there, not here.
	"""
	_check_permission()

	if isinstance(rows, str):
		rows = json.loads(rows)

	settings = frappe.get_single("Transport Settings")
	default_responsibility = settings.get("imported_fine_responsibility") or "Company"

	promoted, skipped = [], []
	for name in rows:
		staging = frappe.get_doc("Traffic Fine Staging", name)

		if staging.status != "New":
			skipped.append({"row": name, "reason": staging.status})
			continue

		fine = frappe.get_doc({
			"doctype": "Transport Traffic Fine",
			"source": "Portal Sync",
			"source_portal": staging.portal,
			"ticket_number": staging.ticket_number,
			"vehicle": staging.vehicle,
			# The plate travels independently of the vehicle link. A portal can
			# report a fine for a plate that is not in the fleet yet, and losing
			# it here would leave that fine with nothing identifying the car.
			"plate_number": staging.plate,
			"date_time": staging.fine_datetime,
			"fine_type": staging.fine_type,
			"fine_location": staging.fine_location,
			"ticket_type": staging.ticket_type,
			"description": staging.description,
			# The authority's own point count, not the flat per-fine default.
			"black_points": staging.black_points,
			"portal_status": staging.portal_status,
			"amount": staging.amount,
			"discounted_amount": staging.discounted_amount,
			"add_vat": 0,
			"responsibility": default_responsibility,
			"status": "Unpaid",
			"black_points_on_hold": 1,
		})
		fine.flags.ignore_mandatory = True
		fine.insert(ignore_permissions=True)

		staging.db_set({"status": "Promoted", "transport_traffic_fine": fine.name})
		promoted.append(fine.name)

	return {"promoted": len(promoted), "fines": promoted, "skipped": skipped}


@frappe.whitelist()
def capture_portal_page(portal):
	"""Run the authorized capture step against a portal's public form.

	This is what has to happen before any portal can actually be scraped: no
	backend request has ever been captured for any of the thirteen. It reads
	the page and reports its form fields and whether a CAPTCHA is present,
	and submits nothing.
	"""
	_check_permission()

	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	if not portal_doc.has_written_authorization:
		frappe.throw(
			_("Record the written authorization for {0} before capturing its pages.").format(
				portal_doc.name
			),
			title=_("Authorization Required"),
		)

	# Capture is the step that comes BEFORE a portal has a fetcher, so it must
	# not require one - demanding a fetcher first is circular. It locked capture
	# out of the 6 portals with neither a fetcher_key nor the MOI route (RAKTA,
	# RTA, DARB, TAMM, Dubai Police, EVG), which are exactly the ones whose
	# contract is still unknown and most need capturing. A portal-specific
	# fetcher is used when one exists (it may know a better entry point);
	# otherwise the generic browser suffices, since capture_page() only reads a
	# public URL and the resulting DOM.
	if is_supported(portal_doc):
		fetcher = get_fetcher(portal_doc)
	else:
		fetcher = BrowserFetcher(portal_doc)

	try:
		report = fetcher.capture_page()
	finally:
		fetcher.close()

	report["fetcher_used"] = type(fetcher).__name__

	# The page body is useful for writing selectors but far too big to show.
	html = report.pop("html", "")
	report["html_saved_to"] = _save_capture(portal_doc.name, html)
	return report


def _save_capture(portal_name, html):
	if not html:
		return None
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": f"capture-{frappe.scrub(portal_name)}-{frappe.generate_hash(length=6)}.html",
		"content": html,
		"is_private": 1,
	})
	f.insert(ignore_permissions=True)
	return f.file_url


def run_scheduled_syncs():
	"""Scheduler entry point. Does nothing unless explicitly switched on."""
	if not frappe.db.get_single_value("Transport Settings", "enable_scheduled_fine_sync"):
		return

	from transport.transport.doctype.traffic_fine_portal.traffic_fine_portal import get_syncable_portals

	for portal in get_syncable_portals():
		try:
			run_sync(portal.name)
		except Exception:
			frappe.log_error(title=f"Scheduled fine sync failed: {portal.name}")
