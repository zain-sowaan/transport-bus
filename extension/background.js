// Orchestrates one fetch: open the portal, wait for the operator, read, hand up.
//
// Nothing here parses a fine. The rows go to ERPNext exactly as the page gave
// them, and the server runs the same parser it has always run - which is what
// keeps the two halves from drifting into two different ideas of what a fine is.

// No portal origin or path is hardcoded here. The server names the page to open
// and the reader to run against it, because that is portal knowledge: TAMM
// carries the fleet in a query parameter, a login-based portal carries it in
// the session and takes no parameter at all. An extension that built its own
// URLs would have to know that difference and would be wrong about it the first
// time a portal moved.

// Sent on every call to ERPNext. ngrok's free tier answers the first request
// from a browser with an HTML interstitial instead of the thing that was asked
// for, and this header is its documented opt-out. A fetch that receives that
// page fails as "unexpected token < in JSON", which says nothing about the
// actual cause; harmless everywhere else, so it is not made conditional.
const TUNNEL_HEADERS = { "ngrok-skip-browser-warning": "true" };

const INGEST_METHOD = "transport.transport.fine_sync.service.ingest_client_fetch";
const TARGET_METHOD = "transport.transport.fine_sync.service.get_client_fetch_target";

// Matches the server's sign-in window. A push notification approved on a phone
// is a human-paced step, and rushing it only produces a failure the operator
// then has to repeat.
const SIGN_IN_WINDOW_MS = 45 * 60 * 1000;

// How long the reading itself may take once the table is up. Reading is not
// waiting: this bounds the pager walk and the detail modals, not the operator.
const READ_BUDGET_MS = 25 * 60 * 1000;

// One fetch at a time. Two tabs walking the same client-side pager would
// interleave their clicks in one browser profile and produce two partial reads.
let inFlight = null;

const settings = {
	async closeTabWhenDone() {
		const stored = await chrome.storage.sync.get({ closeTabWhenDone: true });
		return !!stored.closeTabWhenDone;
	},
	async includeDetailsDefault() {
		const stored = await chrome.storage.sync.get({ includeDetails: true });
		return !!stored.includeDetails;
	},
};

// -- desk conversation -------------------------------------------------------

function toDesk(tabId, payload) {
	chrome.tabs.sendMessage(tabId, payload).catch(() => {
		// The operator closed the desk tab. The fetch itself is unaffected and
		// the rows still reach the server, so this is not worth aborting for.
	});
}

const progress = (tabId, requestId, stage, message, count) =>
	toDesk(tabId, { type: "TFF_PROGRESS", requestId, stage, message, count });

const done = (tabId, requestId, payload) =>
	toDesk(tabId, { type: "TFF_DONE", requestId, ...payload });

// -- portal tab --------------------------------------------------------------

// Whether a tab has landed back on the portal's fines page, judged against the
// origin and path the server gave us rather than anything known here.
const onFinesPage = (url, target) => {
	try {
		const parsed = new URL(url);
		return (
			parsed.origin === target.origin &&
			parsed.pathname.startsWith(target.path_prefix || "/")
		);
	} catch {
		return false;
	}
};

/** Whether we may read this portal's origin.
 *
 * Checks only. It does NOT request: chrome.permissions.request() needs a user
 * gesture, and by the time the click has travelled desk page -> content script
 * -> service worker, there is none left to spend. Asking here fails silently
 * and the operator is told to approve a prompt that never appears - which is
 * exactly the dead end this replaced.
 *
 * The origins of whichever readers ship are granted in the manifest instead,
 * written there at package time from each fetcher's `client_origin`.
 */
async function hasOriginPermission(origin) {
	try {
		return await chrome.permissions.contains({ origins: [`${origin}/*`] });
	} catch {
		return false;
	}
}

/** Put the reader for this portal into the tab.
 *
 * Injected rather than declared in the manifest, for the same reason the origin
 * is requested rather than granted: the set of portals is server-side data, and
 * a static content_scripts entry cannot name a host this build has never seen.
 * The extractors go in first - the reader calls them.
 */
async function injectReader(tabId, reader) {
	await chrome.scripting.executeScript({
		target: { tabId },
		files: ["content/extractors.generated.js", `content/readers/${reader}.js`],
	});
}

/** Resolve once the tab is sitting on the fines page with its document loaded.
 *
 * Signing in navigates away to the identity provider and back, which tears down
 * the content script and everything it was doing. So the read is not sent until
 * the tab has landed back on the portal - waiting for the *page*, not for the
 * operator, which is why this survives however many redirects the sign-in takes.
 */
function waitForFinesPage(tabId, timeoutMs, target) {
	return new Promise((resolve) => {
		let settled = false;

		const finish = (value) => {
			if (settled) return;
			settled = true;
			chrome.tabs.onUpdated.removeListener(onUpdated);
			chrome.tabs.onRemoved.removeListener(onRemoved);
			clearTimeout(timer);
			resolve(value);
		};

		const onUpdated = (updatedId, info, tab) => {
			if (updatedId !== tabId) return;
			if (info.status !== "complete") return;
			if (onFinesPage(tab.url || "", target)) finish("ready");
		};

		const onRemoved = (removedId) => {
			if (removedId === tabId) finish("tab-closed");
		};

		const timer = setTimeout(() => finish("timeout"), timeoutMs);

		chrome.tabs.onUpdated.addListener(onUpdated);
		chrome.tabs.onRemoved.addListener(onRemoved);

		// The tab may already be there - a banked session lands straight on the
		// fines page and fires no further update.
		chrome.tabs.get(tabId).then((tab) => {
			if (tab && tab.status === "complete" && onFinesPage(tab.url || "", target)) finish("ready");
		}).catch(() => finish("tab-closed"));
	});
}

// -- asking ERPNext what to open ---------------------------------------------

/** Ask the server which page to open for this portal.
 *
 * The traffic file number is client data. It is fetched here, held for the
 * length of one navigation and never stored, never logged, and never passed
 * through the desk page - the same standard the server already applies to the
 * sign-in phone number, which is kept off every screen a System Manager can
 * open.
 *
 * Asking rather than being told also means the permission check stays on the
 * server, where it can refuse.
 */
async function fetchTarget(erpOrigin, portal) {
	const url = new URL(`/api/method/${TARGET_METHOD}`, erpOrigin);
	url.searchParams.set("portal", portal);

	const response = await fetch(url.toString(), {
		method: "GET",
		credentials: "include",
		headers: { Accept: "application/json", ...TUNNEL_HEADERS },
	});

	let payload = null;
	try {
		payload = await response.json();
	} catch {
		payload = null;
	}

	if (!response.ok) {
		const detail =
			(payload && (payload.exception || payload._server_messages)) ||
			`${response.status} ${response.statusText}`;
		throw new Error(String(detail));
	}

	return (payload && payload.message) || {};
}

// -- handing the rows up -----------------------------------------------------

/** Post the rows to ERPNext as the signed-in desk user.
 *
 * The traffic file number is deliberately absent from this payload. The server
 * reads it from the portal record instead, so a browser can never name which
 * fleet a batch of fines belongs to.
 */
async function ingest({ erpOrigin, csrfToken, portal, rows, truncated }) {
	const response = await fetch(`${erpOrigin}/api/method/${INGEST_METHOD}`, {
		method: "POST",
		credentials: "include",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-Frappe-CSRF-Token": csrfToken || "",
			...TUNNEL_HEADERS,
		},
		body: JSON.stringify({ portal, rows, truncated, fetched_at: new Date().toISOString() }),
	});

	let payload = null;
	try {
		payload = await response.json();
	} catch {
		payload = null;
	}

	if (!response.ok) {
		// Frappe puts the readable half of a server error in `exception`; the
		// status line on its own tells an operator nothing they can act on.
		const detail =
			(payload && (payload.exception || payload._server_messages)) ||
			`${response.status} ${response.statusText}`;
		throw new Error(String(detail));
	}

	return (payload && payload.message) || {};
}

// -- the run -----------------------------------------------------------------

async function runFetch(request, deskTabId, erpOrigin) {
	const { requestId, portal, csrfToken } = request;
	const includeDetails =
		typeof request.includeDetails === "boolean"
			? request.includeDetails
			: await settings.includeDetailsDefault();

	let portalTabId = null;

	try {
		progress(deskTabId, requestId, "opening", "Opening the fines page…");

		// The server answers with a page to open and a reader to run, or it
		// refuses and says why. A portal with no reader is refused there, not
		// here: the extension must never fall back to "run the nearest reader
		// and see", because a reader written for another portal's markup finds
		// no rows, and no rows is indistinguishable from a fleet with no fines.
		const target = await fetchTarget(erpOrigin, portal);
		if (!target.url || !target.origin || !target.reader) {
			return {
				ok: false,
				message:
					"The server did not name a page and a reader for this portal, so there " +
					"is nothing to open. Nothing was read.",
			};
		}

		if (!(await hasOriginPermission(target.origin))) {
			// A build whose manifest does not grant the origin its own reader
			// needs. Not something an operator can fix from here, so say what is
			// actually wrong rather than sending them to look for a prompt.
			return {
				ok: false,
				message:
					`This build is not allowed to read ${target.origin}, so the fines page ` +
					"cannot be opened. The extension needs rebuilding with that address " +
					"granted - tell the team rather than retrying.",
			};
		}

		// Active on purpose. Chrome may freeze or discard a background tab, and
		// this portal's pagination is client-side - a discarded tab loses the
		// rows already walked, with no way to ask for page three again.
		const tab = await chrome.tabs.create({ url: target.url, active: true });
		portalTabId = tab.id;

		const landed = await waitForFinesPage(portalTabId, SIGN_IN_WINDOW_MS, target);
		if (landed === "tab-closed") {
			return { ok: false, message: "The fines tab was closed before anything was read." };
		}
		if (landed === "timeout") {
			return {
				ok: false,
				message:
					"The fines page never finished loading. Nothing was read - press Fetch Fines " +
					"Now to try again.",
			};
		}

		progress(deskTabId, requestId, "reading", "Reading the fines table…");

		// After landing, not before: signing in navigates away and back, and an
		// injection made earlier would have been torn down with the page it was
		// injected into.
		try {
			await injectReader(portalTabId, target.reader);
		} catch (err) {
			return {
				ok: false,
				message:
					`The reader for this portal could not be loaded (${err && err.message}). ` +
					"Nothing was read.",
			};
		}

		const read = await chrome.tabs.sendMessage(portalTabId, {
			type: "TFF_READ",
			requestId,
			includeDetails,
			budgetMs: READ_BUDGET_MS,
			// Present only for portals whose results have no addressable URL, so
			// the reader has to submit the search itself. Forwarded untouched and
			// never stored here: the worker holds it for the length of one
			// message, the same way it holds the target URL.
			prefill: target.prefill || null,
		});

		if (!read || !read.ok) {
			return { ok: false, message: describeReadFailure(read && read.state) };
		}

		progress(
			deskTabId,
			requestId,
			"staging",
			`Sending ${read.rows.length} fine(s) to ERPNext…`,
			read.rows.length
		);

		const result = await ingest({
			erpOrigin,
			csrfToken,
			portal,
			rows: read.rows,
			truncated: read.truncated,
		});

		if (await settings.closeTabWhenDone()) {
			await chrome.tabs.remove(portalTabId).catch(() => {});
			portalTabId = null;
		}

		// Coverage, where the reader reports it. A portal that splits its fines
		// across several lists can be read completely or partly, and the count
		// alone cannot tell those apart - so the lists that yielded nothing are
		// named rather than folded into the total.
		const unread = (read.coverage || []).filter((c) => c.read === null);
		const coverageNote = unread.length
			? ` Not read: ${unread.map((c) => `${c.list} (${c.reason})`).join(", ")}.`
			: "";

		return {
			ok: true,
			staged: result.staged,
			truncated: read.truncated || unread.length > 0,
			message:
				(result.message || `${read.rows.length} fine(s) sent to ERPNext.`) + coverageNote,
		};
	} catch (error) {
		return { ok: false, message: String((error && error.message) || error) };
	} finally {
		inFlight = null;
	}
}

function describeReadFailure(state) {
	if (state === "not-signed-in") {
		return (
			"The sign-in was not completed, so there was nothing to read. The fines page needs " +
			"a UAE Pass sign-in approved on your phone."
		);
	}
	if (state === "no-table") {
		return (
			"Signed in, but the fines table never appeared. Nothing was read - if the page looks " +
			"normal in the tab, press Fetch Fines Now again."
		);
	}
	if (state === "session-expired") {
		return (
			"The portal ended the session before the read finished, so this is a partial " +
			"answer at best. Nothing already sent is wrong, but fines may be missing - press " +
			"Fetch Fines In This Browser again to read it in one go."
		);
	}
	if (state === "no-prefill") {
		return (
			"This portal is searched by traffic file, and the server sent no number to search " +
			"with. Check that the portal has an active credential carrying one. Nothing was read."
		);
	}
	if (state === "no-form" || state === "no-search-button") {
		return (
			"The portal's search form was not where this build expects it, so the search was " +
			"never submitted. Nothing was read - the page has probably changed and the reader " +
			"needs updating."
		);
	}
	if (state === "no-results") {
		return (
			"The search was submitted but the portal never showed a result. Nothing was read - " +
			"it may have asked for a sign-in or a challenge in the tab."
		);
	}
	return "The fines page could not be read. Nothing was staged.";
}

// -- entry point -------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
	if (!message || message.type !== "TFF_START") return undefined;

	// Only a desk tab may start a fetch. The content script that relays these
	// is injected on the ERP origins alone, but a check here means a compromised
	// page cannot start one by messaging the worker directly.
	if (!sender.tab || !sender.tab.id || !sender.origin) {
		sendResponse({ rejected: true, message: "Fetches can only be started from ERPNext." });
		return undefined;
	}

	if (inFlight) {
		sendResponse({
			rejected: true,
			message: "A fetch is already running. Wait for it to finish before starting another.",
		});
		return undefined;
	}

	if (!message.portal) {
		sendResponse({ rejected: true, message: "No portal was named, so there is nothing to fetch." });
		return undefined;
	}

	const deskTabId = sender.tab.id;
	const erpOrigin = sender.origin;

	inFlight = { requestId: message.requestId, deskTabId };
	sendResponse({ accepted: true });

	runFetch(message, deskTabId, erpOrigin).then((outcome) => {
		done(deskTabId, message.requestId, outcome);
	});

	return undefined;
});

// Progress raised inside the portal tab is addressed to the worker, because a
// content script cannot message another tab. Forward it to the one desk tab
// that asked - never broadcast, since a second desk tab would then show a
// running fetch it did not start.
chrome.runtime.onMessage.addListener((message, sender) => {
	if (!message || message.type !== "TFF_PROGRESS") return undefined;
	if (!sender.tab || !inFlight) return undefined;
	if (message.requestId !== inFlight.requestId) return undefined;
	toDesk(inFlight.deskTabId, message);
	return undefined;
});
