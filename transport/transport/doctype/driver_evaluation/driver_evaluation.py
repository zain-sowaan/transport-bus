# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Matches the client's real monthly Driver Performance Evaluation form (found
as an embedded image in the source workflow doc, not the "10 criteria x 10
points" description an earlier session's memory had recorded from a
different, unverified reading) - one row per rating staff member, each
scoring five Good/Poor categories, countersigned by the Operations Manager.
Deliberately separate from Trip Feedback: this is a monthly HR-side
qualitative record, not a per-trip rating, and doesn't feed
Driver.average_rating or auto-blacklisting."""

from frappe.model.document import Document

CATEGORY_FIELDS = ("respect_to_staff", "pickup_drop_ontime", "communication", "attitude", "behaviour")


class DriverEvaluation(Document):
	def validate(self):
		self.calculate_totals()

	def calculate_totals(self):
		good = poor = 0
		for row in self.ratings:
			for fieldname in CATEGORY_FIELDS:
				if row.get(fieldname) == "Poor":
					poor += 1
				else:
					good += 1

		self.total_good = good
		self.total_poor = poor
		self.good_percentage = (good / (good + poor) * 100) if (good + poor) else 0
