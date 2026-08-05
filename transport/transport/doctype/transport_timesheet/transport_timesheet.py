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
from frappe.utils import getdate, today

from transport.transport.utils import is_project_holiday

OPERATIONS_ROLES = ("Transport Operations", "System Manager")
ACCOUNTS_ROLES = ("Transport Accounts", "System Manager")


class TransportTimesheet(Document):
	def validate(self):
		if self.period_to and getdate(self.period_to) < getdate(self.period_from):
			frappe.throw(_("Period To cannot be before Period From."))
		self.calculate_totals()

	def calculate_totals(self):
		self.total_trips = len(self.trips)
		self.total_duty_trips = sum(1 for t in self.trips if t.duty_type == "Duty")
		self.total_ot_trips = sum(1 for t in self.trips if t.duty_type == "OT")
		self.total_additional_trips = sum(1 for t in self.trips if t.is_additional)

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
				"is_holiday": is_project_holiday(self.project, trip.trip_date),
			})

		self.save()
		for row in self.trips:
			frappe.db.set_value("Trip", row.trip, "timesheet", self.name)

		return {"pulled": len(trips)}

	def set_sales_order(self):
		if self.sales_order:
			return
		self.sales_order = frappe.db.get_value(
			"Sales Order", {"project": self.project, "docstatus": 1}, "name"
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
		invoice.insert()

		self.db_set({"sales_invoice": invoice.name, "status": "Invoiced"})

		certificate = frappe.get_doc({
			"doctype": "Certificate of Completion",
			"transport_timesheet": self.name,
		}).insert()
		self.db_set("certificate_of_completion", certificate.name)
		frappe.db.set_value("Sales Invoice", invoice.name, "certificate_of_completion", certificate.name)

		return invoice.name
