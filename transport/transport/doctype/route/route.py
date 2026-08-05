# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Route(Document):
	def validate(self):
		if self.from_place and self.to_place and self.from_place == self.to_place:
			frappe.throw(_("From and To cannot be the same Place."))
