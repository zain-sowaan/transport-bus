// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

// Every button here is gated on a capability the server reports, never on the
// document alone. Guessing from the document is what put "Run Sync" on a portal
// with no fetcher and "Capture Page" on one with no public form URL: both
// appeared to work, then failed with a message about something else entirely -
// in one case a missing traffic file number, which sent the operator off to add
// a credential that could not have helped.

frappe.ui.form.on("Traffic Fine Portal", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (!frm.doc.has_written_authorization) {
			// Authorization governs looking at the portal at all - a capture
			// reads it just as a fetch does - so nothing is offered without it.
			frm.set_intro(
				__("This portal cannot be used until written authorization is recorded."),
				"red"
			);
			return;
		}

		frappe.call({
			method: "transport.transport.fine_sync.service.get_portal_session_status",
			args: { portal: frm.doc.name },
			callback: (r) => {
				const s = r.message || {};
				// An empty message clears the banner rather than leaving a stale one.
				frm.set_intro(s.message || "", s.indicator);

				if (s.fetch_implemented) add_fetch_button(frm);
				// Capture only stands in for a fetch that does not exist yet.
				else if (s.can_capture) add_session_capture_button(frm);
				if (s.can_run_sync) add_run_sync_button(frm);
				if (s.can_capture_public) add_public_capture_button(frm);
			},
		});
	},
});

// Operator-assisted fetch: opens a window, a person signs in, the whole traffic
// file comes back in one read.
function add_fetch_button(frm) {
	frm.add_custom_button(__("Fetch Fines Now"), () => {
		frappe.confirm(
			__(
				"A browser window will open on the server for you to sign in. The whole traffic file is fetched in one go. Continue?"
			),
			() => queue(frm, "enqueue_operator_assisted_sync", __("Fetch Queued"))
		);
	});
}

// Capture-first: for a portal whose pages behind the sign-in have never been
// seen, so no parse can be written yet.
function add_session_capture_button(frm) {
	frm.add_custom_button(__("Capture Portal Pages"), () => {
		frappe.confirm(
			__(
				"A browser window will open. Sign in yourself, then walk to the fines list and page through it - every page you visit is recorded so the fetch can be written from the real structure. No fines are collected. Continue?"
			),
			() => queue(frm, "enqueue_portal_capture", __("Capture Queued"))
		);
	});
}

// Unattended fleet sync. Only ever offered where a fetcher exists AND the
// portal is enabled for scheduled access.
function add_run_sync_button(frm) {
	frm.add_custom_button(__("Run Sync"), () => {
		frappe.confirm(__("Fetch fines for the whole fleet from {0}?", [frm.doc.portal_name]), () => {
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
		});
	});
}

// Records a portal's PUBLIC form - the step that says what a page asks for and
// whether it puts a CAPTCHA in the way. Distinct from Capture Portal Pages,
// which records what is behind a sign-in.
function add_public_capture_button(frm) {
	frm.add_custom_button(__("Capture Public Page"), () => {
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
					.map(
						(f) =>
							`<li><code>${frappe.utils.escape_html(f.name || f.id || f.tag)}</code> (${
								f.type || f.tag
							})</li>`
					)
					.join("");
				frappe.msgprint({
					title: __("Capture Result"),
					indicator: d.captcha_detected ? "orange" : "blue",
					message: `
						<p><b>${__("Final URL")}:</b> ${frappe.utils.escape_html(d.final_url)}</p>
						<p><b>${__("Redirected")}:</b> ${d.redirected ? __("Yes") : __("No")}</p>
						<p><b>${__("CAPTCHA detected")}:</b> ${
							d.captcha_detected ? __("Yes - this portal needs a person") : __("No")
						}</p>
						<p><b>${__("Form fields")}:</b></p><ul>${fields || "<li>none</li>"}</ul>`,
				});
			})
			.catch(() => frappe.dom.unfreeze());
	});
}

function queue(frm, method, title) {
	frappe.call({
		method: `transport.transport.fine_sync.service.${method}`,
		args: { portal: frm.doc.name },
		freeze: true,
		freeze_message: __("Queueing..."),
		callback: (r) => {
			if (!r.message) return;
			frappe.msgprint({ title: title, indicator: "blue", message: r.message.message });
		},
	});
}
