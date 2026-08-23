# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Master data this app needs to function, created once and then left alone.

The Expense Claim Types below used to ship as a fixture. Fixture sync does not
merge - `frappe.modules.import_file.delete_old_doc` deletes the record and
re-inserts it from the JSON - so every `bench migrate` wiped the per-company GL
accounts an implementer had mapped against these types, and
Trip Expense.book_to_expense_claim() then throws because hrms cannot resolve an
account for the claim. Seeding create-if-missing instead means this app
supplies the types once and never overwrites site configuration it doesn't own.

Two entry points, because neither covers both cases on its own: the patch
handles sites that already have this app, and after_install handles new ones -
`frappe.installer.set_all_patches_as_completed` records an app's patches as
done at install time without running them.
"""

import frappe

# Matched to Trip Expense.expense_type's Select options exactly - Expense Claim
# Type autonames on field:expense_type, so the name IS the mapping and no
# lookup table is needed.
#
# "Others" is deliberately absent: hrms already seeds it (hrms/setup.py
# make_fixtures), and on an existing site it is typically configured with
# company GL accounts this app has no business touching.
EXPENSE_CLAIM_TYPES = ("Fuel", "Salik / Toll", "Parking", "Maintenance")


def after_install():
	ensure_expense_claim_types()


def ensure_expense_claim_types():
	"""Create the expense types this app books against, if they are missing.

	Deliberately never updates one that already exists: the `accounts` child
	table on an Expense Claim Type is per-company site configuration, and
	rewriting it is the exact bug this function replaced.
	"""
	for expense_type in EXPENSE_CLAIM_TYPES:
		if frappe.db.exists("Expense Claim Type", expense_type):
			continue

		frappe.get_doc(
			{
				"doctype": "Expense Claim Type",
				"expense_type": expense_type,
				"description": f"Transport Trip Expense - {expense_type}",
			}
		).insert(ignore_if_duplicate=True)
