# What the extension needs from the server

The extension does not parse a fine. It reads the rendered rows and hands them
up exactly as the page produced them, so the server keeps running the parser it
already has and the two halves cannot drift into two different ideas of what a
fine is.

That needs two whitelisted methods, neither of which exists yet. Both belong in
`transport/transport/fine_sync/service.py`, which is owned by the fine-sync
session — **this file is the specification, not an instruction to add them.**

---

## 1. `get_client_fetch_target(portal)`

Called first, over `GET`, so no CSRF token is involved.

```
GET /api/method/transport.transport.fine_sync.service.get_client_fetch_target?portal=<name>
```

**Returns**

```json
{ "message": {
    "url": "https://…the page to open…",
    "origin": "https://www.tamm.abudhabi",
    "path_prefix": "/wb/adp/pay-traffic-fines/companies",
    "reader": "tamm"
} }
```

**It does not return the traffic file number.** An earlier shape did, and the
extension built the URL itself. That was TAMM's shape mistaken for every
portal's: TAMM carries the fleet in a query parameter, a login-based portal
carries it in the session and takes no parameter at all. An extension building
its own URLs has to know that difference and is wrong about it the first time a
portal moves.

So the server builds the target. The fleet identifier is read from the
credential, embedded in a URL that goes straight to `chrome.tabs.create`, and is
never a value the extension holds - one less place it can be stored or logged.

**`reader`** names the reading code to run, and is the `client_reader` the
portal's fetcher declares. The generated extractors are keyed by the same name,
so the reader that runs is always the one that portal's fetcher named.

**Refusals are the interesting case.** The server answers with a reason, not a
generic failure, and the extension shows the server's wording:

| Situation | What the server says |
|---|---|
| No fetcher at all | `No fetcher is implemented for …` |
| Fetcher, no reader | `… has no client fetch path.` |
| Fetcher that knows why | its own message - RTA explains that its fines page has never been captured and what would unblock it |

That distinction is the point. "No client fetch path" and "nobody has ever seen
this portal's fines table, here is what would unblock it" are different answers,
and flattening them loses the only part that tells somebody what to do next.

**Requirements**

- `_check_permission()`, `is_enabled`, `has_written_authorization`.
- Return no reader ⇒ refuse. A target without a reader would send the browser to
  a real page with nothing to read it, and an unread page reports no fines.

---

## 2. `ingest_client_fetch(portal, rows, truncated, fetched_at)`

Called once, over `POST`, with the desk session's cookies and its
`X-Frappe-CSRF-Token` header.

```json
{
  "portal": "TAMM",
  "truncated": false,
  "fetched_at": "2026-09-05T12:41:08.221Z",
  "rows": [ … ]
}
```

**Note what is absent: the traffic file number.** The server must re-read it
from the portal credential rather than accept it here. A browser that could name
the traffic file could attach a batch of fines to any fleet on the site.

### The row shape

Each row is what `EXTRACT_ROWS_JS` returns, untouched — the same dictionary
`_to_fine()` already consumes, with `_details` attached by the same code path
that fills it server-side. Keys are the portal's own column ids, so they change
when the portal is re-themed; that is why the whole row is archived rather than
a chosen subset.

```json
{
  "fineNumber": "000000000",
  "dateTime": "01 Jan 2026 - 12:00 pm",
  "amount": "300.00 400.00",
  "plateNumber": "…",
  "types": "1 Black Point",
  "source": "…",
  "_code": "…",
  "_emirate": "…",
  "_number": "…",
  "_details": {
    "Ticket Number": "000000000",
    "Description": "…",
    "Fine Location": "…",
    "Status": "…",
    "Ticket Type": "…"
  }
}
```

*(Values above are placeholders. Real rows carry fleet data and must not be
pasted into tickets, commits or comments.)*

`_details` is absent when the operator turned detail reading off, and `{}` when
a fine's modal would not open — best effort per row, matching
`_details_for_visible()`.

### Suggested implementation

```python
fetcher = get_fetcher(portal_doc, credential)
transform = getattr(fetcher, "_to_fine", None)
if not getattr(fetcher, "client_reader", None) or transform is None:
    frappe.throw(_("{0} has no client fetch path.").format(portal_doc.name))
fines = [f for f in (transform(row) for row in rows) if f]
```

`_to_fine()` is a pure transform and touches no browser, so the fetcher can be
constructed and never started.

**It is not portal-general, and the guard is the point.** `_to_fine()` is
defined on `TammFetcher` (`portals/tamm.py:455`) and on `MoiFetcher`
(`portals/moi.py:356`), but not on the base fetcher and not on `SrtaFetcher` —
it is a convention across two of the three, not a contract. Without the check,
this method signature promises to accept any portal and answers a portal that
has no transform with an `AttributeError` instead of something an operator can
read.

Rejecting those portals with a typed error is the right shape while TAMM is the
only portal the extension targets. Promoting a public `fines_from_rows()` onto
the base — `NotImplementedError` by default, overridden where a transform
exists — is the better answer, but it is a refactor worth doing when a second
portal actually needs one rather than on spec.

From there the existing path applies unchanged: a Fine Sync Run, `_stage_fine()`
per fine, dedup on ticket number.

### `truncated` must survive

`true` means the read stopped at its budget with pages still unread. The rows
that arrived are real, but the batch is **not** the whole list, and the absence
of a fine from it does not mean it does not exist. `FetchResult` already carries
this flag and `_collect_all_pages()` was written to protect the distinction —
the run record should show it the same way a server-side partial read does.

### Requirements

- `_check_permission()`, then the same portal checks the other entry points make
  (`is_enabled`, `has_written_authorization`).
- Reject a `rows` payload that is not a list, and cap its length.
- Treat every value in a row as untrusted text. It arrives from a browser, and
  a row is not evidence that a page was ever visited.
- Record the run as client-fetched, so a later question about where a fine came
  from has an answer.

---

## What the extension does not do

- It never asks for a credential, and never types one.
- It never answers a CAPTCHA. The operator signs in themselves, in their own
  browser, which is not the same thing as a program solving one.
- It never writes the traffic file number to storage or to a log.
- It never runs unattended. There is no alarm, no schedule, and no retry loop —
  a fetch begins when somebody presses the button.
