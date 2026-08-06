# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from transport.transport.utils import check_sales_order_exists, is_project_holiday

LMV_LABEL = "LMV (Light Motor Vehicle)"
HMV_LABEL = "HMV (Heavy Motor Vehicle)"


class Trip(Document):
	def validate(self):
		self.check_sales_order()
		self.set_route_endpoints()
		self.check_double_booking()
		self.check_blacklisted_driver()
		self.set_duty_type()
		self.set_assigned()

	def check_sales_order(self):
		"""Only on creation, or if the Project is being changed - so switching
		this control on doesn't retroactively block edits to Trips that already
		exist on a site being brought into compliance."""
		if self.is_new() or self.has_value_changed("project"):
			check_sales_order_exists(self.project)

	def check_blacklisted_driver(self):
		if self.driver and frappe.db.get_value("Driver", self.driver, "is_blacklisted"):
			frappe.throw(_("Driver {0} is blacklisted and cannot be assigned to a Trip.").format(self.driver))

	def set_assigned(self):
		"""Driver & Vehicle Assignment (doc section 8) is a distinct step after
		Trip Planning (section 7): a Trip starts Draft and only becomes
		Assigned once both are set, which also starts the driver-confirmation
		escalation clock."""
		if self.status == "Draft" and self.vehicle and self.driver:
			self.status = "Assigned"
			self.assigned_on = now_datetime()
			self.reminder_sent = 0

	def set_route_endpoints(self):
		"""Fill from_place/to_place from the Route when creating a Trip by hand
		with only a Route selected. Does NOT touch scheduled_time here - by the
		time validate() runs, an unset Time field has already been silently
		filled with the current wall-clock time by Document.insert(), so a
		`self.scheduled_time or ...` fallback can never fire. Route selection
		on the client (trip.js) fills the standard time instead, while it's
		still genuinely blank in the browser."""
		if not self.route or (self.from_place and self.to_place):
			return

		route = frappe.get_doc("Route", self.route)
		if self.direction == "Drop":
			self.from_place, self.to_place = route.to_place, route.from_place
		else:
			self.from_place, self.to_place = route.from_place, route.to_place

	def check_double_booking(self):
		if not (self.trip_date and self.scheduled_time):
			return

		for fieldname, label in (("driver", _("Driver")), ("vehicle", _("Vehicle"))):
			value = self.get(fieldname)
			if not value:
				continue

			clash = frappe.db.exists(
				"Trip",
				{
					fieldname: value,
					"trip_date": self.trip_date,
					"scheduled_time": self.scheduled_time,
					"status": ("not in", ["Rejected", "Cancelled"]),
					"name": ("!=", self.name or ""),
				},
			)
			if clash:
				frappe.throw(
					_("{0} {1} is already booked on another Trip ({2}) at {3} on {4}.").format(
						label, value, clash, self.scheduled_time, self.trip_date
					)
				)

	def set_duty_type(self):
		if self.timesheet:
			# Already pulled onto a Transport Timesheet - what got billed must
			# not silently drift from what the Timesheet/OT report show, even
			# if a later same-day Trip is added and this one gets resaved.
			return

		if not (self.vehicle and self.trip_date and self.vehicle_class):
			self.duty_type = "Duty"
			return

		if is_project_holiday(self.project, self.trip_date):
			# Weekend/PH duty is always OT, even a vehicle's first trip that day.
			self.duty_type = "OT"
			return

		daily_limit = self.get_daily_trip_limit()
		if not daily_limit:
			self.duty_type = "Duty"
			return

		trips_today = frappe.db.count(
			"Trip",
			{
				"vehicle": self.vehicle,
				"trip_date": self.trip_date,
				"status": ("not in", ["Rejected", "Cancelled"]),
				"name": ("!=", self.name or ""),
			},
		)
		self.duty_type = "OT" if trips_today >= daily_limit else "Duty"

	def get_daily_trip_limit(self):
		settings_field = "hmv_daily_trip_limit" if self.vehicle_class == HMV_LABEL else "lmv_daily_trip_limit"
		return frappe.db.get_single_value("Transport Settings", settings_field)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_vehicles(doctype, txt, searchfield, start, page_len, filters=None):
	filters = frappe._dict(filters or {})
	trip_date, scheduled_time = filters.get("trip_date"), filters.get("scheduled_time")

	if not (trip_date and scheduled_time):
		frappe.throw(_("Set Trip Date and Scheduled Time first."))

	booked = frappe.get_all(
		"Trip",
		filters={
			"trip_date": trip_date,
			"scheduled_time": scheduled_time,
			"status": ("not in", ["Rejected", "Cancelled"]),
			"vehicle": ("is", "set"),
		},
		pluck="vehicle",
	)

	vehicle_filters = {"status": "Available", searchfield: ("like", f"%{txt}%")}
	if booked:
		vehicle_filters["name"] = ("not in", booked)
	if filters.get("vehicle_category"):
		vehicle_filters["vehicle_type"] = filters["vehicle_category"]

	return frappe.get_list(
		"Rental Vehicle",
		filters=vehicle_filters,
		fields=["name", "vehicle_make", "vehicle_model", "vehicle_class"],
		as_list=True,
		limit_start=start,
		limit_page_length=page_len,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_drivers(doctype, txt, searchfield, start, page_len, filters=None):
	filters = frappe._dict(filters or {})
	trip_date, scheduled_time = filters.get("trip_date"), filters.get("scheduled_time")

	if not (trip_date and scheduled_time):
		frappe.throw(_("Set Trip Date and Scheduled Time first."))

	booked = frappe.get_all(
		"Trip",
		filters={
			"trip_date": trip_date,
			"scheduled_time": scheduled_time,
			"status": ("not in", ["Rejected", "Cancelled"]),
			"driver": ("is", "set"),
		},
		pluck="driver",
	)

	driver_filters = {"status": "Active", "is_blacklisted": 0, searchfield: ("like", f"%{txt}%")}
	if booked:
		driver_filters["name"] = ("not in", booked)

	return frappe.get_list(
		"Driver", filters=driver_filters, fields=["name", "full_name"],
		as_list=True, limit_start=start, limit_page_length=page_len,
	)
