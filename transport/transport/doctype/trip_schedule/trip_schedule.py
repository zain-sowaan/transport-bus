# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate

WEEKDAY_FIELDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class TripSchedule(Document):
	def validate(self):
		if self.effective_to and getdate(self.effective_to) < getdate(self.effective_from):
			frappe.throw(_("Effective To cannot be before Effective From."))

		if not any(self.get(day) for day in WEEKDAY_FIELDS):
			frappe.throw(_("Select at least one day of the week."))

	@frappe.whitelist()
	def generate_trips(self, from_date=None, to_date=None):
		route = frappe.get_doc("Route", self.route)

		from_date = getdate(from_date) if from_date else self.next_generation_start()
		to_date = getdate(to_date) if to_date else (getdate(self.effective_to) if self.effective_to else from_date)

		if self.effective_to:
			to_date = min(to_date, getdate(self.effective_to))
		if from_date < getdate(self.effective_from):
			from_date = getdate(self.effective_from)

		if from_date > to_date:
			return {"created": 0, "message": _("Nothing to generate - the range is already covered.")}

		created = 0
		cursor = from_date
		while cursor <= to_date:
			if self.get(cursor.strftime("%A").lower()):
				created += self.create_trips_for_date(cursor, route)
			cursor = add_days(cursor, 1)

		self.db_set("last_generated_till", to_date)
		return {"created": created, "from_date": from_date, "to_date": to_date}

	def next_generation_start(self):
		if self.last_generated_till:
			return add_days(getdate(self.last_generated_till), 1)
		return getdate(self.effective_from)

	def create_trips_for_date(self, trip_date, route):
		created = 0
		legs = []
		if self.generate_pickup_leg:
			legs.append(("Pickup", route.pickup_time, route.from_place, route.to_place))
		if self.generate_drop_leg:
			legs.append(("Drop", route.drop_time, route.to_place, route.from_place))

		for direction, scheduled_time, from_place, to_place in legs:
			if frappe.db.exists(
				"Trip", {"trip_schedule": self.name, "trip_date": trip_date, "direction": direction}
			):
				continue

			frappe.get_doc(
				{
					"doctype": "Trip",
					"trip_schedule": self.name,
					"project": self.project,
					"route": self.route,
					"direction": direction,
					"trip_date": trip_date,
					"scheduled_time": scheduled_time,
					"from_place": from_place,
					"to_place": to_place,
				}
			).insert()
			created += 1

		return created
