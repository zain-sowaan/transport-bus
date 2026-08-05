# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""A driver's lightweight expense capture against a Trip - receipt photo +
amount + type, pending approval. This intentionally does NOT post to
accounts: Phase 4 (Cost Booking & Allocation) converts approved rows into
proper ERPNext Expense Claims with cost-center/accounting allocation and
duplicate-bill detection. Phase 3's job is just capturing it in the field."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class TripExpense(Document):
	def validate(self):
		if self.amount and self.amount <= 0:
			frappe.throw(_("Amount must be greater than zero."))

	@frappe.whitelist()
	def approve(self):
		self.check_permission("write")
		self.db_set(
			{"status": "Approved", "approved_by": frappe.session.user, "approved_on": now_datetime()}
		)

	@frappe.whitelist()
	def reject(self):
		self.check_permission("write")
		self.db_set(
			{"status": "Rejected", "approved_by": frappe.session.user, "approved_on": now_datetime()}
		)
