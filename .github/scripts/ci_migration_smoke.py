#!/usr/bin/env python3
"""
Exe ERP — migration / patch smoke check (lightweight, infra-free).

A full `bench migrate` needs Postgres + Redis + a bootstrapped site, which is
too heavy for a per-PR gate. This script catches the *most common* migration
breakage class without any infra: a patches.txt entry that points at a Python
module path which does not exist or does not parse. Either of those crashes
`bench migrate` at runtime (the production migration step in
stack.release.json), so this fails CI closed before an image is published.

Scope: validates that every module-style patch entry in each app's patches.txt
resolves to a real, parseable .py file, and that every Exe fork module
(exe_auth / exe_bridge / exe_monitor / exe_setup and the hooks that wire them)
compiles. Inherited upstream patches are AST-parsed but a missing inherited
module is reported as a warning unless --strict is passed, so we never fail on
pre-existing upstream drift while still hard-failing on Exe-owned breakage.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# (app import root, app source dir, patches.txt path)
APPS = [
	("frappe", REPO / "frappe", REPO / "frappe" / "patches.txt"),
	("erpnext", REPO / "apps" / "erpnext" / "erpnext", REPO / "apps" / "erpnext" / "erpnext" / "patches.txt"),
	("hrms", REPO / "apps" / "hrms" / "hrms", REPO / "apps" / "hrms" / "hrms" / "patches.txt"),
]

# Exe-owned source trees: a break here is always a hard failure.
EXE_DIRS = [
	REPO / "apps" / "erpnext" / "erpnext" / "exe_auth",
	REPO / "apps" / "erpnext" / "erpnext" / "exe_bridge",
	REPO / "apps" / "erpnext" / "erpnext" / "exe_monitor",
	REPO / "apps" / "erpnext" / "erpnext" / "exe_setup",
]


def module_to_path(app_root: Path, module: str) -> Path:
	"""erpnext.patches.v12_0.foo -> <erpnext>/patches/v12_0/foo.py"""
	parts = module.split(".")
	# drop the leading app package name (it maps to app_root itself)
	rel = parts[1:]
	return app_root.joinpath(*rel).with_suffix(".py")


def parse_file(path: Path) -> str | None:
	"""Return an error string if the file is missing or does not parse."""
	if not path.exists():
		return f"missing module file: {path.relative_to(REPO)}"
	try:
		ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
	except SyntaxError as exc:  # noqa: BLE001 - we want the message
		return f"syntax error in {path.relative_to(REPO)}: {exc}"
	return None


def is_exe_owned(path: Path) -> bool:
	return any(str(path).startswith(str(d)) for d in EXE_DIRS) or path.name.startswith("exe_")


def main() -> int:
	strict = "--strict" in sys.argv
	hard_errors: list[str] = []
	warnings: list[str] = []
	checked = 0

	# 1. Every Exe-owned module must compile.
	for d in EXE_DIRS:
		if not d.exists():
			hard_errors.append(f"expected Exe module dir is missing: {d.relative_to(REPO)}")
			continue
		for py in sorted(d.rglob("*.py")):
			checked += 1
			err = parse_file(py)
			if err:
				hard_errors.append(err)

	# 2. Every module-style patches.txt entry must resolve + parse.
	for app_name, app_root, patches in APPS:
		if not patches.exists():
			warnings.append(f"no patches.txt for app '{app_name}' ({patches.relative_to(REPO)})")
			continue
		for raw in patches.read_text(encoding="utf-8").splitlines():
			line = raw.strip()
			if not line or line.startswith("#") or line.startswith("["):
				continue
			if line.startswith("execute:"):
				# inline python executed by the patch runner — can't statically resolve
				continue
			module = line.split("#", 1)[0].strip()
			if not module or "." not in module:
				continue
			path = module_to_path(app_root, module)
			err = parse_file(path)
			checked += 1
			if err:
				(hard_errors if (is_exe_owned(path) or strict) else warnings).append(
					f"[{app_name}] {err}"
				)

	for w in warnings:
		print(f"WARN  {w}")
	for e in hard_errors:
		print(f"ERROR {e}")

	print(f"\nmigration smoke: checked {checked} modules, "
	      f"{len(hard_errors)} error(s), {len(warnings)} warning(s)")
	return 1 if hard_errors else 0


if __name__ == "__main__":
	raise SystemExit(main())
