import json
import os

import frappe

DOCTYPE = "Traffic Fine Portal"

# Fields the seed owns. Everything NOT listed here is operational state that
# belongs to the site, and this patch must never touch it on an existing
# record - see the docstring below.
SEED_FIELDS = (
	"portal_name", "authority", "emirate", "access_route", "scope",
	"public_form_url", "tos_url", "tos_note", "authentication", "captcha_type",
	"required_inputs", "confidence", "last_verified", "scope_note", "notes",
	"backend_api_status", "fetch_mode", "fetcher_key",
)


def execute():
	"""Seed the portal registry, creating only what is missing.

	This started life as a fixture and had to be moved, because a fixture
	overwrites the whole document on every `bench migrate` (import runs with
	force=True). That silently wiped real operational state: the written
	authorization recorded against MOI and SRTA, and the findings written back
	onto them by a page capture, were all reset to the shipped defaults by the
	next migrate.

	The registry mixes two kinds of data. Reference data about public
	government services - URLs, scope, required inputs - is ours to ship.
	Authorization, enablement and capture results belong to the site and are
	earned, not shipped. Seeding create-only keeps the two apart: an existing
	portal is left completely alone, so nothing a person recorded can be
	clobbered by a deployment.

	If reference data genuinely needs to change later, that is a new patch
	updating those specific fields deliberately - not a blanket overwrite.
	"""
	path = os.path.join(
		frappe.get_app_path("transport"), "data", "traffic_fine_portals.json"
	)
	if not os.path.exists(path):
		return

	with open(path) as f:
		portals = json.load(f)

	created = 0
	for row in portals:
		name = row.get("name") or row.get("portal_name")
		if not name or frappe.db.exists(DOCTYPE, name):
			continue

		doc = frappe.new_doc(DOCTYPE)
		for field in SEED_FIELDS:
			if row.get(field) is not None:
				doc.set(field, row[field])
		# Never seeded as authorized or enabled: both have to be granted by a
		# person, per portal, and recorded with a reference.
		doc.is_enabled = 0
		doc.has_written_authorization = 0
		doc.flags.ignore_permissions = True
		doc.insert()
		created += 1

	if created:
		frappe.db.commit()
		print(f"Seeded {created} traffic fine portal(s).")
