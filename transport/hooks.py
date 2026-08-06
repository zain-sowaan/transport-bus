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
		"filters": {"module": "Transport"},
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
	{
		# Matched to Trip Expense.expense_type's Select options exactly -
		# Expense Claim Type's autoname is field:expense_type, so the name
		# IS the mapping, no lookup table needed.
		"doctype": "Expense Claim Type",
		# "Others" is deliberately excluded: on this site it's a pre-existing
		# site record this app doesn't own, already configured with real
		# per-company GL accounts - fixture-syncing it would silently
		# overwrite that live config with whatever ships in this repo.
		"filters": [["name", "in", ["Fuel", "Salik / Toll", "Parking", "Maintenance"]]],
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
# doctype_js = {"doctype" : "public/js/doctype.js"}
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

# before_install = "transport.install.before_install"
# after_install = "transport.install.after_install"

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
	"cron": {
		# Every 5 minutes: fine enough granularity for a 20-min trip reminder
		# and a 5-min driver-confirmation escalation window without being a
		# noisy background job.
		"*/5 * * * *": [
			"transport.transport.driver_portal.send_trip_reminders",
			"transport.transport.driver_portal.escalate_unconfirmed_trips",
		],
	},
}

# Document Events
# ---------------
# `lost_reasons` already exists natively on Opportunity and Quotation, but
# only `declare_enquiry_lost` enforces it — a plain save with status=Lost
# does not. This makes it mandatory, per the source document.

doc_events = {
	"Opportunity": {"validate": "transport.transport.crm_controls.enforce_lost_reason"},
	"Quotation": {"validate": "transport.transport.crm_controls.enforce_lost_reason"},
	"Sales Order": {
		"validate": "transport.transport.crm_controls.sync_transport_project",
		"on_submit": "transport.transport.crm_controls.notify_operations_on_sales_order",
	},
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

