# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Erase actual start/end stamps that were never observed.

Frappe fills every blank Time field with the current wall-clock time when a
document is built server-side (frappe/model/create_new.py - the Time branch,
unlike Datetime, is not gated on a "now" default). So every Trip a Trip
Schedule generated was stored claiming it had already started and ended, at
the instant of generation, and the value copied on into Transport Timesheet
Trip with the rest of the leg.

Trip.clear_unstarted_actuals() stops new rows being written that way, but it
only runs on save; these rows are already on disk and are read as evidence a
leg ran - by Trip.get_driver_hours_for_day(), which filters on
`actual_start_time is set`, and by the driver-hours summary in the portal.
"""

import frappe

PRE_START_STATUSES = ("Draft", "Assigned", "Accepted", "Rejected")


def execute():
	trip = frappe.qb.DocType("Trip")
	frappe.qb.update(trip).set(trip.actual_start_time, None).set(trip.actual_end_time, None).where(
		trip.status.isin(PRE_START_STATUSES)
	).run()

	stop = frappe.qb.DocType("Trip Stop")
	frappe.qb.update(stop).set(stop.actual_time, None).where(
		stop.parenttype == "Trip"
	).where(
		stop.parent.isin(
			frappe.qb.from_(trip).select(trip.name).where(trip.status.isin(PRE_START_STATUSES))
		)
	).run()

	# The timesheet copies the trip's stamps onto its own rows, so the same
	# fabricated times are sitting there too.
	line = frappe.qb.DocType("Transport Timesheet Trip")
	frappe.qb.update(line).set(line.actual_start_time, None).set(line.actual_end_time, None).where(
		line.trip.isin(
			frappe.qb.from_(trip).select(trip.name).where(trip.status.isin(PRE_START_STATUSES))
		)
	).run()
