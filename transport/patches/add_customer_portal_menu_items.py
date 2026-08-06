import frappe


def execute():
	"""Portal Settings is a shared Single doctype - fixturing the whole thing
	would blanket-overwrite other apps' menu items on every migrate, so add
	these two rows idempotently instead of exporting the singleton."""
	settings = frappe.get_single("Portal Settings")
	existing_routes = {row.route for row in settings.menu}

	new_items = [
		{"title": "My Trips", "route": "/customer", "reference_doctype": "Trip", "role": "Customer", "enabled": 1},
		{
			"title": "My Requests",
			"route": "/customer/requests",
			"reference_doctype": "Customer Request",
			"role": "Customer",
			"enabled": 1,
		},
	]

	changed = False
	for item in new_items:
		if item["route"] not in existing_routes:
			settings.append("menu", item)
			changed = True

	if changed:
		settings.save()
