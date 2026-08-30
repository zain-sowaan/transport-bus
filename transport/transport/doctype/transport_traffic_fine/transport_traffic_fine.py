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
		self.check_duplicate_ticket()

	def check_duplicate_ticket(self):
		"""A ticket number identifies a fine, but only within the authority that
		issued it - two emirates can legitimately issue the same number. The
		identity of a fine is therefore (source portal, ticket number).

		Enforced here rather than by a database unique index on purpose: Frappe
		stores an empty Data field as '' rather than NULL, so a unique index
		would treat every blank ticket number as a collision and block a second
		manually-entered fine that has not been given one yet.
		"""
		if not self.ticket_number:
			return

		duplicate = frappe.db.exists(
			"Transport Traffic Fine",
			{
				"ticket_number": self.ticket_number,
				"source_portal": self.source_portal or ("in", ("", None)),
				"name": ("!=", self.name or ""),
			},
		)
		if duplicate:
			frappe.throw(
				_("Ticket {0} is already recorded on {1}{2}.").format(
					self.ticket_number,
					duplicate,
					_(" for the same portal") if self.source_portal else "",
				),
				title=_("Duplicate Fine"),
			)

	def calculate_vat(self):
		self.vat_amount = flt(self.amount) * VAT_RATE if self.add_vat else 0
		self.total_cost = flt(self.amount) + flt(self.vat_amount)

	def on_update(self):
		self.apply_black_points()

	def apply_black_points(self):
		if self.black_points_on_hold:
			# Imported fines arrive here already marked Paid. Without this hold,
			# importing a driver's fine history would satisfy the condition
			# below on the very first save and blacklist them retroactively for
			# fines that were settled long ago. A human clears the hold once
			# they have confirmed the driver is genuinely responsible.
			return
		if self.black_points_applied or self.responsibility != "Driver" or self.status != "Paid":
			return
		if not self.driver:
			return

		# The authority's own count wins when we have it. The Transport Settings
		# figure is a flat default for hand-entered fines, and applying it to an
		# imported one would record a 12-point violation as a 2-point one -
		# understating exactly the fines that matter most for blacklisting.
		points = self.black_points or frappe.db.get_single_value(
			"Transport Settings", "black_points_per_fine"
		) or 0
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
