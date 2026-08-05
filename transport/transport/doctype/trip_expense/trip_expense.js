// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Trip Expense", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		if (frm.doc.status === "Pending") {
			frm.add_custom_button(__("Approve"), () => {
				frm.call("approve").then(() => frm.reload_doc());
			});
			frm.add_custom_button(__("Reject"), () => {
				frm.call("reject").then(() => frm.reload_doc());
			});
		}

		if (frm.doc.status === "Approved" && !frm.doc.expense_claim) {
			frm.add_custom_button(__("Book to Expense Claim"), () => {
				frappe.confirm(
					__("Create an Expense Claim for {0} - {1}?", [frm.doc.driver, format_currency(frm.doc.amount)]),
					() => {
						frm.call("book_to_expense_claim").then((r) => {
							frm.reload_doc();
							if (r.message) {
								frappe.set_route("Form", "Expense Claim", r.message);
							}
						});
					}
				);
			});
		}

		if (frm.doc.expense_claim) {
			frm.add_custom_button(__("Expense Claim"), () => {
				frappe.set_route("Form", "Expense Claim", frm.doc.expense_claim);
			}, __("View"));
		}
	},
});
