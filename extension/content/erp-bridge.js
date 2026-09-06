// Runs on the ERPNext desk. The only thing that connects the two worlds.
//
// A Client Script cannot talk to the extension directly. `externally_connectable`
// is the documented route and it rejects hostnames without a second-level
// domain, so it fails on `localhost` - which is exactly where the desk runs
// during development. This bridge works the same in both places: the page posts
// a window message, this content script relays it to the service worker, and
// results come back the same way.
//
// It also means the Client Script never needs the extension's ID, so the ID can
// change - a reload of an unpacked build changes it - without editing anything
// inside ERPNext.

(() => {
	if (globalThis.__tffBridgeLoaded) return;
	globalThis.__tffBridgeLoaded = true;

	const FROM_PAGE = "transport-fine-fetch/page";
	const FROM_EXTENSION = "transport-fine-fetch/extension";

	// Reloading an unpacked extension does not remove the content script already
	// running in an open page - it severs it. The old instance keeps its
	// listeners, so the button still looks live, but every chrome.runtime call
	// throws "Extension context invalidated". Unhandled, that is a button that
	// does nothing at all and says nothing about why, which during a demo reads
	// as a broken feature rather than a stale tab.
	const STALE_MESSAGE =
		"This page was open while the Fine Fetch extension was reloaded, so the two are " +
		"no longer connected. Reload this page and press the button again - nothing was " +
		"started, and nothing was lost.";

	const alive = () => {
		try {
			// Reading `id` is the cheap test: it is undefined once the context is
			// gone, where getManifest() throws instead.
			return !!(chrome.runtime && chrome.runtime.id);
		} catch {
			return false;
		}
	};

	const version = () => {
		try {
			return chrome.runtime.getManifest().version;
		} catch {
			return null;
		}
	};

	const toPage = (payload) => {
		// Explicit target origin rather than "*": these messages carry fetch
		// results, and a page that framed the desk should not receive them.
		window.postMessage({ source: FROM_EXTENSION, ...payload }, window.location.origin);
	};

	// -- page -> service worker -------------------------------------------

	window.addEventListener("message", (event) => {
		// Only this document, only this origin. Without both checks any frame
		// on the page could start a portal fetch.
		if (event.source !== window) return;
		if (event.origin !== window.location.origin) return;

		const data = event.data;
		if (!data || data.source !== FROM_PAGE) return;

		if (data.kind === "PING") {
			// Deliberately silent when severed. The desk treats no answer as "no
			// extension" and shows its own install-and-reload message, which is
			// the right advice; answering with a version would tell it the bridge
			// is healthy moments before the next call throws.
			if (alive()) toPage({ kind: "PONG", requestId: data.requestId, version: version() });
			return;
		}

		if (data.kind !== "FETCH") return;

		if (!alive()) {
			toPage({ kind: "DONE", requestId: data.requestId, ok: false, message: STALE_MESSAGE });
			return;
		}

		try {
			chrome.runtime.sendMessage(
				{
					type: "TFF_START",
					requestId: data.requestId,
					portal: data.portal,
					includeDetails: !!data.includeDetails,
					csrfToken: data.csrfToken,
				},
				(ack) => {
					// A service worker that failed to wake leaves lastError set and
					// no reply. Reported rather than swallowed: the operator is
					// watching a dialog that would otherwise sit there forever.
					if (chrome.runtime.lastError) {
						toPage({
							kind: "DONE",
							requestId: data.requestId,
							ok: false,
							message:
								"The Fine Fetch extension did not respond. Check that it is enabled, " +
								"then press the button again.",
						});
						return;
					}
					if (ack && ack.rejected) {
						toPage({ kind: "DONE", requestId: data.requestId, ok: false, message: ack.message });
					}
				}
			);
		} catch {
			// The context can be torn down between the check above and this call.
			toPage({ kind: "DONE", requestId: data.requestId, ok: false, message: STALE_MESSAGE });
		}
	});

	// -- service worker -> page -------------------------------------------

	chrome.runtime.onMessage.addListener((message) => {
		if (!message || !message.type) return undefined;
		if (message.type === "TFF_PROGRESS") {
			toPage({ kind: "PROGRESS", requestId: message.requestId, stage: message.stage, message: message.message, count: message.count });
		} else if (message.type === "TFF_DONE") {
			toPage({
				kind: "DONE",
				requestId: message.requestId,
				ok: message.ok,
				message: message.message,
				staged: message.staged,
				truncated: message.truncated,
			});
		}
		return undefined;
	});

	// Lets the desk tell "extension missing" from "extension busy" the moment
	// the form loads, instead of after a fetch that was never going to start.
	const loaded = version();
	if (loaded) toPage({ kind: "READY", version: loaded });
})();
