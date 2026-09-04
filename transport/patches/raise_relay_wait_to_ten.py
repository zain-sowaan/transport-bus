import frappe

DOCTYPE = "Transport Settings"
FIELD = "relay_wait_minutes"
OLD_DEFAULT = 5
NEW_DEFAULT = 10


def execute():
	"""Give the relay ten minutes instead of five, without overruling anyone.

	Five was sized on the assumption that the whole window is spent waiting for
	a phone tap. It is not: the clock starts at the sign-in click, and anything
	UAE Pass puts on screen first comes out of the same budget. A reCAPTCHA
	challenge - which is what has actually been happening on UAT - can spend a
	sixth of it before the push is even sent.

	**Only ever moves the value off the old default.** A site that has chosen
	its own figure has chosen it, and a migrate that quietly resets a deliberate
	setting is worse than one that leaves it low. So the three states are kept
	apart: no row at all (never configured - seed the new default), a row still
	holding exactly the old default (nobody chose it - move it), and anything
	else (somebody chose it - leave it).

	Reads `tabSingles` directly for the reason p9 and p10 both record:
	`get_single_value` returns a typed zero for an absent row, so it cannot tell
	"never configured" from "deliberately set to nothing".
	"""
	row = frappe.db.sql(
		"select value from tabSingles where doctype = %s and field = %s limit 1",
		(DOCTYPE, FIELD),
	)

	if not row:
		frappe.db.set_single_value(DOCTYPE, FIELD, NEW_DEFAULT)
		return

	try:
		current = int(row[0][0] or 0)
	except (TypeError, ValueError):
		# A non-numeric value is somebody's typo, not a default. Leave it where
		# it is and let the clamp in _relay_timeout_ms deal with it, rather than
		# silently replacing something a person will go looking for.
		return

	if current == OLD_DEFAULT:
		frappe.db.set_single_value(DOCTYPE, FIELD, NEW_DEFAULT)
