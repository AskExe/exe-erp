"""
SSO callback redirect-target contract test — bug 2e8744b0.

THE DEFECT
──────────
After every other SSO blocker was fixed (dead button #65, legacy query param
#69, 429-response builder #70), a full SSO login finally worked — and then
landed the browser on https://erp.<apex>/api/method/desk, a Frappe error page.
A curl of the callback showed `Location: desk` — no leading slash.

`gotrue_login_callback` set:

	frappe.local.response["location"] = get_home_page() or "/desk"

frappe.website.utils.get_home_page() returns the home page NAME configured for
the site ("desk"), not a URL path. Per RFC 3986 section 5 a relative reference
without a leading slash resolves against the CURRENT request's directory — the
callback is served from /api/method/ — so "desk" resolved to /api/method/desk.
The login itself was fine (a real sid cookie was set; navigating to /app
worked); only the redirect TARGET was malformed.

WHAT THIS FILE GUARDS
─────────────────────
The callback is driven END TO END against a stubbed Frappe whose
get_home_page() returns the exact live-bug shape ("desk", no slash): the
successful login must redirect to /desk, never to a relative reference that
browsers would resolve against /api/method/. Absolute paths ("/app"), full
URLs and the empty-fallback case are pinned too, so the normalization cannot
over-apply to targets that are already absolute.

Deliberately frappe-free (Frappe is stubbed, never imported), like its
siblings test_sso_callback_token_source.py and test_sso_cookie_contract.py,
so it runs under plain `python -m unittest` in CI with no bench and no live
site.
"""

import importlib.util
import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))

STATE_COOKIE = "exe_sso_state"
CREDENTIAL_COOKIE = "exe_sess"

FAKE_JWT = "header.payload.signature"
SSO_EMAIL = "someone@acme.test"


class _StubAuthenticationError(Exception):
	pass


class _StubValidationError(Exception):
	pass


class _CookieManager:
	def set_cookie(self, name, value, **kwargs):
		pass

	def delete_cookie(self, name):
		pass


class _LoginManager:
	def __init__(self):
		self.logged_in_as = None

	def login_as(self, user):
		self.logged_in_as = user


class _Local:
	def __init__(self):
		self.cookie_manager = _CookieManager()
		self.login_manager = _LoginManager()
		self.response = {}


class _Request:
	def __init__(self, cookies):
		self.cookies = dict(cookies)


class _Db:
	def __init__(self, existing_users):
		self._existing = set(existing_users)

	def exists(self, doctype, name):
		return doctype == "User" and name in self._existing


class _GoTrueResponse:
	def __init__(self, status_code, payload=None):
		self.status_code = status_code
		self._payload = payload or {}
		self.text = ""

	def json(self):
		return self._payload


def _identity_decorator(*args, **kwargs):
	def wrap(fn):
		return fn

	return wrap


def _build_frappe_stub(home_page):
	"""A Frappe just real enough to drive gotrue_login_callback to its redirect.

	`home_page` is what frappe.website.utils.get_home_page() reports for the
	site — "desk" (the live bug shape), "/app", a full URL, or None.
	"""
	frappe = types.ModuleType("frappe")

	frappe.AuthenticationError = _StubAuthenticationError
	frappe.ValidationError = _StubValidationError
	frappe.whitelist = _identity_decorator
	frappe.form_dict = {}
	frappe.request = None
	frappe.local = _Local()
	frappe.conf = {}
	frappe.db = _Db([])
	frappe.session = types.SimpleNamespace(sid="stub-sid")
	frappe.logged_errors = []

	def _throw(message, exc=_StubValidationError):
		raise exc(message)

	frappe.throw = _throw
	frappe.log_error = lambda title=None, message=None, **kw: frappe.logged_errors.append(
		(title, message)
	)
	frappe.get_doc = lambda *a, **kw: (_ for _ in ()).throw(
		AssertionError(f"unexpected frappe.get_doc{a!r}")
	)
	frappe.logger = lambda *a, **kw: types.SimpleNamespace(
		debug=lambda *x: None,
		info=lambda *x: None,
		warning=lambda *x: None,
		error=lambda *x: None,
	)
	frappe.utils = types.SimpleNamespace(
		get_url=lambda path="": "https://erp.acme.test" + path
	)

	rate_limiter = types.ModuleType("frappe.rate_limiter")
	rate_limiter.rate_limit = _identity_decorator

	website = types.ModuleType("frappe.website")
	website_utils = types.ModuleType("frappe.website.utils")
	# THE LIVE BUG SHAPE: get_home_page() returns a bare page NAME, which the
	# pre-fix code put straight into the Location header.
	website_utils.get_home_page = lambda: home_page
	website.utils = website_utils

	frappe.rate_limiter = rate_limiter
	frappe.website = website

	return frappe, rate_limiter, website, website_utils


def _build_requests_stub():
	requests = types.ModuleType("requests")

	class RequestException(Exception):
		pass

	requests.RequestException = RequestException
	requests.get = lambda *a, **kw: (_ for _ in ()).throw(
		AssertionError("requests.get called before the test stubbed it")
	)
	requests.post = lambda *a, **kw: (_ for _ in ()).throw(
		AssertionError("requests.post called before the test stubbed it")
	)
	return requests


_PATCHED_MODULES = (
	"frappe",
	"frappe.rate_limiter",
	"frappe.website",
	"frappe.website.utils",
	"requests",
	"erpnext",
	"erpnext.exe_auth",
	"erpnext.exe_auth.exe_perms",
	"erpnext.exe_auth.api",
)


def _load_api_module(home_page):
	"""Import erpnext.exe_auth.api against fresh stubs, then restore sys.modules."""
	frappe, rate_limiter, website, website_utils = _build_frappe_stub(home_page)
	requests = _build_requests_stub()

	erpnext_pkg = types.ModuleType("erpnext")
	erpnext_pkg.__path__ = []
	exe_auth_pkg = types.ModuleType("erpnext.exe_auth")
	exe_auth_pkg.__path__ = [_HERE]

	saved = {name: sys.modules.get(name) for name in _PATCHED_MODULES}

	sys.modules["frappe"] = frappe
	sys.modules["frappe.rate_limiter"] = rate_limiter
	sys.modules["frappe.website"] = website
	sys.modules["frappe.website.utils"] = website_utils
	sys.modules["requests"] = requests
	sys.modules["erpnext"] = erpnext_pkg
	sys.modules["erpnext.exe_auth"] = exe_auth_pkg
	sys.modules.pop("erpnext.exe_auth.exe_perms", None)
	sys.modules.pop("erpnext.exe_auth.api", None)

	try:
		exe_perms_spec = importlib.util.spec_from_file_location(
			"erpnext.exe_auth.exe_perms", os.path.join(_HERE, "exe_perms.py")
		)
		exe_perms = importlib.util.module_from_spec(exe_perms_spec)
		sys.modules["erpnext.exe_auth.exe_perms"] = exe_perms
		exe_perms_spec.loader.exec_module(exe_perms)
		exe_auth_pkg.exe_perms = exe_perms
		spec = importlib.util.spec_from_file_location(
			"erpnext.exe_auth.api", os.path.join(_HERE, "api.py")
		)
		api = importlib.util.module_from_spec(spec)
		sys.modules["erpnext.exe_auth.api"] = api
		spec.loader.exec_module(api)
	finally:
		for name, module in saved.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	return api, frappe, requests


def _run_callback(home_page):
	"""One successful callback request (apex cookie + state echo) → (api, frappe)."""
	api, frappe, requests = _load_api_module(home_page)
	frappe.form_dict = {STATE_COOKIE: "nonce-set-by-login-start"}
	frappe.request = _Request(
		{STATE_COOKIE: "nonce-set-by-login-start", CREDENTIAL_COOKIE: FAKE_JWT}
	)
	frappe.conf = {"gotrue_url": "http://gotrue:9999"}
	frappe.db = _Db([SSO_EMAIL])
	requests.get = lambda *a, **kw: _GoTrueResponse(
		200, {"email": SSO_EMAIL}
	)  # no app_metadata => unmanaged, role reconciliation stays out of the stub
	api.gotrue_login_callback()
	return api, frappe


class TestSsoCallbackRedirectTarget(unittest.TestCase):
	"""bug 2e8744b0 — the post-login Location must never be a relative ref."""

	def testBareHomePageNameGetsLeadingSlash(self):
		# The live bug: site home page is "desk"; the redirect went out as
		# `Location: desk` and browsers resolved it to /api/method/desk.
		_, frappe = _run_callback("desk")
		self.assertEqual(frappe.local.response["type"], "redirect")
		self.assertEqual(frappe.local.response["location"], "/desk")

	def testAbsolutePathPassesThroughUnchanged(self):
		_, frappe = _run_callback("/app")
		self.assertEqual(frappe.local.response["location"], "/app")

	def testFullUrlPassesThroughUnchanged(self):
		_, frappe = _run_callback("https://erp.acme.test/app")
		self.assertEqual(frappe.local.response["location"], "https://erp.acme.test/app")

	def testEmptyHomePageFallsBackToDesk(self):
		_, frappe = _run_callback(None)
		self.assertEqual(frappe.local.response["location"], "/desk")

	def testSuccessfulLoginStillHappensBeforeRedirect(self):
		# The redirect fix must not regress the login itself: the user is
		# logged in and the redirect is the LAST thing the callback does.
		_, frappe = _run_callback("desk")
		self.assertEqual(frappe.local.login_manager.logged_in_as, SSO_EMAIL)
		self.assertEqual(frappe.local.response["type"], "redirect")


if __name__ == "__main__":  # pragma: no cover
	unittest.main()
