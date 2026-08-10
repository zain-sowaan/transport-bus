# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Cost build-up and pricing behind a transport quotation.

Originally one vehicle per document. The client's price calculation sheet is
a row per vehicle type - fuel, salary, vehicle rent, room rent and maintenance
costed separately for a car, a 7-seater, a 13-seater and so on, each with its
own approved price - so the build-up moved into a child table and this
document became the contract that carries them.

Two consequences of that shape are deliberate:

* **The minimum margin is enforced on each vehicle, not on the total.** A
  blended total would let a vehicle quoted below cost hide inside a healthy
  overall margin, which is precisely the mistake the control exists to catch.
* **The Quotation gets one line per vehicle**, so the customer sees what they
  are paying for each vehicle type rather than a single opaque figure.

`total_estimated_cost` stays on the parent because the Project P&L report
reads it directly; it now means the total across all vehicles.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

QUOTATION_ROLES = ("Transport Operations", "System Manager")

COST_COMPONENTS = (
	"vehicle_rental_cost",
	"fuel_cost",
	"salik_toll_parking_cost",
	"driver_salary_cost",
	"room_rent_cost",
	"maintenance_cost",
)


class TransportRateCalculation(Document):
	def validate(self):
		self.calculate()
		self.enforce_minimum_margin()

	def calculate(self):
		"""Cost and price every vehicle, then roll the totals up.

		Runs on the parent rather than in the child controller because Frappe
		does not call a child doctype's validate() during the parent's save.
		"""
		default_margin = frappe.db.get_single_value(
			"Transport Settings", "default_minimum_margin_percent"
		)

		for row in self.vehicles:
			# A vehicle supplied without fuel or without a driver cannot carry
			# those costs, whatever was typed before the option was changed.
			if row.with_fuel != "With Fuel":
				row.fuel_cost = 0
			if row.with_driver != "With Driver":
				row.driver_salary_cost = 0
				row.room_rent_cost = 0

			row.total_estimated_cost = sum(flt(row.get(f)) for f in COST_COMPONENTS)
			row.cost_per_trip = (
				row.total_estimated_cost / row.estimated_trips_per_month
				if row.estimated_trips_per_month
				else 0
			)

			if not row.minimum_margin_percent:
				row.minimum_margin_percent = default_margin or 0

			row.minimum_price = row.total_estimated_cost * (1 + flt(row.minimum_margin_percent) / 100)
			row.final_price = flt(row.proposed_price) * (1 - flt(row.discount_percent) / 100)

		self.total_estimated_cost = sum(flt(r.total_estimated_cost) for r in self.vehicles)
		self.total_minimum_price = sum(flt(r.minimum_price) for r in self.vehicles)
		self.total_proposed_price = sum(flt(r.proposed_price) for r in self.vehicles)
		self.total_final_price = sum(flt(r.final_price) for r in self.vehicles)
		self.total_margin = self.total_final_price - self.total_estimated_cost
		self.total_margin_percent = (
			self.total_margin / self.total_estimated_cost * 100 if self.total_estimated_cost else 0
		)
		self.requires_discount_approval = 1 if any(flt(r.discount_percent) > 0 for r in self.vehicles) else 0

	def enforce_minimum_margin(self):
		if not frappe.db.get_single_value("Transport Settings", "enforce_minimum_margin"):
			return

		for row in self.vehicles:
			if row.total_estimated_cost and row.final_price < row.minimum_price:
				frappe.throw(
					_(
						"Row {0} ({1}): Final Price ({2}) is below the minimum price ({3}) required to "
						"cover cost plus the {4}% minimum margin. Lower the discount or raise the "
						"proposed price."
					).format(
						row.idx,
						self.describe_vehicle(row),
						frappe.format_value(row.final_price, {"fieldtype": "Currency"}),
						frappe.format_value(row.minimum_price, {"fieldtype": "Currency"}),
						row.minimum_margin_percent,
					),
					title=_("Below Minimum Margin"),
				)

	@staticmethod
	def describe_vehicle(row):
		"""A label the customer would recognise on a quotation line."""
		parts = [row.vehicle_category]
		if row.seating_capacity:
			parts.append(_("{0} Seater").format(row.seating_capacity))
		if row.with_driver:
			parts.append(row.with_driver)
		if row.with_fuel:
			parts.append(row.with_fuel)
		return " - ".join(p for p in parts if p)

	@frappe.whitelist()
	def create_quotation(self):
		if not any(role in frappe.get_roles() for role in QUOTATION_ROLES):
			frappe.throw(_("Not permitted to create a Quotation."), frappe.PermissionError)
		if self.quotation:
			frappe.throw(_("Quotation {0} has already been created from this calculation.").format(self.quotation))
		if not self.vehicles:
			frappe.throw(_("Add at least one vehicle before creating a Quotation."))

		service_item = frappe.db.get_single_value("Transport Settings", "transport_service_item")
		if not service_item:
			frappe.throw(_("Set a Transport Service Item in Transport Settings before creating a Quotation."))

		quotation = frappe.new_doc("Quotation")
		quotation.update(
			{
				"quotation_to": "Customer",
				"party_name": self.customer,
				"transaction_date": nowdate(),
				"items": [
					{
						"item_code": service_item,
						"description": self.describe_vehicle(row),
						"qty": 1,
						"rate": row.final_price,
					}
					for row in self.vehicles
				],
				"transport_rate_calculation": self.name,
				"transport_project": self.project,
				"transport_trip_type": self.trip_type,
				"transport_start_date": self.start_date,
				"transport_end_date": self.end_date,
				"transport_locations": self.locations,
			}
		)

		# The vehicle-specific fields on Quotation describe a single vehicle and
		# cannot honestly describe several. Fill them only when there is exactly
		# one, rather than quietly reporting the first row as if it were the
		# whole quotation.
		if len(self.vehicles) == 1:
			row = self.vehicles[0]
			quotation.update(
				{
					"transport_vehicle_category": row.vehicle_category,
					"transport_seating_capacity": row.seating_capacity,
					"transport_with_driver": row.with_driver,
					"transport_with_fuel": row.with_fuel,
					"transport_estimated_km": row.estimated_km,
					"transport_estimated_trips_per_month": row.estimated_trips_per_month,
				}
			)

		quotation.insert()

		self.db_set("quotation", quotation.name)
		return quotation.name
