# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, nowdate

from transport.transport.driver_portal import get_driver_for_user

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/driver"
		raise frappe.Redirect

	context.driver = get_driver_for_user()
	if not context.driver:
		context.error = "No Driver record is linked to your account. Contact Operations."
		return

	context.trips = frappe.get_all(
		"Trip",
		filters={"driver": context.driver, "trip_date": ("between", [nowdate(), add_days(nowdate(), 6)])},
		fields=[
			"name", "trip_date", "scheduled_time", "direction", "from_place", "to_place",
			"status", "vehicle", "route", "needs_reallocation",
		],
		order_by="trip_date, scheduled_time",
	)
	context.title = "My Trips"
	context.no_breadcrumbs = True
