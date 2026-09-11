# Testing the client-side fetch

Written for the run-through that decides whether this goes to UAT. It assumes a dev bench on `http://localhost:8001` and a Chrome you can load an unpacked extension into.

## What had to exist first

The scaffold could not be tested when it landed: the two server methods it calls
did not exist, so step one of any run-through returned 404. They exist now, in
`fine_sync/service.py`:

- `get_client_fetch_target(portal)` — returns `{url, origin, path_prefix,
  reader}`: the page to open and the reader to run against it. It does **not**
  return the traffic file number.
- `ingest_client_fetch(portal, rows, truncated, fetched_at)` — POST only.

Two other things changed to make a test possible at all.

**The button's gate.** It was `fetch_mode !== "Operator Assisted"`, which hid the
button on the one portal this exists for — TAMM is configured `Automated`, and it
is precisely the portal the server cannot read reliably. The gate is now
`is_enabled`, and portals with no reader are refused by name on click.

**A new field, `fetch_source`, on Traffic Fine Sync Run.** Blank means the server
read the rows itself. `Operator Browser` means they were posted from a person's
Chrome. Without it there was no way to answer "where did this fine come from".

## Before you start

The dev bench needs its queue up, or the button reports a failure instead of
queuing silently into nothing:

```bash
redis-cli -p 13001 ping     # cache
redis-cli -p 11001 ping     # queue
pgrep -af 'frappe worker'   # at least one --queue long
```

If Redis is down, start it from the bench root:

```bash
redis-server config/redis_cache.conf --daemonize yes
redis-server config/redis_queue.conf --daemonize yes
```

## Install

1. **Do not commit a host edit.** `manifest.json` already carries
   `http://localhost:8001/*`, which is all a dev bench needs. For UAT the
   operator edits the manifest **on their own machine** — the hostname is client
   infrastructure and this repository is public.
2. `chrome://extensions` → **Developer mode** → **Load unpacked** → pick
   `apps/transport/extension` **itself**, not a packaged copy of it.

   This matters more than it looks. `tools/package.sh` produces a zip for
   somebody else to load, and a zip is a *snapshot*: once it is unpacked
   somewhere and loaded, nothing you change in the repository reaches Chrome
   until you rebuild and replace it. A build made before a reader existed has
   no reader, and the extension then refuses the portal by name - which reads
   as a bug in the reader rather than as a stale build. Loading the repository
   directory instead means a change needs only the reload arrow.

   If you are testing a packaged build on purpose, rebuild it after every
   change:

   ```
   extension/tools/package.sh --erp-origin http://127.0.0.1:8001 -o /tmp/tff.zip
   ```

   and check `content/readers/` in the result holds the reader you expect.
3. Nothing to do in ERPNext. The desk script loads from `doctype_js`. If the
   button never appears, check that `bench build --app transport` has run and
   that `assets/transport/js/traffic_fine_portal_fetch.js` exists — and that no
   stale Client Script on Traffic Fine Portal is still enabled.
4. Reload the desk, open an enabled portal record. **Fetch Fines In This
   Browser** appears under the top-right menu.

If the button says the extension is missing, the manifest's origins do not match
the desk's origin. Scheme and port both count — `localhost` and `127.0.0.1` are
different origins.

## The run-through

1. Press the button. A tab opens on the portal's fines page.
2. If there is no live sign-in, the portal sends you to UAE Pass. **Sign in
   yourself and approve the push on your phone.** Nothing types a credential for
   you, and if a CAPTCHA appears you answer it — that is the whole point of this
   path.
3. **Leave the tab in the foreground while it reads.** Pagination is client-side
   and fires no network request, so later pages exist only in that tab's memory.
   A backgrounded tab can be discarded, and a discarded tab cannot be asked for
   page three.
4. When it finishes, check **Traffic Fine Sync Run**, newest first.

## What a good result looks like

| Field | Expected |
|---|---|
| `fetch_source` | `Operator Browser` |
| `status` | `Completed`, or `Completed with Errors` if the read was cut short |
| `fines_found` | rows the page produced that parsed as fines |
| `fines_new` | those not already staged — a second run should be near 0 |
| Per-vehicle rows | one per fine; unmatched plates say so explicitly |

**A truncated read must never say `Completed`.** If the browser stopped with
pages unread, the run says `Completed with Errors` and the log explains that the
missing fines were never seen — which is not the same as them not existing.

## Checks worth making deliberately

- **Run it twice.** The second run should stage almost nothing. Dedup is on
  ticket number; if `fines_new` matches `fines_found` both times, dedup is broken.
- **Close the tab mid-read.** Nothing should be staged. A batch that cannot be
  described honestly is not written.
- **Press it on a portal with no reader** (any enabled non-TAMM one). Expect
  "has no client fetch path", by name — not a stack trace, and no run record
  left behind suggesting the portal was read.
- **Confirm the traffic file number never reaches the desk page.** Open DevTools
  on the desk tab and watch the console and the `postMessage` traffic. The
  extension is handed a URL, not a fleet identifier; nothing carrying the number
  should appear in the desk page's JavaScript or in any window message.

- **Press it on RTA Dubai.** Expect a refusal explaining that RTA's fines page
  has never been captured, and what would unblock it — not "not supported", and
  not a stack trace. No run record should be left behind.

## What this does not do, and UAT should agree to before it starts

- **There is no schedule.** Fines arrive when somebody presses the button. The
  hourly sweep has nothing to run on this path. Moving the read to an operator's
  laptop means fines arrive only when that laptop is on and someone signs in.
  That is a change in how the system behaves, not a detail.
- **It is TAMM only.** MOI, EVG and SRTA still fetch server-side and still meet
  CAPTCHAs there. This does not retire the sign-in console.
- **Loading unpacked does not scale.** More than one operator needs Chrome
  enterprise policy or a Web Store listing, which is a procurement question with
  a longer lead time than the code.

## Portals other than TAMM

The extension is no longer TAMM-shaped. The server names the page and the
reader; the extension asks Chrome for permission on that origin when the button
is pressed, then injects the reader for it. Nothing about a portal is compiled
into the extension.

Adding one is four things, and only the first needs a person:

1. **Someone signs in and captures the fines table markup.** This is the whole
   blocker. It cannot be reasoned out, guessed, or fetched from the bench — the
   table is behind the portal's login.
2. Three extraction constants on the fetcher, modelled on the ones at the top of
   `portals/tamm.py`, plus `_to_fine()` mapping rows onto `FetchedFine`.
3. `client_reader = "<name>"` on the fetcher, and `content/readers/<name>.js`.
4. `python3 extension/tools/generate_extractors.py` — it walks every portal
   module, keys the output by each fetcher's `client_reader`, and **fails
   loudly** if a fetcher declares a reader without defining what it reads with.

### RTA Dubai specifically

`RtaFetcher` is registered and deliberately cannot fetch. `fetch_implemented`
and `client_reader` are both left at their refusing defaults, so the button
refuses with the actual reason rather than looking unrecognised.

It is blocked on step 1 and nothing else. RTA's sign-in is username, password
and an OTP to a person's phone, and it is one of the two portals
`assert_no_captcha` names — all of which the extension route handles fine,
because a person is sitting there. What no amount of engineering supplies is the
markup of a page nobody has opened.

**Deployment note:** `fetcher_key` must be set to `rta` on the RTA portal record
for the fetcher to be reachable at all. It is set on the development site; every
other environment needs the same, alongside the three Place fields.
