#!/usr/bin/env bash
# Build the zip an operator or reviewer loads into Chrome.
#
# Ships only what the browser needs. docs/ and tools/ are for whoever maintains
# this side, not the operator, so they stay out of the zip. The ERPNext half
# needs nothing here at all: its Client Script installs with the app.
#
# The build can carry the address of the bench it was built for, so a reviewer
# has nothing to paste and nothing to grant:
#
#   tools/package.sh --erp-origin https://example.ngrok-free.dev
#   tools/package.sh --erp-origin https://a.example --erp-origin https://b.example
#   tools/package.sh                      # reads extension/.erp-origin, one per line
#
# Repeatable, because one build is usually pointed at more than one bench - a
# tunnel for a reviewer and the UAT host for everyone else. Every address given
# is granted; the FIRST is the one the options page offers as its default.
#
# That address is written into the staging copy only - the manifest and config.js
# in the repository are never touched. This is deliberate. `transport-bus` is
# public, and a tunnel address is a live way in to a bench holding real fleet
# data. It belongs in the zip you hand to one person, not in the source.
# `.erp-origin` is gitignored for the same reason.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT=""
ERP_ORIGINS=""

while [ $# -gt 0 ]; do
	case "$1" in
		--erp-origin) ERP_ORIGINS="${ERP_ORIGINS}${2:-}"$'\n'; shift 2 ;;
		-o|--out)     OUT="${2:-}"; shift 2 ;;
		-h|--help)    sed -n '2,24p' "${BASH_SOURCE[0]}"; exit 0 ;;
		*)            OUT="$1"; shift ;;
	esac
done

OUT="${OUT:-$HOME/transport-fine-fetch.zip}"
if [ -z "${ERP_ORIGINS//[[:space:]]/}" ] && [ -f "$HERE/.erp-origin" ]; then
	# One address per line; blank lines and # comments ignored.
	ERP_ORIGINS="$(grep -vE '^\s*(#|$)' "$HERE/.erp-origin" || true)"
fi

cd "$HERE"
python3 tools/generate_extractors.py >/dev/null

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -r manifest.json background.js options.html options.js config.js content "$STAGE/"

if [ -n "${ERP_ORIGINS//[[:space:]]/}" ]; then
	ERP_ORIGINS="$ERP_ORIGINS" python3 - "$STAGE" <<'PY'
import json, os, pathlib, re, sys
from urllib.parse import urlparse

stage = pathlib.Path(sys.argv[1])

origins = []
for raw in os.environ["ERP_ORIGINS"].split():
	parsed = urlparse(raw)
	if parsed.scheme not in ("http", "https") or not parsed.netloc:
		sys.exit(f"--erp-origin must be a full http(s) URL, got {raw!r}")
	origin = f"{parsed.scheme}://{parsed.netloc}"
	if origin not in origins:
		origins.append(origin)

manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
hosts = manifest.setdefault("host_permissions", [])

# Granted through the manifest rather than requested at run time. An optional
# permission needs a user gesture to grant, and the button's gesture is spent on
# the desk page long before the service worker could ask.
for origin in origins:
	pattern = f"{origin}/*"
	if pattern not in hosts:
		hosts.append(pattern)
	for entry in manifest.get("content_scripts", []):
		if "content/erp-bridge.js" in entry.get("js", []) and pattern not in entry["matches"]:
			entry["matches"].append(pattern)
	print(f"  ERP address granted: {origin}")

# Grant the origins of whichever readers this build ships, for the same reason.
# A reader whose origin is not granted here opens a tab it is not allowed to read.
generated = (stage / "content" / "extractors.generated.js").read_text(encoding="utf-8")
found = re.search(r"globalThis\.PORTAL_ORIGINS = (\{.*?\});", generated, re.S)
for reader, portal_origin in sorted(json.loads(found.group(1)).items() if found else []):
	portal_pattern = f"{portal_origin}/*"
	if portal_pattern not in hosts:
		hosts.append(portal_pattern)
	print(f"  reader {reader} may read: {portal_origin}")

(stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

# The first address is the options page's default - the one whoever receives the
# zip lands on without typing anything. The rest are granted and selectable.
(stage / "config.js").write_text(
	"// Written by tools/package.sh at build time. Do not commit a real address.\n"
	f"globalThis.TFF_DEFAULT_ERP_ORIGIN = {json.dumps(origins[0])};\n"
	f"globalThis.TFF_KNOWN_ERP_ORIGINS = {json.dumps(origins)};\n",
	encoding="utf-8",
)
print(f"  options default: {origins[0]}")
PY
else
	echo "  no ERP address baked in - the reviewer sets one on the options page"
fi

rm -f "$OUT"
(cd "$STAGE" && zip -q -r "$OUT" . -x '*/.*' -x '__MACOSX*')

echo "Wrote $OUT"
echo "Readers included:"
python3 - <<'PY'
import re, pathlib
s = pathlib.Path("content/extractors.generated.js").read_text(encoding="utf-8")
for name in re.findall(r'^\t(\w+): \{', s, re.M):
	print(f"  - {name}")
PY
