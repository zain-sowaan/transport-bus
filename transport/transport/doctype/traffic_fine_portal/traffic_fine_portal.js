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

				if (s.fetch_implemented) add_fetch_button(frm, s);
				// Capture only stands in for a fetch that does not exist yet.
				else if (s.can_capture) add_session_capture_button(frm);
				if (s.can_run_sync) add_run_sync_button(frm);
				if (s.can_capture_public) add_public_capture_button(frm);
				// Offered wherever the relay is even possible, not only where it
				// is switched on - its whole value is answering "would this
				// work here?" before anyone depends on it.
				if (s.supports_relay) add_reach_test_button(frm);
				// Not gated on the session status call: the console is about
				// answering a challenge on a sign-in that has not happened yet,
				// so it has to be reachable precisely when the portal looks
				// unhealthy. Its own permission check runs server-side.
				add_signin_console_button(frm);
			},
		});
	},
});

// Operator-assisted fetch. Two shapes, and the button has to say which one it
// is about to start: the window mode needs the person at the server's own
// screen, the relay needs their phone. Someone told the wrong one waits at a
// window that was never going to open.
function add_fetch_button(frm, status) {
	frm.add_custom_button(__("Fetch Fines Now"), () => {
		const prompt = status.relay_available
			? __(
					"Sign-in runs on the server without a window. The UAE Pass screen will appear here, and you confirm the request in the app on your phone. Keep this page open. Continue?"
			  )
			: __(
					"A browser window will open on the server for you to sign in - you need to be at that machine. The whole traffic file is fetched in one go. Continue?"
			  );
		frappe.confirm(prompt, () => {
			// Subscribed BEFORE the call, not in its callback. The job is
			// queued on a long worker that can pick it up and reach the
			// sign-in screen while the round trip is still in flight, and a
			// listener attached afterwards misses those first frames.
			if (status.relay_available) watch_relay(frm);
			queue(frm, "enqueue_operator_assisted_sync", __("Fetch Queued"));
		});
	});
}

// Answers "would a headless sign-in even reach the form from this server?"
// without typing anything, without a push, and without an operator - so it can
// be run any time, including before anyone is relying on the relay.
function add_reach_test_button(frm) {
	frm.add_custom_button(
		__("Test Headless Reach"),
		() => {
			watch_relay(frm, { probe: true });
			queue(frm, "enqueue_relay_reachability_probe", __("Reach Test Queued"));
		},
		__("Diagnostics")
	);
}

// The relay's screen. Live-only by design: the sign-in screenshot is published
// to this one user and never written to disk, so there is nothing to reload
// from. The server re-sends the current frame every few seconds, which is what
// makes that survivable - reopen this dialog and the next frame refills it.
function watch_relay(frm, opts = {}) {
	const dialog = new frappe.ui.Dialog({
		title: opts.probe ? __("Headless Reach Test") : __("UAE Pass Sign-In"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "screen" }],
		primary_action_label: __("Close"),
		primary_action: () => dialog.hide(),
	});

	const handler = (data) => {
		if (!data || data.portal !== frm.doc.name) return;
		dialog.fields_dict.screen.$wrapper.empty().append(render_relay(data));
		if (data.stage === "finished" || data.stage === "probe-finished") {
			frm.dashboard && frm.dashboard.clear_headline();
		}
	};

	frappe.realtime.on("transport_fine_relay", handler);
	// Off on close, or every press of the button stacks another listener and
	// the same frame renders into a dialog that is no longer on screen.
	dialog.$wrapper.on("hidden.bs.modal", () => frappe.realtime.off("transport_fine_relay", handler));

	dialog.fields_dict.screen.$wrapper.html(
		`<p class="text-muted">${__("Starting...")}</p>`
	);
	dialog.show();
	return dialog;
}

function render_relay(data) {
	const wrap = $("<div></div>");
	const message = $("<p></p>").text(data.message || "");
	wrap.append(message);

	if (data.stage === "awaiting-confirmation") {
		// The picture is the authority and the scrape is not, so they are
		// labelled that way round. The confirmation screen's markup has never
		// been captured, which makes any selector for the code a guess - and a
		// guess that renders the WRONG code is worse than none, because it
		// would have someone tap the wrong option on their phone.
		if (data.code_guess) {
			wrap.append(
				$("<p></p>")
					.css({ "font-size": "13px" })
					.append($("<span></span>").addClass("text-muted").text(__("Reading the screen, this looks like") + " "))
					.append($("<b></b>").css("font-size", "22px").text(data.code_guess))
					.append(
						$("<span></span>")
							.addClass("text-muted")
							.text(" - " + __("but trust the picture below, not this line."))
					)
			);
		}
		wrap.append(
			$("<div></div>")
				.addClass("alert alert-warning")
				.css({ padding: "8px 12px", "font-size": "12px" })
				.text(
					__(
						"If what your phone shows does not match this screen, reject it on your phone. Do not approve a request you cannot match."
					)
				)
		);
		if (data.seconds_left != null) {
			wrap.append(
				$("<p></p>")
					.addClass("text-muted")
					.css("font-size", "11px")
					.text(__("Waiting about {0} more seconds.", [data.seconds_left]))
			);
		}
		if (data.screen) {
			// Assigned as a property, never interpolated into markup.
			const img = $("<img>").css({
				width: "100%",
				border: "1px solid var(--border-color)",
				"border-radius": "4px",
			});
			img.attr("alt", __("The UAE Pass sign-in screen as the server sees it"));
			img[0].src = data.screen;
			wrap.append(img);
		}
	}

	if (data.outcome && data.outcome.sync_run) {
		const link = $("<a></a>")
			.text(__("Open the sync run"))
			.attr("href", `/app/traffic-fine-sync-run/${encodeURIComponent(data.outcome.sync_run)}`);
		wrap.append($("<p></p>").append(link));
	}

	if (data.report) {
		const rows = Object.entries(data.report)
			.filter(([, v]) => v !== null && typeof v !== "object")
			.map(([k, v]) => $("<tr></tr>").append($("<td></td>").text(k), $("<td></td>").text(String(v))));
		wrap.append($("<table></table>").addClass("table table-bordered").css("font-size", "11px").append(rows));
	}

	return wrap;
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

// The operator's window onto the server's own screen. Everything else here
// reports on a sign-in; this one lets somebody complete a sign-in that has
// stalled on something only a human can clear - a reCAPTCHA image challenge,
// which is what UAT has been hitting.
//
// Opened as a real tab first, and the modal offered second, deliberately. The
// console sits on its own origin behind its own password, and browsers refuse
// to show an HTTP Basic auth prompt inside a cross-origin iframe - so a modal
// on a first visit renders a blank white box and nothing explains why. Once
// the operator has authenticated to that origin in a normal tab, the browser
// reuses those credentials and the embedded view works.
function add_signin_console_button(frm) {
	frm.add_custom_button(
		__("Authenticate Traffic Portal"),
		() => {
			frappe.call({
				method: "transport.transport.fine_sync.service.get_portal_signin_console",
				callback: (r) => {
					const c = r.message || {};
					if (!c.available) {
						frappe.msgprint({
							title: __("Sign-In Console Unavailable"),
							indicator: "orange",
							message: c.reason || __("The sign-in console is not configured."),
						});
						return;
					}
					show_signin_console(c);
				},
			});
		},
		__("Diagnostics")
	);
}

function show_signin_console(c) {
	const url = c.url;
	const dialog = new frappe.ui.Dialog({
		title: __("Portal Sign-In Console"),
		size: "extra-large",
		primary_action_label: __("Open In New Tab"),
		primary_action: () => {
			// noopener: the console holds a live portal session, and a tab
			// opened without it keeps a handle back to this one.
			window.open(url, "_blank", "noopener,noreferrer");
		},
	});

	dialog.$body.html(`
		<div class="alert alert-warning" style="margin-bottom:12px">
			${frappe.utils.escape_html(c.warning || "")}
		</div>
		<p class="text-muted small">
			${__("If the panel below is blank, open the console in a new tab once and sign in to it. The embedded view works after that.")}
		</p>
		<iframe
			src="${frappe.utils.escape_html(url)}"
			style="width:100%;height:60vh;border:1px solid var(--border-color);border-radius:var(--border-radius-md)"
			referrerpolicy="no-referrer"
			title="${__("Portal sign-in console")}"
		></iframe>
	`);

	dialog.show();
}
