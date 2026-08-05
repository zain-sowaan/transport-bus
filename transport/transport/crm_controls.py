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
