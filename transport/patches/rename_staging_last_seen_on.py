# Copyright (c) 2026, Sowaan and contributors
# For license information, please see license.txt

"""Rename Traffic Fine Staging.last_seen_on, which Frappe silently hid.

`frappe.model.db_query.set_optional_columns` drops a requested field when an
"optional" column name is a SUBSTRING of it and that optional column is absent
from the table:

    to_remove.extend(fld for f in optional_fields if f in fld and f not in self.columns)

`_seen` is one of those optional names, and it is a substring of `last_seen_on`.
This table has no `_seen` column, so every DatabaseQuery - get_all, get_list,
reportview and therefore the list view itself - deleted the field from its
SELECT and returned rows without it. The column held correct data the whole
time; only raw SQL and the query builder could see it.

`last_fetched_on` collides with none of those names.
"""

import frappe


def execute():
	frappe.reload_doc("transport", "doctype", "traffic_fine_staging")

	columns = frappe.db.get_table_columns("Traffic Fine Staging")
	if "last_seen_on" not in columns:
		return

	# Carry the history over before dropping it - these stamps are the evidence
	# that earlier syncs ran.
	frappe.db.sql(
		"""update `tabTraffic Fine Staging`
		   set last_fetched_on = last_seen_on
		   where last_seen_on is not null and last_fetched_on is null"""
	)
	frappe.db.sql_ddl("alter table `tabTraffic Fine Staging` drop column `last_seen_on`")

	# Without this, a desk session still holding the old metadata asks for a
	# column that no longer exists and gets "Field not permitted in query:
	# tabTraffic Fine Staging.last_seen_on" on the next list refresh. The rename
	# is only half the migration; invalidating what remembers the old name is
	# the other half. Users with the list already open still need one hard
	# reload, since the browser caches the doctype too.
	frappe.clear_cache(doctype="Traffic Fine Staging")
