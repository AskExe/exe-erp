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
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlparse

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
	def __init__(self):
		self.set_calls = []
		self.deleted = []

	def set_cookie(self, name, value, **kwargs):
		self.set_calls.append((name, value, kwargs))

	def delete_cookie(self, name):
		self.deleted.append(name)


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


class TestSsoStateHandoff(unittest.TestCase):
	"""Auth follows the encoded callback verbatim; it does not echo outer state."""

	def _start(self, override=None):
		api, frappe, requests = _load_api_module("desk")
		frappe.conf = {"gotrue_url": "http://gotrue:9999"}
		if override is not None:
			frappe.conf["gotrue_auth_redirect_url"] = override
		frappe.www = types.ModuleType("frappe.www")
		frappe.www.login = types.ModuleType("frappe.www.login")
		frappe.www.login.get_exe_auth_url = lambda: "https://auth.acme.test"
		saved = {name: sys.modules.get(name) for name in ("frappe.www", "frappe.www.login")}
		sys.modules["frappe.www"] = frappe.www
		sys.modules["frappe.www.login"] = frappe.www.login
		try:
			with mock.patch("secrets.token_urlsafe", return_value="one-time-nonce"):
				api.gotrue_login_start()
		finally:
			for name, module in saved.items():
				if module is None:
					sys.modules.pop(name, None)
				else:
					sys.modules[name] = module
		return api, frappe, requests

	def test_override_keeps_parameters_but_moves_state_inside_erp_callback(self):
		callback = "https://erp.acme.test/api/method/erpnext.exe_auth.api.gotrue_login_callback"
		override = "https://auth.acme.test/login?" + urlencode(
			{"product": "ERP", "redirect": callback, "state": "stale-outer", "theme": "dark"}
		)
		_, frappe, _ = self._start(override)
		query = parse_qs(urlparse(frappe.local.response["location"]).query)
		self.assertEqual(query["redirect"], [callback + "?state=one-time-nonce"])
		self.assertEqual(query["theme"], ["dark"])
		self.assertNotIn("state", query)

	def test_override_without_exact_erp_callback_rejected_before_cookie(self):
		for override in (
			"https://auth.acme.test/login?product=ERP",
			"https://auth.acme.test/login?redirect=https%3A%2F%2Fevil.test%2Fcallback",
			"https://auth.acme.test/login?redirect=https%3A%2F%2Ferp.acme.test%2Fapi%2Fmethod%2Ferpnext.exe_auth.api.gotrue_login_callback%3Faccess_token%3Dleak",
		):
			with self.subTest(override=override):
				with self.assertRaises(_StubValidationError):
					self._start(override)

	def test_existing_and_new_login_follow_same_stateful_callback(self):
		api, frappe, requests = self._start()
		auth_url = urlparse(frappe.local.response["location"])
		query = parse_qs(auth_url.query)
		self.assertEqual(auth_url.netloc, "auth.acme.test")
		self.assertEqual(query["product"], ["ERP"])
		self.assertNotIn("state", query)
		self.assertNotIn("access_token", query)
		callback = query["redirect"][0]
		self.assertEqual(
			callback,
			"https://erp.acme.test/api/method/erpnext.exe_auth.api.gotrue_login_callback?state=one-time-nonce",
		)
		self.assertEqual(
			frappe.local.cookie_manager.set_calls,
			[(STATE_COOKIE, "one-time-nonce", {"httponly": True, "samesite": "Lax", "max_age": 600})],
		)

		# Auth's existing-session and post-login branches both follow `redirect`
		# exactly. The callback consumes its query state and HttpOnly credential.
		for _flow in ("existing_session", "new_login"):
			with self.subTest(flow=_flow):
				frappe.form_dict = {"state": parse_qs(urlparse(callback).query)["state"][0]}
				frappe.request = _Request({STATE_COOKIE: "one-time-nonce", CREDENTIAL_COOKIE: FAKE_JWT})
				frappe.db = _Db([SSO_EMAIL])
				frappe.local.login_manager = _LoginManager()
				requests.get = lambda *a, **kw: _GoTrueResponse(200, {"email": SSO_EMAIL})
				api.gotrue_login_callback()
				self.assertEqual(frappe.local.login_manager.logged_in_as, SSO_EMAIL)
				self.assertIn(STATE_COOKIE, frappe.local.cookie_manager.deleted)

	def test_tampered_or_replayed_state_rejected_before_user_fetch(self):
		api, frappe, requests = self._start()
		requests.get = lambda *a, **kw: self.fail("tampered state reached GoTrue")
		for received, cookies in (
			("tampered", {STATE_COOKIE: "one-time-nonce", CREDENTIAL_COOKIE: FAKE_JWT}),
			("one-time-nonce", {CREDENTIAL_COOKIE: FAKE_JWT}),
		):
			with self.subTest(received=received, cookies=cookies):
				frappe.form_dict = {"state": received}
				frappe.request = _Request(cookies)
				frappe.local.login_manager = _LoginManager()
				with self.assertRaises(_StubAuthenticationError):
					api.gotrue_login_callback()
				self.assertIsNone(frappe.local.login_manager.logged_in_as)


if __name__ == "__main__":  # pragma: no cover
	unittest.main()
