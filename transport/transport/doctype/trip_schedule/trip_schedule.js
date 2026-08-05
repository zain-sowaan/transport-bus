// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Trip Schedule", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Active") {
			return;
		}

		frm.add_custom_button(__("Generate Trips"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Generate Trips"),
				fields: [
					{
						fieldname: "from_date",
						fieldtype: "Date",
						label: __("From Date"),
						description: __(
							"Leave blank to continue from Last Generated Till ({0})",
							[frm.doc.last_generated_till || frm.doc.effective_from]
						),
					},
					{
						fieldname: "to_date",
						fieldtype: "Date",
						label: __("To Date"),
						description: __("Leave blank to generate up to Effective To"),
					},
				],
				primary_action_label: __("Generate"),
				primary_action(values) {
					frm.call("generate_trips", {
						from_date: values.from_date,
						to_date: values.to_date,
					}).then((r) => {
						dialog.hide();
						if (r.exc) {
							return;
						}
						const result = r.message || {};
						frm.reload_doc();
						frappe.msgprint({
							title: __("Trips Generated"),
							indicator: result.created ? "green" : "orange",
							message:
								result.message ||
								__("{0} trip(s) created between {1} and {2}.", [
									result.created,
									result.from_date,
									result.to_date,
								]),
						});
					});
				},
			});
			dialog.show();
		});
	},
});
