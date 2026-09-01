#!/usr/bin/env python3
"""
Run the Exe-owned Python unit tests in CI (bug 483cc712).

WHY THIS EXISTS
───────────────
CI verified Exe-owned Python with `ruff` + `compileall`, plus a hand-maintained
allowlist of one-file steps (`run: python3 <path>`). `compileall` byte-compiles
a file; it never executes a test. So a test module only ran if someone
remembered to add a bespoke workflow step for it.

That allowlist was incomplete. It ran `exe_auth/test_sso_cookie_contract.py`
(added with the SSO fix, bug 3938ac3d) but never referenced:

    apps/erpnext/erpnext/exe_auth/test_exe_perms.py         56 tests
    apps/erpnext/erpnext/exe_monitor/test_error_reporter.py  3 tests

59 tests that existed, passed locally, and had never once executed in CI.

The allowlist is the defect: it is opt-in, so the default for a new test file
is "silently not run". This script inverts that — the manifest below is
checked, and a module that goes missing from disk fails the build.

WHAT IT RUNS
────────────
Only the Exe-owned, deliberately FRAPPE-FREE test modules listed in
EXPECTED_MODULES. The inherited upstream suites (erpnext/, hrms/, frappe/)
are NOT run here: they subclass `frappe.tests.utils.FrappeTestCase` and need a
bootstrapped bench site plus a live MariaDB + Redis, which this CI job does not
have. Running them would need a full `bench init` + `bench new-site` on every
PR. That is a separate, much larger piece of work — it is deliberately out of
scope here and is NOT silently claimed as covered. See the CI-scope note in
`.github/workflows/ci-checks.yml`.

Each Exe-owned test module loads its subject BY FILE PATH (not via the
`erpnext` package, whose `__init__` imports frappe), so they run under a plain
interpreter. This script loads the test modules the same way.

FAIL-CLOSED GUARANTEES (the whole point — a guard that can pass vacuously is
not a guard):

  1. Every module in EXPECTED_MODULES must exist. A deleted or renamed test
     file fails the build instead of quietly shrinking coverage.
  2. Every module in EXPECTED_MODULES must contribute at least one test.
  3. Every class in REQUIRED_TEST_CLASSES must actually be collected. These
     are the specific SSO/deploy-manifest contract classes this bug is about.
  4. The total collected test count must be > 0, and the run must report
     having executed exactly the number of tests collected.

A "0 tests, exit 0" run — the classic silent green — is a FAILURE here.
"""

import importlib.util
import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Exe-owned, frappe-free test modules. Paths are relative to the repo root.
# ADD a module here whenever you add an Exe-owned test file — a test that is
# not in this list is a test CI does not run.
EXPECTED_MODULES = (
	"apps/erpnext/erpnext/exe_auth/test_sso_cookie_contract.py",
	"apps/erpnext/erpnext/exe_auth/test_exe_perms.py",
	"apps/erpnext/erpnext/exe_auth/test_login_page_contract.py",
	# Bug adf77179 / 83ba9546 — SSO callback token source. This module existed
	# but had never been listed here, so it had never run in CI: exactly the
	# opt-in gap this runner was written to close.
	"apps/erpnext/erpnext/exe_auth/test_sso_callback_token_source.py",
	# The SSO 429 -> 500 crash. Needs Werkzeug (installed in the CI venv
	# alongside ruff) because it drives the real `Request.application`
	# boundary that raised the production TypeError.
	"apps/erpnext/erpnext/exe_auth/test_sso_rate_limit_response.py",
	"apps/erpnext/erpnext/exe_monitor/test_error_reporter.py",
	"apps/erpnext/erpnext/exe_auth/test_oauth_csrf_contract.py",
)

# Contract classes that MUST be collected and run. These are the SSO +
# deploy-manifest guards from bugs 3938ac3d / 96e6b8b6 that answered the
# erp.askexe.com/login outage. Naming them explicitly means a refactor that
# renames or drops a class fails this job, instead of quietly reporting a
# smaller green run — the failure mode that outage was made of.
REQUIRED_TEST_CLASSES = (
	"TestSsoGateUsesCanonicalCookie",
	"TestDeadCookieNamesAbsentFail",
	"TestDeployManifestShipsTheSsoFix",
	# Bug e1a9e4e9 — the login page's inline script must PARSE, and the SSO
	# control must carry a real href in the server-rendered markup. The outage
	# was a single quote character; nothing but a machine check catches that.
	"TestLoginPageScriptParses",
	"TestSsoControlHasServerRenderedHref",
	# Bug 42470087 — no downgraded (http://) SSO callback URL may be emitted.
	"TestForceHttpsCallbackUrl",
	# The SSO 429 -> 500 crash: a rate-limited endpoint must answer 429, and no
	# branch of handle_exception may hand None back to Werkzeug.
	"TestRateLimiterRespondAlwaysBuildsAResponse",
	"TestWerkzeugBoundaryGetsAWsgiCallable",
	"TestHandleExceptionNeverReturnsNone",
	"TestTracingMiddlewareDoesNotReplayTheRequest",
	"TestCiWerkzeugPinMatchesProduction",
	# Bug 8eab0042 (repeat of fba616eb) — the SSO callback must keep requiring
	# the token-bearing state echo. PR #59 relaxed it, was flagged and closed;
	# the same stale branch returned as PR #66 and merged. This class is what
	# makes a third return fail CI instead of sailing through green.
	"TestOAuthStateCsrfContract",
)


def _fail(message):
	print(f"::error::{message}")
	sys.exit(1)


def _load_module(rel_path):
	"""Import a test module by file path, bypassing the frappe-dependent
	`erpnext` package __init__."""
	full = os.path.join(_REPO_ROOT, rel_path)
	if not os.path.isfile(full):
		_fail(
			f"expected test module is missing: {rel_path}. It was either "
			"deleted or renamed without updating EXPECTED_MODULES in "
			".github/scripts/ci_python_tests.py — which would silently drop "
			"it from CI."
		)
	name = "ci_" + rel_path.replace("/", "_").removesuffix(".py")
	spec = importlib.util.spec_from_file_location(name, full)
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module


def _collected_class_names(suite):
	names = set()
	for test in suite:
		if isinstance(test, unittest.TestSuite):
			names |= _collected_class_names(test)
		else:
			names.add(type(test).__name__)
	return names


def main():
	loader = unittest.TestLoader()
	master = unittest.TestSuite()
	all_classes = set()

	for rel_path in EXPECTED_MODULES:
		module = _load_module(rel_path)
		suite = loader.loadTestsFromModule(module)
		count = suite.countTestCases()
		if count == 0:
			_fail(
				f"{rel_path} contributed 0 tests. A test module that collects "
				"nothing is the silent-green failure this job exists to "
				"prevent."
			)
		all_classes |= _collected_class_names(suite)
		print(f"collected {count:3d} test(s) from {rel_path}")
		master.addTest(suite)

	total = master.countTestCases()
	if total == 0:
		_fail("collected 0 tests overall — refusing to report a vacuous pass")

	missing_classes = sorted(set(REQUIRED_TEST_CLASSES) - all_classes)
	if missing_classes:
		_fail(
			"required contract test class(es) were not collected: "
			f"{missing_classes}. These are the SSO/deploy-manifest guards from "
			"bugs 3938ac3d / 96e6b8b6. If they stop being collected, the guard "
			"is inert and the login outage can silently recur."
		)

	print(f"\nrunning {total} Exe-owned Python test(s)\n")
	result = unittest.TextTestRunner(verbosity=2).run(master)

	if result.testsRun != total:
		_fail(
			f"expected to run {total} tests but the runner reported "
			f"{result.testsRun} — refusing to trust this result"
		)

	if not result.wasSuccessful():
		_fail(
			f"Exe-owned Python tests FAILED: "
			f"{len(result.failures)} failure(s), {len(result.errors)} error(s)"
		)

	print(f"\nOK — {total} Exe-owned Python tests passed")


if __name__ == "__main__":
	main()
