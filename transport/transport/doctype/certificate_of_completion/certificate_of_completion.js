// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Certificate of Completion", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(__("Mark Issued"), () => {
				frm.call("mark_issued").then(() => frm.reload_doc());
			});
		}

		if (frm.doc.status === "Issued") {
			frm.add_custom_button(__("Record Signed Copy"), () => {
				const dialog = new frappe.ui.Dialog({
					title: __("Record Signed Copy"),
					fields: [
						{
							fieldname: "signed_copy",
							fieldtype: "Attach",
							label: __("Signed Copy"),
							reqd: 1,
						},
					],
					primary_action_label: __("Save"),
					primary_action(values) {
						frm.call("record_signed_copy", { signed_copy: values.signed_copy }).then(() => {
							dialog.hide();
							frm.reload_doc();
						});
					},
				});
				dialog.show();
			});
		}
	},
});
