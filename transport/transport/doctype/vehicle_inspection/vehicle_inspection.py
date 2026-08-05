# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Doc section "Vehicle Check-In & Check-Out Checklist" - every allocated
vehicle gets inspected at delivery (Check-In) and at return (Check-Out) for
a project. Not wired as a hard gate on Trip creation - the source doc lists
it as an operational requirement, not one of the "Mandatory System
Controls" it enumerates separately."""

from frappe.model.document import Document


class VehicleInspection(Document):
	pass
