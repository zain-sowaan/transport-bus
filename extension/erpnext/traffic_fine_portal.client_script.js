// Paste into: Client Script -> DocType "Traffic Fine Portal", Apply To "Form".
//
// This is the whole ERPNext-side footprint of the browser extension. It adds one
// button, hands the request to the extension, and shows what comes back. It
// never talks to a portal itself - a desk page cannot, and this is not the file
// to try it in.

frappe.ui.form.on("Traffic Fine Portal", {
	refresh(frm) {
		if (frm.is_new()) return;
		// Only the portals whose fetch needs a person in front of it. A portal
		// the server can read on its own has no use for a button that opens a
		// tab on this machine.
		if (frm.doc.fetch_mode !== "Operator Assisted") return;

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
