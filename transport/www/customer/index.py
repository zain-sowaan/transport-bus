# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, nowdate

from transport.transport.customer_portal import get_customers_for_user

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/customer"
		raise frappe.Redirect

	customers = get_customers_for_user()
	if not customers:
		context.error = "Your login is not linked to a Customer account. Contact Sowaan Transport."
		return

	context.trips = frappe.get_all(
		"Trip",
		filters={
			"customer": ("in", customers),
			"trip_date": ("between", [add_days(nowdate(), -7), add_days(nowdate(), 7)]),
		},
		fields=[
			"name", "project", "trip_date", "scheduled_time", "direction",
			"from_place", "to_place", "status",
		],
		order_by="trip_date desc, scheduled_time desc",
	)
	context.title = "My Trips"
	context.no_breadcrumbs = True
