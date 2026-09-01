"""
The SSO redirect response can never silently be None at the Werkzeug boundary.

WHAT THIS PINS
──────────────
Bug be1870d0: `GET /api/method/erpnext.exe_auth.api.gotrue_login_start` — the
first hop of "Sign in via Exe SSO" — 500'd in production (v0.3.3, Python 3.14,
Werkzeug 3.1.6) with, twice, stacked:

    File ".../werkzeug/wrappers/request.py", line 196, in application
        return resp(*args[-2:])
    TypeError: 'NoneType' object is not callable

The reported theory was that the redirect-response construction itself
(`frappe.local.response["type"] = "redirect"` + `["location"]`, built by
`frappe.utils.response.build_response()` → `redirect()`) is broken on that
Python/Werkzeug combination. It is not: these tests drive the REAL shipped
`frappe/utils/response.py` through the REAL `Request.application` boundary at
the pinned Werkzeug and show the redirect path always hands Werkzeug a real,
callable Response. The production crash was the 429 builder returning None —
fixed by PR #70 and pinned by test_sso_rate_limit_response.py.

Nothing here is version-specific; it runs on whatever interpreter executes the
suite (CI pins Werkzeug to the same version pyproject.toml ships).

Same constraints as its siblings: frappe is stubbed, never imported, so this
runs under plain `python -m unittest` with no bench and no live site. Werkzeug
and orjson are NOT stubbed — they are the production halves under test.
"""

import ast
import importlib.util
import os
import sys
import types
import unittest

import orjson
from werkzeug.test import Client
from werkzeug.wrappers import Request

_HERE = os.path.dirname(os.path.abspath(__file__))
# .../apps/erpnext/erpnext/exe_auth/ -> up 4
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))

_RESPONSE_PY = os.path.join(_REPO_ROOT, "frappe", "utils", "response.py")
_API_PY = os.path.join(_REPO_ROOT, "apps", "erpnext", "erpnext", "exe_auth", "api.py")
_FRAPPE_API_PY = os.path.join(_REPO_ROOT, "frappe", "api", "__init__.py")

# The exact redirect the login page's SSO button triggers via
# gotrue_login_start: customer auth domain + product tag + callback + state.
SSO_TARGET = "https://auth.example.com/login?product=ERP&redirect=https%3A%2F%2Ferp.example.com%2Fapi%2Fmethod%2Ferpnext.exe_auth.api.gotrue_login_callback&state=s0m3st4t3"


class _AttrDict(dict):
	"""frappe.local.response is a frappe._dict: attribute access on missing
	keys yields None (frappe._dict.__getattr__ = dict.get), never AttributeError.
	as_json() relies on that for the optional http_status_code."""

	__getattr__ = dict.get


def _load_real_response_module(response_dict):
	"""Load the REAL frappe/utils/response.py by file path, frappe stubbed.

	Only frappe's own surface is faked (the names the module touches at import
	time); the response builders, Werkzeug and orjson are the shipped code.
	"""
	frappe_stub = types.ModuleType("frappe")

	class _Local:
		pass

	local = _Local()
	local.response = response_dict
	frappe_stub.local = local
	frappe_stub.response = response_dict
	frappe_stub._ = lambda s, *a, **k: s
	frappe_stub.db = None
	frappe_stub.message_log = []
	frappe_stub.debug_log = []

	utils_stub = types.ModuleType("frappe.utils")
	utils_stub.format_timedelta = lambda x: x
	utils_stub.orjson_dumps = orjson.dumps
	frappe_stub.utils = utils_stub

	def _register(name, module):
		sys.modules[name] = module
		setattr(frappe_stub, name.split(".")[-1], module)

	# Ancestors must exist in sys.modules for `from a.b.c import d` to resolve
	# without touching the real frappe tree.
	for name in (
		"frappe.model",
		"frappe.model.document",
		"frappe.sessions",
		"frappe.core",
		"frappe.core.doctype",
		"frappe.core.doctype.access_log",
		"frappe.core.doctype.access_log.access_log",
		"frappe.core.doctype.file",
		"frappe.core.doctype.file.utils",
	):
		_register(name, types.ModuleType(name))

	sys.modules["frappe.core.doctype.access_log.access_log"].make_access_log = (
		lambda *a, **k: None
	)
	sys.modules["frappe.core.doctype.file.utils"].check_path_safety = (
		lambda *a, **k: True
	)

	saved = {k: sys.modules.get(k) for k in sys.modules if k == "frappe" or k.startswith("frappe.")}
	sys.modules["frappe"] = frappe_stub
	sys.modules["frappe.utils"] = utils_stub
	try:
		spec = importlib.util.spec_from_file_location(
			"_exe_test_frappe_response", _RESPONSE_PY
		)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name in list(sys.modules):
			if name == "frappe" or name.startswith("frappe."):
				sys.modules.pop(name, None)
		for name, mod in saved.items():
			if mod is not None:
				sys.modules[name] = mod
	return module


class TestRedirectResponseIsAWsgiCallable(unittest.TestCase):
	"""`build_response()` on the redirect path must return a real Response.

	gotrue_login_start sets exactly these two keys (api.py); build_response
	directs them to redirect() -> werkzeug.utils.redirect. Its result goes
	straight back to Werkzeug's `return resp(*args[-2:])`, so None there is a
	guaranteed 500 with the production traceback.
	"""

	def test_redirect_build_returns_302_with_location(self):
		response_dict = _AttrDict({"type": "redirect", "location": SSO_TARGET})
		response_module = _load_real_response_module(response_dict)

		response = response_module.build_response()

		self.assertIsNotNone(
			response,
			"build_response() returned None for type='redirect' — Werkzeug would "
			"call this None as a WSGI callable and 500 (bug be1870d0 traceback)",
		)
		self.assertTrue(callable(response))
		self.assertEqual(302, response.status_code)
		self.assertEqual(SSO_TARGET, response.headers["Location"])

	def test_redirect_build_never_returns_none_across_targets(self):
		# Same builder, the other redirect target the SSO flow uses: the
		# callback's post-login redirect to the home page.
		for target in (SSO_TARGET, "/desk", "/app/home"):
			with self.subTest(target=target):
				response_dict = _AttrDict({"type": "redirect", "location": target})
				response_module = _load_real_response_module(response_dict)

				response = response_module.build_response()

				self.assertIsNotNone(response)
				self.assertEqual(302, response.status_code)


class TestRedirectThroughWerkzeugApplicationBoundary(unittest.TestCase):
	"""Drive the exact production boundary that raised the TypeError.

	`frappe.app.application` is decorated `@Request.application`, whose last
	line is `return resp(*args[-2:])`. This wires the REAL redirect builder in
	the place the view's return value occupies in production, at the pinned
	Werkzeug, on this interpreter. If the redirect path ever returned None this
	raises `TypeError: 'NoneType' object is not callable` from inside
	werkzeug/wrappers/request.py, byte for byte the production traceback.
	"""

	def _client(self, response_dict):
		response_module = _load_real_response_module(response_dict)

		@Request.application
		def application(request):
			# frappe/app.py returns frappe.api.handle()'s value verbatim; for the
			# redirect path that is build_response()'s value.
			return response_module.build_response()

		return Client(application)

	def test_sso_start_redirect_answers_302_not_a_type_error(self):
		client = self._client(_AttrDict({"type": "redirect", "location": SSO_TARGET}))

		response = client.get(
			"/api/method/erpnext.exe_auth.api.gotrue_login_start",
			headers={
				"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
			},
		)

		self.assertEqual(302, response.status_code)
		self.assertEqual(SSO_TARGET, response.headers["Location"])

	def test_sso_callback_redirect_answers_302_not_a_type_error(self):
		client = self._client(_AttrDict({"type": "redirect", "location": "/desk"}))

		response = client.get(
			"/api/method/erpnext.exe_auth.api.gotrue_login_callback",
			follow_redirects=False,
		)

		self.assertEqual(302, response.status_code)
		self.assertEqual("/desk", response.headers["Location"])


class TestNonSsoPathsReturnAResponse(unittest.TestCase):
	"""gotrue_login and admin_token return dicts -> the JSON builder.

	They must not be able to hand None to Werkzeug either: bug be1870d0's
	crash was reached through these endpoints' shared rate-limit decorator, so
	pin that their own (success) response path always builds a Response.
	"""

	def test_json_build_returns_200_response(self):
		response_dict = _AttrDict(
			{"success": True, "message": "Logged In", "sid": "abc", "home_page": "/desk"}
		)
		response_module = _load_real_response_module(response_dict)
		# make_logs touches message/log plumbing outside the builder contract.
		response_module.make_logs = lambda *a, **k: None

		response = response_module.as_json()

		self.assertIsNotNone(response)
		self.assertTrue(callable(response))
		self.assertEqual(200, response.status_code)


class TestSsoEndpointsStillUseTheRedirectContract(unittest.TestCase):
	"""Structural guard: both SSO endpoints keep setting the redirect response.

	If either endpoint stops setting `response["type"] = "redirect"` (e.g. by
	returning a dict instead), the SSO flow silently changes shape and the
	boundary tests above stop describing it.
	"""

	def _assigned_constants(self, function_node):
		found = set()
		for node in ast.walk(function_node):
			if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
				found.add(node.value.value)
		return found

	def test_gotrue_login_start_sets_redirect_type_and_location(self):
		tree = ast.parse(open(_API_PY, encoding="utf-8").read(), filename=_API_PY)
		functions = {
			n.name: n
			for n in ast.walk(tree)
			if isinstance(n, ast.FunctionDef)
			and n.name in ("gotrue_login_start", "gotrue_login_callback")
		}

		for name in ("gotrue_login_start", "gotrue_login_callback"):
			with self.subTest(endpoint=name):
				self.assertIn(name, functions, f"{name}() not found in api.py")
				constants = self._assigned_constants(functions[name])
				self.assertIn("redirect", constants)

	def test_frappe_api_handle_returns_response_objects_verbatim(self):
		# frappe/api/__init__.py handle(): a Response returned by an endpoint
		# (the redirect path) is returned un-wrapped, never dropped for None.
		source = open(_FRAPPE_API_PY, encoding="utf-8").read()
		self.assertIn("isinstance(data, Response)", source)
		self.assertIn("return data", source)


if __name__ == "__main__":
	unittest.main(verbosity=2)
