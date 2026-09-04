#!/usr/bin/env bash
#
# Stand up the traffic-portal sign-in console on a display-less server.
#
# Run as root ON THE TARGET SERVER, not on a workstation. It installs packages,
# writes two password files and enables three services. It does NOT touch
# nginx: that config carries a hostname and a certificate path, so it is copied
# and edited by hand (see nginx-portal-console.conf and README.md).
#
#   sudo ./install.sh <bench-user>
#
# Idempotent - safe to re-run. It never overwrites an existing password file.

set -euo pipefail

BENCH_USER="${1:-frappe}"
CONF_DIR=/etc/portal-console
UNIT_DIR=/etc/systemd/system

if [[ $EUID -ne 0 ]]; then
	echo "Run this with sudo - it installs packages and writes to /etc." >&2
	exit 1
fi

if ! id -u "$BENCH_USER" >/dev/null 2>&1; then
	echo "No such user: $BENCH_USER. Pass the bench user as the first argument." >&2
	exit 1
fi

echo "==> Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# novnc ships the web assets websockify serves; apache2-utils is only for
# htpasswd. Chromium's own libraries are NOT installed here - that is
# `./env/bin/playwright install-deps chromium`, run as the bench user.
apt-get install -y --no-install-recommends \
	xvfb x11vnc websockify novnc apache2-utils

echo "==> Creating $CONF_DIR"
install -d -m 0750 -o root -g "$BENCH_USER" "$CONF_DIR"

# -- VNC password (inner gate) --------------------------------------------
# Two independent passwords by design. nginx's stops strangers reaching the
# console; this one means that a mistake in the nginx config - a stale
# sites-enabled symlink, an auth_basic line lost in an edit - still does not
# hand out a keyboard on a signed-in government portal.
if [[ -f "$CONF_DIR/vncpasswd" ]]; then
	echo "==> VNC password already set, leaving it alone"
else
	VNC_PASS="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)"
	x11vnc -storepasswd "$VNC_PASS" "$CONF_DIR/vncpasswd" >/dev/null
	chown root:"$BENCH_USER" "$CONF_DIR/vncpasswd"
	chmod 0640 "$CONF_DIR/vncpasswd"
	echo
	echo "    VNC password (record it in the password manager NOW - not shown again):"
	echo "        $VNC_PASS"
	echo
fi

# -- nginx password (outer gate) ------------------------------------------
if [[ -f "$CONF_DIR/htpasswd" ]]; then
	echo "==> htpasswd already exists, leaving it alone"
	echo "    Add an operator with: htpasswd $CONF_DIR/htpasswd <name>"
else
	echo "==> Creating htpasswd. Choose an operator name and password."
	htpasswd -c "$CONF_DIR/htpasswd" operator
	chown root:www-data "$CONF_DIR/htpasswd"
	chmod 0640 "$CONF_DIR/htpasswd"
fi

echo "==> Installing systemd units"
cp "$(dirname "$0")"/systemd/portal-console-*@.service "$UNIT_DIR/"
systemctl daemon-reload

for svc in xvfb x11vnc websockify; do
	systemctl enable --now "portal-console-$svc@$BENCH_USER.service"
done

echo
echo "==> Services"
systemctl --no-pager --lines=0 status \
	"portal-console-xvfb@$BENCH_USER" \
	"portal-console-x11vnc@$BENCH_USER" \
	"portal-console-websockify@$BENCH_USER" || true

cat <<'NEXT'

==> Done with the server side. Three steps left, all by hand:

 1. nginx - copy nginx-portal-console.conf into /etc/nginx/sites-available/,
    replace CONSOLE_HOSTNAME and ERP_ORIGIN, get a certificate, symlink it
    into sites-enabled, `nginx -t`, reload.

 2. The bench - tell the workers which display to use, then restart them so
    they pick it up:

        bench set-config -g portal_console_display ":99"
        sudo supervisorctl restart all

    (":99" is also the built-in default, so this is only needed if you chose
    a different display. The app sets DISPLAY itself - do NOT add an
    environment= line to supervisor.conf, because bench rewrites that file.)

 3. ERPNext - on Transport Settings, tick "Enable Portal Sign-In Console" and
    set "Portal Sign-In Console URL" to https://<hostname>/vnc.html

 Then verify capture works on this machine before an operator relies on it:

        bench --site <site> console
        >>> from transport.transport.fine_sync.browser_fetcher import launch_chromium, get_playwright
        >>> pw = get_playwright().start()
        >>> b = launch_chromium(pw, headless=False)
        >>> p = b.new_context(viewport={"width":1500,"height":950}).new_page()
        >>> p.set_content("<h1>capture check</h1>")
        >>> len(p.screenshot(type="jpeg", quality=60))   # must be > 0, not an exception
        >>> b.close(); pw.stop()

NEXT
