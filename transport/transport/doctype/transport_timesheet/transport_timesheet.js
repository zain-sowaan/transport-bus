// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Timesheet", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		// Which table the Timesheet bills from is the Project's Billing Basis,
		// fetched onto billing_basis. The two buttons are mutually exclusive
		// for the same reason the two sections are: a monthly-per-vehicle
		// contract has no trips, and a per-trip contract has no deployments.
		const monthly = frm.doc.billing_basis === "Monthly per Vehicle";

		if (frm.doc.status === "Draft") {
			if (monthly) {
				frm.add_custom_button(__("Get Vehicles"), () => {
					const existing = (frm.doc.monthly_deployments || []).length;
					const run = () =>
						frm.call("populate_vehicle_deployments").then((r) => {
							frm.reload_doc();
							frappe.msgprint(
								__("Pulled {0} vehicle line(s) from the Sales Order.", [
									r.message ? r.message.pulled : 0,
								])
							);
						});

					// The server replaces the table outright, so warn before
					// discarding readings someone has already typed in.
					if (existing) {
						frappe.confirm(
							__(
								"This replaces {0} deployment row(s), including any odometer, fuel or replacement details entered on them. Continue?",
								[existing]
							),
							run
						);
					} else {
						run();
					}
				});
			} else {
				frm.add_custom_button(__("Populate Trips"), () => {
					frm.call("populate_trips").then((r) => {
						frm.reload_doc();
						frappe.msgprint(__("Pulled {0} approved trip(s).", [r.message ? r.message.pulled : 0]));
					});
				});
			}

			const rows = monthly ? frm.doc.monthly_deployments : frm.doc.trips;
			if (rows && rows.length) {
				frm.add_custom_button(__("Send for Approval"), () => {
					frm.call("approve_by_operations").then(() => frm.reload_doc());
				});
			}
		}

		if (frm.doc.status === "Pending Customer Approval") {
			frm.add_custom_button(__("Record Customer Approval"), () => {
				const dialog = new frappe.ui.Dialog({
					title: __("Record Customer Approval"),
					fields: [
						{
							fieldname: "approved_by",
							fieldtype: "Data",
							label: __("Customer Signatory Name"),
							reqd: 1,
						},
						{
							fieldname: "attachment",
							fieldtype: "Attach",
							label: __("Signed Copy"),
						},
					],
					primary_action_label: __("Save"),
					primary_action(values) {
						frm.call("record_customer_approval", {
							approved_by: values.approved_by,
							attachment: values.attachment,
						}).then(() => {
							dialog.hide();
							frm.reload_doc();
						});
					},
				});
				dialog.show();
			});
		}

		if (frm.doc.status === "Customer Approved" && !frm.doc.sales_invoice) {
			frm.add_custom_button(__("Create Sales Invoice"), () => {
				frappe.confirm(__("Create a Sales Invoice from Sales Order {0}?", [frm.doc.sales_order]), () => {
					frm.call("create_sales_invoice").then((r) => {
						frm.reload_doc();
						if (r.message) {
							frappe.set_route("Form", "Sales Invoice", r.message);
						}
					});
				});
			});
		}

		if (frm.doc.sales_invoice) {
			frm.add_custom_button(__("Sales Invoice"), () => {
				frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice);
			}, __("View"));
		}
	},
});
