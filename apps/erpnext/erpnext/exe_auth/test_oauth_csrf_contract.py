"""
OAuth state CSRF contract test (bug 8eab0042 — REPEAT-CLASS defect).

THE DEFECT
──────────
`login_oauth_user` in `frappe/utils/oauth.py` is the OAuth2/SSO callback.
Upstream Frappe hard-requires the echoed `state` to carry the token issued in
`get_oauth2_authorize_url()`:

    if not (state and state["token"]):
        frappe.respond_as_web_page(..., http_status_code=417)
        return

That check is the CSRF defence of the whole SSO flow: without it, an attacker
who gets a victim's browser to hit the callback URL (with attacker-supplied
`code`/`data`) can log the victim into the attacker's SSO session — login
CSRF.

The relaxation was introduced on branch `fix/adf77179-sso-state-echo` (PR #59),
flagged by bug fba616eb, and PR #59 was closed UNMERGED. PR #60 landed a safe
fix elsewhere (exe_auth) that kept SSO working without touching this check.
Twelve days later the SAME stale branch was re-opened as PR #66 and merged,
changing `if not (state and state["token"])` to `if state and not
state.get("token")` — i.e. an absent/empty state echo now BYPASSES the CSRF
gate entirely and logs straight in. The rejected commit 76d4f15 and the merge
commit e60f35c have byte-identical diffs.

WHAT THIS FILE GUARDS
─────────────────────
That the hard requirement cannot silently come back a third time. The subject
lives in `frappe/utils/oauth.py`, which needs a bootstrapped bench to import
normally — so this test loads it BY FILE PATH with a stubbed `frappe` module
(the same trick `ci_python_tests.py` uses for Exe-owned modules) and exercises
`login_oauth_user` directly:

  * state absent (None), empty string, a decoded dict without our token, or an
    undecodable/tampered state MUST each be rejected with HTTP 417 and MUST
    NOT log anyone in;
  * a state carrying our token completes the login.

Deliberately frappe-free, like its sibling test_sso_cookie_contract.py, so it
runs under plain `python -m unittest` in CI with no bench and no live site.
See .github/scripts/ci_python_tests.py — a test module not listed there is a
test CI does not run.
"""

import base64
import importlib.util
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

# Repo root: .../apps/erpnext/erpnext/exe_auth/ -> up 4
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
OAUTH_PATH = os.path.join(_REPO_ROOT, "frappe", "utils", "oauth.py")


def _encode_state(payload: dict) -> str:
	return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class TestOAuthStateCsrfContract(unittest.TestCase):
	"""The SSO callback must reject any state echo that does not carry our token."""

	@classmethod
	def setUpClass(cls):
		# Load frappe/utils/oauth.py under a stubbed `frappe` package. Only the
		# module-level imports matter (frappe.PermissionError, frappe._, the
		# frappe.apps / frappe.utils.password / frappe.website.utils names);
		# everything else the callback touches is patched per-test.
		saved = {name: sys.modules.get(name) for name in (
			"frappe", "frappe.utils", "frappe.apps",
			"frappe.utils.password", "frappe.website", "frappe.website.utils",
		)}

		frappe = types.ModuleType("frappe")
		frappe.PermissionError = type("PermissionError", (Exception,), {})
		frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
		frappe._ = lambda s: s
		frappe.respond_as_web_page = MagicMock()
		frappe.local = MagicMock()
		frappe.db = MagicMock()
		frappe.cache = MagicMock()
		frappe.response = {}

		frappe.utils = types.ModuleType("frappe.utils")
		frappe.utils.cint = lambda v: 0
		frappe.utils.get_url = lambda *a, **kw: "http://test.local"
		frappe.apps = types.ModuleType("frappe.apps")
		frappe.apps.get_default_path = lambda *a, **kw: None
		frappe.utils.password = types.ModuleType("frappe.utils.password")
		frappe.utils.password.get_decrypted_password = lambda *a, **kw: ""
		frappe.website = types.ModuleType("frappe.website")
		frappe.website.utils = types.ModuleType("frappe.website.utils")
		frappe.website.utils.get_home_page = lambda *a, **kw: "/"

		for name, module in (
			("frappe", frappe), ("frappe.utils", frappe.utils),
			("frappe.apps", frappe.apps), ("frappe.utils.password", frappe.utils.password),
			("frappe.website", frappe.website), ("frappe.website.utils", frappe.website.utils),
		):
			sys.modules[name] = module

		try:
			spec = importlib.util.spec_from_file_location("oauth_under_test", OAUTH_PATH)
			oauth = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(oauth)
		finally:
			for name, module in saved.items():
				if module is None:
					sys.modules.pop(name, None)
				else:
					sys.modules[name] = module

		cls.oauth = oauth
		cls.frappe = frappe

	def _run_callback(self, state):
		self.frappe.respond_as_web_page.reset_mock()
		self.frappe.local.login_manager.login_as.reset_mock()
		# Same-module helpers: patch them on the module under test.
		self.oauth.update_oauth_user = lambda *a, **kw: True
		self.oauth.redirect_post_login = MagicMock()
		self.oauth.login_oauth_user(
			{"email": "sso@example.com"}, provider="frappe", state=state
		)

	def _assert_rejected_417(self):
		self.frappe.respond_as_web_page.assert_called_once()
		kwargs = self.frappe.respond_as_web_page.call_args.kwargs
		self.assertEqual(kwargs.get("http_status_code"), 417)
		self.frappe.local.login_manager.login_as.assert_not_called()

	def test_missing_state_rejected_Fail(self):
		"""No state echo at all must NOT log in — that is the login-CSRF hole."""
		self._run_callback(state=None)
		self._assert_rejected_417()

	def test_empty_state_rejected_Fail(self):
		"""An empty-string state echo is the same hole and must be rejected."""
		self._run_callback(state="")
		self._assert_rejected_417()

	def test_state_without_token_rejected_Fail(self):
		"""A decodable state that does not carry our token is forged."""
		self._run_callback(state=_encode_state({"redirect_to": "/app"}))
		self._assert_rejected_417()

	def test_malformed_state_rejected_Fail(self):
		"""An undecodable state is tampered and must be rejected."""
		self._run_callback(state="!!!not-base64!!!")
		self._assert_rejected_417()

	def test_valid_state_completes_login(self):
		"""A state carrying our token logs in — the guard must not over-block."""
		self._run_callback(state=_encode_state({"token": "abc123", "redirect_to": "/app"}))
		self.frappe.respond_as_web_page.assert_not_called()
		self.frappe.local.login_manager.login_as.assert_called_once_with("sso@example.com")


if __name__ == "__main__":
	unittest.main()
