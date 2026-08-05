import frappe


def execute():
	"""Single DocTypes don't persist their JSON `default` values to the DB
	until the form is opened and saved once — seed them here so
	`frappe.db.get_single_value` returns real numbers from the first migrate,
	not None/0."""
	settings = frappe.get_single("Transport Settings")

	defaults = {
		"lmv_daily_trip_limit": 5,
		"hmv_daily_trip_limit": 4,
		"fatigue_max_driving_hours": 12,
		"trip_alert_lead_minutes": 20,
		"driver_confirmation_window_minutes": 5,
		"service_interval_km_first": 5000,
		"service_interval_km_second": 10000,
		"compliance_expiry_soon_days": 30,
		"contract_expiry_alert_days": 30,
		"enforce_minimum_margin": 1,
	}

	changed = False
	for fieldname, value in defaults.items():
		if not settings.get(fieldname):
			settings.set(fieldname, value)
			changed = True

	if changed:
		settings.save()
