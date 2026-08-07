// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Traffic Fine Staging", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status === "New") {
			frm.add_custom_button(__("Promote to Fine"), () => {
				frm.call("promote").then(() => frm.reload_doc());
			});
			frm.add_custom_button(__("Ignore"), () => {
				frm.call("ignore").then(() => frm.reload_doc());
			});
		}

		if (frm.doc.status === "Duplicate" && frm.doc.transport_traffic_fine) {
			frm.dashboard.set_headline(
				__("Already recorded as {0}.", [frm.doc.transport_traffic_fine])
			);
		}
	},
});
