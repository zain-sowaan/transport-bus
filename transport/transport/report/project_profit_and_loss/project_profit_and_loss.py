# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Doc section 14 (Profit & Loss System), Project dimension. Revenue is
invoiced Sales Invoice totals against the project; cost is booked/approved
Trip Expenses only - driver salary isn't included (driver salary basis is
still blocked pending the client's HR policy, see project memory), and
shared-vehicle fuel/maintenance isn't allocated per-project either, since a
vehicle can serve multiple projects and the source doc doesn't specify an
allocation policy. Scoped to Projects with at least one Trip, so this
doesn't list every unrelated Project on the site."""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if filters.get("project"):
		project_filters = {"name": filters.project}
	else:
		project_filters = {"name": ("in", frappe.get_all("Trip", pluck="project", distinct=True))}

	projects = frappe.get_all("Project", filters=project_filters, fields=["name", "project_name", "customer"])

	data = []
	for project in projects:
		revenue = get_revenue(project.name, filters)
		expenses = get_expenses(project.name, filters)
		estimated_cost = get_estimated_cost(project.name)
		profit = revenue - expenses

		data.append(
			{
				"project": project.name,
				"project_name": project.project_name,
				"customer": project.customer,
				"revenue": revenue,
				"actual_expenses": expenses,
				"estimated_monthly_cost": estimated_cost,
				"profit": profit,
				"margin_percent": (profit / revenue * 100) if revenue else 0,
				"result": _("Profit") if profit >= 0 else _("Loss"),
			}
		)

	return get_columns(), data


def get_revenue(project, filters):
	invoice_filters = {"project": project, "docstatus": 1}
	if filters.get("from_date") and filters.get("to_date"):
		invoice_filters["posting_date"] = ("between", [filters.from_date, filters.to_date])

	return flt(
		frappe.db.get_value("Sales Invoice", invoice_filters, "sum(grand_total)") or 0
	)


def get_expenses(project, filters):
	expense_filters = {"project": project, "status": ("in", ["Approved", "Booked"])}
	if filters.get("from_date") and filters.get("to_date"):
		expense_filters["expense_date"] = ("between", [filters.from_date, filters.to_date])

	return flt(frappe.db.get_value("Trip Expense", expense_filters, "sum(amount)") or 0)


def get_estimated_cost(project):
	return flt(
		frappe.db.get_value(
			"Transport Rate Calculation", {"project": project}, "total_estimated_cost"
		)
		or 0
	)


def get_columns():
	return [
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
		{"label": _("Project Name"), "fieldname": "project_name", "fieldtype": "Data", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Revenue (Invoiced)"), "fieldname": "revenue", "fieldtype": "Currency", "width": 130},
		{"label": _("Actual Expenses"), "fieldname": "actual_expenses", "fieldtype": "Currency", "width": 130},
		{"label": _("Est. Monthly Cost"), "fieldname": "estimated_monthly_cost", "fieldtype": "Currency", "width": 130},
		{"label": _("Profit"), "fieldname": "profit", "fieldtype": "Currency", "width": 120},
		{"label": _("Margin %"), "fieldname": "margin_percent", "fieldtype": "Percent", "width": 100},
		{"label": _("Result"), "fieldname": "result", "fieldtype": "Data", "width": 90},
	]
