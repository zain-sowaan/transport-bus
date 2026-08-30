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
from frappe.utils import now_datetime

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
def run_operator_assisted_sync(portal, traffic_file_number, include_details=0, wait_for_login=1):
	"""Fetch a whole company traffic file with a person completing the sign-in.

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
	_check_permission()

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
	* **Never overlaps itself.** The lock is taken with a 1-second timeout, so a
	  fire that finds one in progress gives up instead of queueing behind it.

	Its useful window is the ~90 minutes a TAMM session lasts after a person
	signs in; outside that it costs a file check and returns.
	"""
	if not frappe.db.get_single_value("Transport Settings", "enable_scheduled_fine_sync"):
		return

	from frappe.utils.synchronization import LockTimeoutError, filelock

	portals = frappe.get_all(
		"Traffic Fine Portal",
		filters={"fetch_mode": "Operator Assisted", "has_written_authorization": 1},
		fields=["name", "fetcher_key"],
	)

	for portal in portals:
		traffic_file_number = frappe.db.get_value(
			"Traffic Fine Portal Credential",
			{"portal": portal.name, "is_active": 1},
			"traffic_file_number",
		)
		if not traffic_file_number:
			# Without a traffic file there is nothing to query; a per-vehicle
			# fallback would be a different (and much slower) thing entirely.
			continue

		portal_doc = frappe.get_doc("Traffic Fine Portal", portal.name)
		try:
			fetcher = get_fetcher(portal_doc)
		except Exception:
			continue

		# Check for a banked session before opening anything at all.
		has_session = getattr(fetcher, "has_saved_session", None)
		if not (has_session and has_session()):
			continue

		try:
			with filelock(f"fine_sync_{portal.name}", timeout=1):
				run_operator_assisted_sync(
					portal.name, traffic_file_number, include_details=1, wait_for_login=0
				)
		except LockTimeoutError:
			# A run is already going. Skipping is correct - two browsers sharing
			# one portal session is how a session gets invalidated.
			continue
		except Exception:
			frappe.log_error(title=f"Scheduled operator-assisted sync failed: {portal.name}")


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
