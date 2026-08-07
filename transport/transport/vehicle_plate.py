# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Structured plate fields on Rental Vehicle.

UAE traffic-fine portals take the plate as three separate inputs - issuing
emirate, plate code and plate number - and none of them accept a single
combined string. fleetify's `license_plate` is one free-text Data field, and
real fleets write it inconsistently (this bench alone holds two different
shapes), so it cannot be split reliably at query time. The three parts are
therefore captured explicitly, as Custom Fields shipped by this app.

Rental Vehicle belongs to fleetify, so nothing here edits their doctype - the
fields arrive via transport/fixtures/custom_field.json and the behaviour via a
doc_event registered in this app's hooks. Both are confined to sites where
`transport` is installed.
"""

import frappe
from frappe import _

PLATE_FIELDS = ("plate_emirate", "plate_code", "plate_number")

# plate_emirate is a Select: its value has to keep the exact casing of the
# option list, so it is trimmed but never upper-cased.
UPPERCASED_FIELDS = ("plate_code", "plate_number")


def normalize_plate(doc, method=None):
	"""Trim and upper-case the plate parts, then refuse a half-filled set.

	The completeness check only fires once someone has entered *something*, so
	it can never block a vehicle whose structured plate has simply not been
	filled in yet - which is every existing record on any site that installs
	this app. A partially filled plate, on the other hand, is a genuine error:
	it looks populated in the list view but cannot be queried against any
	portal, and would otherwise be discovered only as a silent gap in a fine
	sync.
	"""
	for fieldname in PLATE_FIELDS:
		value = doc.get(fieldname)
		if not isinstance(value, str):
			continue
		# Portals match the code exactly; "a" and " A " are the same plate.
		value = value.strip()
		doc.set(fieldname, value.upper() if fieldname in UPPERCASED_FIELDS else value)

	filled = [f for f in PLATE_FIELDS if doc.get(f)]
	if filled and len(filled) != len(PLATE_FIELDS):
		missing = [f for f in PLATE_FIELDS if not doc.get(f)]
		frappe.throw(
			_("Vehicle {0}: the structured plate is incomplete - {1} still needed. "
			  "Traffic-fine lookups need the emirate, code and number together, so a "
			  "partial plate cannot be used. Fill all three, or clear them all.").format(
				doc.get("license_plate") or doc.name,
				", ".join(_(frappe.unscrub(f)) for f in missing),
			),
			title=_("Incomplete Plate"),
		)


def get_plate_parts(vehicle):
	"""The three parts for a vehicle, or None if it is not queryable yet.

	Callers fetching fines should treat None as "cannot look this vehicle up"
	and surface it, never as "this vehicle has no fines" - conflating those
	under-reports real liabilities.
	"""
	parts = frappe.db.get_value("Rental Vehicle", vehicle, PLATE_FIELDS, as_dict=True)
	if not parts or not all(parts.get(f) for f in PLATE_FIELDS):
		return None
	return parts


def get_vehicles_without_plate_parts():
	"""Active vehicles that cannot be queried against a fine portal yet."""
	# or_filters, not filters: a vehicle is unqueryable if ANY one part is
	# missing, not only when all three are.
	return frappe.get_all(
		"Rental Vehicle",
		or_filters=[[f, "in", ("", None)] for f in PLATE_FIELDS],
		fields=["name", "license_plate", *PLATE_FIELDS],
		order_by="license_plate",
	)
