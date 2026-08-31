"""
The SSO 429 -> 500 crash: a rate-limited endpoint must answer 429, not None.

WHAT BROKE
──────────
`GET /api/method/erpnext.exe_auth.api.gotrue_login_start` — the FIRST hop of
"Sign in via Exe SSO", before any redirect to the auth domain — returned 500
with this, twice, stacked:

    File ".../werkzeug/wrappers/request.py", line 196, in application
        return resp(*args[-2:])
    TypeError: 'NoneType' object is not callable

The reported theory was that building a redirect response
(`frappe.local.response["type"] = "redirect"`) is broken under Werkzeug 3.1.6 on
Python 3.14. It is not. The redirect path is fine and was never reached. Nothing
about this is version-specific.

Werkzeug's `Request.application` decorator (which wraps `frappe.app.application`)
ends with `return resp(*args[-2:])` — it CALLS whatever the wrapped function
returned, as a WSGI callable. `TypeError: 'NoneType' object is not callable`
there means one thing and only one thing: the view returned `None`.

`frappe.app.application` returns whatever `handle_exception()` produced. For a
429 that is not answered as JSON, `handle_exception` does:

    elif http_status_code == 429:
        response = frappe.rate_limiter.respond()

and `frappe.rate_limiter.respond()` returned `None` unless
`frappe.local.rate_limiter` existed AND had `rejected` set. That attribute is
only ever set by `frappe.rate_limiter.apply()`, and only when
`site_config.rate_limit` is configured — which no deployment here sets.

So EVERY 429 reaching that branch returned `None` -> 500 TypeError.

WHY SSO, WHY NOW
────────────────
`gotrue_login_start` carries `@rate_limit(key="gotrue_login_start", limit=10,
seconds=900)`. That decorator raises `frappe.RateLimitExceededError`
(http_status_code 429) by itself, with no `frappe.local.rate_limiter` involved.
The eleventh click of the SSO button inside 15 minutes is a 429 — and a browser
navigation sends `Accept: text/html,...`, so `handle_exception`'s
`respond_as_json` is False (the request is under /api/ but the Accept header
starts with "text"), which routes it to exactly the broken branch. An
XHR/JSON client on the same endpoint gets a correct 429 and never sees this.

The endpoint only became reachable at all this week (PR #65 gave the SSO button
an href, PR #69 fixed the callback), and it takes eleven clicks to trigger — so
the crash showed up the first time someone debugged the login flow by clicking
it repeatedly. The same hole sits under `gotrue_login` (limit 5),
`gotrue_login_callback` (10) and `admin_token` (5).

THE DOUBLED TRACEBACK
─────────────────────
The identical error appeared twice with "During handling of the above exception,
another exception occurred". That is `exe_bridge/middleware.py`: its
`except Exception` handler re-invoked `self.app(environ, start_response)` — i.e.
it REPLAYED the failed request, hitting the same TypeError, and in the process
spent a second rate-limit token per click. Covered by
`TestTracingMiddlewareDoesNotReplayTheRequest` below.

Deliberately frappe-free (Frappe is stubbed, never imported) like its siblings
in this directory, so it runs under plain `python -m unittest` in CI with no
bench and no live site. Werkzeug is NOT stubbed: the whole point is to drive the
real `Request.application` boundary that raised the production TypeError, at the
version this repo pins.
"""

import ast
import contextlib
import importlib.util
import os
import re
import sys
import types
import unittest

from werkzeug.test import Client
from werkzeug.wrappers import Request, Response

_HERE = os.path.dirname(os.path.abspath(__file__))
# .../apps/erpnext/erpnext/exe_auth/ -> up 4
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))

_RATE_LIMITER_PY = os.path.join(_REPO_ROOT, "frappe", "rate_limiter.py")
_APP_PY = os.path.join(_REPO_ROOT, "frappe", "app.py")
_MIDDLEWARE_PY = os.path.join(
	_REPO_ROOT, "apps", "erpnext", "erpnext", "exe_bridge", "middleware.py"
)

# A browser navigating to the SSO button sends this. It is what makes
# handle_exception pick the HTML branch instead of the JSON one.
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


class _Local:
	"""Stand-in for `frappe.local`. Note what is NOT here: `rate_limiter`.

	That is the production state — `frappe.local.rate_limiter` is only set when
	`site_config.rate_limit` is configured, and it is not configured on any
	exe-erp deployment.
	"""


def _load_real_rate_limiter(local=None):
	"""Load the REAL frappe/rate_limiter.py by file path, with frappe stubbed.

	Everything under test here is the shipped source of that module and the
	shipped Werkzeug. Only frappe's own surface is faked, and only the handful
	of names the module touches at import time.
	"""
	frappe_stub = types.ModuleType("frappe")
	frappe_stub.local = local if local is not None else _Local()
	frappe_stub._ = lambda s, *a, **k: s
	frappe_stub.cache = None
	frappe_stub.request = None
	frappe_stub.form_dict = {}

	class _TooManyRequestsError(Exception):
		http_status_code = 429

	frappe_stub.TooManyRequestsError = _TooManyRequestsError

	utils_stub = types.ModuleType("frappe.utils")
	utils_stub.cint = lambda v, default=0: int(v or default)
	frappe_stub.utils = utils_stub

	saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils")}
	sys.modules["frappe"] = frappe_stub
	sys.modules["frappe.utils"] = utils_stub
	try:
		spec = importlib.util.spec_from_file_location(
			"_exe_test_frappe_rate_limiter", _RATE_LIMITER_PY
		)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for name, mod in saved.items():
			if mod is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = mod
	return module


class TestRateLimiterRespondAlwaysBuildsAResponse(unittest.TestCase):
	"""`frappe.rate_limiter.respond()` must never return None.

	Its sole caller assigns the result straight into the response handed back to
	Werkzeug, so None is not "no opinion" — it is a guaranteed 500.
	"""

	def test_no_site_level_limiter_still_responds_429(self):
		# THE PRODUCTION STATE: site_config has no `rate_limit`, so
		# frappe.local.rate_limiter was never set. The @rate_limit decorator on
		# gotrue_login_start raised 429 anyway. Before the fix: None.
		rate_limiter = _load_real_rate_limiter()
		response = rate_limiter.respond()

		self.assertIsNotNone(
			response,
			"respond() returned None with no site-level limiter — this is the "
			"exact value Werkzeug then tries to call as a WSGI callable",
		)
		self.assertEqual(429, response.status_code)

	def test_site_level_limiter_that_rejected_responds_429(self):
		rate_limiter = _load_real_rate_limiter()
		local = rate_limiter.frappe.local
		limiter = rate_limiter.RateLimiter.__new__(rate_limiter.RateLimiter)
		limiter.rejected = True
		local.rate_limiter = limiter

		response = rate_limiter.respond()

		self.assertIsNotNone(response)
		self.assertEqual(429, response.status_code)

	def test_site_level_limiter_that_did_not_reject_responds_429(self):
		# The decorator-raised 429 can coexist with a site-level limiter that
		# did not itself reject. That combination also returned None before.
		rate_limiter = _load_real_rate_limiter()
		local = rate_limiter.frappe.local
		limiter = rate_limiter.RateLimiter.__new__(rate_limiter.RateLimiter)
		limiter.rejected = False
		local.rate_limiter = limiter

		response = rate_limiter.respond()

		self.assertIsNotNone(response)
		self.assertEqual(429, response.status_code)


class TestWerkzeugBoundaryGetsAWsgiCallable(unittest.TestCase):
	"""Drive the real production boundary that raised the TypeError.

	`frappe.app.application` is decorated `@Request.application`, and that
	decorator ends with `return resp(*args[-2:])`. This wires the REAL
	`rate_limiter.respond()` into the REAL decorator, at the pinned Werkzeug, on
	this interpreter — the two halves that met in the container. Before the fix
	this test raises `TypeError: 'NoneType' object is not callable` from inside
	werkzeug/wrappers/request.py, byte for byte the production traceback.

	`TestHandleExceptionNeverReturnsNone` below pins the other half: that
	frappe/app.py really does hand this value straight back to Werkzeug.
	"""

	def _client(self):
		rate_limiter = _load_real_rate_limiter()

		@Request.application
		def application(request):
			# frappe/app.py handle_exception(), 429 branch, verbatim.
			response = rate_limiter.respond()
			return response

		return Client(application)

	def test_rate_limited_sso_start_returns_429_not_a_type_error(self):
		client = self._client()

		response = client.get(
			"/api/method/erpnext.exe_auth.api.gotrue_login_start",
			headers={"Accept": BROWSER_ACCEPT},
		)

		self.assertEqual(429, response.status_code)

	def test_response_is_a_wsgi_callable(self):
		rate_limiter = _load_real_rate_limiter()

		response = rate_limiter.respond()

		self.assertTrue(
			callable(response),
			"Werkzeug calls this value as a WSGI application; a non-callable "
			"here is the 500 the container logged",
		)


def _function_def(path, name):
	tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
	for node in ast.walk(tree):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in {path}")


class TestHandleExceptionNeverReturnsNone(unittest.TestCase):
	"""`frappe.app.handle_exception` must be structurally unable to return None.

	It is a long if/elif chain over `http_status_code` that assigns `response`
	and then returns it. Two branches assigned nothing: the 429 branch (via
	`rate_limiter.respond()`, fixed at source above) and the 508
	deadlock/timeout branch, which only rewrites the status code. Any future
	branch that forgets to assign is the same 500 again, so the guarantee is
	pinned here rather than left to branch-by-branch discipline.
	"""

	def test_handle_exception_has_a_none_backstop_before_returning(self):
		func = _function_def(_APP_PY, "handle_exception")

		backstop_index = None
		for index, stmt in enumerate(func.body):
			if not isinstance(stmt, ast.If):
				continue
			test = stmt.test
			# `if response is None:`
			if (
				isinstance(test, ast.Compare)
				and isinstance(test.left, ast.Name)
				and test.left.id == "response"
				and len(test.ops) == 1
				and isinstance(test.ops[0], ast.Is)
				and isinstance(test.comparators[0], ast.Constant)
				and test.comparators[0].value is None
			):
				assigns_response = any(
					isinstance(inner, ast.Assign)
					and any(
						isinstance(t, ast.Name) and t.id == "response" for t in inner.targets
					)
					for inner in ast.walk(stmt)
				)
				self.assertTrue(
					assigns_response,
					"the `if response is None:` guard must assign a response",
				)
				backstop_index = index

		self.assertIsNotNone(
			backstop_index,
			"handle_exception() has no `if response is None:` backstop — a branch "
			"that assigns no response returns None to Werkzeug, which raises "
			"TypeError: 'NoneType' object is not callable",
		)

		returns = [
			index
			for index, stmt in enumerate(func.body)
			if isinstance(stmt, ast.Return)
		]
		self.assertTrue(returns, "handle_exception() must return a response")
		self.assertTrue(
			all(index > backstop_index for index in returns),
			"the None backstop must run before handle_exception() returns",
		)

	def test_application_returns_handle_exception_result_to_werkzeug(self):
		"""Pins the assumption TestWerkzeugBoundaryGetsAWsgiCallable relies on.

		If `application()` ever stops handing the exception response straight
		back to the `@Request.application` decorator, the boundary test above is
		no longer modelling production and must be revisited.
		"""
		func = _function_def(_APP_PY, "application")

		assigns_from_handle_exception = any(
			isinstance(node, ast.Call)
			and isinstance(node.func, ast.Name)
			and node.func.id == "handle_exception"
			for node in ast.walk(func)
		)
		self.assertTrue(
			assigns_from_handle_exception,
			"application() no longer routes exceptions through handle_exception()",
		)

		returns_bare_response = any(
			isinstance(node, ast.Return)
			and isinstance(node.value, ast.Name)
			and node.value.id == "response"
			for node in func.body
		)
		self.assertTrue(
			returns_bare_response,
			"application() must return `response` — whatever value it holds is "
			"what Werkzeug calls as a WSGI callable",
		)


@contextlib.contextmanager
def _real_middleware():
	"""Yield the REAL exe_bridge/middleware.py, loaded by file path.

	`erpnext.exe_bridge.tracing` is imported lazily inside `__call__`, so a stub
	registered in sys.modules under that name is enough to exercise the real
	middleware without importing erpnext (and therefore frappe). The stubs stay
	installed for the duration of the `with` block, because that lazy import
	happens per request, not at load time.
	"""
	pkg_erpnext = types.ModuleType("erpnext")
	pkg_bridge = types.ModuleType("erpnext.exe_bridge")
	tracing = types.ModuleType("erpnext.exe_bridge.tracing")

	class _Span:
		def __enter__(self):
			return self

		def __exit__(self, *exc):
			return False

		def set_attribute(self, *a, **k):
			pass

		def set_status(self, *a, **k):
			pass

	class _Tracer:
		trace_id = "test-trace-id"

		@classmethod
		def from_request(cls, environ):
			return cls()

		def span(self, *a, **k):
			return _Span()

		def flush(self):
			pass

	tracing.RequestTracer = _Tracer
	tracing.set_current_tracer = lambda tracer: None
	tracing.clear_current_tracer = lambda: None

	saved = {
		k: sys.modules.get(k)
		for k in ("erpnext", "erpnext.exe_bridge", "erpnext.exe_bridge.tracing")
	}
	sys.modules["erpnext"] = pkg_erpnext
	sys.modules["erpnext.exe_bridge"] = pkg_bridge
	sys.modules["erpnext.exe_bridge.tracing"] = tracing
	try:
		spec = importlib.util.spec_from_file_location(
			"_exe_test_bridge_middleware", _MIDDLEWARE_PY
		)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		yield module
	finally:
		for name, mod in saved.items():
			if mod is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = mod


class TestTracingMiddlewareDoesNotReplayTheRequest(unittest.TestCase):
	"""A failing request must reach the WSGI server once, not twice.

	The middleware's `except Exception` handler used to re-invoke the wrapped
	app. Every 500 was therefore executed twice — which is why the container
	logged the same TypeError stacked under "During handling of the above
	exception, another exception occurred", and why each SSO click spent two
	rate-limit tokens instead of one. Replaying a request whose side effects
	already happened (cookies, counters, login attempts, DB writes) is not a
	safe fallback.
	"""

	def test_application_error_is_not_retried(self):
		calls = []

		def failing_app(environ, start_response):
			calls.append(environ["PATH_INFO"])
			raise TypeError("'NoneType' object is not callable")

		with _real_middleware() as middleware_module:
			app = middleware_module.TracingMiddleware(failing_app)
			environ = {
				"PATH_INFO": "/api/method/erpnext.exe_auth.api.gotrue_login_start",
				"REQUEST_METHOD": "GET",
			}

			with self.assertRaises(TypeError):
				app(environ, lambda status, headers, exc_info=None: None)

		self.assertEqual(
			1,
			len(calls),
			"the middleware replayed a request that had already run and failed",
		)

	def test_successful_request_is_passed_through_once(self):
		calls = []

		def ok_app(environ, start_response):
			calls.append(environ["PATH_INFO"])
			start_response("200 OK", [])
			return [b"ok"]

		seen = {}

		def start_response(status, headers, exc_info=None):
			seen["status"] = status
			seen["headers"] = headers

		with _real_middleware() as middleware_module:
			app = middleware_module.TracingMiddleware(ok_app)
			result = list(
				app(
					{"PATH_INFO": "/api/method/ping", "REQUEST_METHOD": "GET"},
					start_response,
				)
			)

		self.assertEqual([b"ok"], result)
		self.assertEqual(1, len(calls))
		self.assertEqual("200 OK", seen["status"])
		self.assertIn("X-Trace-Id", dict(seen["headers"]))


class TestCiWerkzeugPinMatchesProduction(unittest.TestCase):
	"""The CI venv must install the Werkzeug this repo actually ships.

	`TestWerkzeugBoundaryGetsAWsgiCallable` is only evidence about production if
	it runs against production's Werkzeug. CI installs Werkzeug into a small
	lint venv rather than resolving pyproject.toml, so the two pins can drift
	apart silently and leave the boundary test testing a version nobody runs.
	"""

	def test_ci_workflow_pins_the_pyproject_werkzeug(self):
		pyproject = open(
			os.path.join(_REPO_ROOT, "pyproject.toml"), encoding="utf-8"
		).read()
		workflow = open(
			os.path.join(_REPO_ROOT, ".github", "workflows", "ci-checks.yml"),
			encoding="utf-8",
		).read()

		declared = re.findall(r'"Werkzeug==([0-9][^"]*)"', pyproject)
		self.assertEqual(
			1, len(declared), f"expected exactly one Werkzeug pin in pyproject.toml, got {declared}"
		)

		in_ci = re.findall(r'"Werkzeug==([0-9][^"]*)"', workflow)
		self.assertIn(
			declared[0],
			in_ci,
			"ci-checks.yml does not install the Werkzeug version pyproject.toml "
			f"pins ({declared[0]}) — the boundary regression test would run "
			"against a version production never sees",
		)


if __name__ == "__main__":
	unittest.main(verbosity=2)
