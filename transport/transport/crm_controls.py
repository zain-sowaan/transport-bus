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


# Word's bullet glyphs and the dashes people type instead: pick-up points are
# pasted straight out of the customer's table, and the marker is not data.
BULLET_CHARS = "•●▪‣⁃·-*–— \t"

TBA = "TBA"
# What customers actually write when the time is not settled yet.
TBA_SYNONYMS = {"tba", "tbc", "tbd", "tba.", "to be advised", "to be confirmed", "n/a", "-", "--"}


def normalize_time_text(value):
	"""Tidy a time written by a customer, without ever discarding it.

	Returns a canonical 24-hour "HH:MM" when the text parses as a time, the
	literal "TBA" when it is blank or one of the ways people say "not yet",
	and otherwise exactly what was typed. That last case is the important
	one: enquiries carry things like "before 07:30" or "after prayers", and
	rewriting those into something tidier would change the customer's terms.
	"""
	text = (value or "").strip()
	if not text or text.lower() in TBA_SYNONYMS:
		return TBA

	try:
		parsed = frappe.utils.get_time(text)
	except Exception:
		return text

	return parsed.strftime("%H:%M") if parsed else text


def normalize_enquiry_schedule(doc, method=None):
	"""Tidy and check the requested shuttle schedule on an Opportunity.

	Deliberately light: an enquiry records what the customer asked for, not
	what we have agreed to, so locations stay free text and an unsettled
	schedule saves happily. The single thing actually enforced is that a batch
	does not name the same day twice, which is a transcription error rather
	than a schedule anyone meant.
	"""
	rows = doc.get("transport_enquiry_schedule")
	if not rows:
		return

	seen = {}
	for row in rows:
		row.pickup_time = normalize_time_text(row.pickup_time)
		row.dropoff_time = normalize_time_text(row.dropoff_time)

		if row.pickup_points:
			row.pickup_points = "\n".join(
				line
				for line in (
					raw.strip().lstrip(BULLET_CHARS).strip() for raw in row.pickup_points.splitlines()
				)
				if line
			)

		if not row.day:
			continue

		key = ((row.batch or "").strip().lower(), row.day)
		if key in seen:
			frappe.throw(
				_("Rows {0} and {1} both set {2} for batch {3}. Each day may appear once per batch.").format(
					seen[key], row.idx, row.day, row.batch or _("(no batch)")
				),
				title=_("Duplicate Day"),
			)
		seen[key] = row.idx


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
