"""
SSO callback token-source contract test — the ERP login blocker.

THE DEFECT
──────────
Clicking "Sign in via Exe SSO" on erp.<apex> correctly redirected to
auth.<apex>, but the round trip back to
`/api/method/erpnext.exe_auth.api.gotrue_login_callback` failed with
401 "No access token provided" every single time. Network inspection showed the
callback URL coming back with ZERO query parameters — no `?access_token=`, no
`?state=`.

`gotrue_login_callback` read the token from exactly one place:

	access_token = frappe.form_dict.get("access_token")
	if not access_token:
		frappe.throw("No access token provided", frappe.AuthenticationError)

exe-auth stopped emitting `?access_token=` in redirects site-wide (bug 83ba9546
— tokens in redirect URLs leak via browser history, access logs and Referer
headers). It now authenticates server-side and sets the credential as an
HttpOnly `exe_sess` cookie on the registrable apex domain. Because that
cookie's Domain covers every sibling subdomain, the browser attaches it
automatically to the callback request against erp.<apex> — HttpOnly only blocks
`document.cookie` reads from page JS, it does not stop the cookie travelling on
the wire.

exe-wiki (`resolveSsoJwt`, bugs 7772300f / 6017dd9f) and exe-crm were both
already moved onto the cookie. ERP's callback was the one integration that was
missed, so ERP was the only product in the stack where SSO login could not
complete at all.

TWO COOKIES, ONE CREDENTIAL
───────────────────────────
This is the trap the fix must not fall into. Both are set by the same function
in exe-auth (`setSessionCookies`, auth.njs.js):

  * `exe_sess` — HttpOnly, Secure, SameSite=Lax, Domain=.<apex>, and its VALUE
	is the GoTrue access JWT. exe-auth itself bearer-authenticates with it
	(`me()`, `logout()`), and `build_logout_revocation_request` in exe_perms.py
	already hands this exact value back to exe-auth.
  * `exe_access_token` — the "canonical SSO cookie" of
	test_sso_cookie_contract.py, but that contract is about the redirect GATE,
	not the credential. It is a JS-readable PRESENCE SENTINEL whose value is
	the literal "1" (`httpOnly: false`; see frappe/templates/base.html, bug
	62c42448). Sending it to GoTrue as a bearer token sends `Bearer 1`.

`testSentinelCookieIsNeverUsedAsBearerFail` below is the machine guard for that
distinction.

WHAT THIS FILE GUARDS
─────────────────────
The callback is driven END TO END against a stubbed Frappe, so this is a real
reproduction of the reported 401 rather than a spelling check on the source: a
request carrying the apex `exe_sess` cookie and NO query parameters must log the
user in. On the parent commit that request throws
`AuthenticationError("No access token provided")` and these tests go red.

The legacy `?access_token=` query path and the CSRF `state` gate are both
asserted UNCHANGED — the query-param requirement was the only broken thing.

Deliberately frappe-free (Frappe is stubbed, never imported), like its siblings
test_sso_cookie_contract.py and test_login_page_contract.py, so it runs under
plain `python -m unittest` in CI with no bench and no live site.
"""

import importlib.util
import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))

# The credential: HttpOnly, apex-scoped, value IS the GoTrue JWT.
CREDENTIAL_COOKIE = "exe_sess"
# The presence sentinel: JS-readable, value is the literal "1". NOT a token.
SENTINEL_COOKIE = "exe_access_token"
SENTINEL_VALUE = "1"
STATE_COOKIE = "exe_sso_state"

FAKE_JWT = "header.payload.signature"
LEGACY_QUERY_JWT = "legacy.query.jwt"
SSO_EMAIL = "someone@acme.test"


class _StubAuthenticationError(Exception):
	pass


class _StubValidationError(Exception):
	pass


class _CookieManager:
	def __init__(self):
		self.deleted = []
		self.written = {}

	def set_cookie(self, name, value, **kwargs):
		self.written[name] = value

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
	"""Minimal stand-in for a `requests` response from GoTrue /user."""

	def __init__(self, status_code, payload=None, text=""):
		self.status_code = status_code
		self._payload = payload or {}
		self.text = text

	def json(self):
		return self._payload


def _identity_decorator(*args, **kwargs):
	def wrap(fn):
		return fn

	return wrap


def _build_frappe_stub():
	"""A Frappe just real enough to drive gotrue_login_callback."""
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

	def _log_error(title=None, message=None, **kwargs):
		frappe.logged_errors.append((title, message))

	def _get_doc(*args, **kwargs):	# pragma: no cover - not reached on these paths
		raise AssertionError("unexpected frappe.get_doc%r" % (args,))

	def _logger(*args, **kwargs):
		noop = lambda *a, **k: None	 # noqa: E731
		return types.SimpleNamespace(
			debug=noop, info=noop, warning=noop, error=noop
		)

	frappe.throw = _throw
	frappe.log_error = _log_error
	frappe.get_doc = _get_doc
	frappe.logger = _logger
	frappe.utils = types.SimpleNamespace(
		get_url=lambda path="": "https://erp.acme.test" + path
	)

	rate_limiter = types.ModuleType("frappe.rate_limiter")
	rate_limiter.rate_limit = _identity_decorator

	website = types.ModuleType("frappe.website")
	website_utils = types.ModuleType("frappe.website.utils")
	website_utils.get_home_page = lambda: "/app"
	website.utils = website_utils

	frappe.rate_limiter = rate_limiter
	frappe.website = website

	return frappe, rate_limiter, website, website_utils


def _build_requests_stub():
	requests = types.ModuleType("requests")

	class RequestException(Exception):
		pass

	requests.RequestException = RequestException

	def _unstubbed(*args, **kwargs):  # pragma: no cover
		raise AssertionError("requests called before the test stubbed it")

	requests.get = _unstubbed
	requests.post = _unstubbed
	return requests


def _load_by_path(module_name, filename):
	path = os.path.join(_HERE, filename)
	spec = importlib.util.spec_from_file_location(module_name, path)
	module = importlib.util.module_from_spec(spec)
	sys.modules[module_name] = module
	spec.loader.exec_module(module)
	return module


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


def _load_api_module():
	"""Import erpnext.exe_auth.api against fresh stubs, then restore sys.modules.

	Each call yields an isolated (api, exe_perms, frappe, requests) quadruple so
	tests cannot leak stub state into one another. `exe_perms` is loaded for
	real — it is frappe-free by design — so the token-resolution policy under
	test is the production one, not a copy.
	"""
	frappe, rate_limiter, website, website_utils = _build_frappe_stub()
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
		exe_perms = _load_by_path("erpnext.exe_auth.exe_perms", "exe_perms.py")
		exe_auth_pkg.exe_perms = exe_perms
		api = _load_by_path("erpnext.exe_auth.api", "api.py")
	finally:
		for name, module in saved.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	return api, exe_perms, frappe, requests


class _Callback:
	"""Drives one gotrue_login_callback request against the stubs."""

	def __init__(self, query=None, cookies=None, existing_user=True, gotrue_status=200):
		self.api, self.exe_perms, self.frappe, self.requests = _load_api_module()
		self.frappe.form_dict = dict(query or {})
		self.frappe.request = _Request(cookies or {})
		self.frappe.conf = {"gotrue_url": "http://gotrue:9999"}
		self.frappe.db = _Db([SSO_EMAIL] if existing_user else [])
		self.bearers = []

		def _get(url, headers=None, timeout=None):
			self.bearers.append((headers or {}).get("Authorization"))
			if gotrue_status != 200:
				return _GoTrueResponse(gotrue_status, text="denied")
			# No app_metadata => unmanaged user, so role reconciliation is a
			# no-op and the login path stays inside the stubbed surface.
			return _GoTrueResponse(200, {"email": SSO_EMAIL})

		self.requests.get = _get

	def run(self):
		return self.api.gotrue_login_callback()

	@property
	def logged_in_as(self):
		return self.frappe.local.login_manager.logged_in_as


def _apex_cookies(**overrides):
	"""The cookies a real post-83ba9546 callback request carries."""
	cookies = {
		STATE_COOKIE: "nonce-set-by-login-start",
		CREDENTIAL_COOKIE: FAKE_JWT,
		SENTINEL_COOKIE: SENTINEL_VALUE,
	}
	cookies.update(overrides)
	return {k: v for k, v in cookies.items() if v is not None}


class TestCallbackAcceptsApexSessionCookie(unittest.TestCase):
	"""A callback with the apex cookie and NO query parameters must work."""

	def testApexCookieWithoutQueryTokenLogsUserIn(self):
		"""REPRODUCER — red on the parent commit with the reported 401.

		This is the exact shape observed in the browser: the callback URL has
		no query string at all, and the only thing identifying the user is the
		HttpOnly apex `exe_sess` cookie the browser attached on its own.
		"""
		callback = _Callback(query={}, cookies=_apex_cookies())

		callback.run()

		self.assertEqual(
			callback.logged_in_as,
			SSO_EMAIL,
			"a callback carrying the apex exe_sess cookie and no query "
			"parameters must complete login; before the fix it threw "
			"AuthenticationError('No access token provided') — the 401 that "
			"made SSO sign-in impossible on erp.<apex>",
		)

	def testApexCookieRedirectsToDeskOnSuccess(self):
		callback = _Callback(query={}, cookies=_apex_cookies())

		callback.run()

		self.assertEqual(callback.frappe.local.response["type"], "redirect")
		self.assertEqual(callback.frappe.local.response["location"], "/app")

	def testApexCookieIsSentToGoTrueAsBearer(self):
		"""The cookie VALUE is the JWT, validated by the SAME existing call."""
		callback = _Callback(query={}, cookies=_apex_cookies())

		callback.run()

		self.assertEqual(
			callback.bearers,
			["Bearer " + FAKE_JWT],
			"the exe_sess cookie value must flow into the existing GoTrue "
			"/user validation, not into a duplicated code path",
		)

	def testCookieSourcedTokenIsStillValidatedFail(self):
		"""Reading the cookie must not mean trusting it.

		A cookie-sourced token GoTrue rejects must fail exactly like a rejected
		query-sourced one. The fix changes where the token comes FROM, never
		whether it is verified.
		"""
		callback = _Callback(query={}, cookies=_apex_cookies(), gotrue_status=401)

		with self.assertRaises(_StubAuthenticationError) as caught:
			callback.run()

		self.assertIn("Invalid or expired SSO token", str(caught.exception))
		self.assertIsNone(callback.logged_in_as)

	def testSentinelCookieIsNeverUsedAsBearerFail(self):
		"""`exe_access_token` is the literal "1" — never a credential.

		exe-auth sets it with `httpOnly: false` purely so downstream presence
		gates keep working. If the callback ever fell back to it, ERP would
		send `Bearer 1` to GoTrue and every login would 401 for a much more
		confusing reason.
		"""
		callback = _Callback(
			query={},
			cookies=_apex_cookies(**{CREDENTIAL_COOKIE: None}),
		)

		with self.assertRaises(_StubAuthenticationError) as caught:
			callback.run()

		self.assertIn("No access token provided", str(caught.exception))
		self.assertEqual(
			callback.bearers,
			[],
			"the sentinel cookie must never reach GoTrue as a bearer token",
		)


class TestCallbackBehaviourUnchanged(unittest.TestCase):
	"""Everything except the token SOURCE must behave exactly as before."""

	def testLegacyQueryTokenStillAccepted(self):
		"""Backward compatibility with deployments on the old redirect."""
		callback = _Callback(
			query={"access_token": LEGACY_QUERY_JWT},
			cookies={STATE_COOKIE: "nonce-set-by-login-start"},
		)

		callback.run()

		self.assertEqual(callback.logged_in_as, SSO_EMAIL)
		self.assertEqual(callback.bearers, ["Bearer " + LEGACY_QUERY_JWT])

	def testQueryTokenTakesPrecedenceOverCookie(self):
		"""An explicit query token wins — the cookie is only a fallback."""
		callback = _Callback(
			query={"access_token": LEGACY_QUERY_JWT}, cookies=_apex_cookies()
		)

		callback.run()

		self.assertEqual(callback.bearers, ["Bearer " + LEGACY_QUERY_JWT])

	def testNoTokenFromEitherSourceStillThrowsFail(self):
		"""With neither source, the original error is preserved verbatim."""
		callback = _Callback(
			query={}, cookies={STATE_COOKIE: "nonce-set-by-login-start"}
		)

		with self.assertRaises(_StubAuthenticationError) as caught:
			callback.run()

		self.assertIn("No access token provided", str(caught.exception))

	def testCsrfStateMismatchStillRejectedFail(self):
		"""The CSRF gate is untouched and still runs BEFORE token resolution."""
		callback = _Callback(
			query={"state": "attacker-nonce"},
			cookies=_apex_cookies(**{STATE_COOKIE: "victim-nonce"}),
		)

		with self.assertRaises(_StubAuthenticationError) as caught:
			callback.run()

		self.assertIn("Invalid or missing login state", str(caught.exception))
		self.assertIsNone(callback.logged_in_as)
		self.assertEqual(
			callback.bearers,
			[],
			"a CSRF-rejected callback must never reach GoTrue, cookie or not",
		)


class TestResolveSsoAccessTokenPolicy(unittest.TestCase):
	"""Unit-level cover for the pure policy in exe_perms."""

	@classmethod
	def setUpClass(cls):
		_api, cls.exe_perms, _frappe, _requests = _load_api_module()

	def resolve(self, query_token, cookies):
		return self.exe_perms.resolve_sso_access_token(query_token, cookies)

	def testQueryTokenPreferred(self):
		self.assertEqual(
			self.resolve("from-query", {CREDENTIAL_COOKIE: "from-cookie"}),
			"from-query",
		)

	def testFallsBackToCredentialCookie(self):
		self.assertEqual(
			self.resolve(None, {CREDENTIAL_COOKIE: FAKE_JWT}), FAKE_JWT
		)

	def testEmptyQueryTokenFallsThroughToCookie(self):
		"""An empty/whitespace query param is absence, not a token."""
		for empty in ("", "	  "):
			with self.subTest(query_token=empty):
				self.assertEqual(
					self.resolve(empty, {CREDENTIAL_COOKIE: FAKE_JWT}), FAKE_JWT
				)

	def testCookieNameIsTheSharedConstant(self):
		"""One constant, so login and logout can never drift apart."""
		self.assertEqual(self.exe_perms.EXE_SESS_COOKIE, CREDENTIAL_COOKIE)

	def testNoSourceReturnsNoneFail(self):
		self.assertIsNone(self.resolve(None, {}))
		self.assertIsNone(self.resolve(None, None))
		self.assertIsNone(self.resolve("", {CREDENTIAL_COOKIE: ""}))

	def testSentinelCookieIsNotATokenFail(self):
		self.assertIsNone(
			self.resolve(None, {SENTINEL_COOKIE: SENTINEL_VALUE}),
			"exe_access_token is a presence sentinel with the literal value "
			"'1'; resolving it as the access token sends `Bearer 1` to GoTrue",
		)


if __name__ == "__main__":
	unittest.main()
