# Transport Fine Fetch — browser extension

Reads the company traffic fines page **in the operator's own Chrome** and hands
the rows to ERPNext, instead of driving a browser on the server.

The reason is not preference. A headless browser on a datacentre address is the
profile reCAPTCHA scores worst, and hammering the portal from one burned our
score to the point where five sign-ins in a row were challenged. The operator's
real browser, on the office connection, is the profile that passes — and it is
the same person doing the same sign-in they would do by hand.

An ERPNext Client Script cannot do this on its own. A tab the desk opens to
another origin is one the desk cannot read: no cookies, no DOM, no credentialed
request. An extension holding `host_permissions` for the portal can, which is
the only reason this directory exists.

## Status

**Scaffold. Not yet wired end to end.** The two server methods it calls do not
exist — see [`docs/ingest-contract.md`](docs/ingest-contract.md), which is the
specification for them. They belong in `fine_sync/service.py`, owned by the
fine-sync session, so they are written up here rather than added.

Nothing in this directory is loaded by the Frappe app. It ships alongside it the
way `deploy/` does: files a human installs somewhere else.

## Layout

```
manifest.json                     MV3. Edit host_permissions for your ERP host.
background.js                     Opens the portal tab, waits, reads, posts up.
content/readers/<reader>.js       Runs in the portal tab: the wait, the pager walk,
                                  the detail modals. One per portal, injected by
                                  name - the server says which.
content/extractors.generated.js   GENERATED. The three reading functions per
                                  portal, lifted verbatim from the Python
                                  fetchers and keyed by client_reader.
content/erp-bridge.js             Relays between the desk page and the extension.
options.html / options.js         Two settings.
erpnext/…client_script.js         Paste into a Client Script. The whole ERPNext
                                  footprint: one button.
tools/generate_extractors.py      Regenerates the extractors from tamm.py.
docs/ingest-contract.md           What the server must accept. For the peer session.
```

## The extractors are generated, not copied

`portals/tamm.py` does not parse HTML in Python. It ships three arrow functions
into the page through `page.evaluate()` — `EXTRACT_ROWS_JS`, `NEXT_PAGE_JS`,
`READ_PANEL_JS` — and those constants are already valid JavaScript. The
extension needs the same three, and a hand-made copy would drift the first time
somebody re-themes the portal and fixes only one side.

So they are lifted out of the Python at development time:

```bash
python3 extension/tools/generate_extractors.py
```

Run it after any change to those constants. The output is committed so the
extension loads without a build step. If a constant is renamed or turned into an
f-string, the generator fails loudly rather than writing a stale file.

The generator reads `tamm.py`; it never writes to it.

## Install, on an operator's machine

1. **Point it at your ERPNext host.** In `manifest.json`, replace
   `https://erp.example.com/*` in both `host_permissions` and the
   `content_scripts` entry. Both need it: one to post results, one to inject the
   bridge. `http://localhost:8001/*` is already there for a dev bench. Portal
   origins need no edit — they are optional permissions, requested per origin
   when the button is pressed.
2. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → pick
   this directory.
3. In ERPNext, create a **Client Script** on DocType `Traffic Fine Portal`,
   Apply To `Form`, and paste
   [`erpnext/traffic_fine_portal.client_script.js`](erpnext/traffic_fine_portal.client_script.js).
4. Reload the desk. Open the portal record — **Fetch Fines In This Browser**
   appears on portals whose Fetch Mode is *Operator Assisted*.

If the button reports the extension is missing, the manifest's origins do not
match the desk's origin. Scheme and port both count.

## What happens when it runs

1. The desk asks the extension to fetch, passing the portal name and the
   session's CSRF token — nothing else.
2. The extension asks the server which page to open. The traffic file number
   comes back over an authenticated call, is used for one navigation, and is
   never stored, logged, or passed through the desk page.
3. A tab opens on the fines page. If there is no live sign-in, the portal sends
   the operator to UAE Pass; they sign in and approve the push on their phone,
   exactly as they would by hand.
4. The read starts when the tab lands back on the fines page — waiting for the
   *page*, not the operator, which is what makes it survive however many
   redirects the sign-in takes.
5. Rows go to ERPNext. The server parses and stages them with the code it
   already uses.

## Known limits

**Leave the fines tab in the foreground while it reads.** Pagination is
client-side and fires no network request, so later pages exist only in that
tab's memory. Chrome can freeze or discard a background tab, and a discarded tab
cannot be asked for page three — it can only be started again from page one. If
the tab is closed mid-read, nothing is staged at all: a batch that cannot be
described honestly is not written.

**Details cost time.** Description, ticket type and location are not in the list
view; each fine has to be opened and closed in turn. Turn it off in the
extension's options for a fast list-only read.

**A partial read is reported as partial.** If the budget runs out with pages
unread, the batch is marked `truncated` all the way through to the run record.
Fines beyond that point were never seen, and their absence is not evidence they
do not exist.

**There is no schedule.** A fetch happens when somebody presses the button.
Moving the browser to a laptop means fines arrive only when that laptop is on,
Chrome is open, and somebody has signed in — the hourly sweep has nothing to run
on this path. That is a real change to how the system behaves and was flagged as
a decision, not a detail.

## Permissions, and the ones deliberately not requested

| Requested | Why |
|---|---|
| `storage` | Two checkboxes on the options page. |
| `tabs` | Open the fines tab, notice when it lands, close it when done. |
| `scripting` | Content scripts on the portal and the desk. |
| `host_permissions` | The ERP host only, to post results and inject the bridge. |
| `optional_host_permissions` | Portal origins, requested one at a time when the button is pressed. Not granted at install, and never for a portal the server has not named. |

**`cookies` is not requested.** An earlier design had the extension read the
portal session cookie and hand it to the server to continue headlessly. That
option is documented in the memo and was not chosen: it still launches a browser
on the server, and whether the portal binds its session to the originating
address has never been tested. Not asking for the permission is the honest
expression of not doing it.

**No credential is ever typed, and no CAPTCHA is ever answered.** If the portal
challenges, the person sitting in front of the tab answers it, which is not the
same thing as a program solving one.

## Packaging a build for somebody else

```bash
tools/package.sh --erp-origin https://your-bench.example.dev -o ~/fine-fetch.zip
tools/package.sh                    # reads extension/.erp-origin if present
```

The address is written into the **staging copy only**: the manifest goes into the
zip with that origin already granted, and `config.js` carries it as the options
page's default, so whoever receives the zip installs it and is finished. The
manifest and `config.js` in this repository are never modified.

That split is deliberate and not just tidiness. This repository is public, and a
tunnel or bench address is a live way in to a system holding real fleet data. It
belongs in a zip handed to one named person, not in source anybody can read.
`extension/.erp-origin` is gitignored for the same reason.

`docs/demo-handout.md` is the install guide to send with the zip. It assumes no
technical background and no file editing.

## Deployment beyond one machine

Loading unpacked is fine for the first operator and useless for a fleet of them.
Silent installation needs Chrome enterprise policy
(`ExtensionInstallForcelist`), and self-hosting under that policy requires
domain-joined machines; without managed Chrome the alternative is an unlisted
Web Store listing and its review. Either way that is a procurement question with
a longer lead time than the code.
