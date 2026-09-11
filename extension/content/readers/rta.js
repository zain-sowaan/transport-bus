// Runs inside the RTA tab. Reads the fines list the operator is looking at.
//
// Two things make this reader different from tamm.js, and both are forced by
// the portal rather than chosen:
//
// 1. **It submits the search itself.** RTA's results live at
//    /violations/public-fines/customer-violations with no query string - the
//    list is client-side state produced by submitting the form, and a GET to
//    that path renders an empty page. So there is no URL the server can hand
//    over that already contains the fleet, and the traffic file number arrives
//    as `prefill` and is typed here. It is used for one form fill, and is never
//    stored, never logged, and never sent back: ingest_client_fetch refuses to
//    accept it, which is what actually stops a browser naming a fleet.
//
// 2. **Details are not optional.** TAMM's list carries the fine number and the
//    plate, so its modal only enriches. RTA's list carries neither - it shows a
//    vehicle description and nothing identifying the fine - so a row read
//    without its panel has no ticket number to dedup on and no plate to match a
//    vehicle by. includeDetails is therefore ignored here, deliberately.
//
// It never types a credential and never answers a CAPTCHA. The traffic file
// number is not a credential - it is printed on the vehicle licence - and the
// search it drives is the public inquiry any member of the public can run.

(() => {
	if (globalThis.__tffRtaLoaded) return;
	globalThis.__tffRtaLoaded = true;

	const { extractRows, nextPage, readPanel } = globalThis.PORTAL_EXTRACTORS.rta;

	// The search is a real HTTP round trip behind a React render, unlike TAMM's
	// purely local pager, so these are network waits rather than render waits.
	const RESULTS_GRACE_MS = 60000;
	const POLL_MS = 500;

	// A detail panel is one click and a local render. Generous anyway: this
	// runs once per fine, and a panel that has not arrived is a dropped fine.
	const PANEL_ATTEMPTS = 20;
	const PANEL_POLL_MS = 300;

	// After a pager click the next page is fetched, so this waits for the row
	// set to actually change rather than for a fixed interval.
	const PAGE_SETTLE_MS = 12000;

	// Bounds a pager that would otherwise cycle. The walk stops on "nothing
	// new" long before this.
	const MAX_PAGES = 25;

	// RTA splits a traffic file across three lists, and reading only the first
	// under-reports what a fleet owes. They are read in order and every row is
	// tagged with the list it came from, so the server can tell a payable fine
	// from one that has to go through the issuing authority.
	//
	// **Only the Payable list's markup has been seen with rows in it.** The
	// other two were empty on the fleet this was written against, so they are
	// read with the same extractor on the reasonable assumption that RTA reuses
	// its own row component. If that assumption is wrong the extractor finds
	// nothing, which is why each list's count is reported separately - a zero
	// that is visible is survivable, a zero folded into a total is not.
	const TABS = [
		{ selector: "#Id_FinesTab", status: "Payable" },
		{ selector: "#Id_ViolationsTab", status: "Non-Payable" },
		{ selector: "#Id_BlackpointsTab", status: "Black Points" },
	];

	// RTA expires the results page and redirects here. Detected explicitly
	// because the alternative reading - a page with no rows on it - is
	// indistinguishable from a fleet that owes nothing.
	const EXPIRED_PATH = "session-expired";

	const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
	const rowEls = () => [...document.querySelectorAll("div.finesRowList")];
	const expired = () => location.pathname.includes(EXPIRED_PATH);

	/** Type into a React-controlled input so React actually sees it.
	 *
	 * Assigning .value updates the DOM and leaves React's state untouched, so
	 * the component re-renders over it and the Search button submits an empty
	 * form. Going through the prototype's native setter and firing a bubbling
	 * input event is what React's synthetic onChange is listening for.
	 */
	function setControlledValue(input, value) {
		const setter = Object.getOwnPropertyDescriptor(
			window.HTMLInputElement.prototype,
			"value"
		).set;
		setter.call(input, value);
		input.dispatchEvent(new Event("input", { bubbles: true }));
		input.dispatchEvent(new Event("change", { bubbles: true }));
	}

	/** Put the search form on the Traffic Code Number mode.
	 *
	 * The four modes are a slick carousel of div.loginIconsView, not anything
	 * carrying role="tab", so they are found by the label span each one holds.
	 */
	function selectTrafficCodeTab() {
		const label = [...document.querySelectorAll("span.trafficCode")].find((el) =>
			/traffic code/i.test(el.textContent || "")
		);
		const tab = label && label.closest(".loginIconsView");
		if (tab) tab.click();
		return !!tab;
	}

	async function runSearch(prefill) {
		if (!prefill || !prefill.value) return { state: "no-prefill" };

		// Already on a results page - a re-injected reader after a navigation.
		if (rowEls().length) return { state: "ready" };

		selectTrafficCodeTab();
		await sleep(600);

		const input = document.querySelector(prefill.selector || "#Id_trafficFileNumber");
		if (!input) return { state: "no-form" };
		setControlledValue(input, String(prefill.value));
		await sleep(300);

		// The Search button, found by its own label rather than by position.
		// Explicitly NOT anything matching pay: #Id_PayNow lives on the results
		// page and must never be reachable from here.
		const search = [...document.querySelectorAll("button")].find(
			(b) => /^\s*search\s*$/i.test(b.textContent || "") && !b.disabled
		);
		if (!search) return { state: "no-search-button" };
		search.click();
		return { state: "submitted" };
	}

	/** Resolve once rows are on screen, or say why they are not. */
	async function awaitRows(progress) {
		const started = Date.now();
		let announced = false;
		while (Date.now() - started < RESULTS_GRACE_MS) {
			if (expired()) return { state: "session-expired" };
			if (rowEls().length) return { state: "ready" };
			if (!announced) {
				progress({ stage: "reading", message: "Waiting for the fines list…" });
				announced = true;
			}
			await sleep(POLL_MS);
		}
		// No rows can mean a clean fleet as well as a failure, and the two must
		// not be reported the same way. A results page that rendered its own
		// empty state is a real answer; anything else is a read that failed.
		const body = document.body.innerText || "";
		if (/no\s+(fines|violations|records|results)/i.test(body)) return { state: "empty" };
		return { state: location.pathname.includes("customer-violations") ? "no-table" : "no-results" };
	}

	/** Open one row's detail panel and read it.
	 *
	 * The row div is clicked, not the <tr>: PrimeReact marks the row
	 * data-p-selectable-row="false", so the table's own selection is off and
	 * the click handler that opens the panel lives on the inner div.
	 *
	 * The checkbox in the first cell is never touched. It feeds "Pay all".
	 */
	async function readDetailFor(el) {
		const before = document.querySelector("div.dataList");
		el.click();
		for (let i = 0; i < PANEL_ATTEMPTS; i++) {
			await sleep(PANEL_POLL_MS);
			const panel = document.querySelector("div.dataList");
			// Wait for a panel that is actually this row's - the previous one
			// stays mounted while the next renders, and reading too early
			// attaches the wrong fine number to the row.
			if (panel && panel !== before) return readPanel() || {};
			if (panel && !before) return readPanel() || {};
		}
		return null;
	}

	async function readVisiblePage(outOfTime, progress, collected, status) {
		const rows = extractRows() || [];
		const els = rowEls();
		let truncated = false;

		for (let i = 0; i < rows.length; i++) {
			if (outOfTime() || expired()) {
				truncated = true;
				break;
			}
			const details = els[i] ? await readDetailFor(els[i]) : null;
			// A row whose panel would not open is dropped rather than sent up
			// without a fine number; _to_fine would reject it anyway, and this
			// way the run's count is the number of fines actually read.
			if (!details) continue;
			rows[i]._details = details;
			rows[i]._tab = status;
			const ticket = (details["Fine Number"] || "").trim();
			// Keyed on the fine number alone, not on the tab: the same fine must
			// never arrive twice because two lists happen to include it.
			if (ticket) collected.set(ticket, rows[i]);
			progress({ stage: "reading", message: `Read ${collected.size} fine(s)…`, count: collected.size });
		}
		return truncated;
	}

	/** Walk every page of the list currently on screen. */
	async function collectPagesOfTab({ outOfTime, progress, collected, status }) {
		let truncated = false;
		const before = collected.size;

		for (let page = 0; page < MAX_PAGES; page++) {
			truncated = (await readVisiblePage(outOfTime, progress, collected, status)) || truncated;
			if (truncated || outOfTime() || expired()) {
				truncated = true;
				break;
			}

			const sizeBeforePage = collected.size;
			if (!nextPage()) break;

			// Wait for the page to actually turn. Comparing the row set rather
			// than sleeping a fixed interval is what stops a slow response
			// being read as the last page.
			const settleStart = Date.now();
			let turned = false;
			while (Date.now() - settleStart < PAGE_SETTLE_MS) {
				await sleep(POLL_MS);
				const ids = (extractRows() || []).map((r) => r._rowId).join(",");
				if (ids && ids !== globalThis.__tffRtaLastIds) {
					globalThis.__tffRtaLastIds = ids;
					turned = true;
					break;
				}
			}
			if (!turned) break;
			if (collected.size === sizeBeforePage && page > 0) break;
		}

		return { truncated, read: collected.size - before };
	}

	/** Read all three of RTA's lists, in order.
	 *
	 * Coverage is returned per list rather than as one number. A run that says
	 * "4 fines" when two of three lists were never read is the same failure
	 * this whole module is written to avoid - it reads as a complete picture of
	 * what a fleet owes, and it is not one.
	 */
	async function collectAllTabs({ budgetMs, progress }) {
		const started = Date.now();
		const outOfTime = () => budgetMs > 0 && Date.now() - started > budgetMs;
		const collected = new Map();
		const coverage = [];
		let truncated = false;

		for (const tab of TABS) {
			if (outOfTime() || expired()) {
				// Everything from here on is unread, and saying so is the point.
				coverage.push({ list: tab.status, read: null, reason: expired() ? "session expired" : "out of time" });
				truncated = true;
				continue;
			}

			const control = document.querySelector(tab.selector);
			if (!control) {
				coverage.push({ list: tab.status, read: null, reason: "list not offered" });
				continue;
			}

			progress({ stage: "reading", message: `Reading ${tab.status.toLowerCase()} fines…` });
			control.click();

			// The list is re-fetched, so wait for rows rather than assume them.
			// An empty list is a real answer here and must not read as failure:
			// most fleets have nothing under Non-Payable.
			const waitStart = Date.now();
			while (Date.now() - waitStart < PAGE_SETTLE_MS) {
				await sleep(POLL_MS);
				if (rowEls().length || expired()) break;
			}
			if (expired()) {
				coverage.push({ list: tab.status, read: null, reason: "session expired" });
				truncated = true;
				continue;
			}

			globalThis.__tffRtaLastIds = null;
			const result = await collectPagesOfTab({ outOfTime, progress, collected, status: tab.status });
			truncated = truncated || result.truncated;
			coverage.push({ list: tab.status, read: result.read, reason: null });
		}

		return { rows: [...collected.values()], truncated, coverage, empty: collected.size === 0 };
	}

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
			progress({ stage: "reading", message: "Running the traffic file search…" });
			const searched = await runSearch(message.prefill);
			if (searched.state === "no-prefill" || searched.state === "no-form") {
				sendResponse({ ok: false, state: searched.state });
				return;
			}

			const waited = await awaitRows(progress);
			if (waited.state === "empty") {
				// A real answer, and reported as a successful read of zero.
				sendResponse({ ok: true, state: "read", rows: [], truncated: false });
				return;
			}
			if (waited.state !== "ready") {
				sendResponse({ ok: false, state: waited.state });
				return;
			}
			globalThis.__tffRtaLastIds = null;

			const result = await collectAllTabs({ budgetMs: message.budgetMs || 0, progress });
			if (result.empty) {
				// Every list was reachable and every one was empty. That is a
				// clean traffic file, not a failed read - reported as a
				// successful zero so nobody re-runs it looking for a bug.
				const reachable = result.coverage.every((c) => c.read !== null);
				sendResponse(
					reachable
						? { ok: true, state: "read", rows: [], truncated: result.truncated, coverage: result.coverage }
						: { ok: false, state: "no-table", coverage: result.coverage }
				);
				return;
			}

			sendResponse({
				ok: true,
				state: "read",
				rows: result.rows,
				truncated: result.truncated,
				coverage: result.coverage,
			});
		})().catch((error) => {
			sendResponse({ ok: false, state: "error", message: String(error && error.message) });
		});

		// Keeps the message channel open for the async work above.
		return true;
	});
})();
