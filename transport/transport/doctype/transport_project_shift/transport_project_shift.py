# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""One shift's commercial terms on a transport contract.

The client's project sheet is structured **by shift**, not by project: Morning,
Afternoon and Evening each carry their own vehicle type, trips per day, timings,
weekend off day, monthly value and - the part this table records - their own
including/excluding terms for weekend and public-holiday payment, Salik and
Darb, fuel and driver accommodation. A single-shift contract is simply one row.

`Including` / `Excluding` describe who carries the cost: Including means it is
absorbed in the shift's monthly value, Excluding means it is recharged to the
customer on top. That reading follows the sheet's own pairing of a
weekend/public-holiday including-excluding flag with a separate charge amount,
and matches how weekend and public-holiday charges are already invoiced - but
it is an interpretation of a client document, so confirm it before the first
live invoice.

Rates are left blank rather than defaulted. The sheet quotes Salik at AED 6 and
Darb at AED 4, and both figures should be treated as stale: Dubai now prices
Salik by peak/off-peak, and a 5% VAT applies to Salik crossings from 1 June
2026. Encoding either number as a default would quietly under-recover.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TransportProjectShift(Document):
	pass


def validate_project_shifts(doc, method=None):
	"""Keep the shift terms coherent. Registered on Project, not here.

	Frappe does not run a child doctype's own validate() during the parent's
	save, so a child table on a core doctype has to be checked from the
	parent's hook - the same arrangement as the enquiry schedule on Opportunity.
	"""
	shifts = doc.get("transport_shifts") or []

	seen = set()
	for row in shifts:
		if row.shift in seen:
			frappe.throw(
				_("Row {0}: the {1} shift is listed twice. Each shift's terms belong on one row.").format(
					row.idx, row.shift
				)
			)
		seen.add(row.shift)

		# A fuel arrangement only means something when fuel is excluded, and
		# leaving a stale one behind would misstate who pays for fuel.
		if row.fuel != "Excluding":
			row.fuel_arrangement = None
			row.show_consumed_fuel_on_invoice = 0
		elif not row.fuel_arrangement:
			frappe.throw(
				_("Row {0}: fuel is excluded for the {1} shift, so say whether the client "
				  "supplies it or the company does and recharges it.").format(row.idx, row.shift)
			)

		if row.fuel_arrangement == "Fuel from Client":
			# Nothing is consumed at our cost, so there is nothing to show.
			row.show_consumed_fuel_on_invoice = 0

		# Tolls absorbed into the monthly value are never recharged, so a rate
		# on that row would be read as billable by anything downstream.
		if row.salik_darb == "Including" and (row.salik_rate or row.darb_rate):
			frappe.throw(
				_("Row {0}: Salik & Darb are Including for the {1} shift, so they are absorbed "
				  "in the monthly value. Clear the rates, or set the term to Excluding if they "
				  "are recharged.").format(row.idx, row.shift)
			)
