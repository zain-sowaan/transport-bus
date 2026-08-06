# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Dispatches Trip/Trip Expense/Customer Request row-level permission
checks to whichever portal module (driver_portal.py, customer_portal.py)
owns that role's identity resolution, so each portal module stays focused
on its own audience instead of knowing about the others.

Fixes a real gap found while building the Customer Portal (Phase 6): Trip
Expense grants Transport Driver role-level read access, but hooks.py never
registered a permission_query_conditions/has_permission for it the way
Trip has - desk_access=0 hides this from the Desk UI, but the REST API
respects doctype permissions regardless of desk_access, so a driver's
token could have read every OTHER driver's Trip Expense rows via the API.
Closed here, not left for a later phase, since the mechanism to fix it
already exists (get_driver_for_user) and the risk is real, not
speculative."""

import frappe

from transport.transport import customer_portal, driver_portal

MANAGING_ROLES = ("System Manager", "Transport Operations", "Transport In-Charge", "Transport Accounts")


def get_trip_permission_query_conditions(user=None):
	user = user or frappe.session.user
	roles = frappe.get_roles(user)

	if any(role in roles for role in MANAGING_ROLES):
		return ""
	if "Transport Driver" in roles:
		return driver_portal.get_permission_query_conditions(user)
	if "Customer" in roles:
		return customer_portal.get_permission_query_conditions(user)
	return "1=0"


def get_trip_has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	roles = frappe.get_roles(user)

	if any(role in roles for role in MANAGING_ROLES):
		return True
	if "Transport Driver" in roles:
		return driver_portal.has_permission(doc, ptype, user)
	if "Customer" in roles:
		return customer_portal.has_permission(doc, ptype, user)
	return False


def get_trip_expense_permission_query_conditions(user=None):
	user = user or frappe.session.user
	roles = frappe.get_roles(user)

	if any(role in roles for role in MANAGING_ROLES):
		return ""
	if "Transport Driver" in roles:
		driver = driver_portal.get_driver_for_user(user)
		if not driver:
			return "1=0"
		return f"(`tabTrip Expense`.driver = {frappe.db.escape(driver)})"
	return "1=0"


def get_trip_expense_has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	roles = frappe.get_roles(user)

	if any(role in roles for role in MANAGING_ROLES):
		return True
	if "Transport Driver" in roles:
		driver = driver_portal.get_driver_for_user(user)
		return bool(driver) and doc.driver == driver
	return False


def get_customer_request_permission_query_conditions(user=None):
	user = user or frappe.session.user
	roles = frappe.get_roles(user)

	if any(role in roles for role in MANAGING_ROLES):
		return ""
	if "Customer" in roles:
		return customer_portal.get_permission_query_conditions_for_customer_request(user)
	return "1=0"


def get_customer_request_has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	roles = frappe.get_roles(user)

	if any(role in roles for role in MANAGING_ROLES):
		return True
	if "Customer" in roles:
		return customer_portal.has_permission_for_customer_request(doc, user)
	return False
