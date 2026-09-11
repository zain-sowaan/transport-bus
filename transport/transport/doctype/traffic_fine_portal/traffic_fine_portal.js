// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

// This form no longer starts anything. It reports.
//
// Every button that used to live here drove a browser on the SERVER - Playwright
// or headed Chromium under Xvfb - and that approach has been rejected. Six went:
//
//   Fetch Fines Now        opened a window on the server, or relayed a UAE Pass
//                          screen to the operator, and fetched from there
//   Run Sync               the same fetch without the sign-in step
//   Capture Portal Pages   drove a signed-in session to record its markup
//   Capture Public Page    the same for a portal's public form
//   Test Headless Reach    launched a headless browser to see if it could reach
//                          the sign-in at all
//   Sign-in console        published a noVNC view of the server's own browser so
//                          an operator could answer a challenge on it
//
// Fines are read in the operator's own browser now, by the extension, and its
// button is added by transport/public/js/traffic_fine_portal_fetch.js - a
// separate file loaded through doctype_js, which is why nothing here adds it.
//
// What remains is the status banner, and it is worth keeping on its own: it
// answers whether a portal is authorized, whether anything can read it yet, and
// which route that would be. Removing it would leave a form that shows settings
// and says nothing about whether they work.
//
// The endpoints those buttons called still exist in fine_sync/service.py. They
// are no longer reachable from the desk, which is the change that was asked for;
// deciding whether they should stay callable at all is a separate question, and
// the scheduler still uses some of them.

frappe.ui.form.on("Traffic Fine Portal", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (!frm.doc.has_written_authorization) {
			// Authorization governs looking at the portal at all - reading it in
			// a person's own browser is still reading it - so the banner says so
			// before anything else is reported.
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
			},
		});
	},
});
