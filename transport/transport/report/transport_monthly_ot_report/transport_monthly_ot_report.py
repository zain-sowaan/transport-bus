# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Doc section 'Weekend & Overtime': "Monthly OT report for payroll", "OT
must be approved by operations before payroll". Driver-centric (a driver can
run trips across multiple projects in the same month - Timesheet's
multi-project support), and scoped to Approved trips only, so an
unapproved/still-in-dispute trip never leaks into a payroll number. Does NOT
compute a payroll amount - driver salary basis (trip-count vs hourly, salary
slip integration) is still blocked on the client's HR policy, forwarded to
the consultant."""

import frappe
from frappe import _
from frappe.utils import get_first_day, get_last_day, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("month"):
		frappe.throw(_("Select a Month."))

	month_date = getdate(filters.month)
	from_date, to_date = get_first_day(month_date), get_last_day(month_date)

	conditions = {"trip_date": ("between", [from_date, to_date]), "status": "Approved"}
	if filters.get("project"):
		conditions["project"] = filters.project
	if filters.get("driver"):
		conditions["driver"] = filters.driver

	trips = frappe.get_all(
		"Trip",
		filters=conditions,
		fields=["driver", "duty_type", "is_additional"],
	)

	by_driver = {}
	for trip in trips:
		if not trip.driver:
			continue
		row = by_driver.setdefault(
			trip.driver, {"total": 0, "duty": 0, "ot": 0, "additional": 0}
		)
		row["total"] += 1
		row["ot" if trip.duty_type == "OT" else "duty"] += 1
		if trip.is_additional:
			row["additional"] += 1

	driver_names = frappe.get_all(
		"Driver", filters={"name": ("in", list(by_driver))}, fields=["name", "full_name"]
	)
	names = {d.name: d.full_name for d in driver_names}

	data = [
		{
			"driver": driver,
			"driver_name": names.get(driver),
			"total_trips": row["total"],
			"duty_trips": row["duty"],
			"ot_trips": row["ot"],
			"additional_trips": row["additional"],
		}
		for driver, row in sorted(by_driver.items(), key=lambda kv: -kv[1]["ot"])
	]

	return get_columns(), data


def get_columns():
	return [
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver", "width": 120},
		{"label": _("Driver Name"), "fieldname": "driver_name", "fieldtype": "Data", "width": 160},
		{"label": _("Total Trips"), "fieldname": "total_trips", "fieldtype": "Int", "width": 100},
		{"label": _("Duty Trips"), "fieldname": "duty_trips", "fieldtype": "Int", "width": 100},
		{"label": _("OT Trips"), "fieldname": "ot_trips", "fieldtype": "Int", "width": 100},
		{"label": _("Additional Trips"), "fieldname": "additional_trips", "fieldtype": "Int", "width": 120},
	]
