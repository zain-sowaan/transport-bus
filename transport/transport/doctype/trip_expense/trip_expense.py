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

ACCOUNTS_ROLES = ("Transport Accounts", "System Manager")


class TripExpense(Document):
	def validate(self):
		if self.amount and self.amount <= 0:
			frappe.throw(_("Amount must be greater than zero."))
		self.check_duplicate_receipt()

	def check_duplicate_receipt(self):
		"""Prevent same bill upload twice (doc section 12) - matched by the
		attached file's content hash, not filename/amount/date, so a
		re-uploaded copy of the same receipt is caught even if the driver
		changes the entered amount or date by mistake."""
		if not self.attachment:
			return

		content_hash = frappe.db.get_value(
			"File", {"file_url": self.attachment, "attached_to_doctype": "Trip Expense"}, "content_hash"
		)
		if not content_hash:
			return

		duplicate = frappe.db.sql(
			"""
			select te.name from `tabTrip Expense` te
			inner join `tabFile` f
				on f.file_url = te.attachment and f.attached_to_doctype = 'Trip Expense'
			where f.content_hash = %s and te.name != %s and te.status != 'Rejected'
			limit 1
			""",
			(content_hash, self.name or ""),
		)
		if duplicate:
			frappe.throw(_("This receipt appears to already be uploaded on Trip Expense {0}.").format(duplicate[0][0]))

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

	@frappe.whitelist()
	def book_to_expense_claim(self):
		if not any(role in frappe.get_roles() for role in ACCOUNTS_ROLES):
			frappe.throw(_("Not permitted to book Expense Claims."), frappe.PermissionError)
		if self.status != "Approved":
			frappe.throw(_("Only Approved expenses can be booked to an Expense Claim (current status: {0}).").format(self.status))
		if self.expense_claim:
			frappe.throw(_("Already booked to Expense Claim {0}.").format(self.expense_claim))

		employee = frappe.db.get_value("Driver", self.driver, "employee")
		if not employee:
			frappe.throw(_("Driver {0} has no linked Employee - cannot book an Expense Claim.").format(self.driver))

		company = frappe.db.get_value("Project", self.project, "company") or frappe.defaults.get_global_default("company")
		if not company:
			frappe.throw(_("No Company found for Project {0} - set one before booking.").format(self.project))

		claim = frappe.get_doc(
			{
				"doctype": "Expense Claim",
				"employee": employee,
				"company": company,
				"posting_date": self.expense_date,
				"project": self.project,
				"expenses": [
					{
						"expense_date": self.expense_date,
						"expense_type": self.expense_type,
						"amount": self.amount,
						"sanctioned_amount": self.amount,
						"project": self.project,
						"description": self.remarks or self.expense_type,
					}
				],
			}
		)
		claim.insert(ignore_permissions=True)

		if self.attachment:
			frappe.get_doc(
				{
					"doctype": "File",
					"file_url": self.attachment,
					"attached_to_doctype": "Expense Claim",
					"attached_to_name": claim.name,
					"is_private": 1,
				}
			).insert(ignore_permissions=True)

		self.db_set({"status": "Booked", "expense_claim": claim.name})
		return claim.name
