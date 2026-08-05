# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe

from transport.transport.driver_portal import get_driver_for_user

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/driver"
		raise frappe.Redirect

	driver = get_driver_for_user()
	trip_name = frappe.form_dict.name
	if not (driver and trip_name):
		context.error = "Trip not found."
		return

	trip = frappe.db.get_value(
		"Trip",
		{"name": trip_name, "driver": driver},
		[
			"name", "trip_date", "scheduled_time", "direction", "from_place", "to_place",
			"status", "vehicle", "route", "project", "customer", "rejection_reason", "remarks",
		],
		as_dict=True,
	)
	if not trip:
		context.error = "This trip is not assigned to you."
		return

	context.trip = trip
	context.title = trip.name
	context.no_breadcrumbs = True
