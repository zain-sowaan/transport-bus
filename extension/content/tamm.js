// Runs inside the portal tab. Reads the fines table the operator is looking at.
//
// This is the half of the fetcher that used to be Playwright driving a browser
// on the server. The reading itself is unchanged - `extractors.generated.js`
// carries the same three functions the Python fetcher evaluates - and what
// changed is only who runs them and where.
//
// It never types a credential and never touches a CAPTCHA. The operator signs
// in themselves, in their own browser, exactly as they would by hand; this
// script waits until the table is on screen and then reads what is already
// rendered.

(() => {
	// Content scripts are injected once per document, but a re-injection after
	// an SPA route change would otherwise register a second listener and answer
	// every request twice.
	if (globalThis.__tffTammLoaded) return;
	globalThis.__tffTammLoaded = true;

	const { extractRows, nextPage, readPanel } = globalThis.TAMM_EXTRACTORS;

	// Mirrors the fetcher's own grace. Checking once, immediately after the
	// navigation resolves, is what used to make a live session look dead: the
	// page is a single-page app and the table lands well after the document
	// does. The interval is shorter than the server's 3s because this runs
	// inside the page - there is no round trip to pay for.
	const TABLE_GRACE_MS = 90000;
	const TABLE_POLL_MS = 500;

	// After a pager click the next page renders locally; no request is made, so
	// this is a render wait rather than a network wait.
	const PAGE_SETTLE_MS = 1500;

	// Bounds a pager that would otherwise cycle. The walk stops on "nothing
	// new" long before this.
	const MAX_PAGES = 25;

	const PANEL_ATTEMPTS = 12;   // ~6s for a detail modal to render
	const PANEL_POLL_MS = 500;
	const DISMISS_ATTEMPTS = 6;

	// The identity provider's own hostnames, matching `UAE_PASS_HOSTS` in the
	// Python. A substring test against the URL would be wrong: `provider=uaepass`
	// appears in the portal's own sign-in link, so it says "on UAE Pass" while
	// the browser is still on a portal page.
	const AUTH_HOSTS = ["id.uaepass.ae", "ids.uaepass.ae", "stg-id.uaepass.ae", "stg-ids.uaepass.ae"];
	const AUTH_DOMAIN = ".uaepass.ae";

	const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

	const visible = (el) =>
		!!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);

	function onAuthPage() {
		const host = location.hostname.toLowerCase();
		return AUTH_HOSTS.includes(host) || host.endsWith(AUTH_DOMAIN);
	}

	function tablePresent() {
		try {
			const rows = extractRows();
			return Array.isArray(rows) && rows.length > 0;
		} catch {
			return false;
		}
	}

	// -- waiting -----------------------------------------------------------

	/** Wait for the table, reporting which of the two failure states we are in.
	 *
	 * "Nobody has signed in" and "signed in but the table never rendered" need
	 * different words in front of an operator - the first is something they can
	 * fix in ten seconds, the second is a broken read - so they are never
	 * collapsed into one message here.
	 */
	async function awaitTable(progress) {
		let waited = 0;
		let announcedSignIn = false;

		while (waited < TABLE_GRACE_MS) {
			if (tablePresent()) return { state: "ready" };

			if (onAuthPage() && !announcedSignIn) {
				announcedSignIn = true;
				progress({
					stage: "awaiting-sign-in",
					message:
						"Sign in with UAE Pass in the tab that just opened, and approve the " +
						"request on your phone. The fetch continues by itself once the fines " +
						"page loads.",
				});
			}

			await sleep(TABLE_POLL_MS);
			waited += TABLE_POLL_MS;
		}

		return {
			state: onAuthPage() ? "not-signed-in" : "no-table",
		};
	}

	// -- detail modal ------------------------------------------------------

	/** Close the detail panel and confirm it has really gone.
	 *
	 * Returning before the panel has left the DOM is what makes the *next*
	 * row's button unclickable, so this confirms rather than assumes.
	 */
	async function dismissPanel() {
		for (let i = 0; i < DISMISS_ATTEMPTS; i++) {
			if (!readPanel()) return true;

			const buttons = [
				...document.querySelectorAll(
					'[role="dialog"] button, .ui-lib-modal button, .ui-lib-drawer button'
				),
			];
			const close = buttons.find((b) =>
				/close/i.test(`${b.getAttribute("aria-label") || ""} ${b.className || ""}`)
			);

			if (close) {
				close.click();
			} else {
				document.body.dispatchEvent(
					new KeyboardEvent("keydown", { key: "Escape", bubbles: true })
				);
			}
			await sleep(600);
		}
		return false;
	}

	/** Open one fine's details, read them, close.
	 *
	 * The row's chevron looks like the opener but is hidden until hover and
	 * never resolves to a click. The real control is a button in the `link`
	 * column, rendered twice - a labelled one for wide screens and an icon-only
	 * one for narrow - with only one visible at any width, hence the visibility
	 * filter rather than an index.
	 */
	async function readDetailPanel(ticket) {
		// Any panel left open from the previous row covers this one's button,
		// so start from a known-closed state rather than trusting the last
		// close.
		await dismissPanel();

		const row = [...document.querySelectorAll("tr.ui-lib-table-row")].find((tr) =>
			(tr.innerText || "").includes(ticket)
		);
		if (!row) return null;

		const button = [...row.querySelectorAll('td[data-id="link"] button')].find(visible);
		if (!button) return null;
		button.click();

		let details = null;
		for (let i = 0; i < PANEL_ATTEMPTS; i++) {
			details = readPanel();
			if (details) break;
			await sleep(PANEL_POLL_MS);
		}

		await dismissPanel();
		return details;
	}

	/** Attach detail values to every row currently rendered.
	 *
	 * Best effort per row: a fine whose modal will not open keeps its list-view
	 * values instead of failing the run. The list simply does not carry the
	 * violation description, so without this those fields stay empty.
	 *
	 * Returns true if the time limit stopped it partway. The budget is checked
	 * per row rather than per page because this is where the time goes.
	 */
	async function detailsForVisible(rows, outOfTime) {
		for (const row of rows) {
			if (outOfTime()) return true;
			const ticket = (row.fineNumber || "").trim();
			if (!/^\d+$/.test(ticket)) continue;
			try {
				row._details = (await readDetailPanel(ticket)) || {};
			} catch {
				row._details = {};
			}
		}
		return false;
	}

	// -- the walk ----------------------------------------------------------

	/** Walk every page, keyed on fine number so repeats merge harmlessly.
	 *
	 * Details are read page by page rather than at the end, because only the
	 * rows currently rendered can be opened - once the walk finishes, the
	 * earlier pages are no longer in the DOM to click.
	 *
	 * Stops when a click yields nothing new rather than when it runs out of
	 * buttons: that terminates correctly whether the control is a numbered
	 * pager, a next-arrow, or something that silently does nothing.
	 */
	async function collectAllPages({ includeDetails, budgetMs, progress }) {
		const started = Date.now();
		const outOfTime = () => budgetMs > 0 && Date.now() - started > budgetMs;

		const collected = new Map();
		let truncated = false;

		const absorb = (rows) => {
			for (const row of rows) {
				const ticket = (row.fineNumber || row.ticketNumber || "").trim();
				if (ticket) collected.set(ticket, row);
			}
		};

		let rows = extractRows();
		if (!rows || !rows.length) return { rows: [], truncated: false, empty: true };

		absorb(rows);
		if (includeDetails) truncated = await detailsForVisible(rows, outOfTime);
		progress({ stage: "reading", message: `Read ${collected.size} fine(s) so far.`, count: collected.size });

		for (let page = 0; page < MAX_PAGES; page++) {
			if (truncated || outOfTime()) {
				// Out of budget with pages still unread. Reported rather than
				// treated as the end of the list: what has been collected is
				// real, but it is not everything.
				truncated = true;
				break;
			}

			const before = collected.size;
			let advanced = null;
			try {
				advanced = nextPage();
			} catch {
				break;
			}
			if (!advanced) break;

			await sleep(PAGE_SETTLE_MS);

			try {
				rows = extractRows() || [];
			} catch {
				break;
			}
			absorb(rows);
			if (includeDetails && rows.length) {
				truncated = await detailsForVisible(rows, outOfTime);
			}
			progress({
				stage: "reading",
				message: `Read ${collected.size} fine(s) so far.`,
				count: collected.size,
			});

			if (collected.size === before) break;
		}

		return { rows: [...collected.values()], truncated, empty: false };
	}

	// -- message handling --------------------------------------------------

	chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
		if (!message || message.type !== "TFF_READ") return undefined;

		const progress = (payload) => {
			chrome.runtime
				.sendMessage({ type: "TFF_PROGRESS", requestId: message.requestId, ...payload })
				.catch(() => {
					// The desk tab can be closed mid-run. Losing the progress
					// line is not a reason to abandon a fetch in flight.
				});
		};

		(async () => {
			const waited = await awaitTable(progress);
			if (waited.state !== "ready") {
				sendResponse({ ok: false, state: waited.state });
				return;
			}

			const result = await collectAllPages({
				includeDetails: !!message.includeDetails,
				budgetMs: message.budgetMs || 0,
				progress,
			});

			if (result.empty) {
				sendResponse({ ok: false, state: "no-table" });
				return;
			}

			sendResponse({
				ok: true,
				state: "read",
				rows: result.rows,
				truncated: result.truncated,
			});
		})().catch((error) => {
			sendResponse({ ok: false, state: "error", message: String(error && error.message) });
		});

		// Keeps the message channel open for the async work above.
		return true;
	});
})();
