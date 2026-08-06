# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""An intermediate stop on a Trip. The source workflow document asks the
system to support "Multiple pickup/drop locations", and the client's own
enquiry email shows one shuttle run collecting from three pickup points
before a single drop-off.

Trip keeps its `from_place`/`to_place` as the run's origin and destination -
the trip sheet, the monthly timesheet matrix and Route all read those two
fields, and a stop list is additive detail rather than a replacement. When
stops are present, Trip.validate() keeps from_place/to_place aligned with the
first and last stop so the two views can never disagree."""

from frappe.model.document import Document


class TripStop(Document):
	pass
