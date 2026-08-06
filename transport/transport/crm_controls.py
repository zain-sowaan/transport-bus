# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Enforces controls the source document calls mandatory but native ERPNext
leaves optional: a Lost reason exists on both Opportunity and Quotation
already (`lost_reasons`, via `declare_enquiry_lost`), but nothing stops a
plain save with status=Lost and no reason recorded."""

import frappe
from frappe import _


def enforce_lost_reason(doc, method=None):
	if doc.status == "Lost" and not doc.get("lost_reasons"):
		frappe.throw(_("Select at least one Lost Reason before marking this {0} as Lost.").format(doc.doctype))


def sync_transport_project(doc, method=None):
	"""Quotation carries the project in its own `transport_project` custom
	field (Quotation has no native `project` field to map from), so ERPNext's
	`make_sales_order` mapper has nothing to copy into Sales Order.project.
	Without this, a Sales Order created through the app's own
	Rate Calculation -> Quotation -> Sales Order flow would leave the native
	field blank, and Transport Timesheet.set_sales_order() - which looks the
	Sales Order up *by* that native field - would never find it, failing at
	invoicing time with "No submitted Sales Order found"."""
	if not doc.get("project") and doc.get("transport_project"):
		doc.project = doc.transport_project


def notify_operations_on_sales_order(doc, method=None):
	"""Source doc, Sales Order section: "Notification to Operations along with
	the detailed budget of the project once the sales order is approved."
	Only fires for transport Sales Orders - this is ERPNext's shared doctype,
	and a non-transport order shouldn't page the transport Operations team."""
	if not doc.get("transport_project"):
		return

	from transport.transport.driver_portal import notify_roles

	message = _("Sales Order {0} for {1} has been confirmed.<br>Project: {2}<br>Order Value: {3}").format(
		doc.name,
		doc.customer,
		doc.get("project") or doc.transport_project,
		frappe.format_value(doc.grand_total, {"fieldtype": "Currency", "options": "currency"}),
	)

	notify_roles(
		["Transport Operations", "Transport In-Charge"],
		_("Sales Order confirmed - plan trips"),
		message,
		reference_doctype="Sales Order",
		reference_name=doc.name,
	)
