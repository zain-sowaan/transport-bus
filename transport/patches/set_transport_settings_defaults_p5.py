import frappe


def execute():
	"""Phase 5 addition to Transport Settings - the original
	set_transport_settings_defaults patch already ran on existing sites and
	won't fire again, so new Single-doctype defaults need their own patch
	(see reference_frappe_single_doctype_defaults)."""
	settings = frappe.get_single("Transport Settings")

	defaults = {
		"feedback_escalation_rating": 2.5,
		"blacklist_threshold_points": 10,
		"black_points_per_fine": 2,
	}

	changed = False
	for fieldname, value in defaults.items():
		if not settings.get(fieldname):
			settings.set(fieldname, value)
			changed = True

	if changed:
		settings.save()
