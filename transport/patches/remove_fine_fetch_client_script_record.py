# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Delete the Client Script record the fine-fetch fixture created.

The desk half of the browser extension moved again, and this clears the middle
step. It was a file to paste in by hand, became a Client Script fixture on
2026-09-11, and is now a `doctype_js` file loaded from hooks - which is where
it should have gone in the first place.

Dropping the record from the fixture JSON does not remove it from sites that
already migrated: fixture sync imports and updates, it never deletes what it
stops shipping. Left alone the record would keep working, and the form would
then register the button twice - once from this record and once from the
doctype_js file - along with two bridge listeners answering the same PING.

Deleted rather than disabled, unlike the pasted copies the previous patch
handled. This record was created by an app, holds nothing anybody typed, and is
reproduced exactly by the file now shipping in `transport/public/js/`. There is
nothing in it to preserve.
"""

import frappe

NAME = "Traffic Fine Portal-transport-client-fetch"


def execute():
	frappe.delete_doc_if_exists("Client Script", NAME)
	frappe.db.commit()
