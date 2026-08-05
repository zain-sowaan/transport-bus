# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate

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
		self.calculate_cost()
		self.calculate_pricing()
		self.enforce_minimum_margin()

	def calculate_cost(self):
		if self.with_fuel != "With Fuel":
			self.fuel_cost = 0
		if self.with_driver != "With Driver":
			self.driver_salary_cost = 0
			self.room_rent_cost = 0

		self.total_estimated_cost = sum(flt(self.get(f)) for f in COST_COMPONENTS)
		self.cost_per_trip = (
			self.total_estimated_cost / self.estimated_trips_per_month
			if self.estimated_trips_per_month
			else 0
		)

	def calculate_pricing(self):
		if not self.minimum_margin_percent:
			self.minimum_margin_percent = (
				frappe.db.get_single_value("Transport Settings", "default_minimum_margin_percent") or 0
			)

		self.minimum_price = self.total_estimated_cost * (1 + flt(self.minimum_margin_percent) / 100)
		self.final_price = flt(self.proposed_price) * (1 - flt(self.discount_percent) / 100)
		self.requires_discount_approval = 1 if flt(self.discount_percent) > 0 else 0

	def enforce_minimum_margin(self):
		if not frappe.db.get_single_value("Transport Settings", "enforce_minimum_margin"):
			return

		if self.total_estimated_cost and self.final_price < self.minimum_price:
			frappe.throw(
				_(
					"Final Price ({0}) is below the minimum price ({1}) required to cover cost plus the "
					"{2}% minimum margin. Lower the discount or raise the proposed price."
				).format(
					frappe.format_value(self.final_price, {"fieldtype": "Currency"}),
					frappe.format_value(self.minimum_price, {"fieldtype": "Currency"}),
					self.minimum_margin_percent,
				)
			)

	@frappe.whitelist()
	def create_quotation(self):
		if self.quotation:
			frappe.throw(_("Quotation {0} has already been created from this calculation.").format(self.quotation))

		service_item = frappe.db.get_single_value("Transport Settings", "transport_service_item")
		if not service_item:
			frappe.throw(_("Set a Transport Service Item in Transport Settings before creating a Quotation."))

		quotation = frappe.new_doc("Quotation")
		quotation.update(
			{
				"quotation_to": "Customer",
				"party_name": self.customer,
				"transaction_date": nowdate(),
				"items": [{"item_code": service_item, "qty": 1, "rate": self.final_price}],
				"transport_rate_calculation": self.name,
				"transport_project": self.project,
				"transport_vehicle_category": self.vehicle_category,
				"transport_seating_capacity": self.seating_capacity,
				"transport_with_driver": self.with_driver,
				"transport_with_fuel": self.with_fuel,
				"transport_trip_type": self.trip_type,
				"transport_start_date": self.start_date,
				"transport_end_date": self.end_date,
				"transport_locations": self.locations,
				"transport_estimated_km": self.estimated_km,
				"transport_estimated_trips_per_month": self.estimated_trips_per_month,
			}
		)
		quotation.insert()

		self.db_set("quotation", quotation.name)
		return quotation.name
