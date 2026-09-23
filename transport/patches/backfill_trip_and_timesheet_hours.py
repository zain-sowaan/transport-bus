import frappe

DOCTYPE = "Transport Timesheet"
TRIP = "Trip"
CHILD = "Transport Timesheet Trip"


PARENT_FIELDS = (
	"total_trips",
	"total_duty_trips",
	"total_ot_trips",
	"total_additional_trips",
	"total_hours",
	"total_weekend_trips",
	"total_public_holiday_trips",
	"total_weekend_days",
	"total_public_holiday_days",
	"total_weekend_hours",
	"total_public_holiday_hours",
	"total_duty_hours",
	"total_ot_hours",
)



def execute():
	"""
	Recalculate missing trip durations and timesheet totals.
	"""
	create_missing_duration()
	backfill_trip_and_timesheet_hours()
	backfill_trip_and_timesheet_totals()



def create_missing_duration():
	"""
	Create missing duration for trips that have no duration set.
	"""

	names = frappe.get_all(TRIP, filters={"actual_start_time": ("is", "set"), "actual_end_time": ("is", "set")}, pluck="name")
	if not names:
		return

	for name in names:
		doc = frappe.get_doc(TRIP, name)
		doc.set_duration()                 # reuse the controller method

		frappe.db.set_value(
				TRIP, name, "duration", doc.duration, update_modified=False
			)

	frappe.db.commit()
	print("Transport: created missing trip durations.")

def backfill_trip_and_timesheet_hours():
	"""corrected the duration of trips in timesheet child table to match the trip duration"""
	rows = frappe.get_all(CHILD, fields=["trip", "name"])
	durations = dict(frappe.get_all(TRIP, fields=["name", "duration"], as_list=True))
	for row in rows:
		frappe.db.set_value(
			CHILD, row.name, "duration", durations.get(row.trip) or 0, update_modified=False
		)

	frappe.db.commit()
	print(f"Transport: backfilled trip and timesheet hours for {len(rows)} documents.")


def backfill_trip_and_timesheet_totals():
	"""Recalculate and write back totals for all timesheets. This is a repair, not a migration,
	so it must not throw on a document whose content is invalid."""
	names = frappe.get_all(DOCTYPE, pluck="name")

	repaired, failed = 0, []
	for name in names:
		try:
			doc = frappe.get_doc(DOCTYPE, name)
			doc.calculate_totals()
		except Exception as exc:

			failed.append(f"{name}: {exc}")
			continue
		frappe.db.set_value(
						DOCTYPE, name, {f: doc.get(f) for f in PARENT_FIELDS}, update_modified=False
				)
		repaired += 1
	if failed:
		# Printed, not thrown. One unrepairable document must not block a
		# migrate, and the names are what somebody needs in order to open them.
		for line in failed:
			print(f"    {line}")
	frappe.db.commit()

	print(f"Transport: backfilled trip and timesheet totals for {repaired} documents.")
