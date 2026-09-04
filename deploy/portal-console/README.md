# Traffic-portal sign-in console

A noVNC view of the server's own browser, so an operator anywhere can complete
a UAE Pass sign-in that has stalled on something only a person can clear.

## What problem this actually solves

UAE Pass sign-in is a push to a phone. That part always needs a human and no
amount of infrastructure changes it. What breaks on a display-less server is
narrower:

- **Headless can't answer a reCAPTCHA challenge.** reCAPTCHA Enterprise scores
  the session and escalates to a visible image challenge when the score is bad.
  Our score is bad from a datacenter IP, and it got worse after a runaway
  scheduler hammered the portal. Headless has nobody in front of it.
- **Headed can't start at all** without an X display: Chromium exits with
  "Missing X server or $DISPLAY", surfacing as a `TargetClosedError` from
  whatever Playwright call was in flight.

Xvfb fixes the second. **Xvfb alone does not fix the first** - it converts a
crash into a browser sitting on a display nobody is watching, which fails at
the end of the wait instead of the start. x11vnc plus noVNC is what puts a
person in front of it.

This does **not** prevent challenges. The IP is unchanged and the automation is
still detectable. The value is that a challenge stops being fatal.

## Layout

```
operator's browser
   │  https + basic auth
   ▼
nginx  :443                     ← the only public listener
   │  proxy + websocket upgrade
   ▼
websockify  127.0.0.1:6080      ← serves noVNC's web assets
   │
   ▼
x11vnc  127.0.0.1:5900          ← -localhost, second password
   │
   ▼
Xvfb  :99  (1500x950x24)        ← where headed Chromium draws
   ▲
   │  DISPLAY=:99
bench workers (Playwright)
```

## Install

```bash
sudo ./install.sh <bench-user>
```

Then the three manual steps the script prints: nginx, `bench set-config -g
portal_console_display ":99"`, and the two Transport Settings fields.

### Why DISPLAY is not set in supervisor

The obvious approach is `environment=DISPLAY=":99"` on the worker programs in
`config/supervisor.conf`. Don't. Bench *generates* that file, so
`bench setup supervisor` silently reverts it and the fault reappears at the
next deploy looking like a new bug. The app sets `DISPLAY` itself in
`browser_fetcher._ensure_display()`, reading `portal_console_display` from site
config and falling back to `:99`. It uses `setdefault`, so a developer running
a bench from a real desktop session keeps their own display.

## Running a sign-in

1. Transport Settings → **UAE Pass Sign-In Mode** = `Operator Signs In At The
   Server`. Relay mode forces headless regardless of Xvfb, so the console shows
   nothing in that mode.
2. Traffic Fine Portal → **Diagnostics → Authenticate Traffic Portal**.
3. Sign in to the console when the browser asks (first visit only - the modal
   embeds it afterwards).
4. Press **Fetch Fines Now**. Answer any challenge in the console, approve the
   push on the phone.
5. The session is banked the instant sign-in succeeds.

### The landmine that will cost you a demo

**Two browsers on one TAMM session is what the portal answers by invalidating
it** (`operator_fetcher.py`). So:

- Pre-warm by running **our own** sign-in through the console and letting it
  finish. The fetcher closes its browser when it is done.
- Never open the portal manually in a second browser on the virtual desktop
  while a banked session exists. That invalidates both and costs a fresh push -
  live, on the call.

## Security

The console is not a screenshot. It is a mouse and a keyboard on a browser
signed in to a government portal, and whoever holds it can reach that portal's
payment controls. No application rule can prevent that.

- Both gates are real. nginx's password keeps strangers out; x11vnc's separate
  password means a broken nginx config still isn't an open keyboard.
- Nothing but nginx listens off loopback. If you ever need to "just test 5900
  from outside", the answer is an SSH tunnel, not removing `-localhost`.
- Neither password is in this repository and neither should be. `install.sh`
  generates the VNC one and prints it once.
- `Content-Security-Policy: frame-ancestors` is what stops any site other than
  the ERP embedding the console.
- Turn the feature off in Transport Settings when it is not in use. The switch
  is server-side, so it removes access rather than hiding a button.
