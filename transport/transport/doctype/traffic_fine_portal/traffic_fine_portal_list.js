// Copyright (c) 2026, Sowaan and contributors
// For license information, please see license.txt

// Thirteen portals are seeded, and two of them can currently be read. The other
// eleven are real reference data - scope, authentication, CAPTCHA, terms - and
// deleting them to tidy the list would throw away the research that says why
// they are not readable. So they are filtered out of the default view instead.
//
// This hides NOTHING permanently and destroys nothing. It applies one filter on
// first load, exactly as if a person had typed it, and the "Filter" area shows
// it. Clear that filter, or click "Show all", and all thirteen are back.
//
// The filter is on `fetcher_key` because that is the field which actually
// answers "is there code that can read this?" - registry.py looks the portal up
// by it. The two values below are the readers that exist in
// extension/content/readers/. Add a key here when a third reader ships; the
// list is short on purpose, so that a portal appearing in this view is a claim
// somebody has to make deliberately.
//
// Note that `srta` and `evg` also carry a fetcher_key and are still filtered
// out. SRTA's fetcher drives a browser on the server, which is the approach that
// was rejected; EVG has a key and no implementation at all.

const READABLE_IN_BROWSER = ["tamm", "rta"];

frappe.listview_settings["Traffic Fine Portal"] = {
	onload(listview) {
		// Only on a clean arrival. A route carrying its own filters - a link
		// from a report, a saved filter, the back button - is somebody asking
		// for something specific, and overriding that would be the one way this
		// could genuinely hide a portal from a person looking for it.
		if (frappe.route_options) return;
		if (listview.filter_area && listview.filter_area.get().length) return;

		listview.filter_area.add([
			["Traffic Fine Portal", "fetcher_key", "in", READABLE_IN_BROWSER],
		]);
	},
};
