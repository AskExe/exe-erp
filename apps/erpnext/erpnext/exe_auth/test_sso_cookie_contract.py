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

THE THIRD HALF (bug 96e6b8b6) — added after the source fix above shipped and
production STILL served the dead cookie for weeks.

The template fix landed on main (5aebfb9, PR #43) and was baked into the v0.3.0
image. The v0.3.0 release commit (5659b47) is even titled "retire the
orphaned/poisoned v0.2.0-final8 pin". But it retired that pin in
`stack.release.json` ONLY. `docker-compose.yml` — the file that actually runs on
the host — kept all six of its services pinned to
`v0.2.0-final8@sha256:2d55a7c3…`, an image built BEFORE 5aebfb9. So every test
in this file passed, the cross-product check in exe-os failed against live, and
erp.askexe.com/login bounced forever on `exe_sso_token`.

A fix that is merged but not deployable is not a fix. The classes below make the
deploy manifest part of the machine contract: docker-compose.yml must pin the
exact image `stack.release.json` publishes, and must never pin an image known to
predate the SSO cookie fix.
"""

import json
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


# ─────────────────────────────────────────────────────────────────────────────
# Deploy-manifest half of the contract (bug 96e6b8b6)
# ─────────────────────────────────────────────────────────────────────────────

DEPLOY_MANIFEST = "docker-compose.yml"
RELEASE_MANIFEST = "stack.release.json"

# Images built BEFORE the SSO cookie fix (5aebfb9). Pinning any of these in the
# deploy manifest ships the infinite login bounce no matter how correct the
# source tree is. Never remove an entry here to make a pin pass — cut a new
# release instead.
PRE_SSO_FIX_IMAGE_TAGS = (
	"v0.2.0-final8",
	"v0.2.0-final7",
	"v0.2.0-final3",
	"v0.2.0",
)

# `image: ghcr.io/askexe/exe-erp:<tag>@sha256:<digest>` in docker-compose.yml.
_COMPOSE_IMAGE_RE = re.compile(
	r"^\s*image:\s*(?P<ref>ghcr\.io/askexe/exe-erp[^\s]*)\s*$", re.MULTILINE
)


def _compose_erp_image_refs():
	"""Every exe-erp image reference the deploy manifest pins."""
	return _COMPOSE_IMAGE_RE.findall(_read(DEPLOY_MANIFEST))


def _released_erp_image_ref():
	"""The image reference the published release manifest names."""
	manifest = json.loads(_read(RELEASE_MANIFEST))
	return manifest["components"]["erp"]


class TestDeployManifestShipsTheSsoFix(unittest.TestCase):
	"""docker-compose.yml must deploy an image that contains the cookie fix."""

	def testComposePinsExist(self):
		"""Guard the guard: a regex that matches nothing would pass vacuously."""
		refs = _compose_erp_image_refs()
		self.assertNotEqual(
			refs,
			[],
			f"{DEPLOY_MANIFEST} names no ghcr.io/askexe/exe-erp image — either "
			"the deploy manifest moved or this test's parser is broken. Either "
			"way the deploy half of the SSO contract is unenforced.",
		)

	def testComposeMatchesPublishedReleaseFail(self):
		"""Every service must pin exactly the image stack.release.json publishes."""
		released = _released_erp_image_ref()
		mismatched = sorted({r for r in _compose_erp_image_refs() if r != released})
		self.assertEqual(
			mismatched,
			[],
			f"{DEPLOY_MANIFEST} pins image(s) that are not the published release "
			f"{released!r}: {mismatched}. exe-erp runs all its roles off ONE "
			"image; a compose pin that lags stack.release.json means the host "
			"keeps serving old code after the fix merges — exactly how bug "
			"96e6b8b6 kept the dead `exe_sso_token` gate live in production "
			"after 5aebfb9 fixed the template.",
		)

	def testComposeHasNoPreSsoFixImageFail(self):
		"""No service may pin an image built before the SSO cookie fix."""
		offenders = []
		for ref in _compose_erp_image_refs():
			for tag in PRE_SSO_FIX_IMAGE_TAGS:
				if re.search(r":" + re.escape(tag) + r"(?:@|$)", ref):
					offenders.append((tag, ref))
		self.assertEqual(
			offenders,
			[],
			f"{DEPLOY_MANIFEST} pins image(s) built BEFORE the SSO cookie fix "
			f"(5aebfb9): {offenders}. Those images gate on the DEAD cookie "
			f"`{DEAD_COOKIE_NAMES[0]}` and bounce every login forever. The "
			f"canonical cookie is `{CANONICAL_COOKIE}`.",
		)

	def testReleaseManifestComponentsAreIdenticalFail(self):
		"""All four roles run off one image; divergence means a partial rollout."""
		components = json.loads(_read(RELEASE_MANIFEST))["components"]
		distinct = sorted(set(components.values()))
		self.assertEqual(
			len(distinct),
			1,
			f"{RELEASE_MANIFEST} components must all name ONE image (exe-erp "
			f"runs erp/websocket/queue/scheduler off the same build); found "
			f"{distinct}. A split here rolls the SSO fix out to some roles only.",
		)


if __name__ == "__main__":
	unittest.main()
