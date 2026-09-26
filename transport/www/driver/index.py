# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, getdate, nowdate

from transport.transport.driver_portal import (
	format_amount,
	format_day,
	format_time_short,
	get_driver_for_user,
	status_slug,
)

no_cache = 1

HISTORY_PAGE_SIZE = 20
UPCOMING_DAYS = 7
# Trips from earlier days that the driver still has to act on. Without this
# a trip accepted/started yesterday but never ended vanished from the list,
# and the driver had no way back to it to end it or log its expenses.
OPEN_STATUSES = ("Assigned", "Accepted", "Started")
DONE_STATUSES = ("Completed", "Approved")

TRIP_FIELDS = [
	"name",
	"trip_date",
	"scheduled_time",
	"direction",
	"from_place",
	"to_place",
	"status",
	"vehicle",
	"route",
	"project",
	"duty_type",
	"needs_reallocation",
	"actual_start_time",
	"actual_end_time",
	"start_odometer",
	"end_odometer",
]


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/driver"
		raise frappe.Redirect

	context.title = _("My Trips")
	context.no_breadcrumbs = True

	driver = get_driver_for_user()
	context.driver = driver
	if not driver:
		context.error = _("No Driver record is linked to your account. Contact Operations.")
		return

	context.driver_name = frappe.db.get_value("Driver", driver, "full_name") or driver
	context.today_label = getdate(nowdate()).strftime("%A, %d %B")
	context.view = "history" if frappe.form_dict.view == "history" else "upcoming"
	context.stats = get_stats(driver)

	if context.view == "history":
		trips = get_history(context, driver)
	else:
		trips = get_upcoming(driver)

	decorate(trips)
	context.groups = group_by_day(trips)
	context.trip_count = len(trips)


def get_upcoming(driver):
	today = getdate(nowdate())
	upcoming = frappe.get_all(
		"Trip",
		filters={"driver": driver, "trip_date": ("between", [today, add_days(today, UPCOMING_DAYS - 1)])},
		fields=TRIP_FIELDS,
		order_by="trip_date asc, scheduled_time asc",
	)
	overdue = frappe.get_all(
		"Trip",
		filters={
			"driver": driver,
			"trip_date": ("between", [add_days(today, -UPCOMING_DAYS), add_days(today, -1)]),
			"status": ("in", OPEN_STATUSES),
		},
		fields=TRIP_FIELDS,
		order_by="trip_date asc, scheduled_time asc",
	)
	for trip in overdue:
		trip.overdue = 1
	return overdue + upcoming


def get_history(context, driver):
	today = getdate(nowdate())
	month = frappe.form_dict.month or ""
	try:
		month_start = getdate(f"{month}-01") if month else None
	except Exception:
		month_start = None

	filters = {"driver": driver}
	if month_start:
		month_end = min(get_last_day(month_start), add_days(today, -1))
		filters["trip_date"] = ("between", [month_start, month_end])
	else:
		filters["trip_date"] = ("<", today)

	status = frappe.form_dict.status or ""
	if status in ("Completed", "Approved", "Rejected", "Cancelled"):
		filters["status"] = status

	page = max(cint(frappe.form_dict.page), 1)
	rows = frappe.get_all(
		"Trip",
		filters=filters,
		fields=TRIP_FIELDS,
		order_by="trip_date desc, scheduled_time desc",
		limit_start=(page - 1) * HISTORY_PAGE_SIZE,
		limit_page_length=HISTORY_PAGE_SIZE + 1,
	)

	context.page = page
	context.has_more = len(rows) > HISTORY_PAGE_SIZE
	context.month = month_start.strftime("%Y-%m") if month_start else ""
	context.status_filter = filters.get("status", "")
	context.month_options = month_options(today)
	return rows[:HISTORY_PAGE_SIZE]


def month_options(today, count=12):
	options = []
	cursor = get_first_day(today)
	for _i in range(count):
		options.append({"value": cursor.strftime("%Y-%m"), "label": cursor.strftime("%B %Y")})
		cursor = get_first_day(add_days(cursor, -1))
	return options


def get_stats(driver):
	today = getdate(nowdate())
	month_start = get_first_day(today)

	today_trips = frappe.db.count("Trip", {"driver": driver, "trip_date": today})
	to_accept = frappe.db.count(
		"Trip",
		{"driver": driver, "status": "Assigned", "trip_date": (">=", add_days(today, -UPCOMING_DAYS))},
	)
	month = frappe.db.sql(
		"""
		select count(*) as trips,
			sum(case when end_odometer > start_odometer then end_odometer - start_odometer else 0 end) as km
		from `tabTrip`
		where driver = %(driver)s and trip_date between %(start)s and %(end)s
			and status in %(done)s
		""",
		{"driver": driver, "start": month_start, "end": today, "done": DONE_STATUSES},
		as_dict=True,
	)[0]
	pending = frappe.db.sql(
		"""select count(*) as cnt, coalesce(sum(amount), 0) as total
		from `tabTrip Expense` where driver = %s and status = 'Pending'""",
		driver,
		as_dict=True,
	)[0]

	return {
		"today": today_trips,
		"to_accept": to_accept,
		"month_trips": cint(month.trips),
		"month_km": int(flt(month.km)),
		"pending_expense_count": cint(pending.cnt),
		"pending_expense_total": format_amount(pending.total),
	}


def decorate(trips):
	names = [t.name for t in trips]
	expenses = {}
	if names:
		for row in frappe.db.sql(
			"""select trip, count(*) as cnt, sum(amount) as total
			from `tabTrip Expense` where trip in %(trips)s and status != 'Rejected'
			group by trip""",
			{"trips": tuple(names)},
			as_dict=True,
		):
			expenses[row.trip] = row

	for trip in trips:
		trip.time_label = format_time_short(trip.scheduled_time)
		trip.status_slug = status_slug(trip.status)
		trip.km = (
			int(flt(trip.end_odometer) - flt(trip.start_odometer))
			if trip.start_odometer and trip.end_odometer and trip.end_odometer > trip.start_odometer
			else None
		)
		trip.needs_action = trip.status in ("Assigned", "Accepted", "Started")
		exp = expenses.get(trip.name)
		trip.expense_count = cint(exp.cnt) if exp else 0
		trip.expense_total = format_amount(exp.total) if exp else ""


def group_by_day(trips):
	groups = []
	for trip in trips:
		label = _("Pending from earlier") if trip.get("overdue") else format_day(trip.trip_date)
		if not groups or groups[-1]["label"] != label:
			groups.append({"label": label, "overdue": bool(trip.get("overdue")), "trips": []})
		groups[-1]["trips"].append(trip)
	return groups
