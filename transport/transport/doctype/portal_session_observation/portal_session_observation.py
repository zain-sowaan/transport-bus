# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PortalSessionObservation(Document):
	"""One data point in "how long does a banked portal sign-in last?".

	Deliberately a record of something that already happened rather than a
	probe. The obvious way to measure a session's life is to poll the portal
	until it stops answering, but every poll is another automated hit from an
	address whose reputation is the reason CAPTCHAs started appearing at all -
	the measurement would degrade the thing it measures. Every sync already
	asks the portal to rule on the session; this only writes down the answer.
	"""

	pass
