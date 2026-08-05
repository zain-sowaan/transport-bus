# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Office-side (Transport In-Charge / Operations) trip actions, distinct from
driver_portal.py's driver-facing ones. Approving a Completed trip is what
'hits the timesheet' per the source workflow doc - only Approved trips are
eligible to be pulled onto a Transport Timesheet (see
transport_timesheet.populate_trips)."""

import frappe
from frappe import _
from frappe.utils import now_datetime

APPROVER_ROLES = ("Transport In-Charge", "Transport Operations", "System Manager")


@frappe.whitelist()
def approve_trip(trip_name):
	if not any(role in frappe.get_roles() for role in APPROVER_ROLES):
		frappe.throw(_("Not permitted to approve trips."), frappe.PermissionError)

	trip = frappe.get_doc("Trip", trip_name)
	if trip.status != "Completed":
		frappe.throw(_("Only Completed trips can be approved (current status: {0}).").format(trip.status))

	trip.db_set({"status": "Approved", "approved_by": frappe.session.user, "approved_on": now_datetime()})
	return "Approved"
