# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""The client-signed proof-of-service doc from the source workflow's
Certificate of Completion template (citing PO no. + Invoice no.) - it's also
what the doc's "GRN Management" section is tracking receipt of. Created
after a Transport Timesheet is Invoiced; "Signed Copy Received" here is what
unblocks Sales Invoice.grn_status (see transport/transport/billing.py)."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

ACCOUNTS_ROLES = ("Transport Accounts", "System Manager")


class CertificateofCompletion(Document):
	def validate(self):
		if not any(role in frappe.get_roles() for role in ACCOUNTS_ROLES):
			frappe.throw(_("Not permitted to manage Certificates of Completion."), frappe.PermissionError)

	@frappe.whitelist()
	def mark_issued(self):
		if self.status != "Draft":
			frappe.throw(_("Already issued."))
		self.db_set({"status": "Issued", "issued_on": today()})
		return "Issued"

	@frappe.whitelist()
	def record_signed_copy(self, signed_copy=None):
		if self.status != "Issued":
			frappe.throw(_("Must be Issued before a signed copy can be recorded."))
		self.db_set({"status": "Signed Copy Received", "received_on": today(), "signed_copy": signed_copy})
		return "Signed Copy Received"
