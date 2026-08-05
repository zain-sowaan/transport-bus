# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

DEFAULT_SOON_WINDOW_DAYS = 30


class ComplianceDocument(Document):
	def validate(self):
		self.set_status()

	def set_status(self):
		soon_window = (
			frappe.db.get_single_value("Transport Settings", "compliance_expiry_soon_days")
			or DEFAULT_SOON_WINDOW_DAYS
		)

		self.days_to_expiry = (getdate(self.expiry_date) - getdate(nowdate())).days

		if self.days_to_expiry < 0:
			self.status = "Expired"
		elif self.days_to_expiry <= soon_window:
			self.status = "Expiring Soon"
		else:
			self.status = "Valid"


def refresh_status():
	"""Recompute status/days_to_expiry daily so records reflect *today*, not
	just the last edit. Native `Notification` doctype records (configured per
	document_type once real alert recipients are known) drive the "N days
	before expiry" emails off `expiry_date` directly — this job only keeps
	the Status column and dashboards accurate for anyone looking at a list."""
	soon_window = (
		frappe.db.get_single_value("Transport Settings", "compliance_expiry_soon_days")
		or DEFAULT_SOON_WINDOW_DAYS
	)
	today = getdate(nowdate())

	for row in frappe.get_all(
		"Compliance Document", fields=["name", "expiry_date", "status"]
	):
		days_to_expiry = (getdate(row.expiry_date) - today).days
		if days_to_expiry < 0:
			status = "Expired"
		elif days_to_expiry <= soon_window:
			status = "Expiring Soon"
		else:
			status = "Valid"

		if status != row.status:
			frappe.db.set_value(
				"Compliance Document", row.name, {"status": status, "days_to_expiry": days_to_expiry}
			)
		else:
			frappe.db.set_value("Compliance Document", row.name, "days_to_expiry", days_to_expiry)
