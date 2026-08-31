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

`fleetify` and `hrms` must be installed first - `required_apps` will refuse the
install otherwise. Note that it checks only that they are *present*, not which
version, so a bench carrying an outdated `fleetify` passes the check while
missing DocTypes this app attaches Custom Fields to. `bench migrate` reports
any it had to skip, in the Error Log and in its own output.

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
