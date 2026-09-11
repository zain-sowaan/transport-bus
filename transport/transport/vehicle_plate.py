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

# The Select's options, in its own order and casing. Anything written to
# plate_emirate has to be one of these exactly, or the field holds a value the
# form cannot render and no filter will ever match.
EMIRATES = (
	"Abu Dhabi",
	"Dubai",
	"Sharjah",
	"Ajman",
	"Umm Al Quwain",
	"Ras Al Khaimah",
	"Fujairah",
)

# What the portals actually emit, mapped onto those options.
#
# Keys are already folded by `_fold()` below - lower-cased, stripped of spaces,
# punctuation and the Arabic definite article - so one key covers "Abu Dhabi",
# "abu-dhabi" and "ABUDHABI" without seven entries each.
#
# The Arabic forms are here because TAMM reads the emirate off the rendered
# number plate, where it is Arabic: its extractor already captures
# `.ui-lib-number-plate__serial-area-item-ar` and, until now, threw it away.
_EMIRATE_ALIASES = {
	"abudhabi": "Abu Dhabi",
	"adh": "Abu Dhabi",
	"ad": "Abu Dhabi",
	"ابوظبي": "Abu Dhabi",
	"dubai": "Dubai",
	"dxb": "Dubai",
	"دبي": "Dubai",
	"sharjah": "Sharjah",
	"shj": "Sharjah",
	"شارقه": "Sharjah",
	"شارقة": "Sharjah",
	"ajman": "Ajman",
	"ajm": "Ajman",
	"عجمان": "Ajman",
	"ummalquwain": "Umm Al Quwain",
	"ummalquwan": "Umm Al Quwain",
	"uaq": "Umm Al Quwain",
	"امالقيوين": "Umm Al Quwain",
	"rasalkhaimah": "Ras Al Khaimah",
	"rasalkhaima": "Ras Al Khaimah",
	"rak": "Ras Al Khaimah",
	"راسالخيمه": "Ras Al Khaimah",
	"راسالخيمة": "Ras Al Khaimah",
	"fujairah": "Fujairah",
	"fujairh": "Fujairah",
	"fuj": "Fujairah",
	"فجيره": "Fujairah",
	"فجيرة": "Fujairah",
}


def _fold(text):
	"""Reduce a written emirate to a comparable key.

	Arabic needs three normalisations that Latin text does not: the alef
	variants (أ إ آ) are written interchangeably, the taa marbuta (ة) and haa
	(ه) are routinely swapped at the end of a word, and the definite article
	"ال" prefixes most emirate names in some renderings and not others. Folding
	all three means "الشارقة" and "شارقه" reach the same key.
	"""
	value = (text or "").strip().lower()
	if not value:
		return ""
	for variants, canonical in (("\u0623\u0625\u0622", "\u0627"), ("\u0629", "\u0647")):
		for ch in variants:
			value = value.replace(ch, canonical)
	# Drop everything that is not a letter or digit - spaces, hyphens, the
	# Arabic tatweel, stray punctuation from a scraped cell.
	value = "".join(ch for ch in value if ch.isalnum())
	# The definite article, only where something is left after removing it.
	if value.startswith("\u0627\u0644") and len(value) > 2:
		value = value[2:]
	return value


def normalize_emirate(text):
	"""A portal's emirate as one of `EMIRATES`, or None if it is not one.

	None is a real answer and callers must treat it as "unknown", never as a
	default. Guessing an emirate is how a Dubai fine gets matched against an
	Abu Dhabi vehicle that happens to share a plate code and number - two
	different cars, one of which is about to be billed for the other's fine.
	"""
	folded = _fold(text)
	if not folded:
		return None
	direct = _EMIRATE_ALIASES.get(folded)
	if direct:
		return direct
	# An exact option written in some other casing or spacing.
	for emirate in EMIRATES:
		if _fold(emirate) == folded:
			return emirate
	return None


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
