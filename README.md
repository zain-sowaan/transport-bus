### Transport

This app is used to account the transport system of a company, and it requires fleetify app to work

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app transport
bench --site $YOUR_SITE migrate
```

`fleetify` and `hrms` are declared in `required_apps`, so `bench install-app
transport` installs them onto the site itself before installing this app
(`frappe/installer.py:287-290`) - it does not refuse and wait for you to do it.
What it installs is whatever version the bench happens to carry, and it never
checks which, so a bench holding an outdated `fleetify` satisfies the
requirement completely while lacking DocTypes this app depends on. It can only
install what the bench already has, and how it fails depends on how the app is
missing. With no `fleetify` in the bench at all, the nested install raises
`ModuleNotFoundError: No module named 'fleetify'` from `get_hooks`
(`frappe/__init__.py:1596-1602`), before any apps.txt check is reached; the
line above it prints `Could not find app "fleetify":`. Only when the app is on
disk but absent from `sites/apps.txt` does it get as far as `App fleetify not
in apps.txt` (`installer.py:295`). Both describe the bench rather than a
requirement, which is what makes them hard to place.

`Place` is the one that bites. Trip, Trip Stop and Route link to it, the driver
portal's geofence reads it, and the Trip Sheet report shows two columns of it -
so a stale `fleetify` breaks those flows, not merely the Custom Fields attached
to it, and it does so with errors that name a column rather than the missing
app. `bench migrate` names any DocType it had to skip, in its own output and in
the Error Log. The remedy is to update the dependency in the bench and migrate
again; nothing in this app can substitute for the DocType being absent.

### Traffic fine sync (optional)

Fetching fines drives a real browser, which is kept out of the base install:
most sites never use it, and a browser does not belong in the ERP runtime. On a
bench that needs it, three separate steps - the Python package, the browser
binary, and on a server the libraries that binary links against:

```bash
./env/bin/pip install -e "apps/transport[fines]"
./env/bin/playwright install chromium
sudo ./env/bin/playwright install-deps chromium   # servers only
```

Without them the rest of the app works normally and the fine-sync buttons say
which step is missing.

Every portal served by TAMM or by the MOI route needs an operator to sign in
through UAE Pass, which ends in a push approved on a phone - and MOI puts a
CAPTCHA on its search form as well, which the operator answers themselves. One
fetcher covers the whole MOI route, so that is most of the portals, not two of
them. SRTA is the exception: a public form that needs no sign-in at all.

#### Two ways to sign in

`Transport Settings > UAE Pass Sign-In Mode` chooses between them.

**Operator signs in at the server** (the default) opens a real browser window on
the machine running the worker. Every step is visible and any challenge can be
answered, but only someone at that machine can use it - the window opens on the
server's own display, not in the browser of whoever pressed the button. A
headless server therefore needs a virtual display (Xvfb plus a VNC or noVNC
route) for this mode to be usable at all.

**Relay code to my screen** runs the browser headlessly on the server, types the
number from `Transport Settings > UAE Pass Mobile Number` into the UAE Pass
form, and publishes the resulting sign-in screen to the person who pressed the
button. They confirm the request in the UAE Pass app on their phone, wherever
they are. Nothing about it changes who approves the sign-in - only where the
screen is shown.

The screen is sent as a **picture**, and a code read out of the page is shown
beside it labelled as provisional. That is deliberate: the confirmation screen's
markup has never been captured, so any selector for the code is an inference,
and a wrong code is worse than none - it would have somebody approve a request
they had not actually matched. The picture cannot be wrong about what the screen
says.

Relay applies only where a portal challenges at sign-in and nowhere else. TAMM
qualifies. The MOI route does not and never will: its CAPTCHA is on the *search
form*, so a person is needed for every query, not once at the start. That is
declared per fetcher (`supports_relay`), not by a global switch.

`Test Headless Reach` on the portal record answers "would this work from this
server?" without typing anything, sending a push, or needing anyone present. It
is worth running before depending on the relay - government portals sit behind
WAFs, and a headless browser is the classic thing one turns away.

Scheduled syncs do **not** start a relay unless
`Let Scheduled Syncs Start a Relay Sign-In` is switched on, and it is off by
default. A scheduled relay pushes a confirmation to a phone at whatever hour the
cron fires and publishes the code to a screen nobody is watching, spending a
push that cannot be re-requested. Left off, scheduled runs use the session
banked by the last sign-in and report plainly when it has lapsed.

#### One thing to decide before relying on this

UAE Pass's Standard Implementation Guidelines say a service provider "is
recommended not to initiate UAE Pass authentication on behalf of user"
([source](https://docs.uaepass.ae/guidelines/use-cases/standard-implementation-guidelines)).
It is a recommendation rather than a prohibition, it addresses relying-party
integrations rather than an operator automating their own sign-in, and the
approval itself still happens on the registered phone. No "no automated access"
clause was found in either the developer documentation or the end-user terms.
**This is flagged for a human decision, not settled here.**

For the record on what is *not* possible: UAE Pass documents no
`client_credentials` grant for identity, no refresh token on the standard
web-application token response, and no delegation or service-account flow. Its
one delegation-shaped feature, Data Sharing Authorization, routes every request
through a live person approving in the mobile app and states that consent
"applies solely to the specific authorization request". Their FAQ puts company
representation on the service provider: "Authorization of individuals to
represent an organization is to be managed by the Service Provider post
authentication." So the person is not an implementation shortcut that better
engineering removes - there is no sanctioned path that does not end at one.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/transport
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
