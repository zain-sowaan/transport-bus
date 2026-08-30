# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Drop the Client Script that duplicated Traffic Fine Portal's own form script.

	Both attached a refresh handler to the same form and they disagreed about
	what the portal could do. The doctype script decided from the document alone,
	so it offered Run Sync on a portal with no fetcher and Capture Page on one
	with no public form URL - both failed on a click, with messages about
	something else entirely. The doctype script now asks the server what the
	portal can actually do and is the single source of these buttons; this
	removes the other so the two cannot drift apart again.
	"""
	frappe.delete_doc_if_exists("Client Script", "Traffic Fine Portal-transport-fetch-now")
