# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""One day of a customer's requested shuttle schedule, as they sent it.

The source enquiries arrive as a Word table - a row per day carrying pick-up
and drop-off times, a passenger count, several pick-up points and one drop-off
point. Frappe supports one child table deep, so the pick-up points stay as
lines of text inside the row rather than becoming a table of their own; across
a typical week only the passenger count actually varies anyway.

Nothing here is validated as master data. An enquiry records what a customer
asked for, including places that do not exist in any master yet and will not
if the enquiry is lost - see `normalize_enquiry_schedule` in crm_controls.py
for the small amount that is enforced.

Both times are `Datetime`. Note that the equivalent `Time` fieldtype could not
be used: Frappe fills any blank Time on a new document with the current clock
time unconditionally, before validation can see it, so an unstated pick-up
would silently become a real-looking one. Datetime only does this when a
default of "now" is declared, and none is - so blank stays blank.
"""

from frappe.model.document import Document


class TransportEnquirySchedule(Document):
	pass
