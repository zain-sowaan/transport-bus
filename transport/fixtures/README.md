# Fixtures

## Why there are two Custom Field files

`bench migrate` imports a fixture file **record by record**, and stops the whole
file at the first record whose DocType does not exist on the site. Records
before that point are written; everything after it is lost. It says so only on
stdout — `Skipping fixture syncing from the file <name>` — which is not
somewhere anyone looks during a deploy.

So a single `custom_field.json` holding all 59 fields had a failure mode wildly
out of proportion to its cause. `Place` belongs to **fleetify**, and on a site
whose fleetify predates that doctype the three Place records — which happened to
be **#1, #2 and #3 of 59** — killed the file on its first record and took the
other 56 with them, across eight doctypes with nothing to do with fleetify. A
UAT site ran a month that way. The visible symptom was every report, list view
and card touching a custom field failing with an unknown-column database error
naming the column and never the cause.

Worth seeing how unstable that was: export orders records by `idx asc, creation
asc`, so the position of the Place records is incidental. A re-export that moved
them to the end would have masked the bug entirely, and one that moved them back
to the front would have resurrected it. Measured: bad record last, 2 of 2 good
fields survive; bad record first, 0 of 2.

| file | fields | depends on |
|---|---|---|
| `custom_field.json` | 56 | erpnext + fleetify's Rental Vehicle / Driver |
| `custom_field_place.json` | 3 | fleetify's **Place** doctype |

Now the same missing dependency costs 3 fields, the other 56 install normally,
and the skip message names a file whose whole subject is Place.

## Editing them

`custom_field.json` is generated. Add a field in the UI, then:

    bench --site <site> export-fixtures --app transport

The `fixtures` hook filters `dt != "Place"`, so export can never pull the Place
fields back into the main file. That filter is load-bearing — removing it
silently undoes the split at the next export.

**`custom_field_place.json` is hand-maintained.** Frappe names an exported
fixture after its doctype (`frappe.scrub(doctype) + ".json"`), so two Custom
Field entries in the hook would write to the same path and one would overwrite
the other. Export therefore never regenerates this file. A new field on Place
must be added to it **by hand**, or copied out of `custom_field.json` before an
export drops it.

## Adding a field on another app's doctype

Ask first whether the whole app should fail without it. If not — and it usually
should not — give it its own file, the way Place has one. The cost of getting
this wrong is not the field; it is every other field shipping alongside it.
