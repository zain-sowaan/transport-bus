// Orchestrates one fetch: open the portal, wait for the operator, read, hand up.
//
// Nothing here parses a fine. The rows go to ERPNext exactly as the page gave
// them, and the server runs the same parser it has always run - which is what
// keeps the two halves from drifting into two different ideas of what a fine is.

const PORTAL_ORIGIN = "https://www.tamm.abudhabi";
const FINES_PATH = "/wb/adp/pay-traffic-fines/companies";

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

function finesUrl(trafficFileNumber) {
	const url = new URL(FINES_PATH, PORTAL_ORIGIN);
	url.searchParams.set("lang", "en");
	url.searchParams.set("companyTcf", trafficFileNumber);
	return url.toString();
}

const onFinesPage = (url) => {
	try {
		const parsed = new URL(url);
		return parsed.origin === PORTAL_ORIGIN && parsed.pathname.startsWith(FINES_PATH);
	} catch {
		return false;
	}
};

/** Resolve once the tab is sitting on the fines page with its document loaded.
 *
 * Signing in navigates away to the identity provider and back, which tears down
 * the content script and everything it was doing. So the read is not sent until
 * the tab has landed back on the portal - waiting for the *page*, not for the
 * operator, which is why this survives however many redirects the sign-in takes.
 */
function waitForFinesPage(tabId, timeoutMs) {
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
			if (onFinesPage(tab.url || "")) finish("ready");
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
			if (tab && tab.status === "complete" && onFinesPage(tab.url || "")) finish("ready");
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
		headers: { Accept: "application/json" },
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

		const target = await fetchTarget(erpOrigin, portal);
		const trafficFileNumber = target.traffic_file_number;
		if (!trafficFileNumber) {
			return {
				ok: false,
				message:
					"This portal has no active credential carrying a traffic file number, so " +
					"there is no page to open. Add one in ERPNext first.",
			};
		}

		// Active on purpose. Chrome may freeze or discard a background tab, and
		// this portal's pagination is client-side - a discarded tab loses the
		// rows already walked, with no way to ask for page three again.
		const tab = await chrome.tabs.create({ url: finesUrl(trafficFileNumber), active: true });
		portalTabId = tab.id;

		const landed = await waitForFinesPage(portalTabId, SIGN_IN_WINDOW_MS);
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

		const read = await chrome.tabs.sendMessage(portalTabId, {
			type: "TFF_READ",
			requestId,
			includeDetails,
			budgetMs: READ_BUDGET_MS,
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

		return {
			ok: true,
			staged: result.staged,
			truncated: read.truncated,
			message: result.message || `${read.rows.length} fine(s) sent to ERPNext.`,
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
