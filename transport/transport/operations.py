# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Office-side (Transport In-Charge / Operations) trip actions, distinct from
driver_portal.py's driver-facing ones. Approving a Completed trip is what
'hits the timesheet' per the source workflow doc - only Approved trips are
eligible to be pulled onto a Transport Timesheet (see
transport_timesheet.populate_trips)."""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, nowtime

APPROVER_ROLES = ("Transport In-Charge", "Transport Operations", "System Manager")

# A trip the office may still start. "Accepted" is the driver having agreed to
# it; "Assigned" is the far more common case here, because a driver who is not
# using the portal never presses Accept at all - and that is precisely who this
# action exists for.
STARTABLE_STATUSES = ("Assigned", "Accepted")


@frappe.whitelist()
def approve_trip(trip_name):
	if not any(role in frappe.get_roles() for role in APPROVER_ROLES):
		frappe.throw(_("Not permitted to approve trips."), frappe.PermissionError)

	trip = frappe.get_doc("Trip", trip_name)
	if trip.status != "Completed":
		frappe.throw(_("Only Completed trips can be approved (current status: {0}).").format(trip.status))

	trip.db_set({"status": "Approved", "approved_by": frappe.session.user, "approved_on": now_datetime()})
	return "Approved"


@frappe.whitelist()
def start_trip(trip_name, start_odometer=None):
	"""Start a trip from the office, for a driver who is not using the portal.

	The driver's own start (driver_portal.start_trip) demands a vehicle photo
	and checks the phone's position against the pickup geofence. Neither is
	available to somebody at a desk, and requiring them would make this unusable
	for the case it exists for - a driver who phones in, or who has no portal
	account. What survives is everything that does not depend on being there:
	who is allowed to do this, and whether the trip is in a state where starting
	it means anything.

	Those two checks are the whole point. This replaces a Server Script that ran
	the same action through `safe_exec` with no role check at all, so any user
	who could reach the endpoint could start any trip in the system. It also
	could not be read, reviewed or deployed with the app, and it broke on an
	ordinary `.format()` call because `format` is a blocked attribute inside
	`safe_exec` - a sandbox rule that has nothing to do with starting trips.

	`db_set` rather than `save`, matching the driver path. Trip.validate clears
	`actual_start_time` for every status before Started (Trip.clear_unstarted_
	actuals, which exists because Frappe fills blank Time fields with the
	current time), so saving would wipe the stamp being set here.
	"""
	if not any(role in frappe.get_roles() for role in APPROVER_ROLES):
		frappe.throw(_("Not permitted to start trips."), frappe.PermissionError)

	trip = frappe.get_doc("Trip", trip_name)
	if trip.status not in STARTABLE_STATUSES:
		frappe.throw(
			_("Only an Assigned or Accepted trip can be started (current status: {0}).").format(
				trip.status
			)
		)

	trip.db_set({
		"status": "Started",
		"actual_start_time": nowtime(),
		"start_odometer": start_odometer,
	})
	return "Started"


@frappe.whitelist()
def end_trip(trip_name, end_odometer=None, driver_remarks=None):
	"""End a trip from the office, for a driver who is not using the portal.

	The counterpart to `start_trip` above, and the same trade: the driver's own
	end (driver_portal.end_trip) requires a photo of the drop location, which
	nobody at a desk can supply. Everything that does not need you to be there
	is kept, including the two steps that happen after the stamps are written.

	**`recalculate_after_actuals` is the reason this belongs in the app at all.**
	Duration and Duty/OT are derived from the actual times, and `db_set` skips
	`validate()`, so without it the trip keeps the duration it was *planned*
	with and hour-based OT is never applied to the leg that just finished. The
	Server Script this replaces could not do it: the function is not whitelisted
	so `frappe.call()` cannot reach it, and calling `doc.set_duration()` from
	inside `safe_exec` fails with "Not allowed to write to object", because a
	script may read a Document but never mutate one. A trip ended through that
	script was therefore costed on planned hours while one ended in the portal
	was costed on real ones - the same trip worth different money depending on
	which button finished it.
	"""
	if not any(role in frappe.get_roles() for role in APPROVER_ROLES):
		frappe.throw(_("Not permitted to end trips."), frappe.PermissionError)

	trip = frappe.get_doc("Trip", trip_name)
	if trip.status != "Started":
		frappe.throw(
			_("Only a Started trip can be ended (current status: {0}).").format(trip.status)
		)

	if end_odometer not in (None, ""):
		end_odometer = flt(end_odometer)
		# `is not None`, not a truth test: a start odometer of 0 is falsy, so a
		# plain `if trip.start_odometer` skips the check on every vehicle that
		# began the day on a fresh meter - and accepts a reading below it.
		if trip.start_odometer is not None and end_odometer < flt(trip.start_odometer):
			frappe.throw(
				_("End odometer ({0}) cannot be less than the start odometer ({1}).").format(
					end_odometer, flt(trip.start_odometer)
				)
			)
	else:
		end_odometer = None

	trip.db_set({
		"status": "Completed",
		"actual_end_time": nowtime(),
		"end_odometer": end_odometer,
		"driver_remarks": driver_remarks,
	})

	from transport.transport.driver_portal import check_fatigue, notify_roles
	from transport.transport.doctype.trip.trip import recalculate_after_actuals

	notify_roles(
		("Transport In-Charge",),
		_("Trip {0} finished - approval needed").format(trip.name),
		_("{0} ended Trip {1} from the office. Approve it to include in the timesheet.").format(
			frappe.session.user, trip.name
		),
		trip.doctype,
		trip.name,
	)
	recalculate_after_actuals(trip.name)
	check_fatigue(trip.driver, trip.trip_date)
	return "Completed"
