// Two behaviour switches, and the ERPNext address.
//
// The address is a setting rather than a line in manifest.json on purpose. A
// tunnel address changes every time the tunnel restarts, and whoever is testing
// is not always the person who can edit a file, reload an unpacked extension and
// know which of the two places to change. Pasting it here does the same work:
// permission for the origin is requested, and the desk bridge is registered
// against it.

const FIELDS = {
	closeTabWhenDone: true,
	includeDetails: true,
};

const BRIDGE_SCRIPT_ID = "erp-bridge";

const saved = document.getElementById("saved");
const originInput = document.getElementById("erpOrigin");
const originStatus = document.getElementById("originStatus");
let clearMessage = null;

function announce(text) {
	saved.textContent = text;
	clearTimeout(clearMessage);
	clearMessage = setTimeout(() => {
		saved.textContent = "";
	}, 2500);
}

function say(text, kind) {
	originStatus.textContent = text;
	originStatus.className = `status ${kind || ""}`.trim();
}

/** The origin of what was typed, or null.
 *
 * Deliberately strict about the scheme. A bare host is ambiguous - Chrome needs
 * to know whether to grant http or https - and guessing produces a permission
 * for an origin the desk is not actually served on, which fails later and
 * further away.
 */
function readOrigin(text) {
	const raw = (text || "").trim();
	if (!raw) return null;
	try {
		const url = new URL(raw);
		if (url.protocol !== "https:" && url.protocol !== "http:") return null;
		return url.origin;
	} catch {
		return null;
	}
}

/** Point the extension at an ERPNext address.
 *
 * Two things have to happen together and both need the origin granted first:
 * the service worker must be allowed to POST fines to it, and the bridge has to
 * run in its pages so the button can talk to the extension at all. Registering
 * the bridge is what a manifest `content_scripts` entry would have done, done
 * at run time because the address is not known when the extension is built.
 */
async function connect(origin) {
	const origins = [`${origin}/*`];

	let granted = await chrome.permissions.contains({ origins });
	if (!granted) {
		granted = await chrome.permissions.request({ origins });
	}
	if (!granted) {
		say("Chrome was not given access to that address, so nothing was changed.", "bad");
		return false;
	}

	// Replace rather than add. Leaving the previous address registered would
	// mean an old tunnel kept working silently, which is how somebody ends up
	// demonstrating against yesterday's data.
	try {
		await chrome.scripting.unregisterContentScripts({ ids: [BRIDGE_SCRIPT_ID] });
	} catch {
		// Nothing registered yet. Fine - this is the first address.
	}

	await chrome.scripting.registerContentScripts([
		{
			id: BRIDGE_SCRIPT_ID,
			matches: origins,
			js: ["content/erp-bridge.js"],
			runAt: "document_idle",
			allFrames: false,
		},
	]);

	await chrome.storage.sync.set({ erpOrigin: origin });
	return true;
}

document.getElementById("saveOrigin").addEventListener("click", async () => {
	const origin = readOrigin(originInput.value);
	if (!origin) {
		say(
			"That does not look like a web address. Include https:// at the front, " +
				"for example https://example.ngrok-free.app",
			"bad"
		);
		return;
	}

	originInput.value = origin;
	say("Connecting…");
	try {
		if (await connect(origin)) {
			say(`Connected to ${origin}. Open ERPNext there and reload the page.`, "ok");
		}
	} catch (error) {
		say(`Could not connect: ${(error && error.message) || error}`, "bad");
	}
});

async function load() {
	const stored = await chrome.storage.sync.get({ ...FIELDS, erpOrigin: "" });
	for (const name of Object.keys(FIELDS)) {
		document.getElementById(name).checked = !!stored[name];
	}
	// A packaged build carries the address it was built for, so a reviewer has
	// nothing to paste. Anything they set themselves wins over it - the baked
	// value is a default, not a lock.
	const baked = globalThis.TFF_DEFAULT_ERP_ORIGIN || "";
	const origin = stored.erpOrigin || baked;
	originInput.value = origin;

	if (!origin) {
		say("Not set yet. Paste the address you open ERPNext at, then press Connect.");
		return;
	}
	if (!stored.erpOrigin && baked) {
		// Built-in and already granted through the manifest, so there is nothing
		// to request and nothing for the reviewer to do.
		const ready = await chrome.permissions.contains({ origins: [`${baked}/*`] });
		say(
			ready
				? `Connected to ${baked}.`
				: `Set to ${baked}, but Chrome has not granted access. Press Connect.`,
			ready ? "ok" : "bad"
		);
		return;
	}
	// Chrome can revoke an optional permission without telling the page, so the
	// stored address is not proof the extension can still reach it.
	const live = await chrome.permissions.contains({ origins: [`${origin}/*`] });
	say(
		live
			? `Connected to ${origin}.`
			: `Access to ${origin} was withdrawn. Press Connect to grant it again.`,
		live ? "ok" : "bad"
	);
}

for (const name of Object.keys(FIELDS)) {
	document.getElementById(name).addEventListener("change", async (event) => {
		await chrome.storage.sync.set({ [name]: event.target.checked });
		announce("Saved.");
	});
}

load();
