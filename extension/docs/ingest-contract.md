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
{ "message": { "traffic_file_number": "…" } }
```

**Why it exists.** The extension has to build the portal URL, and the traffic
file number is the only thing in it that names the fleet. Asking the server
keeps the number out of the desk page's JavaScript and out of the window message
between page and extension — the same standard `get_relay_mobile()` already
applies to the sign-in phone number, and for the same reason.

**Requirements**

- Run `_check_permission()`. This is the gate on who may start a fetch.
- Read the number from the active `Traffic Fine Portal Credential` for that
  portal, exactly as `enqueue_operator_assisted_sync` does today.
- Throw the existing "no active credential" message when there isn't one; the
  extension surfaces the server's own wording.
- Return nothing else. Not the mobile number, not the session state, not the
  credential name.

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
fines = [f for f in (fetcher._to_fine(row) for row in rows) if f]
```

`_to_fine()` is a pure transform and touches no browser, so the fetcher can be
constructed and never started. It is private today; a public `fines_from_rows()`
on the fetcher would say plainly that this is a supported entry point rather
than something reaching into internals — your call.

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
