#!/usr/bin/env python3
"""Lift the portal-reading JavaScript out of the Python fetcher into the extension.

The server-side fetcher does not parse HTML in Python. It ships three arrow
functions into the page through `page.evaluate()`, and those constants are
already valid JavaScript expressions:

    EXTRACT_ROWS_JS   read the rendered fines rows
    NEXT_PAGE_JS      advance the client-side pager
    READ_PANEL_JS     read one fine's detail modal

The extension needs the same three, and a hand-made copy would drift the first
time somebody re-themes the portal and fixes only one of the two. So this reads
them straight out of `portals/tamm.py` and writes them into the content script's
namespace, verbatim - no reformatting, no reindenting, no "improvements".

Run it after any change to those constants:

    python3 extension/tools/generate_extractors.py

The output is committed so the extension loads unpacked without a build step.
Nothing at runtime reads Python; this is a development-time copy.
"""

import ast
import datetime
import pathlib
import sys

WANTED = {
	"EXTRACT_ROWS_JS": "extractRows",
	"NEXT_PAGE_JS": "nextPage",
	"READ_PANEL_JS": "readPanel",
}

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / "transport" / "transport" / "fine_sync" / "portals" / "tamm.py"
TARGET = HERE.parent / "content" / "extractors.generated.js"


def read_constants(path):
	"""Pull the named string constants out of the module without importing it.

	Parsed rather than imported because importing the fetcher drags in frappe
	and playwright, neither of which has any business being installed just to
	regenerate a JavaScript file.
	"""
	tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	found = {}
	for node in tree.body:
		if not isinstance(node, ast.Assign):
			continue
		if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
			continue
		for target in node.targets:
			if isinstance(target, ast.Name) and target.id in WANTED:
				found[target.id] = node.value.value
	return found


def build(found, source_rel):
	stamp = datetime.date.today().isoformat()
	parts = [
		"// GENERATED FILE - DO NOT EDIT BY HAND.\n",
		f"// Generated {stamp} from {source_rel}\n",
		"// by extension/tools/generate_extractors.py. Change the constants in that\n",
		"// Python module and re-run the generator; edits made here are lost and,\n",
		"// worse, silently diverge from what the server-side fetcher reads.\n",
		"//\n",
		"// Each value is the portal-reading arrow function exactly as the fetcher\n",
		"// evaluates it, comments and all - so a fix to either side is one\n",
		"// regeneration away from reaching the other.\n",
		"\n",
		"globalThis.TAMM_EXTRACTORS = {\n",
	]
	for constant, key in WANTED.items():
		body = found[constant].strip()
		parts.append(f"\n\t// {constant}\n")
		parts.append(f"\t{key}: {body},\n")
	parts.append("};\n")
	return "".join(parts)


def main():
	if not SOURCE.exists():
		sys.exit(f"Cannot find the fetcher at {SOURCE}")

	found = read_constants(SOURCE)
	missing = [name for name in WANTED if name not in found]
	if missing:
		# Renamed or turned into an f-string, either of which means the copy in
		# the extension is now stale and nobody would otherwise be told.
		sys.exit(f"{SOURCE.name} no longer defines: {', '.join(missing)}")

	TARGET.parent.mkdir(parents=True, exist_ok=True)
	TARGET.write_text(build(found, SOURCE.name), encoding="utf-8")
	print(f"Wrote {TARGET.relative_to(HERE.parent.parent)} from {SOURCE.name}")
	for constant, key in WANTED.items():
		print(f"  {constant:<18} -> TAMM_EXTRACTORS.{key}  ({len(found[constant])} chars)")


if __name__ == "__main__":
	main()
