# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Recreates the client-facing monthly timesheet from the source
transport workflow document: one row per calendar day, one
Pickup/Drop column pair per Route, "PH" for public holidays (via the
Project's native Holiday List) and "ADDITIONAL" for any ad-hoc trip that
day - matching the sample monthly timesheet embedded in that document."""

import calendar

import frappe
from frappe import _
from frappe.utils import formatdate, get_first_day, get_last_day, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("project"):
		frappe.throw(_("Select a Project."))

	month_date = getdate(filters.get("month") or frappe.utils.nowdate())
	from_date, to_date = get_first_day(month_date), get_last_day(month_date)

	routes = frappe.get_all(
		"Route",
		filters={"project": filters.project, "is_active": 1},
		fields=["name", "route_name"],
		order_by="route_name",
	)
	columns = get_columns(routes)

	holidays = get_holidays(filters.project, from_date, to_date)
	trips_by_date = get_trips_by_date(filters.project, from_date, to_date)

	data = []
	cursor = from_date
	while cursor <= to_date:
		row = {"date": formatdate(cursor, "dd-MM-yy"), "day": calendar.day_abbr[cursor.weekday()]}

		if cursor in holidays:
			row["remarks"] = "PH"
		else:
			day_trips = trips_by_date.get(cursor, [])
			has_additional = False
			for route in routes:
				pickup = next(
					(t for t in day_trips if t.route == route.name and t.direction == "Pickup"), None
				)
				drop = next(
					(t for t in day_trips if t.route == route.name and t.direction == "Drop"), None
				)
				row[f"{route.name}_pickup"] = format_time(pickup) if pickup else ""
				row[f"{route.name}_drop"] = format_time(drop) if drop else ""
				if (pickup and pickup.is_additional) or (drop and drop.is_additional):
					has_additional = True

			row["remarks"] = "ADDITIONAL" if has_additional else ""

		data.append(row)
		cursor = frappe.utils.add_days(cursor, 1)

	return columns, data


def get_columns(routes):
	columns = [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Data", "width": 90},
		{"label": _("Day"), "fieldname": "day", "fieldtype": "Data", "width": 60},
	]
	for route in routes:
		columns.append(
			{
				"label": f"{route.route_name} - Pickup",
				"fieldname": f"{route.name}_pickup",
				"fieldtype": "Data",
				"width": 110,
			}
		)
		columns.append(
			{
				"label": f"{route.route_name} - Drop",
				"fieldname": f"{route.name}_drop",
				"fieldtype": "Data",
				"width": 110,
			}
		)
	columns.append({"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 100})
	return columns


def get_holidays(project, from_date, to_date):
	holiday_list = frappe.db.get_value("Project", project, "holiday_list")
	if not holiday_list:
		return set()

	dates = frappe.get_all(
		"Holiday",
		filters={"parent": holiday_list, "holiday_date": ("between", [from_date, to_date])},
		pluck="holiday_date",
	)
	return {getdate(d) for d in dates}


def get_trips_by_date(project, from_date, to_date):
	trips = frappe.get_all(
		"Trip",
		filters={
			"project": project,
			"trip_date": ("between", [from_date, to_date]),
			"status": ("not in", ["Rejected", "Cancelled"]),
		},
		fields=["trip_date", "route", "direction", "scheduled_time", "actual_start_time", "is_additional", "status"],
	)

	by_date = {}
	for trip in trips:
		by_date.setdefault(getdate(trip.trip_date), []).append(trip)
	return by_date


def format_time(trip):
	# actual_start_time is a Time field Frappe silently fills with the
	# current wall-clock time on insert (see
	# reference_frappe_time_field_autofill) - it's only meaningful once a
	# driver has actually started the leg, which "status" tells us, not the
	# field's own truthiness.
	value = trip.scheduled_time
	if trip.status in ("Started", "Completed", "Approved") and trip.actual_start_time:
		value = trip.actual_start_time
	return frappe.utils.format_time(value, "hh:mm a") if value else ""
