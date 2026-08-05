# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "trip_date", "fieldtype": "Date", "width": 100},
		{"label": _("Client"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
		{"label": _("From"), "fieldname": "from_place", "fieldtype": "Link", "options": "Place", "width": 150},
		{"label": _("To"), "fieldname": "to_place", "fieldtype": "Link", "options": "Place", "width": 150},
		{"label": _("Pickup/Drop"), "fieldname": "direction", "fieldtype": "Data", "width": 90},
		{"label": _("Time"), "fieldname": "scheduled_time", "fieldtype": "Time", "width": 90},
		{"label": _("Driver"), "fieldname": "driver_name", "fieldtype": "Data", "width": 120},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Rental Vehicle", "width": 110},
		{"label": _("Type"), "fieldname": "vehicle_class", "fieldtype": "Data", "width": 100},
		{"label": _("Duty"), "fieldname": "duty_mark", "fieldtype": "Data", "width": 60},
		{"label": _("OT"), "fieldname": "ot_mark", "fieldtype": "Data", "width": 60},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 200},
	]


def get_data(filters):
	conditions, values = ["1=1"], {}

	if filters.get("project"):
		conditions.append("t.project = %(project)s")
		values["project"] = filters.project
	if filters.get("trip_date"):
		conditions.append("t.trip_date = %(trip_date)s")
		values["trip_date"] = filters.trip_date
	elif filters.get("from_date") and filters.get("to_date"):
		conditions.append("t.trip_date between %(from_date)s and %(to_date)s")
		values["from_date"] = filters.from_date
		values["to_date"] = filters.to_date
	if filters.get("vehicle"):
		conditions.append("t.vehicle = %(vehicle)s")
		values["vehicle"] = filters.vehicle
	if filters.get("driver"):
		conditions.append("t.driver = %(driver)s")
		values["driver"] = filters.driver

	rows = frappe.db.sql(
		f"""
		select
			t.trip_date, t.customer, t.project, t.from_place, t.to_place, t.direction,
			t.scheduled_time, t.driver, d.full_name as driver_name, t.vehicle,
			t.vehicle_class, t.duty_type, t.status, t.remarks
		from `tabTrip` t
		left join `tabDriver` d on d.name = t.driver
		where {" and ".join(conditions)}
		order by t.trip_date, t.scheduled_time
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row["duty_mark"] = "Duty" if row.duty_type == "Duty" else ""
		row["ot_mark"] = "OT" if row.duty_type == "OT" else ""

	return rows
