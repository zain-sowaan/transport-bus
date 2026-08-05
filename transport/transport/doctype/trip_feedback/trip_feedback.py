# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Post-trip feedback from Customer/In-Charge/Admin-HR (doc section 15) -
recorded by Operations on the source's behalf (no live Customer Portal yet,
see [[project_transport_app]]'s Phase 4 scoping note on customer approval).
A low Overall Rating auto-escalates to management, and Driver.average_rating
rolls up every Trip Feedback ever recorded against that driver, feeding
"future trip allocation" per the doc."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

RATING_FIELDS = (
	"driver_behaviour",
	"driving_quality",
	"vehicle_condition",
	"vehicle_cleanliness",
	"driver_hygiene",
	"timing_punctuality",
)


class TripFeedback(Document):
	def validate(self):
		self.validate_rating_range()
		self.calculate_overall_rating()
		self.check_escalation()

	def validate_rating_range(self):
		for fieldname in RATING_FIELDS:
			value = self.get(fieldname)
			if value is not None and not (1 <= value <= 5):
				frappe.throw(_("{0} must be between 1 and 5.").format(self.meta.get_label(fieldname)))

	def calculate_overall_rating(self):
		values = [flt(self.get(f)) for f in RATING_FIELDS]
		self.overall_rating = sum(values) / len(values) if values else 0

	def check_escalation(self):
		if self.escalated:
			return

		threshold = frappe.db.get_single_value("Transport Settings", "feedback_escalation_rating") or 2.5
		if self.overall_rating < threshold:
			self.escalated = 1
			self.escalated_on = now_datetime()

	def on_update(self):
		before = self.get_doc_before_save()
		if self.escalated and not (before and before.escalated):
			self.notify_escalation()
		self.recalculate_driver_rating()

	def notify_escalation(self):
		from transport.transport.driver_portal import notify_roles

		notify_roles(
			("Transport Operations", "Transport In-Charge"),
			_("Low Trip Feedback - {0}").format(self.trip),
			_("Trip {0} received an Overall Rating of {1}/5 from {2}, below the escalation threshold.").format(
				self.trip, self.overall_rating, self.feedback_from
			),
			"Trip Feedback",
			self.name,
		)

	def recalculate_driver_rating(self):
		if not self.driver:
			return

		ratings = frappe.get_all("Trip Feedback", filters={"driver": self.driver}, pluck="overall_rating")
		average = sum(ratings) / len(ratings) if ratings else 0
		frappe.db.set_value("Driver", self.driver, "average_rating", average)
