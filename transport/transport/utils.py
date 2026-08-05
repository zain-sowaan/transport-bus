# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate


def is_project_holiday(project, date):
	"""Shared by Trip.set_duty_type (weekend/PH auto-OT) and Transport
	Timesheet.populate_trips (PH flag on the billing line) so both read the
	same Project holiday_list the same way."""
	if not (project and date):
		return False

	holiday_list = frappe.db.get_value("Project", project, "holiday_list")
	if not holiday_list:
		return False

	return bool(frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": getdate(date)}))
