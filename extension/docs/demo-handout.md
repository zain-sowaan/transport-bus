# Transport Fine Fetch — setting it up to try

Everything below is done once and takes about five minutes. You will not need to
edit any files, and nothing here needs a developer.

You will be given a **zip file** and a **web address** for the test system. The
extension already knows that address — you do not have to type it anywhere.

---

## 1. Open Chrome's extensions page

Type this into the address bar and press Enter:

```
chrome://extensions
```

## 2. Turn on Developer mode

Top right of that page, there is a switch labelled **Developer mode**. Turn it
on. Three buttons appear on the left.

> Chrome may show a warning about developer extensions each time it starts. That
> is normal for an extension installed this way rather than from the Chrome Web
> Store. You can dismiss it.

## 3. Load the extension

1. Unzip the file you were given. You will get a folder.
2. Press **Load unpacked**.
3. Choose that folder — the one containing `manifest.json`. Not a folder above
   it, and not the zip.

**Transport Fine Fetch** now appears in the list.

> **Reload any ERPNext tab you already had open.** A page that was open before
> the extension was installed is not connected to it. This is also true any time
> the extension is updated — reload the page, and the button works again.

## 4. Check it worked

1. Open the test system at the address you were given and sign in.
2. Go to **Traffic Fine Portal** and open **Abu Dhabi Police / TAMM**.
3. Top right, under the **⋯** menu, there is **Fetch Fines In This Browser**.

If the button is missing, reload the page once — the extension only attaches to
pages opened after it was installed.

---

## If you are told the address has changed

Only if someone tells you so. Otherwise skip this.

1. On `chrome://extensions`, press **Details** on Transport Fine Fetch, then
   **Extension options**.
2. Paste the new address into **ERPNext address** and press **Connect**.
3. Chrome asks whether to allow access to it — press **Allow**.

---

## What happens when you press it

1. A new tab opens on the Abu Dhabi government fines page.
2. **If it asks you to sign in, that is the portal asking, not us.** Sign in as
   you normally would and approve the request on your phone. The extension never
   types a password and never answers a security check for you — you do, in your
   own browser, which is the entire point of this approach.
3. **Leave that tab in front while it reads.** It pages through the list in the
   tab itself, so switching away can lose what it has read. It will say when it
   is finished.
4. The fines appear back in ERPNext under **Traffic Fine Sync Run**.

## Things that are meant to happen

- **Only Abu Dhabi (TAMM) works.** Other authorities are listed and will tell
  you they are not available yet. That is the current state, not a fault.
- **A second run finds almost nothing new.** Fines already recorded are not
  recorded twice.
- **If it stops early it says so**, and the result is marked as incomplete
  rather than complete. A short list is never presented as the full picture.

## If something goes wrong

| What you see | What it means |
|---|---|
| No **Fetch Fines In This Browser** button | Reload the ERPNext page. If it is still missing, the address may have changed — see the section above. |
| *This portal has no reader* | Expected on authorities other than Abu Dhabi. |
| The fines tab never finishes loading | The government site was slow or the sign-in did not complete. Nothing was saved; press the button again. |
| *This page was open while the extension was reloaded* | Exactly what it says. Reload the ERPNext page and press the button again. Nothing was started and nothing was lost. |
| The button does nothing at all | Same cause as above on an older build. Reload the page. |
| Anything mentioning a tunnel or connection | The test system is probably not running. Tell the team rather than retrying. |

Nothing you do here can damage live data — the test system is a copy. If you are
unsure at any point, stop and ask rather than pressing on.
