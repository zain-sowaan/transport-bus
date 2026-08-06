# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Server-side support for the customer-facing web portal (Phase 6):
track trips and raise requests. Invoice download reuses ERPNext's own
native customer portal (Sales Invoice already has has_website_permission
wired via erpnext.controllers.website_list_for_contact) - not rebuilt here.

Identity: ERPNext's own Customer doctype has a native "Portal Users" child
table (adding a row there auto-grants the "Customer" role, see
erpnext.controllers.website_list_for_contact.add_role_for_portal_user) -
reused directly rather than inventing a parallel identity mechanism. The
lookup below is a parameterised reimplementation of that module's
get_parents_for_user("Customer"), which only reads frappe.session.user and
can't be pointed at an explicit `user` the way permission_query_conditions/
has_permission hooks are called with.

This is deliberately manual-entry-recording-an-external-event for the
things that still need human judgement (see Phase 4/5 notes on customer
approval) - what's genuinely new here is the customer can see their own
data and log a request themselves, not that every workflow step becomes
self-service."""

import frappe
from frappe import _


def get_customers_for_user(user=None):
	user = user or frappe.session.user
	portal_user = frappe.qb.DocType("Portal User")
	return (
		frappe.qb.from_(portal_user)
		.select(portal_user.parent)
		.where(portal_user.user == user)
		.where(portal_user.parenttype == "Customer")
	).run(pluck="name")


# --------------------------------------------------------------------------
# Trip permissions (dispatched from transport/transport/permissions.py)
# --------------------------------------------------------------------------

def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	customers = get_customers_for_user(user)
	if not customers:
		return "1=0"
	customer_list = ", ".join(frappe.db.escape(c) for c in customers)
	return f"(`tabTrip`.customer in ({customer_list}))"


def has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	return doc.customer in get_customers_for_user(user)


# --------------------------------------------------------------------------
# Customer Request permissions
# --------------------------------------------------------------------------

def get_permission_query_conditions_for_customer_request(user=None):
	user = user or frappe.session.user
	customers = get_customers_for_user(user)
	if not customers:
		return "1=0"
	customer_list = ", ".join(frappe.db.escape(c) for c in customers)
	return f"(`tabCustomer Request`.customer in ({customer_list}))"


def has_permission_for_customer_request(doc, user=None):
	user = user or frappe.session.user
	return doc.customer in get_customers_for_user(user)


# --------------------------------------------------------------------------
# Portal-facing whitelisted methods
# --------------------------------------------------------------------------

@frappe.whitelist()
def get_my_trips(from_date=None, to_date=None):
	customers = get_customers_for_user()
	if not customers:
		return []

	filters = {"customer": ("in", customers)}
	if from_date and to_date:
		filters["trip_date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["trip_date"] = (">=", from_date)
	elif to_date:
		filters["trip_date"] = ("<=", to_date)

	return frappe.get_list(
		"Trip",
		filters=filters,
		fields=[
			"name", "project", "trip_date", "scheduled_time", "direction",
			"from_place", "to_place", "status", "vehicle", "driver",
		],
		order_by="trip_date desc, scheduled_time desc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_my_requests():
	customers = get_customers_for_user()
	if not customers:
		return []

	return frappe.get_list(
		"Customer Request",
		filters={"customer": ("in", customers)},
		fields=["name", "project", "request_type", "description", "status", "response", "creation"],
		order_by="creation desc",
	)


@frappe.whitelist()
def create_request(request_type, description, project=None):
	customers = get_customers_for_user()
	if not customers:
		frappe.throw(_("Your login is not linked to a Customer."), frappe.PermissionError)

	if project and frappe.db.get_value("Project", project, "customer") not in customers:
		frappe.throw(_("That Project does not belong to your account."), frappe.PermissionError)

	request = frappe.get_doc(
		{
			"doctype": "Customer Request",
			"customer": customers[0],
			"project": project,
			"request_type": request_type,
			"description": description,
			"raised_by": frappe.session.user,
		}
	)
	request.insert(ignore_permissions=True)
	return request.name
