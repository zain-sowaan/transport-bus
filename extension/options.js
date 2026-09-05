// Two settings, stored in sync storage so an operator with several machines
// only sets them once.

const FIELDS = {
	closeTabWhenDone: true,
	includeDetails: true,
};

const saved = document.getElementById("saved");
let clearMessage = null;

function announce(text) {
	saved.textContent = text;
	clearTimeout(clearMessage);
	clearMessage = setTimeout(() => {
		saved.textContent = "";
	}, 2500);
}

async function load() {
	const stored = await chrome.storage.sync.get(FIELDS);
	for (const name of Object.keys(FIELDS)) {
		document.getElementById(name).checked = !!stored[name];
	}
}

for (const name of Object.keys(FIELDS)) {
	document.getElementById(name).addEventListener("change", async (event) => {
		await chrome.storage.sync.set({ [name]: event.target.checked });
		announce("Saved.");
	});
}

load();
