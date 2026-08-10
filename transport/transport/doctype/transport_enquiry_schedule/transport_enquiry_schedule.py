# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""One day of a customer's requested shuttle schedule, as they sent it.

The source enquiries arrive as a Word table - a batch header, then a row per
day carrying pick-up and drop-off times, a passenger count, several pick-up
points and one drop-off point. That is four levels of nesting, and Frappe
supports one child table deep, so it cannot be modelled literally.

It does not need to be. Across a typical seven-day batch only the passenger
count actually varies; the times, the locations and the pick-up points repeat
in every row. The nesting is presentational. So batch is a column rather than
a level, and the pick-up points stay as lines of text inside the row.

Nothing here is validated as master data. An enquiry records what a customer
asked for, including places that do not exist in any master yet and will not
if the enquiry is lost - see `normalize_enquiry_schedule` in crm_controls.py
for the small amount that is enforced.

Both times are `Data`, not `Time`, and that is not laziness. Frappe fills any
blank Time field on a new document with the current clock time - unconditionally,
in `frappe/model/create_new.py`, from `_set_defaults()` - and that runs before
`before_validate`, so no hook can tell "left blank" from "typed this exact
moment". On a document whose entire purpose is capturing an incomplete request,
where the customer writes TBA, that would invent a pick-up time on every row.
"""

from frappe.model.document import Document


class TransportEnquirySchedule(Document):
	pass
