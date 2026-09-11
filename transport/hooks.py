app_name = "transport"
app_title = "Sowaan Transport"
app_publisher = "M. Zain Ul Abideen"
app_description = "This app is used to account the transport system of a company, and it requires fleetify app to work"
app_email = "m.zain00727@gmail.com"
app_license = "mit"

# Fixtures
# --------
# Custom Fields on Rental Vehicle (fleetify) are shipped here rather than
# edited directly on fleetify's own doctype JSON, so they only land on sites
# where this app is installed.

fixtures = [
	{
		"doctype": "Custom Field",
		# Everything EXCEPT the fields on Place, which live in
		# fixtures/custom_field_place.json instead. Not tidiness - blast radius.
		#
		# Frappe imports a fixture file record by record and STOPS THE FILE at
		# the first record whose DocType is missing. utils/fixtures.py catches
		# the DoesNotExistError per file and prints "Skipping fixture syncing
		# from the file ..." to stdout, which nobody reads during a deploy.
		# Records before the failure are already written; every record after it
		# is lost. Measured both ways: with the bad record last, 2 of 2 good
		# fields survived; with it first, 0 of 2.
		#
		# `Place` belongs to fleetify, and on a site whose fleetify predates it
		# those three fields were records #1, #2 and #3 of 59 - so the file died
		# on its first record and took all 56 others with it, across eight
		# doctypes that have nothing to do with fleetify. A UAT site ran a month
		# that way, every report and list view reading one of those fields
		# failing with an unknown-column error naming the column and not the
		# cause.
		#
		# Note how fragile that was: export orders by "idx asc, creation asc",
		# so where the Place records land is incidental. A re-export could move
		# them last and mask the whole thing - which is the other reason not to
		# leave this to luck.
		#
		# Split this way, the same missing dependency costs 3 fields, the other
		# 56 install, and the skip message names the file that is actually
		# about Place.
		#
		# **The exclusion has to be here as well as in the file**, because
		# `bench export-fixtures` regenerates custom_field.json from this
		# filter and would pull the Place fields straight back in.
		"filters": [["module", "=", "Transport"], ["dt", "!=", "Place"]],
	},
	{
		"doctype": "Role",
		"filters": [
			[
				"role_name",
				"in",
				["Transport Driver", "Transport In-Charge", "Transport Operations", "Transport Accounts"],
			]
		],
	},
	{
		"doctype": "Client Script",
		"filters": {"module": "Transport"},
	},
]

# Apps
# ------------------

# hrms added in Phase 4: Trip Expense.book_to_expense_claim() creates a real
# HR Expense Claim (Expense Claim Type master data too) once Accounts books
# an approved expense - Driver/Employee themselves are core erpnext, already
# an implicit dependency via fleetify.
required_apps = ["fleetify", "hrms"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "transport",
# 		"logo": "/assets/transport/logo.png",
# 		"title": "Transport",
# 		"route": "/transport",
# 		"has_permission": "transport.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/transport/css/transport.css"
# app_include_js = "/assets/transport/js/transport.js"

# include js, css files in header of web template
# web_include_css = "/assets/transport/css/transport.css"
# web_include_js = "/assets/transport/js/transport.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "transport/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
#
# The desk half of the browser extension. A doctype_js file, deliberately, and
# not a Client Script fixture: Frappe merges every Client Script for a doctype
# into one new Function(), so a second copy declaring the same top-level const
# takes down every script on the form. Shipped this way it is ordinary app code
# - versioned, reviewed and deployed with the rest - and it cannot collide.
doctype_js = {"Traffic Fine Portal": "public/js/traffic_fine_portal_fetch.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "transport/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "transport.utils.jinja_methods",
# 	"filters": "transport.utils.jinja_filters"
# }

# Installation
# ------------

# Expense Claim Types are seeded here rather than shipped as a fixture:
# fixture sync deletes and re-inserts, wiping the per-company GL accounts
# mapped against them. transport/patches/seed_expense_claim_types.py covers
# sites that already have this app; install marks patches done without
# running them, so a new site needs this hook too.
after_install = "transport.setup.after_install"

# Custom Fields are re-checked after every migrate because fixture sync drops a
# whole file when one DocType in it is missing, and says so only on stdout. See
# transport/setup.py ensure_custom_fields for what that cost and how.
after_migrate = "transport.setup.after_migrate"

# before_install = "transport.install.before_install"

# Uninstallation
# ------------

# before_uninstall = "transport.uninstall.before_uninstall"
# after_uninstall = "transport.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "transport.utils.before_app_install"
# after_app_install = "transport.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "transport.utils.before_app_uninstall"
# after_app_uninstall = "transport.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "transport.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"transport.transport.doctype.compliance_document.compliance_document.refresh_status"
	],
	"daily_long": [
		# Gated twice over: does nothing unless Transport Settings enables it,
		# and then only runs against portals holding a valid authorization.
		"transport.transport.fine_sync.service.run_scheduled_syncs"
	],
	"cron": {
		# Every 5 minutes: fine enough granularity for a 20-min trip reminder
		# and a 5-min driver-confirmation escalation window without being a
		# noisy background job.
		"*/5 * * * *": [
			"transport.transport.driver_portal.send_trip_reminders",
			"transport.transport.driver_portal.escalate_unconfirmed_trips",
		],
		# Hourly, on the hour. NOT "*/45": cron has no notion of "every 45
		# minutes" - the step applies within the 0-59 minute field, so */45
		# fires at :00 and :45 and the real gaps alternate 45 then 15. The
		# short leg is what turned a slow fetch into back-to-back sweeps
		# against the portal, which is how the IP's reCAPTCHA score was burned.
		#
		# An hour also has to be long enough that the previous sweep is
		# normally finished: the fetch budget alone defaults to 30 minutes.
		# Overlap is still handled - the job is deduplicated - but a cadence
		# that relies on the dedup for every fire is one bug away from the
		# same incident.
		#
		# How long a banked TAMM session actually lasts is NOT known and is
		# deliberately not asserted here. Portal Session Observation is
		# accumulating the answer from runs we already make; until it has both
		# ends, no figure belongs in a comment or in front of a client.
		"0 * * * *": [
			"transport.transport.fine_sync.service.run_scheduled_operator_syncs",
		],
	},
}

# Document Events
# ---------------
# `lost_reasons` already exists natively on Opportunity and Quotation, but
# only `declare_enquiry_lost` enforces it — a plain save with status=Lost
# does not. This makes it mandatory, per the source document.

doc_events = {
	"Opportunity": {
		"validate": [
			"transport.transport.crm_controls.enforce_lost_reason",
			# The requested shuttle schedule lives on Opportunity as a Custom
			# Field table - tidied and checked here, since a child doctype's own
			# validate() is not run by the parent's save.
			"transport.transport.crm_controls.normalize_enquiry_schedule",
		]
	},
	"Quotation": {"validate": "transport.transport.crm_controls.enforce_lost_reason"},
	"Sales Order": {
		"validate": "transport.transport.crm_controls.sync_transport_project",
		"on_submit": "transport.transport.crm_controls.notify_operations_on_sales_order",
	},
	# The per-shift contract terms live on Project as a Custom Field table, so
	# their coherence has to be checked from the parent - Frappe does not run a
	# child doctype's validate() during the parent's save.
	"Project": {
		"validate": "transport.transport.doctype.transport_project_shift.transport_project_shift.validate_project_shifts"
	},
	# Rental Vehicle is fleetify's doctype - extended here via Custom Field
	# fixtures plus this event, never by editing their files.
	"Rental Vehicle": {"validate": "transport.transport.vehicle_plate.normalize_plate"},
}

# Permissions
# -----------
# A driver's user only ever sees their own Trips/Trip Expenses, and a
# customer's user only ever sees their own Trips/Customer Requests - every
# state change goes through a whitelisted method in driver_portal.py /
# customer_portal.py instead of a raw save. transport/transport/permissions.py
# dispatches to whichever portal module owns the calling user's role.

permission_query_conditions = {
	"Trip": "transport.transport.permissions.get_trip_permission_query_conditions",
	"Trip Expense": "transport.transport.permissions.get_trip_expense_permission_query_conditions",
	"Customer Request": "transport.transport.permissions.get_customer_request_permission_query_conditions",
	# Temporary, and it belongs in THIS dict rather than a second one near the
	# commented example above: hooks.py is an ordinary module, so a second
	# `permission_query_conditions = {...}` does not merge, it rebinds - the
	# earlier one is discarded in silence and its hook simply never runs.
	# Remove this line to show all thirteen portals again.
	"Traffic Fine Portal": "transport.transport.portal_visibility.portal_query_conditions",
}

has_permission = {
	"Trip": "transport.transport.permissions.get_trip_has_permission",
	"Trip Expense": "transport.transport.permissions.get_trip_expense_has_permission",
	"Customer Request": "transport.transport.permissions.get_customer_request_has_permission",
}

# Testing
# -------

# before_tests = "transport.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "transport.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "transport.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["transport.utils.before_request"]
# after_request = ["transport.utils.after_request"]

# Job Events
# ----------
# before_job = ["transport.utils.before_job"]
# after_job = ["transport.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"transport.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

