# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Sales Invoice is ERPNext's own doctype - we extend its schema via Custom
Field fixtures and its form via a Client Script fixture (never its own .js),
and put the actions here rather than a controller override. Mirrors the
source doc's Invoice & GRN Management state machine: GRN Received must come
before the invoice can be marked Submitted."""

import frappe
from frappe import _
from frappe.utils import today

ACCOUNTS_ROLES = ("Transport Accounts", "System Manager")


def _check_accounts_role():
	if not any(role in frappe.get_roles() for role in ACCOUNTS_ROLES):
		frappe.throw(_("Not permitted to manage Invoice/GRN status."), frappe.PermissionError)


@frappe.whitelist()
def mark_grn_received(sales_invoice):
	_check_accounts_role()

	certificate = frappe.db.get_value("Sales Invoice", sales_invoice, "certificate_of_completion")
	certificate_status = frappe.db.get_value("Certificate of Completion", certificate, "status") if certificate else None
	if certificate_status != "Signed Copy Received":
		frappe.throw(_("The Certificate of Completion's signed copy must be received before GRN can be marked Received."))

	frappe.db.set_value(
		"Sales Invoice", sales_invoice, {"grn_status": "Received", "grn_received_on": today()}
	)
	return "Received"


@frappe.whitelist()
def mark_invoice_submitted(sales_invoice):
	_check_accounts_role()
	grn_status = frappe.db.get_value("Sales Invoice", sales_invoice, "grn_status")
	if grn_status != "Received":
		frappe.throw(_("GRN must be Received before the invoice can be marked Submitted."))

	frappe.db.set_value(
		"Sales Invoice",
		sales_invoice,
		{"invoice_submission_status": "Submitted", "invoice_submitted_on": today()},
	)
	return "Submitted"
