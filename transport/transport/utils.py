# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate


def get_project_sales_order(project):
	"""The submitted Sales Order backing a Project, most recent first - a
	Project can have more than one over its life (renewal/amendment)."""
	if not project:
		return None

	return frappe.db.get_value(
		"Sales Order", {"project": project, "docstatus": 1}, "name", order_by="creation desc"
	)


def check_sales_order_exists(project):
	"""The source doc lists "Trip cannot be created without Sales Order" under
	Trip Planning's Mandatory Conditions and repeats it under Mandatory System
	Controls. Gated by a Transport Settings toggle so an existing site can be
	brought into compliance rather than having every in-flight Trip blocked
	the moment this ships."""
	if not frappe.db.get_single_value("Transport Settings", "enforce_sales_order_for_trip"):
		return

	if not project:
		frappe.throw(_("A Trip must belong to a Project that has a submitted Sales Order."))

	if not get_project_sales_order(project):
		frappe.throw(
			_("Project {0} has no submitted Sales Order. A Trip cannot be created without one.").format(
				project
			)
		)


WEEKEND = "Weekend"
PUBLIC_HOLIDAY = "Public Holiday"


def get_project_holiday_kind(project, date):
	"""Returns None, "Weekend" or "Public Holiday" for a date on the Project's
	holiday list. ERPNext's Holiday rows carry a `weekly_off` flag - that is
	what separates a recurring weekend from a one-off public holiday, and the
	source doc prices the two differently (separate Weekend Charges and Public
	Holiday Charges columns on the project contract sheet), so callers that
	bill need the distinction, not just a boolean."""
	if not (project and date):
		return None

	holiday_list = frappe.db.get_value("Project", project, "holiday_list")
	if not holiday_list:
		return None

	weekly_off = frappe.db.get_value(
		"Holiday", {"parent": holiday_list, "holiday_date": getdate(date)}, "weekly_off"
	)
	if weekly_off is None:
		return None

	return WEEKEND if weekly_off else PUBLIC_HOLIDAY


def is_project_holiday(project, date):
	"""Shared by Trip.set_duty_type (weekend/PH auto-OT) and Transport
	Timesheet.populate_trips (PH flag on the billing line) so both read the
	same Project holiday_list the same way."""
	return get_project_holiday_kind(project, date) is not None
