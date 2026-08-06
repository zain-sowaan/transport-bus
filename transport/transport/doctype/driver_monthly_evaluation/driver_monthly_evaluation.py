# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Second, scored driver evaluation form from the client's source workflow
document: ten standard criteria worth ten points each (100 total), summed into
an overall percentage that maps onto the form's rating bands
(Excellent / Very Good / Good / Satisfactory / Needs Improvement), plus a
recommended-action checklist and evaluator/driver sign-off.

Deliberately kept separate from the existing `Driver Evaluation` doctype,
which implements a different Good/Poor multi-rater form that also appears in
the same source document - both forms are real and are used side by side.

Like `Driver Evaluation`, this doctype does NOT feed `Driver.average_rating`
and does not participate in auto-blacklisting; that remains Trip Feedback's
job.
"""

import frappe
from frappe import _
from frappe.model.document import Document

STANDARD_CRITERIA = (
	"Attendance & Punctuality",
	"Compliance with Traffic Rules",
	"Driving Skills & Safety",
	"Vehicle Maintenance & Cleanliness",
	"Route Planning & Time Management",
	"Customer/Client Interaction & Feedback",
	"Reporting & Documentation Accuracy",
	"Fuel Efficiency & Cost Awareness",
	"Incident/Accident Record",
	"Following a supervisor assigned duties",
)

DEFAULT_MAX_SCORE = 10

# (minimum percentage, label) - evaluated highest band first.
RATING_BANDS = (
	(90, "Excellent"),
	(80, "Very Good"),
	(70, "Good"),
	(60, "Satisfactory"),
	(0, "Needs Improvement"),
)


def get_rating_band(percentage):
	"""Return the form's rating label for a 0-100 percentage."""
	for minimum, label in RATING_BANDS:
		if percentage >= minimum:
			return label

	return RATING_BANDS[-1][1]


class DriverMonthlyEvaluation(Document):
	def validate(self):
		self.seed_criteria()
		self.calculate_totals()
		self.validate_scores()

	def seed_criteria(self):
		"""Populate the ten standard criteria on a fresh form.

		Existing rows are never wiped or reordered - the client is free to add,
		remove or rename criteria on a saved evaluation.
		"""
		if self.criteria:
			return

		for criterion in STANDARD_CRITERIA:
			self.append("criteria", {"criterion": criterion, "max_score": DEFAULT_MAX_SCORE})

	def validate_scores(self):
		for row in self.criteria:
			if row.score_given is None or row.score_given == "":
				continue

			score_given = frappe.utils.cint(row.score_given)
			max_score = frappe.utils.cint(row.max_score)

			if score_given < 0 or score_given > max_score:
				frappe.throw(
					_("Row #{0}: Score Given for {1} must be between 0 and {2}.").format(
						row.idx, row.criterion, max_score
					)
				)

	def calculate_totals(self):
		total = maximum = 0
		for row in self.criteria:
			total += frappe.utils.cint(row.score_given)
			maximum += frappe.utils.cint(row.max_score)

		self.total_score = total
		self.max_total_score = maximum

		percentage = (total / maximum * 100) if maximum else 0
		self.overall_rating = get_rating_band(percentage)
