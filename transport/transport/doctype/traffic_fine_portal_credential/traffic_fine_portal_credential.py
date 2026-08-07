# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Login details for a fine portal. The password uses Frappe's Password
fieldtype, which stores it encrypted and keeps it out of the document's own
JSON - so it never reaches fixtures, exports or source control."""

from frappe.model.document import Document


class TrafficFinePortalCredential(Document):
	pass
