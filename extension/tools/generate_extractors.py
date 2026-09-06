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
import json
import datetime
import pathlib
import sys

WANTED = {
	"EXTRACT_ROWS_JS": "extractRows",
	"NEXT_PAGE_JS": "nextPage",
	"READ_PANEL_JS": "readPanel",
}

HERE = pathlib.Path(__file__).resolve().parent
PORTALS_DIR = HERE.parent.parent / "transport" / "transport" / "fine_sync" / "portals"
TARGET = HERE.parent / "content" / "extractors.generated.js"


def read_class_string(tree, attr):
	"""A string class attribute declared by a fetcher in this module, or None."""
	for node in ast.walk(tree):
		if not isinstance(node, ast.ClassDef):
			continue
		for stmt in node.body:
			if not isinstance(stmt, ast.Assign):
				continue
			if not isinstance(stmt.value, ast.Constant) or not isinstance(stmt.value.value, str):
				continue
			for target in stmt.targets:
				if isinstance(target, ast.Name) and target.id == attr:
					return stmt.value.value
	return None


def read_client_reader(tree):
	"""The `client_reader` a fetcher class in this module declares, or None.

	This is what decides whether a portal appears in the generated file at all,
	and it is read from the fetcher rather than from a list kept here. A portal
	whose reader is named in two places drifts the first time one of them moves.
	"""
	return read_class_string(tree, "client_reader")


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


def build(readers):
	stamp = datetime.date.today().isoformat()
	sources = ", ".join(sorted(r["source"] for r in readers.values()))
	parts = [
		"// GENERATED FILE - DO NOT EDIT BY HAND.\n",
		f"// Generated {stamp} from {sources}\n",
		"// by extension/tools/generate_extractors.py. Change the constants in those\n",
		"// Python modules and re-run the generator; edits made here are lost and,\n",
		"// worse, silently diverge from what the server-side fetcher reads.\n",
		"//\n",
		"// Keyed by the `client_reader` each fetcher declares, which is the same name\n",
		"// the server returns from get_client_fetch_target - so the reader that runs\n",
		"// is always the one that portal's fetcher named.\n",
		"//\n",
		"// Each value is the portal-reading arrow function exactly as the fetcher\n",
		"// evaluates it, comments and all - so a fix to either side is one\n",
		"// regeneration away from reaching the other.\n",
		"\n",
		"globalThis.PORTAL_EXTRACTORS = {\n",
	]
	origins = {r: readers[r]["origin"] for r in sorted(readers) if readers[r].get("origin")}
	for reader in sorted(readers):
		found = readers[reader]["constants"]
		parts.append(f"\n\t// from {readers[reader]['source']}\n")
		parts.append(f"\t{reader}: {{\n")
		for constant, key in WANTED.items():
			body = found[constant].strip()
			parts.append(f"\n\t\t// {constant}\n")
			parts.append(f"\t\t{key}: {body},\n")
		parts.append("\t},\n")
	parts.append("};\n")
	# Read by tools/package.sh, which grants these origins in the built manifest.
	# Emitted from the fetchers rather than listed by hand: a reader that ships
	# without its origin granted opens a tab it is not allowed to read.
	parts.append("\nglobalThis.PORTAL_ORIGINS = ")
	parts.append(json.dumps(origins, indent=1, sort_keys=True))
	parts.append(";\n")
	return "".join(parts)


def main():
	if not PORTALS_DIR.is_dir():
		sys.exit(f"Cannot find the portals package at {PORTALS_DIR}")

	readers = {}
	for source in sorted(PORTALS_DIR.glob("*.py")):
		if source.name == "__init__.py":
			continue
		tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
		reader = read_client_reader(tree)
		if not reader:
			# No reader declared: the extension is not meant to read this portal.
			# RTA is the case that matters - its page has never been captured, and
			# emitting an empty reader for it would let the extension open a real
			# page, find nothing, and report a clean zero.
			continue
		found = read_constants(source)
		missing = [name for name in WANTED if name not in found]
		if missing:
			# Declaring a reader and not defining what it reads with is drift, and
			# the whole point of generating this file is that drift is caught here
			# rather than discovered as an empty fetch.
			sys.exit(
				f"{source.name} declares client_reader = {reader!r} but no longer defines: "
				f"{', '.join(missing)}"
			)
		readers[reader] = {
			"constants": found,
			"source": source.name,
			"origin": read_class_string(tree, "client_origin"),
		}

	if not readers:
		sys.exit("No portal declares a client_reader, so there is nothing to generate.")

	TARGET.parent.mkdir(parents=True, exist_ok=True)
	TARGET.write_text(build(readers), encoding="utf-8")
	print(f"Wrote {TARGET.relative_to(HERE.parent.parent)}")
	for reader in sorted(readers):
		found = readers[reader]["constants"]
		origin = readers[reader].get("origin") or "(no client_origin declared)"
		print(f"  {readers[reader]['source']} -> PORTAL_EXTRACTORS.{reader}  on {origin}")
		for constant, key in WANTED.items():
			print(f"      {constant:<18} -> {key}  ({len(found[constant])} chars)")


if __name__ == "__main__":
	main()
