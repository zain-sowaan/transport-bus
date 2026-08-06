# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Customer Portal (Phase 6): "Raise requests" from the source doc's
Customer Portal section. Customers create these through
customer_portal.create_request() (ignore_permissions, after verifying
identity) rather than a raw form save - the Customer role only gets
read here, matching the read-only/whitelisted-method pattern used for
Driver-facing Trip throughout this app."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

MANAGE_ROLES = ("Transport Operations", "Transport In-Charge", "System Manager")


class CustomerRequest(Document):
	@frappe.whitelist()
	def respond(self, response, status="In Progress"):
		if not any(role in frappe.get_roles() for role in MANAGE_ROLES):
			frappe.throw(_("Not permitted to respond to Customer Requests."), frappe.PermissionError)

		self.db_set(
			{
				"response": response,
				"status": status,
				"responded_by": frappe.session.user,
				"responded_on": now_datetime(),
			}
		)
