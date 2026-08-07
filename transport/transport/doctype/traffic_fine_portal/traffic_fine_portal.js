// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

frappe.ui.form.on("Traffic Fine Portal", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Capture comes before any sync: no portal's request/response contract
		// has been recorded yet, so there is nothing for a fetcher to drive.
		if (frm.doc.has_written_authorization) {
			frm.add_custom_button(__("Capture Page"), () => {
				frappe.dom.freeze(__("Opening {0}...", [frm.doc.portal_name]));
				frappe
					.call({
						method: "transport.transport.fine_sync.service.capture_portal_page",
						args: { portal: frm.doc.name },
					})
					.then((r) => {
						frappe.dom.unfreeze();
						if (!r.message) return;
						const d = r.message;
						const fields = (d.form_fields || [])
							.map((f) => `<li><code>${frappe.utils.escape_html(f.name || f.id || f.tag)}</code> (${f.type || f.tag})</li>`)
							.join("");
						frappe.msgprint({
							title: __("Capture Result"),
							indicator: d.captcha_detected ? "orange" : "blue",
							message: `
								<p><b>${__("Final URL")}:</b> ${frappe.utils.escape_html(d.final_url)}</p>
								<p><b>${__("Redirected")}:</b> ${d.redirected ? __("Yes") : __("No")}</p>
								<p><b>${__("CAPTCHA detected")}:</b> ${d.captcha_detected ? __("Yes - this portal needs a person") : __("No")}</p>
								<p><b>${__("Form fields")}:</b></p><ul>${fields || "<li>none</li>"}</ul>
								${d.html_saved_to ? `<p><a href="${d.html_saved_to}" target="_blank">${__("Saved page")}</a></p>` : ""}`,
						});
					})
					.catch(() => frappe.dom.unfreeze());
			});
		}

		if (frm.doc.is_enabled) {
			frm.add_custom_button(__("Run Sync"), () => {
				frappe.confirm(
					__("Fetch fines for the whole fleet from {0}?", [frm.doc.portal_name]),
					() => {
						frappe.dom.freeze(__("Fetching..."));
						frappe
							.call({
								method: "transport.transport.fine_sync.service.run_sync",
								args: { portal: frm.doc.name },
							})
							.then((r) => {
								frappe.dom.unfreeze();
								if (r.message) frappe.set_route("Form", "Traffic Fine Sync Run", r.message.sync_run);
							})
							.catch(() => frappe.dom.unfreeze());
					}
				);
			});
		}

		if (!frm.doc.has_written_authorization) {
			frm.dashboard.set_headline(
				__("This portal cannot be used until written authorization is recorded.")
			);
		}
	},
});
