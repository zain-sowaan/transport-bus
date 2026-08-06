// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Customer Request", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status === "Resolved") {
			return;
		}

		frm.add_custom_button(__("Respond"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Respond to Request"),
				fields: [
					{ fieldname: "response", fieldtype: "Small Text", label: __("Response"), reqd: 1 },
					{
						fieldname: "status",
						fieldtype: "Select",
						label: __("Status"),
						options: "In Progress\nResolved",
						default: "Resolved",
						reqd: 1,
					},
				],
				primary_action_label: __("Save"),
				primary_action(values) {
					frm.call("respond", { response: values.response, status: values.status }).then(() => {
						dialog.hide();
						frm.reload_doc();
					});
				},
			});
			dialog.show();
		});
	},
});
