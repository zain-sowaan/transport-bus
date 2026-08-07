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

import frappe
from frappe import _
from frappe.utils import now_datetime

from transport.transport.fine_sync.base import FineFetchError
from transport.transport.fine_sync.registry import get_fetcher
from transport.transport.vehicle_plate import PLATE_FIELDS

SYNC_ROLES = ("Transport Accounts", "Transport Operations", "System Manager")


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
def run_sync(portal, limit=None):
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

	queryable, unqueryable = get_fleet_for_sync()
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
	try:
		fetcher = get_fetcher(portal_doc, credential_doc)
		for v in queryable:
			_sync_one_vehicle(run, fetcher, portal_doc, v)
	except Exception:
		# A failure setting up (or an unsupported portal) fails the whole run
		# loudly rather than leaving it looking merely empty.
		run.status = "Failed"
		error_log = frappe.get_traceback()
		frappe.log_error(title=f"Traffic fine sync failed: {portal_doc.name}")
	finally:
		if fetcher:
			fetcher.close()
		run.finalize(error_log=error_log)

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
	"""Write one fetched fine to staging. Returns True if it was new."""
	if frappe.db.exists(
		"Traffic Fine Staging", {"portal": portal_doc.name, "ticket_number": fine.ticket_number}
	):
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
		"raw_payload": json.dumps(fine.raw, indent=1, default=str)[:10000],
		"status": "New",
	})
	doc.insert(ignore_permissions=True)
	return doc.status == "New"


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
			"date_time": staging.fine_datetime,
			"fine_type": staging.fine_type,
			"fine_location": staging.fine_location,
			"amount": staging.amount,
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

	fetcher = get_fetcher(portal_doc)
	try:
		report = fetcher.capture_page()
	finally:
		fetcher.close()

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
