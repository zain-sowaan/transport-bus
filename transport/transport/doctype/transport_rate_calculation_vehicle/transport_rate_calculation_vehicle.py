# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""One vehicle's cost build-up inside a Transport Rate Calculation.

A contract is quoted for several vehicle types at once - the client's own
price calculation sheet is a row per vehicle type, each with its own fuel,
salary, rent, room and maintenance figures, and its own approved price. This
holds one of those rows.

Every field here was on the parent until the sheet showed the calculation is
per vehicle, not per enquiry. The arithmetic lives in the parent controller
(`TransportRateCalculation.calculate`), because a child doctype's own
validate() is not run by the parent's save.
"""

from frappe.model.document import Document


class TransportRateCalculationVehicle(Document):
	pass
