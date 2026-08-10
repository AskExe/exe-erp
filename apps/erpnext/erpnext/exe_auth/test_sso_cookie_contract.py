"""
SSO cookie contract test (bug 3938ac3d).

THE CANONICAL SSO COOKIE IS `exe_access_token`. NOTHING ELSE.

Background — this exact bug has now shipped in THREE products:

    exe-wiki   bug f1bb40d8   fixed
    exe-crm    bug 9f60e8e6   fixed (PR AskExe/exe-crm#87)
    exe-erp    bug 3938ac3d   fixed here

The cookie was renamed `exe_sso_token` -> `exe_access_token` when the
exe-sso-edge nginx gate was introduced ("The contract is now
exe_access_token"). The rename landed in exe-wiki and was never propagated.
Every product that still gates on the dead name bounces the user between the
product and auth.<apex> forever, because the cookie it looks for is never set.

This test is the machine enforcement for THIS repo: a documented invariant
with no machine check is not an invariant. It is deliberately frappe-free so
it runs under plain `python -m unittest` in CI without bench or a live site.

The CROSS-PRODUCT half of the contract (asserting the *deployed* HTML of every
product in the stack gates on the canonical name) lives in exe-os:
`scripts/sso-cookie-contract-check.mjs`. Both halves are needed: this one stops
a fourth product from ever merging the bug, that one stops a stale image from
serving it.
"""

import os
import re
import unittest

CANONICAL_COOKIE = "exe_access_token"

# Cookie names that were once used and are now DEAD. Gating on any of these is
# an infinite login bounce. Never add a name here as a way to allow it.
DEAD_COOKIE_NAMES = ("exe_sso_token",)

# Repo root: .../apps/erpnext/erpnext/exe_auth/ -> up 4
_REPO_ROOT = os.path.abspath(
	os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)

# Templates/assets that participate in the SSO gate. Kept explicit rather than
# a whole-tree walk so the test stays fast and its blast radius is obvious.
SSO_GATE_FILES = (
	os.path.join("frappe", "templates", "base.html"),
)

# Directories excluded from the repo-wide sweep: vendored deps, build output,
# and nested worktrees are not shipped source.
_SWEEP_EXCLUDES = (
	"node_modules",
	".git",
	".worktrees",
	".claude",
	"dist",
	"__pycache__",
	"cypress",
)

_SWEEP_EXTENSIONS = (".html", ".py", ".js", ".vue", ".ts")


def _read(rel_path):
	with open(os.path.join(_REPO_ROOT, rel_path), encoding="utf-8") as handle:
		return handle.read()


class TestSsoGateUsesCanonicalCookie(unittest.TestCase):
	"""The SSO gate must read exactly `exe_access_token`."""

	def testBaseTemplateGatesOnCanonicalCookie(self):
		content = _read(os.path.join("frappe", "templates", "base.html"))
		self.assertIn(
			CANONICAL_COOKIE,
			content,
			"frappe/templates/base.html must gate on the canonical SSO cookie "
			f"`{CANONICAL_COOKIE}`",
		)

	def testBaseTemplateReadsCookieInRedirectGuard(self):
		"""The canonical name must appear in the cookie *match*, not just a comment."""
		content = _read(os.path.join("frappe", "templates", "base.html"))
		self.assertRegex(
			content,
			r"document\.cookie\.match\([^\n]*" + re.escape(CANONICAL_COOKIE),
			"the SSO redirect guard must match the canonical cookie name against "
			"document.cookie",
		)


class TestDeadCookieNamesAbsentFail(unittest.TestCase):
	"""No shipped source may reference a retired SSO cookie name."""

	def testSsoGateFilesHaveNoDeadCookieNameFail(self):
		for rel_path in SSO_GATE_FILES:
			content = _read(rel_path)
			for dead in DEAD_COOKIE_NAMES:
				# Allow the name inside an explanatory comment ("Do NOT
				# reintroduce ...") but never as a live cookie read.
				live_uses = re.findall(
					r"document\.cookie[^\n]*" + re.escape(dead), content
				)
				self.assertEqual(
					live_uses,
					[],
					f"{rel_path} reads the DEAD cookie `{dead}`. The canonical "
					f"SSO cookie is `{CANONICAL_COOKIE}` — gating on the old "
					"name is an infinite login bounce (bugs f1bb40d8 / "
					"9f60e8e6 / 3938ac3d).",
				)

	def testNoDeadCookieReadAnywhereInForkSourceFail(self):
		offenders = []
		for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
			dirnames[:] = [d for d in dirnames if d not in _SWEEP_EXCLUDES]
			for filename in filenames:
				if not filename.endswith(_SWEEP_EXTENSIONS):
					continue
				full = os.path.join(dirpath, filename)
				try:
					with open(full, encoding="utf-8") as handle:
						content = handle.read()
				except (OSError, UnicodeDecodeError):
					continue
				for dead in DEAD_COOKIE_NAMES:
					if re.search(r"document\.cookie[^\n]*" + re.escape(dead), content):
						offenders.append(os.path.relpath(full, _REPO_ROOT))
		self.assertEqual(
			offenders,
			[],
			"these files read a DEAD SSO cookie name; the canonical cookie is "
			f"`{CANONICAL_COOKIE}`: {offenders}",
		)


if __name__ == "__main__":
	unittest.main()
