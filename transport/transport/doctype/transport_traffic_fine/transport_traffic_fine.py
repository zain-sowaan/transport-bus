# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Deliberately separate from fleetify's Traffic Fine, which is hard-wired
to a Rental Agreement (self-drive rental) - corporate trips have no
agreement at all, and need Driver/Trip/Project instead, plus a three-way
Responsibility Assignment (doc section "Fine Responsibility Assignment")
fleetify's boolean billed_to_customer can't express.

Paying a Driver-responsibility fine auto-creates a Driver Incident, closing
the doc's "Fine and black points history (auto updated from deductions
list)" loop concretely instead of leaving it as a manual step."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

VAT_RATE = 0.05


class TransportTrafficFine(Document):
	def validate(self):
		self.calculate_vat()

	def calculate_vat(self):
		self.vat_amount = flt(self.amount) * VAT_RATE if self.add_vat else 0
		self.total_cost = flt(self.amount) + flt(self.vat_amount)

	def on_update(self):
		self.apply_black_points()

	def apply_black_points(self):
		if self.black_points_applied or self.responsibility != "Driver" or self.status != "Paid":
			return
		if not self.driver:
			return

		points = frappe.db.get_single_value("Transport Settings", "black_points_per_fine") or 0
		incident = frappe.get_doc(
			{
				"doctype": "Driver Incident",
				"driver": self.driver,
				"trip": self.trip,
				"incident_type": "Traffic Fine",
				"description": _("Traffic Fine {0} ({1}) - {2}").format(
					self.ticket_number or self.name, self.fine_type or "", self.name
				),
				"black_points": points,
				"source_traffic_fine": self.name,
			}
		)
		incident.insert(ignore_permissions=True)

		self.db_set({"black_points_applied": 1, "driver_incident": incident.name})
