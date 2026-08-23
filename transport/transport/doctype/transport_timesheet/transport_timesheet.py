# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""The billing-ready record behind the client-facing monthly timesheet
(the Transport Monthly Timesheet report is the printable view of the same
data). Only Approved trips (see transport.transport.operations.approve_trip)
can be pulled in, and a trip pulled onto one Timesheet is locked
(Trip.timesheet) so it can never be double-billed onto another.

Customer approval here is modelled as recording a signed copy that came back
by email, not a live no-login web link - the source doc's "send via link"
line is a real Customer Portal (trip tracking, invoice download, raise
requests), which is explicitly a later, separate deliverable, not this
Timesheet's job. Don't build a public approval link here without revisiting
that split."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, today

from transport.transport.utils import PUBLIC_HOLIDAY, WEEKEND, get_project_holiday_kind

OPERATIONS_ROLES = ("Transport Operations", "System Manager")
ACCOUNTS_ROLES = ("Transport Accounts", "System Manager")


class TransportTimesheet(Document):
	def validate(self):
		if self.period_to and getdate(self.period_to) < getdate(self.period_from):
			frappe.throw(_("Period To cannot be before Period From."))
		self.calculate_totals()
		self.push_manual_edits_to_trips()

	def push_manual_edits_to_trips(self):
		"""Source doc, Timesheet section: "Manual edits only for driver/vehicle
		changes." Those two columns are the only editable ones on the child
		table, but editing them there would otherwise be cosmetic - the real
		Trip record would keep the old driver/vehicle, so the Trip Sheet, OT
		report and P&L would all still show what was originally assigned.
		Only while Draft: once the timesheet is approved or invoiced, its lines
		are the billing record and must not be rewritten."""
		if self.status != "Draft" or self.is_new():
			return

		for row in self.trips:
			if not row.trip:
				continue

			current = frappe.db.get_value("Trip", row.trip, ["driver", "vehicle"], as_dict=True)
			if not current:
				continue

			changes = {}
			if row.driver and row.driver != current.driver:
				changes["driver"] = row.driver
			if row.vehicle and row.vehicle != current.vehicle:
				changes["vehicle"] = row.vehicle

			if changes:
				frappe.db.set_value("Trip", row.trip, changes)

	def calculate_totals(self):
		self.total_trips = len(self.trips)
		self.total_duty_trips = sum(1 for t in self.trips if t.duty_type == "Duty")
		self.total_ot_trips = sum(1 for t in self.trips if t.duty_type == "OT")
		self.total_additional_trips = sum(1 for t in self.trips if t.is_additional)

		# Both counts are kept because the charge basis is configurable: a rate
		# quoted against a shift reads as per-day, but the client may confirm
		# per-trip. Accounts can also see both before approving the invoice.
		weekend_rows = [t for t in self.trips if t.holiday_type == WEEKEND]
		holiday_rows = [t for t in self.trips if t.holiday_type == PUBLIC_HOLIDAY]

		self.total_weekend_trips = len(weekend_rows)
		self.total_public_holiday_trips = len(holiday_rows)
		self.total_weekend_days = len({t.trip_date for t in weekend_rows})
		self.total_public_holiday_days = len({t.trip_date for t in holiday_rows})

	def on_trash(self):
		if self.status != "Draft":
			frappe.throw(_("Only a Draft Timesheet can be deleted."))
		self.release_trips()

	def release_trips(self):
		for row in self.trips:
			frappe.db.set_value("Trip", row.trip, "timesheet", None)

	@frappe.whitelist()
	def populate_trips(self):
		self.check_permission("write")
		if self.status != "Draft":
			frappe.throw(_("Trips can only be (re)generated while the Timesheet is Draft."))
		if not (self.project and self.period_from and self.period_to):
			frappe.throw(_("Set Project, Period From and Period To first."))

		self.set_sales_order()

		# Release any trips already locked to THIS timesheet before requerying,
		# so calling populate_trips() again (e.g. after a trip's project data
		# changed) is idempotent instead of finding zero unlocked trips left -
		# without this a second click would silently wipe the trips already
		# pulled, since they'd no longer look "unlocked" to the query below.
		self.release_trips()

		trips = frappe.get_all(
			"Trip",
			filters={
				"project": self.project,
				"trip_date": ("between", [self.period_from, self.period_to]),
				"status": "Approved",
				"timesheet": ("in", ("", None)),
			},
			fields=[
				"name", "trip_date", "route", "direction", "driver", "vehicle",
				"scheduled_time", "actual_start_time", "actual_end_time", "duty_type", "is_additional",
			],
			order_by="trip_date, scheduled_time",
		)

		self.set("trips", [])
		for trip in trips:
			holiday_kind = get_project_holiday_kind(self.project, trip.trip_date)
			self.append("trips", {
				"trip": trip.name,
				"trip_date": trip.trip_date,
				"route": trip.route,
				"direction": trip.direction,
				"driver": trip.driver,
				"vehicle": trip.vehicle,
				"scheduled_time": trip.scheduled_time,
				"actual_start_time": trip.actual_start_time,
				"actual_end_time": trip.actual_end_time,
				"duty_type": trip.duty_type,
				"is_additional": trip.is_additional,
				"is_holiday": bool(holiday_kind),
				"holiday_type": holiday_kind,
			})

		self.save()
		for row in self.trips:
			frappe.db.set_value("Trip", row.trip, "timesheet", self.name)

		return {"pulled": len(trips)}

	def set_sales_order(self):
		if self.sales_order:
			return
		# A Project can have more than one submitted Sales Order over its life
		# (renewal/amendment) - take the most recent one rather than whichever
		# row MySQL happens to return first.
		self.sales_order = frappe.db.get_value(
			"Sales Order", {"project": self.project, "docstatus": 1}, "name", order_by="creation desc"
		)

	@frappe.whitelist()
	def approve_by_operations(self):
		if not any(role in frappe.get_roles() for role in OPERATIONS_ROLES):
			frappe.throw(_("Not permitted to approve Timesheets."), frappe.PermissionError)
		if self.status != "Draft":
			frappe.throw(_("Only a Draft Timesheet can be sent for approval."))
		if not self.trips:
			frappe.throw(_("Add at least one trip before approving."))

		self.db_set({
			"status": "Pending Customer Approval",
			"operations_approved_by": frappe.session.user,
			"operations_approved_on": frappe.utils.now_datetime(),
		})
		return "Pending Customer Approval"

	@frappe.whitelist()
	def record_customer_approval(self, approved_by, attachment=None):
		if not any(role in frappe.get_roles() for role in OPERATIONS_ROLES):
			frappe.throw(_("Not permitted to record customer approval."), frappe.PermissionError)
		if self.status != "Pending Customer Approval":
			frappe.throw(_("Timesheet must be Pending Customer Approval first."))
		if not approved_by:
			frappe.throw(_("Enter the customer-side signatory's name."))

		self.db_set({
			"status": "Customer Approved",
			"customer_approved_by": approved_by,
			"customer_approved_on": today(),
			"customer_approval_attachment": attachment,
		})
		return "Customer Approved"

	def add_weekend_holiday_charges(self, invoice):
		"""The client's project contract sheet prices weekend and public-holiday
		duty separately from the monthly rate (e.g. AED 310 weekend / AED 400
		public holiday), so those days are billed on top of the Sales Order
		line rather than being absorbed into it.

		Rates live on the Project because they are per-contract. Nothing is
		added when a rate is zero/unset, when the toggle is off, or when the
		charge item is not configured - a missing rate must never silently
		invoice as 0, and a missing item would otherwise throw mid-invoice."""
		settings = frappe.get_single("Transport Settings")
		if not settings.auto_bill_weekend_holiday:
			return

		per_day = (settings.weekend_holiday_charge_basis or "Per Day") == "Per Day"
		project = frappe.db.get_value(
			"Project",
			self.project,
			["transport_weekend_charge", "transport_public_holiday_charge"],
			as_dict=True,
		) or frappe._dict()

		charges = (
			(
				_("Weekend Duty"),
				settings.weekend_charge_item,
				flt(project.transport_weekend_charge),
				self.total_weekend_days if per_day else self.total_weekend_trips,
			),
			(
				_("Public Holiday Duty"),
				settings.public_holiday_charge_item,
				flt(project.transport_public_holiday_charge),
				self.total_public_holiday_days if per_day else self.total_public_holiday_trips,
			),
		)

		for label, item_code, rate, qty in charges:
			if not (item_code and rate and qty):
				continue

			invoice.append(
				"items",
				{
					"item_code": item_code,
					"qty": qty,
					"rate": rate,
					"description": _("{0} - {1} {2} ({3} to {4})").format(
						label, qty, _("days") if per_day else _("trips"), self.period_from, self.period_to
					),
					"project": self.project,
				},
			)

	@frappe.whitelist()
	def create_sales_invoice(self):
		if not any(role in frappe.get_roles() for role in ACCOUNTS_ROLES):
			frappe.throw(_("Not permitted to create the Invoice."), frappe.PermissionError)
		if self.status != "Customer Approved":
			frappe.throw(_("Timesheet must be Customer Approved before invoicing."))
		if self.sales_invoice:
			frappe.throw(_("Sales Invoice {0} has already been created from this Timesheet.").format(self.sales_invoice))
		if not self.sales_order:
			frappe.throw(_("No submitted Sales Order found for Project {0}.").format(self.project))

		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		invoice = make_sales_invoice(self.sales_order)
		invoice.transport_timesheet = self.name
		invoice.grn_status = "Pending"
		invoice.invoice_submission_status = "Not Submitted"

		# A transport contract is one long-lived Sales Order invoiced every
		# month, and make_sales_invoice() copies the order's payment schedule
		# as-is. That schedule's due date is fixed at the order's own date, so
		# from the second month onward ERPNext rejects the invoice with "Due
		# Date cannot be before Posting Date" - the contract would invoice once
		# and then break. Clearing both lets accounts_controller rebuild the
		# schedule from this invoice's posting date and the customer's payment
		# terms, which is what a monthly billing run actually needs.
		invoice.payment_schedule = []
		invoice.due_date = None

		self.add_weekend_holiday_charges(invoice)
		invoice.insert()

		self.db_set({"sales_invoice": invoice.name, "status": "Invoiced"})

		certificate = frappe.get_doc({
			"doctype": "Certificate of Completion",
			"transport_timesheet": self.name,
		}).insert()
		self.db_set("certificate_of_completion", certificate.name)
		frappe.db.set_value("Sales Invoice", invoice.name, "certificate_of_completion", certificate.name)

		return invoice.name
