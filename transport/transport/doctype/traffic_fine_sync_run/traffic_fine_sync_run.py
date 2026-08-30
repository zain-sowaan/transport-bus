# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""One record per fetch attempt against one portal.

This doctype exists mainly so that a failed lookup is impossible to mistake
for a clean result. The earlier planning round flagged that "lookup failed"
and "no fines found" must be distinguishable from day one, because conflating
them silently under-reports real liabilities - a vehicle whose query errored
looks exactly like a vehicle with a clean record unless the difference is
recorded per vehicle, which is what the child table does.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class TrafficFineSyncRun(Document):
	def add_vehicle_result(self, vehicle, plate, status, message=None, fines_found=0):
		self.append(
			"vehicles",
			{
				"vehicle": vehicle,
				"plate": plate,
				"status": status,
				"message": (message or "")[:500],
				"fines_found": fines_found,
			},
		)

	def finalize(self, error_log=None):
		"""Close the run and derive its status from what actually happened.

		A run is only "Completed" when every vehicle was queried successfully.
		Any failure downgrades it, so a partially-successful sync can never be
		read as a full picture of the fleet's liabilities.
		"""
		self.vehicles_queried = len(self.vehicles)
		self.vehicles_failed = sum(1 for v in self.vehicles if v.status == "Failed")
		self.fines_found = sum(v.fines_found or 0 for v in self.vehicles)
		self.finished_on = now_datetime()

		if error_log:
			self.error_log = error_log[:10000]

		if self.status in ("Failed", "Completed with Errors"):
			# The caller already recorded a hard failure, or a sweep that ran out
			# of time before covering the fleet. Neither may be upgraded here:
			# the whole point of this doctype is that a partial picture cannot
			# present itself as a full one.
			pass
		elif self.vehicles_failed:
			self.status = "Completed with Errors"
		else:
			self.status = "Completed"

		self.save(ignore_permissions=True)

	@frappe.whitelist()
	def promote_staged_fines(self):
		"""Turn every New staging row from this run into a real fine."""
		from transport.transport.fine_sync.service import promote_staging_rows

		rows = frappe.get_all(
			"Traffic Fine Staging", filters={"sync_run": self.name, "status": "New"}, pluck="name"
		)
		if not rows:
			frappe.msgprint(_("No new staged fines on this run."))
			return {"promoted": 0}

		return promote_staging_rows(rows)
