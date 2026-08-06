# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Doc section 14, "Variance Analysis - Planned vs actual cost comparison".
Planned = Transport Rate Calculation's estimated monthly cost (the same
figure Project Profit and Loss shows for reference); Actual = booked/
approved Trip Expenses within the filtered period. Reuses that report's
helpers rather than duplicating the cost math."""

import frappe
from frappe import _

from transport.transport.report.project_profit_and_loss.project_profit_and_loss import (
	get_estimated_cost,
	get_expenses,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if filters.get("project"):
		project_filters = {"name": filters.project}
	else:
		project_filters = {"name": ("in", frappe.get_all("Trip", pluck="project", distinct=True))}

	projects = frappe.get_all("Project", filters=project_filters, fields=["name", "project_name", "customer"])

	data = []
	for project in projects:
		planned = get_estimated_cost(project.name)
		actual = get_expenses(project.name, filters)
		variance = actual - planned

		data.append(
			{
				"project": project.name,
				"project_name": project.project_name,
				"customer": project.customer,
				"planned_cost": planned,
				"actual_cost": actual,
				"variance": variance,
				"variance_percent": (variance / planned * 100) if planned else 0,
			}
		)

	return get_columns(), data


def get_columns():
	return [
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
		{"label": _("Project Name"), "fieldname": "project_name", "fieldtype": "Data", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Planned Cost"), "fieldname": "planned_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Actual Cost"), "fieldname": "actual_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 120},
		{"label": _("Variance %"), "fieldname": "variance_percent", "fieldtype": "Percent", "width": 100},
	]
