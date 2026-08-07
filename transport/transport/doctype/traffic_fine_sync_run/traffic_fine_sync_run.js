// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Traffic Fine Sync Run", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status === "Completed with Errors") {
			frm.dashboard.set_headline(
				__("{0} vehicle(s) could not be queried. Their fines are unknown, not zero.", [
					frm.doc.vehicles_failed,
				])
			);
		}

		if (frm.doc.fines_new) {
			frm.add_custom_button(__("Promote Staged Fines"), () => {
				frm.call("promote_staged_fines").then((r) => {
					if (r.message) {
						frappe.msgprint(
							__("{0} fine(s) created. Black points are held pending review.", [
								r.message.promoted,
							])
						);
					}
				});
			});
		}

		frm.add_custom_button(__("View Staged Fines"), () => {
			frappe.set_route("List", "Traffic Fine Staging", { sync_run: frm.doc.name });
		});
	},
});
