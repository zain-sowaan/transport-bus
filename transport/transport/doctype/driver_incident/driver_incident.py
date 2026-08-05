# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Doc section 16, Blacklist / Incident History. Black points accumulate
across a driver's full history regardless of status - "Resolved" just means
administratively closed (talked to / fine paid), not forgiven, since the
doc frames this as a running history, not a clearable ledger. Crossing the
threshold auto-blacklists (one-way); manually un-blacklisting is a
deliberate action via set_driver_blacklist(), not automatic, since points
dropping isn't itself possible without editing history."""

import frappe
from frappe import _
from frappe.model.document import Document

MANAGE_ROLES = ("Transport Operations", "Transport In-Charge", "System Manager")


class DriverIncident(Document):
	def validate(self):
		if not self.reported_by:
			self.reported_by = frappe.session.user

	def on_update(self):
		recalculate_driver_blacklist(self.driver)

	def on_trash(self):
		recalculate_driver_blacklist(self.driver)


def recalculate_driver_blacklist(driver):
	total = frappe.db.sql(
		"select coalesce(sum(black_points), 0) from `tabDriver Incident` where driver = %s", driver
	)[0][0]
	frappe.db.set_value("Driver", driver, "total_black_points", total)

	if frappe.db.get_value("Driver", driver, "is_blacklisted"):
		return

	threshold = frappe.db.get_single_value("Transport Settings", "blacklist_threshold_points") or 10
	if total >= threshold:
		frappe.db.set_value(
			"Driver",
			driver,
			{
				"is_blacklisted": 1,
				"blacklist_reason": _("Auto-blacklisted: {0} black points reached the threshold ({1}).").format(
					total, threshold
				),
			},
		)


@frappe.whitelist()
def set_driver_blacklist(driver, blacklisted, reason=None):
	if not any(role in frappe.get_roles() for role in MANAGE_ROLES):
		frappe.throw(_("Not permitted to change blacklist status."), frappe.PermissionError)

	blacklisted = frappe.utils.cint(blacklisted)
	frappe.db.set_value(
		"Driver",
		driver,
		{"is_blacklisted": blacklisted, "blacklist_reason": reason if blacklisted else None},
	)
	return "Blacklisted" if blacklisted else "Cleared"
