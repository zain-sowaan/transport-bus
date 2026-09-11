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
import os
import time
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import format_datetime, format_duration, now_datetime

from transport.transport.fine_sync.base import (
	AuthenticationRequired,
	ConfirmationNotApproved,
	FineFetchError,
	safe_slug,
)
from transport.transport.fine_sync.browser_fetcher import BrowserFetcher
from transport.transport.fine_sync.registry import fetcher_class_for, get_fetcher, is_supported
from transport.transport.vehicle_plate import PLATE_FIELDS, normalize_emirate

SYNC_ROLES = ("Transport Accounts", "Transport Operations", "System Manager")

# Used when Transport Settings has no figure of its own. A Single's JSON default
# never reaches tabSingles until the doc is saved once, so reading the setting
# alone would give None on every site that has not opened the form.
DEFAULT_SWEEP_BUDGET_MINUTES = 30


def _log_error(title):
	"""Write to Error Log without dumping every local variable into it.

	`frappe.log_error(title=...)` with no `message` fills the traceback in
	itself, and the default it reaches for is `get_traceback(with_context=True)`
	- the `traceback_with_variables` rendering, which prints each frame's locals
	beside the stack. Its sanitiser blanks only exact dict keys named
	password/passwd/secret/token/key/pwd, so a plain local holding the relay
	mobile number or the traffic file number is written out verbatim: into Error
	Log, which any System Manager can open, and on to Sentry through
	`capture_exception`. That is the same client data `get_relay_mobile()`
	deliberately keeps out of RQ job kwargs, arriving by a quieter route.

	Passing the plain traceback explicitly is what suppresses it. The frame
	locals are a genuine debugging loss and this gives them up on purpose - the
	stack still names the file, the line and the exception, and a Sync Run's own
	`error_log` field has always carried this same plain traceback anyway.

	Call only from inside an `except` block. `get_traceback()` returns "" with no
	exception in flight, and log_error treats an empty message as "not supplied"
	- which would quietly restore the leaky default this exists to avoid.
	"""
	# log_error swaps its two arguments when the title looks like a traceback,
	# and it decides that on a newline. Titles here interpolate names that reach
	# us from a whitelisted call, so flatten rather than trust them.
	title = str(title).replace("\n", " ")
	return frappe.log_error(title=title, message=frappe.get_traceback() or "No traceback available.")


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
	# Read after the try/except, so they must exist even if the first line in it
	# raises. See _record_session_observation for what is and is not recorded.
	banked_before, failure = None, None
	budget = _sweep_budget_seconds()
	deadline = time.monotonic() + budget if budget else None
	try:
		fetcher = get_fetcher(portal_doc, credential_doc)
		fetcher.time_budget_seconds = budget
		fetcher.arm_deadline()
		banked_before = _session_banked_at(fetcher)
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
	except Exception as exc:
		# A failure setting up (or an unsupported portal) fails the whole run
		# loudly rather than leaving it looking merely empty.
		run.status = "Failed"
		failure = exc
		error_log = frappe.get_traceback()
		_log_error(f"Traffic fine sync failed: {portal_doc.name}")
	finally:
		if fetcher:
			fetcher.close()
		# Before finalize, not only after: finalize() saves, and a save that
		# throws would otherwise roll back the Failed status along with it,
		# leaving no record at all of a run that broke.
		_checkpoint()
		run.finalize(error_log=error_log)
		_checkpoint()

	_record_session_observation(portal_doc.name, banked_before, failure, run.name)

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
def portal_lock_key(portal):
	"""A filesystem-safe, collision-free lock name for one portal.

	`filelock` turns its name straight into a path, so a portal name containing
	a slash - "Abu Dhabi Police / TAMM" is a real one - quietly became a
	*directory* `fine_sync_Abu Dhabi Police ` holding a file ` TAMM.lock`, stray
	spaces at both ends. It did exclude correctly, but only by accident: every
	caller happened to build the key from a byte-identical string. Anything that
	stripped the name, or the portal being renamed with different spacing around
	the slash, would mint a second key colliding with nothing, and the lock would
	stop excluding anything without a word. Two browsers on one TAMM session is
	the exact thing this lock exists to prevent, and it costs a person a UAE Pass
	sign-in each time it happens.

	The digest is what makes this safe rather than merely tidy. Slugging alone
	maps "A / B" and "A - B" onto one key, and two portals sharing a lock is the
	same defect wearing the opposite sign.

	Note for anyone reading lock files as evidence: the `.lock` file is left
	behind after the lock is released - the library holds an OS-level lock on it
	and never deletes it - so its presence and its mtime say nothing about
	whether a run is alive. The process holding it is the only signal.
	"""
	return safe_slug(portal, prefix="fine_sync_")


RELAY_EVENT = "transport_fine_relay"


def _setting(fieldname, default=None):
	"""Read a Transport Settings field, tolerating one the site does not have yet.

	`get_single_value` **throws** on an unknown fieldname rather than returning
	None. That turns "this site has newer code than its database" into an
	exception in every caller - including the status call the portal form makes
	on refresh, which would leave the form with no buttons and a traceback in
	place of its banner.

	Not a hypothetical ordering problem: a UAT site running this app had none of
	its 59 Custom Fields, because fixture import abandons a whole file when one
	DocType in it is missing and says so only on stdout. Code that reads a
	recently-added field should assume it might not be there.
	"""
	if not frappe.get_meta("Transport Settings").get_field(fieldname):
		return default
	return frappe.db.get_single_value("Transport Settings", fieldname)


def _relay_timeout_ms():
	# Ten, matching the field's own default. It has to be repeated here rather
	# than read from the DocType: a Single's JSON default never reaches
	# tabSingles until the form is saved once, so on every site that has not
	# opened Transport Settings this literal IS the default.
	minutes = _setting("relay_wait_minutes") or 10
	# Bounded at both ends. Under a minute never survives a push round trip; a
	# quarter of an hour is already far past the point the push itself expires,
	# and beyond it a worker is just parked.
	return int(min(max(int(minutes), 1), 15)) * 60000


def _relay_announcer(run_name, portal_name, user):
	"""Build the callback that puts the sign-in screen in front of a person.

	Realtime rather than a field on a document, and re-sent on every poll rather
	than once, because the confirmation screen is live-only by design: it is
	published as a picture and never written to disk. Re-sending is what lets
	somebody who reloaded their browser get the current screen back within a few
	seconds instead of losing the code with no way to ask for it again.

	Addressed to the one user who started the run. The screenshot can carry the
	registered mobile number as rendered pixels, so it goes to them and to
	nobody else - not to a role, and not to a document room any System Manager
	could join.
	"""
	user = user or frappe.session.user

	def announce(payload):
		message = dict(payload)
		message["run"] = run_name
		message["portal"] = portal_name
		frappe.publish_realtime(RELAY_EVENT, message, user=user)

	return announce


def run_operator_assisted_sync(
	portal,
	traffic_file_number,
	include_details=0,
	wait_for_login=1,
	skip_if_busy=0,
	use_relay=0,
	notify_user=None,
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
	deserves to be told why nothing happened; an hourly cron finding a run
	already going is ordinary, and should say so without filing an error.
	"""
	from frappe.utils.synchronization import LockTimeoutError, filelock

	_check_permission()

	try:
		with filelock(portal_lock_key(portal), timeout=1):
			return _operator_assisted_sync(
				portal,
				traffic_file_number,
				include_details,
				wait_for_login,
				use_relay=use_relay,
				notify_user=notify_user,
			)
	except LockTimeoutError:
		if int(skip_if_busy or 0):
			return {"skipped": True, "reason": _("A fetch for {0} is already running.").format(portal)}
		frappe.throw(
			_("A fetch from {0} is already running. Wait for it to finish - starting a second "
			  "one would sign the first out of the portal and lose its results.").format(portal),
			title=_("Fetch Already Running"),
		)


def _operator_assisted_sync(
	portal, traffic_file_number, include_details=0, wait_for_login=1, use_relay=0, notify_user=None
):
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

	fetcher, error_log, staged, reason = None, None, 0, None
	# Both read after the try/except, so they must exist even when the very
	# first line inside it raises.
	banked_before, failure = None, None
	try:
		fetcher = get_fetcher(portal_doc)
		# The fetcher arms this itself once the sign-in is done, so an operator
		# taking ten minutes to approve a UAE Pass push does not spend the time
		# meant for reading fines.
		fetcher.time_budget_seconds = _sweep_budget_seconds()
		attended = bool(int(wait_for_login))
		relaying = bool(int(use_relay or 0))
		if relaying:
			if not getattr(fetcher, "supports_relay", False):
				# Checked again here, not only at the button, because the
				# scheduled path reaches this function too. A portal that
				# challenges on every search cannot be relayed at all, and
				# failing loudly beats a headless browser waiting on a
				# challenge nobody can see.
				raise AuthenticationRequired(
					f"{portal_doc.name} cannot be signed into by relay - it challenges on "
					"the search itself, not only at sign-in, so a person has to be at the "
					"window. Use the 'Operator signs in at the server' mode for it."
				)
			# Headless is the whole point: the browser runs on the server and
			# the operator confirms on their phone, wherever they are.
			fetcher.headless = True
			fetcher.use_relay = True
			fetcher.relay_announce = _relay_announcer(run.name, portal_doc.name, notify_user)
			fetcher.relay_timeout_ms = _relay_timeout_ms()
		elif not attended:
			# Nobody is watching, so there is nothing for a visible window to
			# show - and a scheduler worker has no display to open one on.
			fetcher.headless = True
		# Read before the fetch, not after: a run that signs in fresh rewrites
		# the session file, and with it the only timestamp saying how old the
		# session under test was.
		banked_before = _session_banked_at(fetcher)
		result = fetcher.fetch_for_traffic_file(
			traffic_file_number,
			include_details=bool(int(include_details)),
			wait_for_login=attended,
		)
		for fine in result.fines:
			vehicle = _vehicle_for_plate(
				fine.raw.get("plate_code"),
				fine.raw.get("plate_number"),
				fine.raw.get("plate_emirate"),
			)
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
			reason = result.message
	except Exception as exc:
		run.status = "Failed"
		failure = exc
		error_log = frappe.get_traceback()
		# One readable line beside the traceback, not instead of it. The sweep
		# writes its outcome onto a settings field nobody would read a traceback
		# out of, and the traceback still has to stay intact on the run.
		reason = _describe_fetch_failure(exc)
		_log_error(f"Operator-assisted fine sync failed: {portal_doc.name}")
	finally:
		if fetcher:
			fetcher.close()
		# Before finalize, not only after: finalize() saves, and a save that
		# throws would otherwise roll back the Failed status along with it,
		# leaving no record at all of a run that broke.
		_checkpoint()
		run.finalize(error_log=error_log)
		_checkpoint()

	_record_session_observation(portal_doc.name, banked_before, failure, run.name)

	outcome = {
		"sync_run": run.name,
		"status": run.status,
		"fines_found": run.fines_found,
		"fines_new": run.fines_new,
		"reason": reason,
	}
	if bool(int(use_relay or 0)):
		# The relay's dialog is watching a stream of progress messages, so the
		# ending has to arrive the same way. Without this it sits on the last
		# "waiting for confirmation" frame forever, which reads as a hang even
		# when the run finished perfectly.
		announcer = _relay_announcer(run.name, portal, notify_user)
		announcer({
			"stage": "finished",
			"message": reason or _("Finished."),
			"outcome": outcome,
		})
	return outcome


def _long_queue_depth():
	"""How many jobs are waiting on `long`, or None if that cannot be read.

	Reported back to the button so "queued" can say how long a wait it really
	is. One worker serves this queue, so depth is very nearly the wait: each
	job ahead is a browser fetch measured in minutes, not milliseconds.

	Never raises. This is a nicety attached to a fetch that has already been
	queued successfully, and a broken count is not a reason to fail the call.
	"""
	try:
		from rq import Queue

		from frappe.utils.background_jobs import get_redis_conn

		return Queue("long", connection=get_redis_conn()).count
	except Exception:
		return None


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

	relaying = relay_is_available(portal)["available"]
	if relaying:
		# Read now rather than inside the job, so a missing or malformed number
		# is a message on the button instead of a Failed run five minutes later.
		# The value is discarded immediately - it is never carried as a job
		# argument, because RQ keeps those in Redis and Frappe renders them in
		# the RQ Job list.
		from transport.transport.fine_sync.uae_pass import get_relay_mobile

		get_relay_mobile()

	# at_front, because this is the one fine-sync job with a person waiting on
	# it. Everything else on `long` - the hourly sweep, the capture, the reach
	# probe - is batch work nobody is watching, and RQ's default tail insert put
	# the button behind all of it.
	#
	# It buys less than it sounds like. There is ONE worker on `long`
	# (`worker_long: bench worker --queue long`, background_workers = 1), and
	# at_front reorders the QUEUE, not the worker. A sweep already in flight
	# holds the worker for as long as it runs - up to the 3600s timeout below -
	# and this job waits it out from the front. Front of the queue is the most
	# that can be fixed here; the rest needs its own worker, which is a bench
	# config change and not something this function can do.
	job = frappe.enqueue(
		"transport.transport.fine_sync.service.run_operator_assisted_sync",
		queue="long",
		timeout=3600,
		at_front=True,
		portal=portal,
		traffic_file_number=traffic_file_number,
		include_details=include_details,
		wait_for_login=1,
		use_relay=1 if relaying else 0,
		notify_user=frappe.session.user,
	)
	if not job:
		# enqueue() returns nothing when it drops the call - a dead queue, or a
		# duplicate it deduplicated. Reporting {"queued": True} regardless is how
		# an operator ends up watching for a window that was never going to open.
		frappe.throw(
			_("The fetch could not be queued. The background queue is not accepting "
			  "work - check that the queue's Redis and a worker for the 'long' queue "
			  "are both running, then try again."),
			title=_("Fetch Not Queued"),
		)

	return {
		"queued": True,
		"job_id": getattr(job, "id", None),
		"ahead": _long_queue_depth(),
		"portal": portal,
		"relay": relaying,
		"event": RELAY_EVENT,
		"message": (
			_("Fetch queued. Keep this page open - the UAE Pass screen will appear here "
			  "shortly, and you confirm the request in the app on your phone.")
			if relaying
			else _("Fetch queued. A browser window will open on the server for sign-in; "
			       "watch Traffic Fine Sync Run for the result.")
		),
	}


# --- Client-side fetch -------------------------------------------------------
#
# The operator's own Chrome reads the portal and posts the rendered rows here,
# instead of a browser being driven on the server. The parsing stays on this
# side: the extension hands up rows exactly as the page produced them, and the
# same `_to_fine` that the server-side fetch uses turns them into fines, so the
# two paths cannot drift into two different ideas of what a fine is.
#
# The contract is written up in extension/docs/ingest-contract.md.

# A whole-fleet TAMM list runs to a few hundred rows. Five thousand is far past
# any real read and still small enough that the transform below cannot be used
# to tie the worker up.
MAX_CLIENT_FETCH_ROWS = 5000


def _client_fetch_target(fetcher, portal_doc, traffic_file_number):
	"""Where the extension should navigate, or a refusal that says why.

	One code path for every portal. A fetcher that can be read in a browser
	overrides `client_fetch_target`; the base class raises NotImplementedError,
	and a fetcher that knows something more specific - RTA knows its page has
	never been captured - raises FineFetchError carrying that reason.

	The distinction matters to whoever presses the button. "No client fetch
	path" and "nobody has ever seen this portal's fines table, here is what
	would unblock it" are different answers, and flattening them into one loses
	the only part that tells somebody what to do next.
	"""
	try:
		target = fetcher.client_fetch_target(traffic_file_number)
	except NotImplementedError:
		frappe.throw(
			_("{0} has no client fetch path. The extension has no reader for this portal, "
			  "so there is nothing for it to open.").format(portal_doc.name),
			title=_("Portal Not Supported"),
		)
	except FineFetchError as exc:
		frappe.throw(str(exc), title=_("Portal Not Readable Yet"))

	if not target.get("reader"):
		# A fetcher that returns a target without naming a reader would send the
		# browser to a real page with nothing to read it. Refusing is the only
		# safe answer: an unread page reports no fines, and no fines reads as a
		# clean result.
		frappe.throw(
			_("{0} returned no reader, so its page cannot be read.").format(portal_doc.name),
			title=_("Portal Not Supported"),
		)
	return target


def _client_fetch_portal(portal):
	"""The portal doc, checked for a client-side fetch, or throw.

	Shared by both halves so the button and the ingest cannot disagree about
	who is allowed to do this.

	`is_enabled` is required here although `_operator_assisted_sync` deliberately
	does not require it. That exemption exists because a named operator standing
	at the server's own browser is not unattended access. This path is a browser
	on a machine this site does not control, posting rows it says it read, and
	the flag is the only place the site records that it wants this portal touched
	at all.
	"""
	_check_permission()

	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	if not portal_doc.is_enabled:
		frappe.throw(
			_("{0} is switched off. Enable it before fetching from it.").format(portal_doc.name),
			title=_("Portal Not Enabled"),
		)
	if not portal_doc.has_written_authorization:
		frappe.throw(
			_("Record the written authorization for {0} before fetching from it.").format(
				portal_doc.name
			),
			title=_("Authorization Required"),
		)
	return portal_doc


@frappe.whitelist()
def get_client_fetch_target(portal):
	"""Which fleet the operator's browser should ask the portal about.

	The traffic file number is the only part of the portal URL that names the
	fleet, and the extension needs it to navigate. It is handed over this
	authenticated call rather than rendered into the desk page, for the reason
	`get_relay_mobile()` already exists: anything else running in that tab can
	read the page's JavaScript and the window messages it posts.

	Returns the number and nothing else - not the mobile, not the session
	state, not the credential's name.
	"""
	portal_doc = _client_fetch_portal(portal)

	# The traffic file number is read here but not returned. TAMM carries the
	# fleet in a query parameter, so its fetcher builds it into the URL; a
	# login-based portal carries the fleet in the session and takes no
	# parameter at all. Handing the extension a page to open rather than a
	# fleet identifier keeps that difference where it belongs, and is one less
	# place the number can be stored or logged.
	traffic_file_number = frappe.db.get_value(
		"Traffic Fine Portal Credential",
		{"portal": portal_doc.name, "is_active": 1},
		"traffic_file_number",
	)

	fetcher = get_fetcher(portal_doc)
	try:
		target = _client_fetch_target(fetcher, portal_doc, traffic_file_number)
	finally:
		fetcher.close()

	return target


@frappe.whitelist(methods=["POST"])
def ingest_client_fetch(portal, rows, truncated=0, fetched_at=None):
	"""Stage fines that the operator's browser read from the portal.

	Note what this does NOT accept: the traffic file number. A browser that
	could name the traffic file could attach a batch of fines to any fleet on
	the site. The credential is re-read here instead, so the rows can only ever
	land against the fleet this portal is configured for.

	Every value in `rows` is untrusted text. It arrives from a browser, and a
	row is not evidence that a page was ever visited - which is why the run
	records where it came from rather than presenting these fines as though the
	server had seen them itself.
	"""
	portal_doc = _client_fetch_portal(portal)

	if not frappe.db.exists(
		"Traffic Fine Portal Credential", {"portal": portal_doc.name, "is_active": 1}
	):
		frappe.throw(
			_("{0} has no active credential. Rows cannot be attached to a fleet without "
			  "one.").format(portal_doc.name),
			title=_("Traffic File Number Missing"),
		)

	if isinstance(rows, str):
		try:
			rows = json.loads(rows)
		except ValueError:
			frappe.throw(_("Rows were not valid JSON."), title=_("Malformed Fetch"))
	if not isinstance(rows, list):
		frappe.throw(_("Rows must be a list."), title=_("Malformed Fetch"))
	if len(rows) > MAX_CLIENT_FETCH_ROWS:
		frappe.throw(
			_("This batch carries {0} rows, past the {1} a single fetch may post.").format(
				len(rows), MAX_CLIENT_FETCH_ROWS
			),
			title=_("Batch Too Large"),
		)
	if any(not isinstance(row, dict) for row in rows):
		frappe.throw(_("Every row must be an object."), title=_("Malformed Fetch"))

	truncated = bool(int(truncated or 0))

	# Before the run record, not inside it: a portal with no transform is a
	# configuration answer, not a failed fetch, and it should not leave a
	# Failed run behind suggesting the portal was actually read.
	fetcher = get_fetcher(portal_doc)
	transform = getattr(fetcher, "_to_fine", None)
	if not getattr(fetcher, "client_reader", None) or transform is None:
		# Two conditions, because they can fail apart. MOI has a `_to_fine` and
		# no reader; a portal could be given a reader before its transform is
		# written. Either way there is no complete route, and accepting rows on
		# half a route stages fines nobody can account for.
		#
		# Checked again here and not only in get_client_fetch_target: that call
		# is advice to a browser, this one is a write. A caller that skipped the
		# first must not thereby skip the gate.
		fetcher.close()
		frappe.throw(
			_("{0} has no client fetch path. Its rows cannot be turned into fines on this "
			  "route.").format(portal_doc.name),
			title=_("Portal Not Supported"),
		)

	run = frappe.get_doc({
		"doctype": "Traffic Fine Sync Run",
		"portal": portal_doc.name,
		"status": "Running",
		"started_on": now_datetime(),
		"triggered_by": frappe.session.user,
		"fetch_source": "Operator Browser",
	})
	run.insert(ignore_permissions=True)
	_checkpoint()

	error_log, staged, reason = None, 0, None
	try:
		# _to_fine returns None for anything that is not a fine - headers,
		# totals, the placeholder row an empty list renders.
		fines = [f for f in (transform(row) for row in rows) if f]
		for fine in fines:
			vehicle = _vehicle_for_plate(
				fine.raw.get("plate_code"),
				fine.raw.get("plate_number"),
				fine.raw.get("plate_emirate"),
			)
			if _stage_fine(run, portal_doc, vehicle, fine):
				staged += 1
			run.add_vehicle_result(
				vehicle.name if vehicle else None,
				fine.plate,
				"Success",
				None if vehicle else _("No Rental Vehicle matches this plate - fine is unlinked."),
				1,
			)
			_checkpoint()
		run.fines_found = len(fines)
		run.fines_new = staged
		if truncated:
			# The read stopped at its budget with pages still unread. Calling
			# that "Completed" would present part of the list as the whole
			# liability, which is the one thing this flag exists to prevent.
			run.status = "Completed with Errors"
			reason = _(
				"The browser stopped before the end of the list, so these are some of the "
				"fines and not all of them. A fine missing from this batch was never seen - "
				"that is not the same as it not existing."
			)
			error_log = reason
	except Exception as exc:
		run.status = "Failed"
		error_log = frappe.get_traceback()
		reason = _describe_fetch_failure(exc)
		_log_error(f"Client fine fetch failed: {portal_doc.name}")
	finally:
		fetcher.close()
		_checkpoint()
		run.finalize(error_log=error_log)
		_checkpoint()

	return {
		"sync_run": run.name,
		"status": run.status,
		"fines_found": run.fines_found,
		"fines_new": run.fines_new,
		"truncated": truncated,
		"reason": reason,
	}


def _session_banked_at(fetcher):
	"""When the fetcher's saved sign-in was written, or None.

	The file's mtime is the only record of it: `save_session()` writes the
	whole storage state at the moment sign-in succeeds and keeps no timestamp
	of its own inside.
	"""
	try:
		path = fetcher.session_path()
		if not path or not os.path.exists(path):
			return None
		return frappe.utils.convert_utc_to_system_timezone(
			datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
		).replace(tzinfo=None)
	except Exception:
		return None


def _record_session_observation(portal, banked_at, failure, sync_run=None):
	"""Write down how old the sign-in was when the portal ruled on it.

	This is the whole session-lifetime measurement, and it costs no portal
	traffic at all. Polling a portal until its session dies would be the
	obvious way to find the number and is the wrong one here: every poll is
	another automated request from the address whose reputation is already the
	reason challenges appear. Each sync already makes the portal rule on the
	session, so the answer is being thrown away rather than gathered.

	**Only the two informative outcomes are recorded.** Silence is the correct
	response to everything else:

	- No prior session - the run signed in fresh, so nothing was tested.
	- `ConfirmationNotApproved` - a push nobody answered. The session was never
	  presented, and filing that as a refusal is exactly the false negative
	  that made the portal banner claim a healthy session had been rejected.
	- Any other failure - a timeout, a parse error, a portal outage. None of
	  them is the portal ruling on a cookie, and a lifetime built from them
	  would read as far shorter than the truth.
	"""
	if not banked_at:
		return
	if failure is not None and not isinstance(failure, AuthenticationRequired):
		return
	if isinstance(failure, ConfirmationNotApproved):
		return

	observed_on = now_datetime()
	try:
		frappe.get_doc({
			"doctype": "Portal Session Observation",
			"portal": portal,
			"banked_on": banked_at,
			"observed_on": observed_on,
			"age_minutes": max(0, int((observed_on - banked_at).total_seconds() // 60)),
			"outcome": "Refused" if failure is not None else "Accepted",
			"sync_run": sync_run,
		}).insert(ignore_permissions=True)
	except Exception:
		# A measurement must never be able to fail a sync. The run's own result
		# is the thing that matters; a missing data point is not.
		_log_error(f"Could not record session observation: {portal}")


@frappe.whitelist()
def portal_session_lifetime(portal):
	"""What the observations say a banked sign-in is good for.

	Reported as a bracket rather than a single figure, because that is what the
	data actually supports: the longest age the portal has still accepted, and
	the shortest age it has refused. The real expiry is somewhere between them,
	and it narrows as more runs happen.

	Written for the sentence somebody wants to put in front of a client. Until
	both ends exist, the honest answer is that we do not know yet - which is
	the answer this returns, rather than a number nobody measured.
	"""
	if not frappe.has_permission("Traffic Fine Portal", "read", doc=portal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	rows = frappe.get_all(
		"Portal Session Observation",
		filters={"portal": portal},
		fields=["outcome", "age_minutes"],
		limit_page_length=0,
	)
	accepted = [r.age_minutes for r in rows if r.outcome == "Accepted" and r.age_minutes is not None]
	refused = [r.age_minutes for r in rows if r.outcome == "Refused" and r.age_minutes is not None]

	longest_accepted = max(accepted) if accepted else None
	shortest_refused = min(refused) if refused else None

	if longest_accepted is None and shortest_refused is None:
		message = _("No observations yet. The figure appears here on its own as fines are fetched.")
	elif shortest_refused is None:
		message = _("At least {0} minutes - no expiry has been observed yet, so this is a floor "
		            "and not the lifetime.").format(longest_accepted)
	elif longest_accepted is None:
		message = _("Under {0} minutes on the only expiry seen so far.").format(shortest_refused)
	elif longest_accepted < shortest_refused:
		message = _("Between {0} and {1} minutes.").format(longest_accepted, shortest_refused)
	else:
		# Overlapping brackets are not bad data - a portal may end a session
		# for its own reasons - so say so instead of picking a side.
		message = _("Not a clean cut: accepted at {0} minutes but refused at {1}, so the portal "
		            "is ending some sessions early for reasons other than age.").format(
			longest_accepted, shortest_refused
		)

	return {
		"observations": len(rows),
		"longest_accepted_minutes": longest_accepted,
		"shortest_refused_minutes": shortest_refused,
		"message": message,
	}


@frappe.whitelist()
def get_portal_signin_console():
	"""Where the operator can watch and drive the server's sign-in browser.

	The console is a noVNC view of the Xvfb display the headed browser runs on,
	so an operator anywhere can answer a CAPTCHA the server cannot. That is the
	whole point of it, and it is also exactly why this is gated three times
	rather than once.

	**What opening this actually grants.** Not a picture of a screen - a mouse
	and a keyboard on a browser that is signed in to a government portal with a
	banked session. Whoever holds it can navigate that browser anywhere the
	session reaches, which on TAMM includes the payment controls. No rule in
	this app can stop them; the only real controls are who gets the console's
	own password and who holds a fine-sync role here.

	So: off unless someone switched it on, refused without a fine-sync role, and
	refused over plain HTTP. The address is never rendered into a page for
	anyone who fails those - it is fetched, not embedded.

	Every grant is written to the site log. There is deliberately no DocType for
	it: an audit trail that any holder of the audited permission can edit is
	worse than none, and Transport roles can write most things here.
	"""
	_check_permission()

	if not _setting("enable_portal_signin_console"):
		return {
			"available": False,
			"reason": _("The sign-in console is switched off. Transport Settings has the "
			            "switch, and its description is worth reading before you use it."),
		}

	url = (_setting("portal_signin_console_url") or "").strip()
	if not url:
		return {
			"available": False,
			"reason": _("No console address is set. Put the noVNC address on Transport "
			            "Settings first."),
		}

	# Plain HTTP would carry the console's own password, every keystroke typed
	# into the portal, and the whole signed-in screen in clear text. Refused
	# rather than warned about.
	if not url.lower().startswith("https://"):
		return {
			"available": False,
			"reason": _("The console address must be HTTPS. It carries a live portal "
			            "session and its own password, and neither may cross the network "
			            "in the clear."),
		}

	frappe.logger("transport.portal_console").info(
		f"portal sign-in console opened by {frappe.session.user}"
	)

	return {
		"available": True,
		"url": url,
		"warning": _("This is a live browser signed in to the portal, not a picture of "
		             "one. Anything you click there really happens - never touch a pay "
		             "or delete control. Answer the challenge, approve the push on your "
		             "phone, then close this window."),
	}


@frappe.whitelist()
def relay_is_available(portal):
	"""Whether this portal, in this configuration, can be signed into by relay.

	One answer used in three places - the button, the queueing path and the job
	itself - because they were each deciding it separately and could disagree.
	The reason is returned alongside, since "no" is the useful case and a bare
	False sends someone looking through settings for a switch that was never
	the problem.
	"""
	from transport.transport.fine_sync.uae_pass import normalize_mobile

	mode = _setting("fine_sync_login_mode")
	if mode != "Relay Code To My Screen":
		return {
			"available": False,
			"reason": _("UAE Pass Sign-In Mode is set to open a window on the server. "
			            "Change it on Transport Settings to relay the code instead."),
		}

	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	fetcher_class = fetcher_class_for(portal_doc)
	if not fetcher_class or not getattr(fetcher_class, "supports_relay", False):
		return {
			"available": False,
			"reason": _("{0} challenges on the search itself, not only at sign-in, so a "
			            "person has to be at the window. It cannot be relayed.").format(portal),
		}

	if not normalize_mobile(_setting("uae_pass_mobile")):
		return {
			"available": False,
			"reason": _("No valid UAE Pass mobile number on Transport Settings. The relay "
			            "types it into the sign-in, so it cannot start without one."),
		}

	return {"available": True, "reason": None}


@frappe.whitelist()
def enqueue_relay_reachability_probe(portal):
	"""Queue the headless reach test. Types nothing, needs nobody, spends no push.

	Worth its own button because the relay rests on an assumption that is cheap
	to test and expensive to be wrong about: that a headless browser is served
	the same sign-in page a windowed one gets. Government portals sit behind
	WAFs, and headless Chromium is exactly what such a filter turns away. The
	first time that mattered, it surfaced during a demo.
	"""
	_check_permission()
	traffic_file_number = frappe.db.get_value(
		"Traffic Fine Portal Credential",
		{"portal": portal, "is_active": 1},
		"traffic_file_number",
	)
	frappe.enqueue(
		"transport.transport.fine_sync.service.run_relay_reachability_probe",
		queue="long",
		timeout=600,
		portal=portal,
		traffic_file_number=traffic_file_number,
		notify_user=frappe.session.user,
	)
	return {
		"queued": True,
		"event": RELAY_EVENT,
		"message": _("Reach test queued. It opens a headless browser, walks as far as the "
		             "UAE Pass sign-in form and stops there - nothing is typed and no "
		             "confirmation is sent to your phone."),
	}


def run_relay_reachability_probe(portal, traffic_file_number=None, notify_user=None):
	"""Land on the sign-in form headlessly and report what was actually served."""
	portal_doc = frappe.get_doc("Traffic Fine Portal", portal)
	fetcher = get_fetcher(portal_doc)
	announce = _relay_announcer(None, portal, notify_user)

	# The probe opens a browser and waits on a slow portal. Nothing may sit in
	# an open transaction across that.
	_checkpoint()

	try:
		if not getattr(fetcher, "supports_relay", False):
			raise AuthenticationRequired(
				f"{portal} is not a relay portal, so there is nothing to reach-test."
			)
		report = fetcher.probe_headless_reach(traffic_file_number)
	except Exception as exc:
		_log_error(f"Relay reach test failed: {portal}")
		announce({"stage": "probe-failed", "message": _describe_fetch_failure(exc)})
		raise
	finally:
		fetcher.close()

	if report["reached_uae_pass"] and report["identifier_field"]:
		message = _("Headless reached the UAE Pass sign-in form and found the field to "
		            "type into. The relay should work from this server.")
	elif report["reached_uae_pass"]:
		message = _("Headless reached UAE Pass, but no sign-in field could be identified "
		            "with confidence. Nothing would be typed. The screen was recorded so "
		            "the field can be named exactly.")
	elif report["captcha_challenge_on_screen"]:
		message = _("UAE Pass put a CAPTCHA challenge on screen for the headless browser. "
		            "Nothing here will answer one - use the window mode for this sign-in.")
	elif report["blocked"]:
		message = _("The portal served a block page to the headless browser rather than "
		            "the sign-in. The relay cannot work from this server as it stands - "
		            "use the window mode.")
	else:
		message = _("Headless did not reach UAE Pass. It stopped on {0}. The page was "
		            "recorded under private/portal-captures.").format(
			report.get("final_host") or _("an unknown page"))

	report["message"] = message
	announce({"stage": "probe-finished", "message": message, "report": report})
	return report


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


def _apply_portal_verdict(status, fetcher, portal_doc):
	"""Let the portal overrule the cookie about whether a session still works.

	A cookie's expiry is a ceiling, not a promise. The portal can end a session
	early - an idle timeout, a sign-out elsewhere, a profile that reset - and
	nothing on disk changes when it does. So the banner cheerfully read
	"live for about another 25m" while every scheduled fetch behind it was
	failing with "No live session", which is the same reading being right about
	the file and wrong about the world.

	The last unattended attempt is the only evidence that reflects the portal's
	own opinion, so when one has failed on authentication *since* the session
	was banked, that verdict wins.
	"""
	if status.get("state") not in ("live", "unknown"):
		return

	try:
		path = fetcher.session_path()
		banked_at = frappe.utils.convert_utc_to_system_timezone(
			datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
		).replace(tzinfo=None)
	except Exception:
		return

	run = frappe.get_all(
		"Traffic Fine Sync Run",
		filters={"portal": portal_doc.name, "status": "Failed"},
		fields=["name", "started_on", "error_log"],
		order_by="started_on desc",
		limit=1,
	)
	if not run:
		return
	run = run[0]
	if not run.started_on or run.started_on < banked_at:
		# Older than the sign-in it would be judging. Says nothing about now.
		return
	if "AuthenticationRequired" not in (run.error_log or ""):
		return

	status["usable"] = False
	status["state"] = "rejected"
	status["indicator"] = "red"
	status["message"] = _(
		"The portal refused this session at {0}, even though the saved cookie has not "
		"expired yet - so a sign-in is needed despite what the clock says. Sync run {1} "
		"has the detail."
	).format(frappe.utils.format_datetime(run.started_on, "HH:mm"), run.name)


def portal_session_reading(fetcher, portal_doc):
	"""The one reading of a portal's sign-in, for the form and the sweep both.

	They used to answer this separately, and a sweep that trusted the cookie
	queued a fetch on every fire that the portal had already refused.
	"""
	reader = getattr(fetcher, "session_status", None)
	status = describe_session_status(
		reader() if reader else {"state": "unsupported", "usable": False}
	)
	_apply_portal_verdict(status, fetcher, portal_doc)
	return status


@frappe.whitelist()
def get_portal_session_status(portal):
	"""How much life is left in an operator's banked sign-in, for the desk.

	Exists because a lapsed session is otherwise invisible on the form. The
	scheduled entry point skips a portal with no live session on purpose - a
	browser opened every hour to fail would bury the log in noise - so
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
			"can_run_sync": False,
			"can_capture_public": False,
			"indicator": "orange",
			"message": _(
				"No fetcher is implemented for {0}, so fines cannot be collected from it "
				"yet - by hand or on a schedule. This is not a credential or "
				"authorization problem."
			).format(portal_doc.name),
		}

	status = portal_session_reading(fetcher, portal_doc)
	status["fetch_implemented"] = bool(getattr(fetcher, "fetch_implemented", True))
	# Whether the browser extension has a reader for this portal. Deliberately
	# separate from fetch_implemented: the two used to be false together and for
	# the same reason ("nobody has ever seen this page"), and RTA is the first
	# portal where they diverge - its pages are read, but only in a person's own
	# browser. Reporting one as though it were the other tells an operator the
	# page has never been captured while a working button sits next to the
	# message saying otherwise.
	status["client_fetch_available"] = bool(getattr(fetcher, "client_reader", None))
	status["can_capture"] = hasattr(fetcher, "capture_signed_in")
	# Every button on the form is gated on one of these. They are answered here,
	# together, because the form previously had two independent scripts each
	# guessing at capability from the document alone - which is how Run Sync came
	# to be offered on a portal with no fetcher, and Capture Page on one with no
	# public form URL to capture.
	status["can_run_sync"] = bool(portal_doc.is_enabled) and status["fetch_implemented"]
	status["can_capture_public"] = bool(portal_doc.public_form_url)
	# The button has to say which of the two sign-ins it is about to start,
	# because they ask completely different things of the person pressing it -
	# one needs them at the server's own screen, the other needs their phone.
	# Guessing wrong is how somebody waits at a window that never opened.
	relay = relay_is_available(portal_doc.name)
	status["supports_relay"] = bool(getattr(fetcher, "supports_relay", False))
	status["relay_available"] = relay["available"]
	status["relay_blocked_reason"] = relay["reason"]

	if not status["fetch_implemented"]:
		if status["client_fetch_available"]:
			# Not a gap. This portal is read in the operator's own browser, and
			# saying "never captured" here would contradict the working button
			# beside it. Blue, not orange: nothing is broken and nothing is
			# waiting on anybody.
			status["indicator"] = "blue"
			status["message"] = _(
				"{0} is read in your own browser, not on the server. Press Fetch Fines In "
				"This Browser: the extension opens the portal, you complete anything it "
				"asks for, and it reads the list back. Run Sync and the unattended sweep "
				"stay off for this portal until it is known whether the same search draws "
				"a challenge when a server repeats it."
			).format(portal_doc.name)
		else:
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


SWEEP_JOB_ID = "transport_operator_fine_sweep"


def _operator_sweep_portals():
	"""Portals the unattended sweep may touch. One query, used by both halves.

	`is_enabled` is in here because it was missing, and its absence made the
	obvious kill switch a lie: an operator who unticked "Enabled" on the portal
	watched the sweep keep fetching, because neither half of this path ever
	looked at the field. Every other route already respected it -
	`get_syncable_portals()` filters on it - so the two had simply drifted.

	Written once and called from both halves for that reason. The queueing half
	and the sweep itself each ran their own copy of this filter, which is how
	they came to disagree with `get_syncable_portals()` in the first place, and
	fixing only one of them would leave a sweep already on the long worker still
	running against a portal somebody had just switched off.

	The three conditions are not interchangeable. `is_enabled` is the operator's
	switch; `has_written_authorization` is the legal gate on touching the portal
	at all; `fetch_mode` is whether an unattended run is even the right shape.
	"""
	return frappe.get_all(
		"Traffic Fine Portal",
		filters={
			"is_enabled": 1,
			"fetch_mode": "Operator Assisted",
			"has_written_authorization": 1,
		},
		fields=["name"],
	)


def run_scheduled_operator_syncs():
	"""The scheduled entry point. Decides whether there is anything to do, and
	hands the doing to the long queue.

	**It hands the fetch to the `long` queue rather than doing it here.** Frappe
	picks a job's queue from its frequency alone - `get_queue_name()` returns
	`long` only for "Daily Long" and maintenance jobs - so this Cron job runs on
	`default`, shoulder to shoulder with the */5 trip reminders and the
	driver-confirmation escalation. Fetching inline held a default worker for
	the whole browser run, up to the 30-minute ceiling: long enough to swallow
	six consecutive escalation cycles without a trace. Enqueueing is what keeps
	a slow portal from becoming a late escalation.

	So this half stays cheap: two reads and an enqueue, done in milliseconds.
	Everything that can take time - opening a browser, restoring a session,
	walking pages - happens in `run_operator_sweep` on the long worker, which is
	also where the outcome is written down.
	"""
	if not frappe.db.get_single_value("Transport Settings", "enable_scheduled_fine_sync"):
		_record_sweep_note(_("Skipped: Traffic Fine Sync is switched off in Transport Settings."))
		return

	portals = _operator_sweep_portals()
	if not portals:
		_record_sweep_note(
			_("Nothing to do: no portal is currently enabled, authorized in writing and set to "
			  "Operator Assisted. Unticking Enabled on the portal is what stops this check.")
		)
		return

	# One fetch may run for the whole time budget and they run one after
	# another, so the ceiling grows with the number of portals - ten minutes each
	# on top of the budget for browser start-up and the sign-in check, five more
	# for the bookkeeping around the loop. Capped at two budgets, because most
	# portals cost milliseconds (no fetcher, or they need a person for every
	# search) and at most a couple can ever fetch unattended. Past two full
	# budgets a check is stuck, and it is holding both the long worker and the
	# slot that keeps the next fire from starting. Being cut short there is safe:
	# the closing note says which portals it never reached.
	budget = _sweep_budget_seconds() or 1800
	timeout = int(min(len(portals) * (budget + 600) + 300, 2 * budget + 900))

	# Deduplicated because a fetch can outlive the gap to the next
	# fire. Without this, a second sweep would start against a portal the first
	# one is still signed in to, which is how a session gets invalidated.
	job = frappe.enqueue(
		"transport.transport.fine_sync.service.run_operator_sweep",
		queue="long",
		timeout=timeout,
		job_id=SWEEP_JOB_ID,
		deduplicate=True,
	)
	if not job:
		# enqueue() returns nothing when it drops a duplicate. Reporting that as
		# "queued" would be the same lie this whole path exists to stop.
		started = frappe.db.get_single_value("Transport Settings", "last_operator_sweep_on")
		_record_sweep_note(
			_("A fetch started at {0} is still running, so this check queued nothing. The next "
			  "check is in an hour.").format(format_datetime(started) if started else _("an earlier check")),
			stamp=False,
		)
		return

	_record_sweep_note(
		_("Checking {0} portal(s) now on the long worker. The Scheduled Job Log only records "
		  "that this check was queued, so it says \"Complete\" either way - what the fetch "
		  "actually found appears here when it finishes.").format(len(portals))
	)


def run_operator_sweep():
	"""Fetch from every operator-assisted portal that can be fetched from, and
	write down what happened to each one.

	Deliberately does nothing far more often than it does something, and that is
	the design rather than a shortcoming:

	* **Off unless switched on.** Same Transport Settings flag as the unattended
	  syncs, which ships off.
	* **Never waits for a login.** A sign-in needs a UAE Pass push approved on a
	  phone. Waiting for one on a schedule would leave a browser hung until the
	  next fire, and on a repeating schedule those stack up until the box dies.
	  No live session simply means no run.
	* **Never overlaps itself.** `run_operator_assisted_sync` holds the portal
	  lock; this passes `skip_if_busy` so a collision is reported, not filed as
	  an error.

	Whatever it decides, it writes down why - and this is the half that can say
	how the fetch *ended*, because it is the half that waits for it. An earlier
	version queued each fetch separately and recorded "queued", which was true
	at the moment it was written and useless a minute later: the run's outcome
	lived only in the Sync Run list, and nothing connected the two. Skipping
	quietly had cost the same way, the Scheduled Job Log recording "Complete"
	for a sweep that fetched the whole fleet and for one that found no session,
	no credential, or a disabled flag.
	"""
	if not frappe.db.get_single_value("Transport Settings", "enable_scheduled_fine_sync"):
		# Re-read rather than trust the half that queued this: the flag can be
		# turned off while an earlier fetch is still holding the long worker.
		_record_sweep_note(_("Stopped: Traffic Fine Sync was switched off before this check ran."))
		return

	portals = _operator_sweep_portals()
	notes = []
	done = 0
	try:
		for portal in portals:
			notes.append(_sweep_one_portal(portal.name))
			done += 1
			if done < len(portals):
				# Between portals only, never mid-fetch: a note written while a
				# browser is open would be overwritten seconds later anyway, and
				# committing mid-fetch is what `_checkpoint` is careful about.
				_record_sweep_note(
					"\n".join(
						[_("Checked {0} of {1} portal(s) so far:").format(done, len(portals))]
						+ notes
						+ [_("Still working through the rest...")]
					),
					stamp=False,
				)
	finally:
		# In `finally` because the alternative is worse than any error: an rq
		# timeout or an unexpected raise would leave "checking now" on screen
		# forever, and someone would read that hours later as a fetch still in
		# progress.
		if done < len(portals):
			notes.append(
				_("The check stopped before reaching {0} more portal(s). The Error Log has "
				  "the reason.").format(len(portals) - done)
			)
		# Counted here rather than taken from the half that queued this. That half
		# announced a number before the fork, and a portal can lose its written
		# authorization in between - so the closing note says what this run actually
		# looked at, and the two can never quietly disagree.
		header = _("Checked {0} of {1} portal(s):").format(done, len(portals))
		_record_sweep_note(
			"\n".join([header] + notes) if notes else _("Nothing to check: no portal qualified."),
			stamp=False,
		)


def _sweep_one_portal(portal_name):
	"""Fetch from one portal, or say why not. Always returns a line for the note."""
	# Whether anything can read this portal is checked before whether a
	# credential exists, because otherwise the note blames the credential:
	# it sent an operator off to add a traffic file number to a portal that
	# has no fetcher, which would not have helped and did not.
	portal_doc = frappe.get_doc("Traffic Fine Portal", portal_name)
	try:
		fetcher = get_fetcher(portal_doc)
	except Exception:
		return _("{0}: no fetcher is implemented for this portal.").format(portal_name)

	if not getattr(fetcher, "supports_unattended", True):
		return _(
			"{0}: needs a person for every search, not just for sign-in, so it is "
			"never fetched on a schedule. Use Fetch Fines Now with an operator "
			"present."
		).format(portal_name)

	if not getattr(fetcher, "fetch_implemented", True):
		return _(
			"{0}: its pages behind the sign-in have not been captured yet, so nothing "
			"can read them. Run Capture Portal Pages on the portal record."
		).format(portal_name)

	traffic_file_number = frappe.db.get_value(
		"Traffic Fine Portal Credential",
		{"portal": portal_name, "is_active": 1},
		"traffic_file_number",
	)
	if not traffic_file_number:
		# Without a traffic file there is nothing to query; a per-vehicle
		# fallback would be a different (and much slower) thing entirely.
		return _("{0}: no active credential carrying a traffic file number.").format(portal_name)

	# Check for a banked session before opening anything at all.
	status = portal_session_reading(fetcher, portal_doc)
	relaying = False
	if not status.get("usable"):
		# A lapsed session normally just means no run. It can instead start a
		# relay sign-in, but only where somebody has deliberately switched that
		# on: a scheduled relay pushes a confirmation to a phone at whatever
		# hour the cron fires, publishes the code to a screen nobody is
		# watching, and spends a push that cannot be re-requested. Off by
		# default for that reason, and the setting says so.
		if not (
			_setting("use_relay_for_scheduled_sync")
			and relay_is_available(portal_name)["available"]
		):
			return _("{0}: not fetched - {1}").format(
				portal_name,
				status.get("message")
				or _("this portal banks no signed-in session, so nothing can be fetched unattended."),
			)
		relaying = True

	try:
		result = run_operator_assisted_sync(
			portal_name,
			traffic_file_number,
			include_details=1,
			# A relay does its own signing in, so it must not also be told to
			# wait at a window nobody opened.
			wait_for_login=0,
			skip_if_busy=1,
			use_relay=1 if relaying else 0,
		)
	except Exception as exc:
		# Only reaches here if the fetch could not be *started* - a permission
		# or authorization refusal. Once it starts, `_operator_assisted_sync`
		# catches its own failures and reports them on the Sync Run.
		_log_error(f"Scheduled fine sweep could not start: {portal_name}")
		return _("{0}: could not start - {1}").format(portal_name, _describe_fetch_failure(exc))

	return _describe_sync_result(portal_name, result)


def _describe_sync_result(portal_name, result):
	"""Turn a finished fetch into the one line a person reads on the settings form."""
	if not result:
		return _("{0}: the fetch returned no result at all. The Error Log has the detail.").format(
			portal_name
		)

	if result.get("skipped"):
		return _("{0}: not fetched - {1}").format(portal_name, result.get("reason"))

	status = result.get("status")
	run_name = result.get("sync_run")
	found = result.get("fines_found") or 0
	new = result.get("fines_new") or 0

	if status == "Completed":
		return _("{0}: fetched {1} fine(s), {2} new. Sync run {3}.").format(
			portal_name, found, new, run_name
		)

	if status == "Completed with Errors":
		# Must not read as a clean fetch: this is the truncated case, where part
		# of the portal's list was never reached.
		return _(
			"{0}: only partly fetched - {1} fine(s) read, {2} new, but the run did not "
			"finish cleanly: {3} Sync run {4} has the detail."
		).format(portal_name, found, new, result.get("reason") or "", run_name)

	return _("{0}: fetch failed - {1} Sync run {2} has the full error.").format(
		portal_name, result.get("reason") or _("no reason was recorded."), run_name
	)


def _describe_fetch_failure(exc):
	"""One readable line for an exception, for a note that has no room for a traceback.

	Kept apart from the traceback rather than replacing it: `error_log` on the
	Sync Run stays the full traceback, both for diagnosis and because
	`_apply_portal_verdict` reads the exception's name out of it.
	"""
	message = (str(exc) or "").strip()
	# Before its parent, which it subclasses. A missed push already carries its
	# own remedy ("press Fetch Fines Now"), and the parent's - go and sign in
	# again - is the wrong instruction for a sign-in that never failed.
	if isinstance(exc, ConfirmationNotApproved):
		return message or _("nobody confirmed the UAE Pass push in time.")
	if isinstance(exc, AuthenticationRequired):
		# The exception already says what the portal did; all this adds is the
		# remedy, because the note is read by whoever has to act on it and the
		# remedy is never obvious from a portal's own wording.
		return _("{0} Nothing scheduled will work until someone signs in from the portal record.").format(
			message or _("The portal would not accept the saved sign-in.")
		)
	if isinstance(exc, FineFetchError):
		return message or _("the portal could not be read, and gave no reason.")
	return _("{0}: {1}").format(exc.__class__.__name__, message or _("no message"))


def _record_sweep_note(note, stamp=True):
	"""Leave the sweep's reasoning somewhere a person can read it.

	`stamp` controls the timestamp, not the note. "Last Checked On" means when
	the current check *started*, so the half-way and closing notes of one check
	leave it alone - moving it forward on every write would make a fetch that ran
	for twenty minutes look like it had just begun.

	Onto Transport Settings rather than the Error Log, because none of these
	outcomes is an error - "nobody has signed in lately" is the normal state of
	an operator-assisted portal - and filing them as errors every hour is
	the noise this whole path was built to avoid. It is a status, so it lives
	where the switch that controls it lives.
	"""
	try:
		frappe.db.set_value(
			"Transport Settings",
			"Transport Settings",
			(
				{"last_operator_sweep_note": note, "last_operator_sweep_on": now_datetime()}
				if stamp
				else {"last_operator_sweep_note": note}
			),
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		# A note that cannot be written must never take the sweep down with it.
		_log_error("Could not record operator sweep note")


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


def _vehicle_for_plate(plate_code, plate_number, plate_emirate=None):
	"""Match a reported plate to a fleet vehicle, or return None.

	Returns a shape `_stage_fine` and `add_vehicle_result` can both use.

	**The emirate used to be hardcoded to "Abu Dhabi".** That was true of the
	only portal there was, and it silently became false the moment a second one
	arrived: RTA reports Dubai plates, and every one of them failed to match a
	vehicle that was sitting in the fleet. Worse, it failed *invisibly* - the
	fine staged unlinked, which is indistinguishable from "this car is not ours"
	and is exactly the signal the "our fleet is smaller than the portal thinks"
	reading was drawn from.

	Three outcomes, and the third is the one worth the extra query:

	* one match - the vehicle,
	* no match - genuinely not in the fleet, as before,
	* **more than one match - None, deliberately.** Plate codes and numbers
	  repeat across emirates, so a fine whose emirate is unknown can land on two
	  real, different vehicles. Picking either would bill one customer for the
	  other's fine. Returning None stages the fine unlinked, where a person
	  decides.
	"""
	if not (plate_code and plate_number):
		return None

	# Stored plate parts are upper-cased and trimmed by `normalize_plate`, so a
	# portal writing "w" or " W " has to be folded the same way or it will not
	# match a vehicle that is plainly there.
	filters = {
		"plate_code": str(plate_code).strip().upper(),
		"plate_number": str(plate_number).strip().upper(),
	}

	emirate = normalize_emirate(plate_emirate)
	if emirate:
		filters["plate_emirate"] = emirate

	# limit 2 is all the question needs: "exactly one" and "more than one" are
	# the only answers that differ, and neither needs the whole list.
	matches = frappe.get_all(
		"Rental Vehicle", filters=filters, fields=["name", "license_plate"], limit=2
	)
	if len(matches) != 1:
		return None

	vehicle = matches[0]
	return frappe._dict({
		"name": vehicle.name,
		# The vehicle's own plate as the fleet records it, not its docname. The
		# two are usually the same string and were conflated here; they are not
		# the same thing, and the run's per-vehicle rows show this one.
		"license_plate": vehicle.license_plate or vehicle.name,
	})


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
			_log_error(f"Scheduled fine sync failed: {portal.name}")
