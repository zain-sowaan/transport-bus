# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""A fine as the portal reported it, before it becomes a real record.

Fetched rows land here rather than straight into Transport Traffic Fine so a
mapping mistake, a mis-matched vehicle or an unexpected response shape can be
seen and corrected without having already created accounting records. The raw
payload is kept alongside so a bad mapping can be diagnosed later without
re-querying the portal.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TrafficFineStaging(Document):
	def validate(self):
		self.flag_if_already_known()

	def flag_if_already_known(self):
		"""Mark rows we have already recorded, so re-running a sync produces
		no duplicates. Identity is (portal, ticket number) - ticket numbers are
		only unique within the issuing authority."""
		if self.status not in ("New", "Duplicate") or not self.ticket_number:
			return

		existing = frappe.db.exists(
			"Transport Traffic Fine",
			{"ticket_number": self.ticket_number, "source_portal": self.portal},
		)
		if existing:
			self.status = "Duplicate"
			self.transport_traffic_fine = existing

	@frappe.whitelist()
	def promote(self):
		from transport.transport.fine_sync.service import promote_staging_rows

		return promote_staging_rows([self.name])

	@frappe.whitelist()
	def ignore(self, reason=None):
		if self.status == "Promoted":
			frappe.throw(_("This row has already been promoted to a fine."))
		self.db_set("status", "Ignored")
		return self.status
