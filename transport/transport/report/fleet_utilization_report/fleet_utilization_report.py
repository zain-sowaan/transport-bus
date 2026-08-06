# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Doc section 19 dashboard requirements: "Daily/monthly usage efficiency",
"Idle vehicles". Scoped to vehicles that have ever appeared on a Trip (the
transport fleet, not fleetify's whole self-drive-rental Rental Vehicle
list) - a vehicle with zero trips in the filtered period still shows up
here, correctly flagged Idle, rather than being silently excluded."""

import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (filters.get("from_date") and filters.get("to_date")):
		frappe.throw(_("Select From Date and To Date."))

	from_date, to_date = getdate(filters.from_date), getdate(filters.to_date)
	total_days = (to_date - from_date).days + 1

	vehicle_names = frappe.get_all("Trip", pluck="vehicle", distinct=True)
	vehicles = frappe.get_all(
		"Rental Vehicle", filters={"name": ("in", vehicle_names)}, fields=["name", "vehicle_make", "vehicle_model"]
	)

	trips = frappe.get_all(
		"Trip",
		filters={
			"vehicle": ("in", vehicle_names),
			"trip_date": ("between", [from_date, to_date]),
			"status": ("not in", ["Rejected", "Cancelled"]),
		},
		fields=["vehicle", "trip_date"],
	)

	by_vehicle = {}
	for trip in trips:
		row = by_vehicle.setdefault(trip.vehicle, {"trip_count": 0, "active_dates": set()})
		row["trip_count"] += 1
		row["active_dates"].add(trip.trip_date)

	data = []
	for vehicle in vehicles:
		stats = by_vehicle.get(vehicle.name, {"trip_count": 0, "active_dates": set()})
		active_days = len(stats["active_dates"])

		data.append(
			{
				"vehicle": vehicle.name,
				"vehicle_make": vehicle.vehicle_make,
				"vehicle_model": vehicle.vehicle_model,
				"trip_count": stats["trip_count"],
				"active_days": active_days,
				"idle_days": total_days - active_days,
				"utilization_percent": (active_days / total_days * 100) if total_days else 0,
				"status": _("Idle") if stats["trip_count"] == 0 else _("Active"),
			}
		)

	data.sort(key=lambda r: r["utilization_percent"])
	return get_columns(), data


def get_columns():
	return [
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Rental Vehicle", "width": 120},
		{"label": _("Make"), "fieldname": "vehicle_make", "fieldtype": "Data", "width": 100},
		{"label": _("Model"), "fieldname": "vehicle_model", "fieldtype": "Data", "width": 100},
		{"label": _("Trips"), "fieldname": "trip_count", "fieldtype": "Int", "width": 80},
		{"label": _("Active Days"), "fieldname": "active_days", "fieldtype": "Int", "width": 100},
		{"label": _("Idle Days"), "fieldname": "idle_days", "fieldtype": "Int", "width": 90},
		{"label": _("Utilization %"), "fieldname": "utilization_percent", "fieldtype": "Percent", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 80},
	]
