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

import json
import os

import click

import frappe
from frappe import _

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
	# Runs before `sync_fixtures` does (frappe/installer.py install_app), so on
	# a site missing one of the DocTypes this app attaches to, the fields are
	# created and the gap reported without waiting for the first migrate.
	ensure_custom_fields()


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


def after_migrate():
	ensure_custom_fields()


def ensure_custom_fields():
	"""Create the Custom Fields fixture sync dropped, and say what it dropped.

	`frappe/utils/fixtures.py` imports one fixture FILE inside a single try
	block, and swallows a missing DocType with nothing but a line on stdout:

	    except (ImportError, frappe.DoesNotExistError) as e:
	        print(f"Skipping fixture syncing from the file {fname}. Reason: {e}")

	So a DocType this app does not own being absent abandons the rest of that
	file. It cost a UAT site all 59 of this app's Custom Fields: `Place` belongs
	to fleetify, that server was running a fleetify old enough to predate it,
	and the three Place records happen to sort first in the export. Roles and
	Client Scripts survived - fixture files sync in alphabetical order and each
	gets its own try block - so the only symptom was a number card failing with
	"Unknown column 'tabSales Invoice.transport_timesheet'", which points
	nowhere near the cause.

	Creates only what is missing, so it costs nothing when fixture sync worked,
	and it never rewrites a field an implementer has adjusted - the same rule
	`ensure_expense_claim_types` follows above, for the same reason.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	missing_doctypes, to_create = {}, {}
	for df in _custom_field_definitions():
		doctype, fieldname = df.get("dt"), df.get("fieldname")
		if not (doctype and fieldname):
			continue

		if not frappe.db.exists("DocType", doctype):
			missing_doctypes.setdefault(doctype, []).append(fieldname)
			continue

		if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": fieldname}):
			continue

		# Order within a DocType is the export's order, which is the order the
		# fields were added - so an `insert_after` never points at a field this
		# loop has not created yet.
		to_create.setdefault(doctype, []).append(_field_definition(df))

	if to_create:
		create_custom_fields(to_create, update=False)
		frappe.db.commit()

	if missing_doctypes:
		_report_missing_doctypes(missing_doctypes)


def _custom_field_definitions():
	"""The shipped fixture, read as data. Never the site's own Custom Fields."""
	path = frappe.get_app_path("transport", "fixtures", "custom_field.json")
	if not os.path.exists(path):
		return []

	with open(path) as fixture:
		return json.load(fixture)


def _field_definition(df):
	"""The fixture record minus the bookkeeping that belongs to the exporting site."""
	bookkeeping = {"doctype", "name", "docstatus", "idx", "creation", "modified", "modified_by", "owner"}
	return {key: value for key, value in df.items() if key not in bookkeeping}


def _report_missing_doctypes(missing_doctypes):
	"""Say which fields could not be created, and what to do about it.

	Loudly, in two places: stdout so it is in the deploy output next to the
	migrate that skipped them, and the Error Log so it is still readable from
	the desk days later by whoever hits the unknown-column error.
	"""
	lines = [
		_("{0} is not installed on this site - {1} field(s) skipped: {2}").format(
			doctype, len(fieldnames), ", ".join(sorted(fieldnames))
		)
		for doctype, fieldnames in sorted(missing_doctypes.items())
	]
	message = "\n".join(
		[_("These Transport Custom Fields could not be created, because the DocType they "
		   "attach to does not exist on this site."), ""]
		+ lines
		+ ["", _("Install or update the app that owns that DocType, then run bench migrate "
		         "again. Until then, any report, list view or card that reads one of these "
		         "fields fails with an unknown-column database error that names the column "
		         "and not the cause.")]
	)

	click.secho(message, fg="yellow")
	frappe.log_error(title="Transport: Custom Fields skipped - DocType missing", message=message)
	frappe.db.commit()
