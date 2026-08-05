// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Timesheet", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Populate Trips"), () => {
				frm.call("populate_trips").then((r) => {
					frm.reload_doc();
					frappe.msgprint(__("Pulled {0} approved trip(s).", [r.message ? r.message.pulled : 0]));
				});
			});

			if (frm.doc.trips && frm.doc.trips.length) {
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
