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
requirement completely while lacking DocTypes this app depends on.

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
which step is missing. Two of the portals need an operator signed in through
UAE Pass, so a headless server also needs a virtual display (Xvfb plus a VNC
route) for anyone to complete a sign-in.

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
