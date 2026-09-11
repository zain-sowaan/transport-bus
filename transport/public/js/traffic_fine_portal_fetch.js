// Loaded by hooks.py -> doctype_js. NOT a Client Script and not a fixture.
//
// It lived as a Client Script fixture for exactly one morning, which was long
// enough to show why that was the wrong home. Frappe concatenates every enabled
// Client Script for a doctype into a single new Function(), so a hand-pasted
// copy of this same file - left over from when it was a paste-in - declared
// BRIDGE_FROM_PAGE a second time and made the combined body a SyntaxError. The
// form then loaded none of its scripts at all, which reads as a broken feature
// rather than a duplicate record.
//
// A doctype_js file is served as its own script, so two of them cannot collide
// in that way, and the desk half of the extension is now ordinary app code that
// ships and reviews with everything else. The IIFE below is kept anyway: it
// costs nothing and it keeps these names out of the global scope.
//
// The extension itself is still not part of the app - it lives in
// apps/transport/extension and a person installs it into Chrome. This file is
// the whole ERPNext-side footprint: one button, handed to the extension, and
// whatever comes back shown to the operator. It never talks to a portal itself.

(() => {
	frappe.ui.form.on("Traffic Fine Portal", {
		refresh(frm) {
			if (frm.is_new()) return;
			// Enabled portals only. NOT gated on fetch_mode = "Operator Assisted",
			// which was the first shape and excluded the one portal this exists for:
			// TAMM is marked "Automated", and it is exactly the portal the server
			// cannot reliably read on its own - the CAPTCHA rate on the server's
			// address is why the extension was written. fetch_mode answers "should
			// this run unattended", which is a different question from "can a person
			// read it in their own browser".
			//
			// Portals the extension has no reader for are refused by the server, by
			// name, when the button is pressed: ingest_client_fetch and
			// get_client_fetch_target both throw "has no client fetch path". A
			// readable refusal on click beats a button that silently never appears.
			if (!frm.doc.is_enabled) return;

			frm.add_custom_button(__("Fetch Fines In This Browser"), () => start_fetch(frm));

			// The bridge announces itself as soon as it loads, which can be before
			// this script has a listener registered. Asking as well as listening
			// means a working install is never reported as a missing one just
			// because the two loaded in the wrong order.
			window.postMessage(
				{ source: BRIDGE_FROM_PAGE, kind: "PING", requestId: "ping" },
				window.location.origin
			);
		},
	});

	const BRIDGE_FROM_PAGE = "transport-fine-fetch/page";
	const BRIDGE_FROM_EXTENSION = "transport-fine-fetch/extension";

	// Set by the extension's bridge as soon as the desk loads. Absent means the
	// extension is not installed, not enabled, or not permitted on this origin.
	let bridge_version = null;

	window.addEventListener("message", (event) => {
		if (event.source !== window || event.origin !== window.location.origin) return;
		const data = event.data;
		if (!data || data.source !== BRIDGE_FROM_EXTENSION) return;
		if (data.kind === "READY" || data.kind === "PONG") bridge_version = data.version;
	});

	function start_fetch(frm) {
		if (!bridge_version) {
			frappe.msgprint({
				title: __("Fine Fetch extension not found"),
				indicator: "orange",
				message: __(
					"This browser does not have the Fine Fetch extension enabled, so there is " +
						"nothing here that can open the portal. Install it, then reload this page."
				),
			});
			return;
		}

		const request_id = frappe.utils.get_random(12);

		const dialog = new frappe.ui.Dialog({
			title: __("Fetching fines"),
			fields: [{ fieldtype: "HTML", fieldname: "status" }],
			// No primary action: the operator's next step is in the portal tab, not
			// in this dialog. Closing it does not stop the fetch.
			secondary_action_label: __("Hide"),
			secondary_action: () => dialog.hide(),
		});

		const say = (message, indicator) => {
			dialog.fields_dict.status.$wrapper.html(
				`<div class="text-muted" style="padding:4px 0 8px">
					<span class="indicator ${indicator || "blue"}">${frappe.utils.escape_html(message)}</span>
				</div>`
			);
		};

		say(__("Opening the fines page…"));
		dialog.show();

		const on_message = (event) => {
			if (event.source !== window || event.origin !== window.location.origin) return;
			const data = event.data;
			if (!data || data.source !== BRIDGE_FROM_EXTENSION) return;
			if (data.requestId !== request_id) return;

			if (data.kind === "PROGRESS") {
				say(data.message);
				return;
			}

			if (data.kind !== "DONE") return;
			window.removeEventListener("message", on_message);

			if (!data.ok) {
				say(data.message, "red");
				return;
			}

			// A partial read is reported as its own outcome rather than folded into
			// a success: fines beyond the point it stopped were never seen, and
			// their absence here does not mean they do not exist.
			say(data.message, data.truncated ? "orange" : "green");
			frappe.show_alert({ message: data.message, indicator: data.truncated ? "orange" : "green" });
			frm.reload_doc();
		};

		window.addEventListener("message", on_message);

		window.postMessage(
			{
				source: BRIDGE_FROM_PAGE,
				kind: "FETCH",
				requestId: request_id,
				portal: frm.doc.name,
				// Sent so the extension can post results back as this user. The
				// traffic file number is deliberately not here - the extension asks
				// the server for it, so it never passes through this page.
				csrfToken: frappe.csrf_token,
			},
			window.location.origin
		);
	}
})();
