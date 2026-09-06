import frappe

TABLE = "tabTransport Rate Calculation Vehicle"
COLUMN = "margin_percent"


def execute():
	"""Empty out margin_percent so it can become a Percent column.

	Runs pre_model_sync, which is the only moment this works: the field was
	first added by hand as Data, so the column is varchar and every existing row
	holds NULL. The DocType now declares it Percent, and Frappe's ALTER makes
	the column `decimal(21,9) NOT NULL DEFAULT 0`. Under MariaDB's strict mode
	that conversion refuses a NULL outright:

	    pymysql.err.DataError: (1265, "Data truncated for column
	    'margin_percent' at row 1")

	which fails the whole migrate, not just this table. Writing a zero into
	every NULL first is what lets the cast through.

	Nothing of value is discarded. The column never held a figure: the field was
	added in the UI and the code that was meant to fill it wrote to
	`line_margin_percent`, a name no field has ever had, so every assignment was
	dropped on save. The real values are recomputed on the next save of each
	document anyway, since calculate() derives them.

	Guarded at each step so it is a no-op on a site that never had the Data
	version - a fresh site simply creates the decimal column and never comes
	near this.
	"""
	if not frappe.db.table_exists(TABLE.replace("tab", "", 1)):
		return

	column_type = frappe.db.sql(
		"""
		select column_type from information_schema.columns
		where table_schema = database() and table_name = %s and column_name = %s
		""",
		(TABLE, COLUMN),
	)
	if not column_type:
		# Never added on this site. The DocType will create it as decimal.
		return
	if not str(column_type[0][0]).startswith("varchar"):
		# Already numeric - either converted before, or created that way.
		return

	frappe.db.sql(f"update `{TABLE}` set `{COLUMN}` = '0' where `{COLUMN}` is null or `{COLUMN}` = ''")
