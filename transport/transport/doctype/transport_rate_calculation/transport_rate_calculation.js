// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Rate Calculation", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.quotation) {
			return;
		}

		if (!frm.doc.customer || !frm.doc.total_final_price) {
			return;
		}

		frm.add_custom_button(__("Create Quotation"), () => {
			frappe.confirm(
				__("Create a Quotation for {0} covering {1} vehicle(s), totalling {2}?", [
					frm.doc.customer,
					(frm.doc.vehicles || []).length,
					format_currency(frm.doc.total_final_price),
				]),
				() => {
					frm.call("create_quotation").then((r) => {
						if (!r.exc) {
							frm.reload_doc();
							frappe.set_route("Form", "Quotation", r.message);
						}
					});
				}
			);
		});
	},
});
