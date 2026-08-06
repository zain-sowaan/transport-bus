# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe

from transport.transport.customer_portal import get_customers_for_user, get_my_requests

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/customer/requests"
		raise frappe.Redirect

	customers = get_customers_for_user()
	if not customers:
		context.error = "Your login is not linked to a Customer account. Contact Sowaan Transport."
		return

	context.requests = get_my_requests()
	context.title = "My Requests"
	context.no_breadcrumbs = True
