# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Switch off hand-pasted copies of the fine-fetch Client Script.

Until 2026-09-11 the extension's desk script was a file in the repository that
somebody opened, copied, and pasted into a new Client Script on each site. It
now ships as an app fixture, which is the right answer - but it does not remove
the pasted copies, and on any site that has one the two now collide.

**The collision is worse than a duplicate button.** Frappe concatenates every
enabled Client Script for a doctype into a single `new Function()`. Two of them
declaring the same top-level `const` is a SyntaxError in that combined body, so
*every* script on the form dies, not just the duplicate - the observed symptom
is "Error in Client Script: Identifier 'BRIDGE_FROM_PAGE' has already been
declared" and a form with none of its buttons.

The script itself is now wrapped in an IIFE so this cannot happen again. This
patch clears the copies that predate that.

**Disabled, not deleted.** These are records a person made by hand, and a patch
that runs unattended on every site should not destroy one. Disabled is enough -
Frappe does not load a disabled script - it is one click to reverse, and
anything an operator changed in their copy is still there to read. The fixture
is the source of truth from here.

Matched on content rather than on name. The earlier
`remove_duplicate_portal_client_script` matched an exact docname, which worked
only because that record was created by an app. A pasted copy is named by
whoever pasted it, so the reliable signal is that it declares the bridge
constant this script owns.
"""

import frappe

DOCTYPE = "Client Script"
TARGET_DT = "Traffic Fine Portal"

# The fixture. Anything else on this doctype carrying the marker is a copy.
FIXTURE_NAME = "Traffic Fine Portal-transport-client-fetch"

# Declared by the desk script and by nothing else in the app.
MARKER = "BRIDGE_FROM_PAGE"


def execute():
	scripts = frappe.get_all(
		DOCTYPE,
		filters={"dt": TARGET_DT, "enabled": 1},
		fields=["name", "script"],
	)

	disabled = []
	for script in scripts:
		if script.name == FIXTURE_NAME:
			continue
		if MARKER not in (script.script or ""):
			continue
		frappe.db.set_value(DOCTYPE, script.name, "enabled", 0, update_modified=False)
		disabled.append(script.name)

	if disabled:
		# Left in the log rather than silently: somebody wrote these, and the
		# next person to wonder where their button went deserves to find out
		# here rather than by diffing two Client Scripts by eye.
		frappe.logger("transport.fine_sync").info(
			f"Disabled {len(disabled)} pasted fine-fetch Client Script(s): {', '.join(disabled)}"
		)

	frappe.db.commit()
