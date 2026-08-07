# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Registry of the official UAE traffic-fine portals, seeded from the reviewed
portal inventory.

Two things this doctype exists to make true in code rather than in a
spreadsheet:

1. **No sync may run against a portal without written authorization.** No
   portal's terms of service permit automated access by default, and several
   explicitly prohibit it. `is_enabled` therefore cannot be set without
   `has_written_authorization`, and an expired authorization disables the
   portal rather than being a note someone is meant to notice.

2. **Scope is explicit.** Three of the registered portals cover transport or
   toll violations only and are not the police traffic-fine registry - reading
   them as a substitute would silently under-report liabilities.

Six of the portals share the single MOI route, so `access_route` is what tells
a future fetcher that one integration serves all of them.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate


class TrafficFinePortal(Document):
	def validate(self):
		self.check_authorization()

	def check_authorization(self):
		if not self.is_enabled:
			return

		if not self.has_written_authorization:
			frappe.throw(
				_("{0} cannot be enabled for sync without written authorization. "
				  "No portal's terms of service permit automated access by default.").format(self.portal_name),
				title=_("Authorization Required"),
			)

		if self.authorization_expires_on and getdate(self.authorization_expires_on) < getdate(nowdate()):
			frappe.throw(
				_("The written authorization for {0} expired on {1}. Renew it before enabling the sync.").format(
					self.portal_name, frappe.format(self.authorization_expires_on, {"fieldtype": "Date"})
				),
				title=_("Authorization Expired"),
			)


def get_syncable_portals():
	"""Portals a fetch may legitimately run against right now.

	Re-checks expiry at call time: a portal enabled while its authorization was
	valid must stop being used the day it lapses, without anyone editing it.
	"""
	portals = frappe.get_all(
		"Traffic Fine Portal",
		filters={"is_enabled": 1, "has_written_authorization": 1},
		fields=["name", "authority", "access_route", "scope", "authorization_expires_on"],
	)
	today = getdate(nowdate())
	return [
		p for p in portals
		if not p.authorization_expires_on or getdate(p.authorization_expires_on) >= today
	]
