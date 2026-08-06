# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Backing methods for the "Custom" Number Cards seeded in
transport/fixtures/number_card.json - anything expressible as a single
aggregate on one doctype uses a plain "Document Type" card instead (see
the fixture); these two need real cross-record logic a generic aggregate
can't express."""

import frappe
from frappe.utils import get_first_day, get_last_day, getdate, nowdate

from transport.transport.report.project_profit_and_loss.project_profit_and_loss import (
	get_expenses,
	get_revenue,
)


@frappe.whitelist()
def trips_today_count(filters=None):
	count = frappe.db.count(
		"Trip", {"trip_date": nowdate(), "status": ("not in", ["Rejected", "Cancelled"])}
	)
	return {"value": count}


@frappe.whitelist()
def loss_making_projects_count(filters=None):
	"""This month's standing, not all-time - matches the doc's "Active vs
	Loss-making projects" as a current-state dashboard signal."""
	period = frappe._dict(from_date=get_first_day(nowdate()), to_date=get_last_day(nowdate()))
	project_names = frappe.get_all("Trip", pluck="project", distinct=True)

	count = 0
	for project in project_names:
		if get_expenses(project, period) > get_revenue(project, period):
			count += 1
	return {"value": count}


@frappe.whitelist()
def fleet_utilization_percent(filters=None):
	from_date, to_date = get_first_day(nowdate()), get_last_day(nowdate())
	total_days = (getdate(to_date) - getdate(from_date)).days + 1

	vehicle_names = frappe.get_all("Trip", pluck="vehicle", distinct=True)
	if not vehicle_names:
		return {"value": 0}

	trips = frappe.get_all(
		"Trip",
		filters={
			"vehicle": ("in", vehicle_names),
			"trip_date": ("between", [from_date, to_date]),
			"status": ("not in", ["Rejected", "Cancelled"]),
		},
		fields=["vehicle", "trip_date"],
	)

	active_dates_by_vehicle = {}
	for trip in trips:
		active_dates_by_vehicle.setdefault(trip.vehicle, set()).add(trip.trip_date)

	total_possible = total_days * len(vehicle_names)
	total_active = sum(len(dates) for dates in active_dates_by_vehicle.values())
	percent = (total_active / total_possible * 100) if total_possible else 0
	return {"value": round(percent, 1)}
