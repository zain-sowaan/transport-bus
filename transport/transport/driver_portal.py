# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Server-side support for the driver-facing web portal (Phase 3):
view assigned trips, accept/reject, start/end with geofence + photo
proof, log expenses, and the notification/escalation jobs around them.

Permission model: a driver's Frappe user only ever gets READ access to
Trip (see hooks.py permission_query_conditions/has_permission below) -
every state change goes through a whitelisted method here, which
verifies the caller owns the trip and that the transition is valid
before touching the database directly. This keeps "can a driver see
their trips" separate from "can a driver arbitrarily edit any Trip
field", which a plain doc.save() would allow if we'd granted write
access instead.
"""

import math

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime, nowdate, nowtime

MANAGING_ROLES = ("System Manager", "Transport Operations", "Transport In-Charge")


# --------------------------------------------------------------------------
# Identity + permissions
# --------------------------------------------------------------------------

def get_driver_for_user(user=None):
	user = user or frappe.session.user
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not employee:
		return None
	return frappe.db.get_value("Driver", {"employee": employee}, "name")


def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if any(role in frappe.get_roles(user) for role in MANAGING_ROLES):
		return ""

	driver = get_driver_for_user(user)
	if not driver:
		return "1=0"
	return f"(`tabTrip`.driver = {frappe.db.escape(driver)})"


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if any(role in frappe.get_roles(user) for role in MANAGING_ROLES):
		return True

	driver = get_driver_for_user(user)
	return bool(driver) and doc.driver == driver


def _get_my_trip(trip_name):
	driver = get_driver_for_user()
	if not driver:
		frappe.throw(_("No Driver record is linked to your user account."))

	trip = frappe.get_doc("Trip", trip_name)
	if trip.driver != driver:
		frappe.throw(_("This Trip is not assigned to you."), frappe.PermissionError)
	return trip


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

@frappe.whitelist()
def get_my_trips(from_date=None, to_date=None):
	driver = get_driver_for_user()
	if not driver:
		frappe.throw(_("No Driver record is linked to your user account."))

	filters = {"driver": driver}
	if from_date and to_date:
		filters["trip_date"] = ("between", [from_date, to_date])
	else:
		filters["trip_date"] = (">=", nowdate())

	return frappe.get_all(
		"Trip",
		filters=filters,
		fields=[
			"name", "project", "customer", "route", "direction", "from_place", "to_place",
			"trip_date", "scheduled_time", "status", "vehicle", "duty_type", "needs_reallocation",
		],
		order_by="trip_date, scheduled_time",
	)


# --------------------------------------------------------------------------
# State transitions
# --------------------------------------------------------------------------

@frappe.whitelist()
def accept_trip(trip_name):
	trip = _get_my_trip(trip_name)
	if trip.status != "Assigned":
		frappe.throw(_("Only an Assigned trip can be accepted (current status: {0}).").format(trip.status))

	trip.db_set({"status": "Accepted", "needs_reallocation": 0})
	return trip.status


@frappe.whitelist()
def reject_trip(trip_name, reason):
	trip = _get_my_trip(trip_name)
	if trip.status not in ("Assigned", "Accepted"):
		frappe.throw(_("This trip cannot be rejected from its current status ({0}).").format(trip.status))
	if not reason:
		frappe.throw(_("Provide a reason for rejecting this trip."))

	trip.db_set({"status": "Rejected", "rejection_reason": reason, "needs_reallocation": 1})
	notify_roles(
		("Transport In-Charge", "Transport Operations"),
		_("Trip {0} rejected").format(trip.name),
		_("Driver {0} rejected Trip {1}: {2}").format(trip.driver, trip.name, reason),
		trip.doctype,
		trip.name,
	)
	return trip.status


@frappe.whitelist()
def start_trip(trip_name, latitude, longitude, vehicle_photo, start_odometer=None):
	trip = _get_my_trip(trip_name)
	if trip.status != "Accepted":
		frappe.throw(_("Trip must be Accepted before it can be started (current status: {0}).").format(trip.status))
	if not vehicle_photo:
		frappe.throw(_("A vehicle photo is required to start the trip."))

	check_geofence(trip.from_place, latitude, longitude)

	trip.db_set(
		{
			"status": "Started",
			"actual_start_time": nowtime(),
			"vehicle_photo": vehicle_photo,
			"start_odometer": start_odometer,
		}
	)
	notify_roles(
		("Transport In-Charge", "Transport Operations"),
		_("Trip {0} started").format(trip.name),
		_("Driver {0} started Trip {1}.").format(trip.driver, trip.name),
		trip.doctype,
		trip.name,
	)
	return trip.status


@frappe.whitelist()
def end_trip(trip_name, drop_photo, end_odometer=None, driver_remarks=None):
	trip = _get_my_trip(trip_name)
	if trip.status != "Started":
		frappe.throw(_("Trip must be Started before it can be ended (current status: {0}).").format(trip.status))
	if not drop_photo:
		frappe.throw(_("A drop-location photo is required to end the trip."))

	trip.db_set(
		{
			"status": "Completed",
			"actual_end_time": nowtime(),
			"drop_photo": drop_photo,
			"end_odometer": end_odometer,
			"driver_remarks": driver_remarks,
		}
	)
	notify_roles(
		("Transport In-Charge",),
		_("Trip {0} finished - approval needed").format(trip.name),
		_("Driver {0} finished Trip {1}. Approve it to include in the timesheet.").format(trip.driver, trip.name),
		trip.doctype,
		trip.name,
	)
	check_fatigue(trip.driver, trip.trip_date)
	return trip.status


@frappe.whitelist()
def log_expense(trip_name, expense_type, amount, attachment, remarks=None):
	trip = _get_my_trip(trip_name)
	expense = frappe.get_doc(
		{
			"doctype": "Trip Expense",
			"trip": trip.name,
			"expense_type": expense_type,
			"amount": amount,
			"attachment": attachment,
			"remarks": remarks,
		}
	)
	expense.insert(ignore_permissions=True)
	return expense.name


# --------------------------------------------------------------------------
# Geofencing
# --------------------------------------------------------------------------

def check_geofence(place, latitude, longitude):
	place_lat, place_lng, radius = frappe.db.get_value(
		"Place", place, ["latitude", "longitude", "geofence_radius_meters"]
	)
	if not (place_lat and place_lng):
		# No coordinates on record for this Place - nothing to check against.
		return

	distance = haversine_distance_meters(float(latitude), float(longitude), place_lat, place_lng)
	if distance > (radius or 200):
		frappe.throw(
			_("You are {0}m from the pickup location ({1}m allowed). Move closer and try again.").format(
				int(distance), radius or 200
			)
		)


def haversine_distance_meters(lat1, lon1, lat2, lon2):
	earth_radius_m = 6371000
	phi1, phi2 = math.radians(lat1), math.radians(lat2)
	d_phi = math.radians(lat2 - lat1)
	d_lambda = math.radians(lon2 - lon1)

	a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
	return 2 * earth_radius_m * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------
# Fatigue check
# --------------------------------------------------------------------------

def check_fatigue(driver, trip_date):
	"""Approximates a driver's duty span for the day as first-trip-start to
	last-trip-end, since Trip legs are point events rather than durations -
	the closest available proxy to "driving hours" without a richer
	time-tracking model. Alerts Operations/In-Charge if it exceeds the
	configured maximum."""
	max_hours = frappe.db.get_single_value("Transport Settings", "fatigue_max_driving_hours")
	if not max_hours:
		return

	rows = frappe.get_all(
		"Trip",
		filters={
			"driver": driver, "trip_date": trip_date,
			"status": ("in", ["Started", "Completed", "Approved"]),
		},
		fields=["trip_date", "actual_start_time", "actual_end_time"],
	)
	starts = [get_datetime(f"{r.trip_date} {r.actual_start_time}") for r in rows if r.actual_start_time]
	ends = [get_datetime(f"{r.trip_date} {r.actual_end_time}") for r in rows if r.actual_end_time]
	if not (starts and ends):
		return

	span_hours = (max(ends) - min(starts)).total_seconds() / 3600
	if span_hours > max_hours:
		notify_roles(
			("Transport In-Charge", "Transport Operations"),
			_("Fatigue alert: {0}").format(driver),
			_("Driver {0} has been on duty {1:.1f} hours on {2} (limit {3}).").format(
				driver, span_hours, trip_date, max_hours
			),
			"Driver",
			driver,
		)


# --------------------------------------------------------------------------
# Reminder / escalation scheduled jobs
# --------------------------------------------------------------------------

def send_trip_reminders():
	"""Runs every few minutes (see hooks.py cron). Notifies a driver
	`trip_alert_lead_minutes` before their next Accepted/Assigned trip,
	once each, tracked via `reminder_sent`."""
	lead_minutes = frappe.db.get_single_value("Transport Settings", "trip_alert_lead_minutes") or 20
	window_start = now_datetime()
	window_end = frappe.utils.add_to_date(window_start, minutes=lead_minutes)

	trips = frappe.get_all(
		"Trip",
		filters={
			"status": ("in", ["Assigned", "Accepted"]),
			"trip_date": ("in", [window_start.date(), window_end.date()]),
			"reminder_sent": 0,
		},
		fields=["name", "driver", "trip_date", "scheduled_time"],
	)

	for trip in trips:
		if not trip.driver:
			continue
		scheduled = get_datetime(f"{trip.trip_date} {trip.scheduled_time}")
		if window_start <= scheduled <= window_end:
			notify_driver(
				trip.driver, _("Upcoming trip"), _("Trip {0} is coming up soon.").format(trip.name), "Trip", trip.name
			)
			frappe.db.set_value("Trip", trip.name, "reminder_sent", 1)


def escalate_unconfirmed_trips():
	"""Runs every few minutes (see hooks.py cron). If a driver hasn't
	accepted/rejected within `driver_confirmation_window_minutes` of
	assignment, flags the trip for reallocation and escalates to
	In-charge, then Operations."""
	window_minutes = frappe.db.get_single_value("Transport Settings", "driver_confirmation_window_minutes") or 5

	trips = frappe.get_all(
		"Trip",
		filters={"status": "Assigned", "needs_reallocation": 0, "assigned_on": ("is", "set")},
		fields=["name", "driver", "assigned_on"],
	)

	for trip in trips:
		deadline = frappe.utils.add_to_date(get_datetime(trip.assigned_on), minutes=window_minutes)
		if now_datetime() < deadline:
			continue

		frappe.db.set_value("Trip", trip.name, "needs_reallocation", 1)
		notify_roles(
			("Transport In-Charge", "Transport Operations"),
			_("Trip {0} not confirmed").format(trip.name),
			_(
				"Driver {0} has not accepted or rejected Trip {1} within {2} minutes. Reallocate to another driver."
			).format(trip.driver, trip.name, window_minutes),
			"Trip",
			trip.name,
		)


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------

def notify_driver(driver, subject, message, reference_doctype=None, reference_name=None):
	user = frappe.db.get_value("Employee", frappe.db.get_value("Driver", driver, "employee"), "user_id")
	if user:
		_notify_users([user], subject, message, reference_doctype, reference_name)


def notify_roles(roles, subject, message, reference_doctype=None, reference_name=None):
	users = set()
	for role in roles:
		users.update(frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"))

	if users:
		_notify_users(list(users), subject, message, reference_doctype, reference_name)


def _notify_users(users, subject, message, reference_doctype=None, reference_name=None):
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	enqueue_create_notification(
		users,
		{
			"subject": subject,
			"email_content": message,
			"type": "Alert",
			"document_type": reference_doctype,
			"document_name": reference_name,
			"from_user": frappe.session.user,
		},
	)
