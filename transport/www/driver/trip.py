# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, format_datetime

from transport.transport.driver_portal import (
	EXPENSE_OPEN_STATUSES,
	EXPENSE_TYPES,
	format_day,
	format_time_short,
	get_driver_for_user,
	list_trip_expenses,
	status_slug,
)

no_cache = 1

TRIP_FIELDS = [
	"name",
	"trip_date",
	"scheduled_time",
	"direction",
	"from_place",
	"to_place",
	"status",
	"vehicle",
	"vehicle_class",
	"route",
	"project",
	"customer",
	"duty_type",
	"is_additional",
	"rejection_reason",
	"remarks",
	"driver_remarks",
	"needs_reallocation",
	"actual_start_time",
	"actual_end_time",
	"duration",
	"start_odometer",
	"end_odometer",
	"vehicle_photo",
	"drop_photo",
	"approved_on",
]

# The normal life of a trip, drawn as a progress bar on the page.
STEPS = ("Assigned", "Accepted", "Started", "Completed", "Approved")


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/driver"
		raise frappe.Redirect

	context.no_breadcrumbs = True
	driver = get_driver_for_user()
	trip_name = frappe.form_dict.name
	if not (driver and trip_name):
		context.error = _("Trip not found.")
		return

	trip = frappe.db.get_value("Trip", {"name": trip_name, "driver": driver}, TRIP_FIELDS, as_dict=True)
	if not trip:
		context.error = _("This trip is not assigned to you.")
		return

	trip.day_label = format_day(trip.trip_date)
	trip.date_label = frappe.utils.formatdate(trip.trip_date)
	trip.time_label = format_time_short(trip.scheduled_time)
	trip.start_label = format_time_short(trip.actual_start_time)
	trip.end_label = format_time_short(trip.actual_end_time)
	trip.status_slug = status_slug(trip.status)
	trip.approved_label = format_datetime(trip.approved_on, "dd-MM-yyyy HH:mm") if trip.approved_on else ""
	trip.km = (
		int(flt(trip.end_odometer) - flt(trip.start_odometer))
		if trip.start_odometer and trip.end_odometer and trip.end_odometer > trip.start_odometer
		else None
	)
	trip.route_name = frappe.db.get_value("Route", trip.route, "route_name") if trip.route else ""
	trip.project_name = frappe.db.get_value("Project", trip.project, "project_name") if trip.project else ""

	context.trip = trip
	context.title = f"{trip.from_place or ''} → {trip.to_place or ''}"
	context.steps = build_steps(trip.status)
	context.stops = frappe.get_all(
		"Trip Stop",
		filters={"parent": trip.name, "parenttype": "Trip"},
		fields=["stop_type", "place", "scheduled_time", "actual_time", "no_of_passengers", "remarks"],
		order_by="idx asc",
	)
	for stop in context.stops:
		stop.time_label = format_time_short(stop.actual_time or stop.scheduled_time)

	expenses = list_trip_expenses(trip.name)
	context.expenses_json = frappe.as_json(expenses)
	context.expense_types = EXPENSE_TYPES
	context.can_log_expense = trip.status in EXPENSE_OPEN_STATUSES
	context.currency = frappe.defaults.get_global_default("currency") or ""


def build_steps(status):
	if status in ("Rejected", "Cancelled", "Draft"):
		return []
	reached = STEPS.index(status) if status in STEPS else -1
	return [
		{"label": _(label), "state": "done" if i < reached else ("current" if i == reached else "todo")}
		for i, label in enumerate(STEPS)
	]
