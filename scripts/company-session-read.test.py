"""Controlled source tests only, not native PostgreSQL/Frappe ACL admission."""
import ast
import base64
import dataclasses
import hashlib
import importlib.util
import ipaddress
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("owned_company_session", ROOT / "frappe/company_session.py")
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
IDS = [f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 6)]
TOKEN = "exs_" + "a" * 43
SECRET = "s" * 43


def config():
	return adapter.Config(
		IDS[0],
		"erp.alpha.example.test",
		IDS[1],
		IDS[2],
		"erp-alpha",
		"erp-alpha",
		"https://erp.alpha.example.test",
		"http://127.0.0.1:8097",
		SECRET,
		{IDS[3]: "reader@example.test"},
		"0" * 64,
	)


def envelope(c=None):
	c = c or config()
	return {
		"version": 1,
		"subject_id": IDS[3],
		"company_id": c.company_id,
		"product": "erp",
		"resource_kind": "erp-site",
		"binding_id": c.binding_id,
		"native_id": c.site,
		"generation_id": c.generation_id,
		"authz_epoch": "1",
		"audience": c.audience,
		"scopes": ["erp:read"],
		"current_role": "member",
		"technical_status": "accepted",
		"subscription_entitled": True,
	}


class Headers(dict):
	def __iter__(self):
		return iter(self.items())


def request(
	path="/api/resource/Customer", query="limit_page_length=10&limit_start=0", method="GET", headers=None
):
	return types.SimpleNamespace(
		path=path,
		query_string=query.encode("ascii"),
		method=method,
		host=config().site,
		headers=Headers({"Cookie": adapter.COOKIE + "=" + TOKEN, **(headers or {})}),
		environ={"RAW_URI": path + ("?" + query if query else "")},
	)


class Tests(unittest.TestCase):
	def test_default_off_and_strict_mode(self):
		with mock.patch.dict(os.environ, {}, clear=True):
			self.assertIsNone(adapter.load_config())
			adapter.refuse_background()
			os.environ["ERP_COMPANY_MODE"] = "false"
			self.assertIsNone(adapter.load_config())
			os.environ["ERP_COMPANY_MODE"] = "yes"
			self.assertRaises(ValueError, adapter.load_config)
			os.environ["ERP_COMPANY_MODE"] = "true"
			self.assertRaises(RuntimeError, adapter.refuse_background)

	def test_immutable_registration_and_private_files(self):
		with tempfile.TemporaryDirectory() as temp:
			dir = pathlib.Path(temp).resolve()
			secret, bindings = dir / "secret", dir / "bindings"
			secret.write_text(SECRET)
			c = config()
			doc = {
				"version": 1,
				"company_id": c.company_id,
				"site": c.site,
				"binding_id": c.binding_id,
				"generation_id": c.generation_id,
				"audience": c.audience,
				"subjects": [{"subject_id": IDS[3], "native_user": "reader@example.test"}],
			}
			bindings.write_text(json.dumps(doc))
			for p in (secret, bindings):
				p.chmod(0o600)
			env = {
				"ERP_COMPANY_MODE": "true",
				"SITE_NAME": c.site,
				"ERP_COMPANY_ID": c.company_id,
				"ERP_COMPANY_SITE": c.site,
				"ERP_COMPANY_ORIGIN": c.origin,
				"ERP_COMPANY_BINDING_ID": c.binding_id,
				"ERP_COMPANY_GENERATION_ID": c.generation_id,
				"ERP_COMPANY_AUDIENCE": c.audience,
				"ERP_COMPANY_CLIENT_ID": c.client_id,
				"ERP_COMPANY_AUTHORITY_URL": c.authority,
				"ERP_COMPANY_CLIENT_SECRET_FILE": str(secret),
				"ERP_COMPANY_BINDINGS_FILE": str(bindings),
				"ERP_COMPANY_BINDINGS_SHA256": hashlib.sha256(bindings.read_bytes()).hexdigest(),
			}
			with mock.patch.dict(os.environ, env, clear=True):
				actual = adapter.load_config()
				self.assertEqual(actual.subjects[IDS[3]], "reader@example.test")
				self.assertNotIn(SECRET, repr(actual))
				with self.assertRaises(TypeError):
					actual.subjects[IDS[4]] = "Administrator"
				for key, value in (
					("ERP_COMPANY_ORIGIN", "https://erp.bravo.example.test"),
					("ERP_COMPANY_SITE", "erp.alpha.example.test/../bravo"),
					("ERP_COMPANY_AUTHORITY_URL", "http://user@127.0.0.1:8097"),
					("ERP_COMPANY_AUTHORITY_URL", "http://127.0.0.1:8097/path"),
					("GOTRUE_ADMIN_TOKEN", "forbidden"),
					("COMPANY_SESSION_PARENT_CIPHER_KEY_FILE", "/not-tenant"),
					("STRIPE_SECRET_KEY", "forbidden"),
					("EXE_OS_MCP_URL", "http://memory"),
					("ERP_COMPANY_JWT_SIGNER", "forbidden"),
					("SITE_NAME", "erp.bravo.example.test"),
					("ERP_COMPANY_BINDINGS_SHA256", "f" * 64),
				):
					with mock.patch.dict(os.environ, {key: value}):
						self.assertRaises(ValueError, adapter.load_config)
				secret.chmod(0o644)
				self.assertRaises(ValueError, adapter.load_config)
				secret.chmod(0o600)
				link = dir / "link"
				link.symlink_to(secret)
				with mock.patch.dict(os.environ, {"ERP_COMPANY_CLIENT_SECRET_FILE": str(link)}):
					self.assertRaises(ValueError, adapter.load_config)
				# File changes do not reload a previously captured startup mapping.
				bindings.write_text("{}")
				self.assertEqual(actual.subjects[IDS[3]], "reader@example.test")
				self.assertRaises(ValueError, adapter.load_config)

	def test_central_company_contract_conformance(self):
		manifest = json.loads((ROOT / "company-access.manifest.json").read_text())
		data = (ROOT / "tests/company-access.fixtures.json").read_bytes()
		self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["fixturesSha256"])
		code = (ROOT / "frappe/company_session.py").read_text()
		generated = code[code.index("# BEGIN GENERATED COMPANY ACCESS V1"):code.index("# END GENERATED COMPANY ACCESS V1") + len("# END GENERATED COMPANY ACCESS V1")]
		self.assertEqual(hashlib.sha256(generated.encode()).hexdigest(), manifest["artifacts"]["exe-erp"]["sha256"])
		for case in json.loads(data)["cases"]:
			with self.subTest(case=case["name"]):
				self.assertEqual(adapter.company_access(case["value"], case["binding"]) is not None, case["accepted"])
				self.assertEqual(adapter.company_access(case["value"]) is not None, case["unboundAccepted"])

	def test_exact_envelope_not_role_entitlement(self):
		self.assertEqual(adapter.envelope(envelope(), config()), "reader@example.test")
		bad = {
			"version": True,
			"company_id": IDS[4],
			"subject_id": IDS[4],
			"binding_id": IDS[4],
			"generation_id": IDS[4],
			"product": "crm",
			"resource_kind": "crm-workspace",
			"native_id": "erp.bravo.example.test",
			"audience": "erp-bravo",
			"scopes": ["erp:read", "erp:write"],
			"current_role": ["owner"],
			"technical_status": "unverified",
			"subscription_entitled": 1,
			"authz_epoch": "01",
		}
		for key, value in bad.items():
			with self.subTest(key=key):
				self.assertRaises(adapter.Denied, adapter.envelope, {**envelope(), key: value}, config())
		self.assertRaises(adapter.Denied, adapter.envelope, {**envelope(), "extra": True}, config())
		self.assertRaises(ValueError, adapter.exact_json, '{"version":1,"version":1}')

	def test_route_inputs_and_credentials(self):
		self.assertEqual(
			adapter.request_policy(request(), config())[:3],
			("read", ("Customer", None), {"limit_page_length": 10, "limit_start": 0}),
		)
		self.assertEqual(
			adapter.request_policy(request("/api/resource/Item/ITM-001", ""), config())[1],
			("Item", "ITM-001"),
		)
		for path in (
			"/",
			"/desk",
			"/api/method/login",
			"/api/v2/document/Customer",
			"/api/resource/User",
			"/files/a",
			"/private/files/a",
			"/socket.io/",
			"/api/resource/Customer/../Item",
			"/api/resource/Customer/a%2fb",
		):
			self.assertRaises(adapter.Denied, adapter.request_policy, request(path, ""), config())
		for query in (
			"",
			"limit_page_length=101&limit_start=0",
			"limit_page_length=1&limit_start=10001",
			"limit_page_length=1&limit_start=0&limit_start=0",
			"limit_page_length=1&limit_start=0&cmd=login",
			"limit_page_length=01&limit_start=0",
			"limit_page_length=1&limit_start=0&fields=name",
			"limit_page_length=1&limit_start=0&user=Administrator",
			"limit_page_length=1&limit_start=0&site=bravo",
			"limit_page_length=1&limit_start=0%26fields=name",
		):
			self.assertRaises(adapter.Denied, adapter.request_policy, request(query=query), config())
		for headers in (
			{"Authorization": "Bearer " + TOKEN},
			{"Authorization": "token native:secret"},
			{"X-Frappe-Site-Name": "erp.bravo.example.test"},
			{"X-FrappE-User": "Administrator"},
			{"X-Forwarded-For": "127.0.0.1"},
			{"X-User": "Administrator"},
			{"Upgrade": "websocket"},
			{"Sec-Fetch-Site": "cross-site"},
			{"Origin": "https://erp.bravo.example.test"},
			{"Cookie": "sid=native"},
			{"Cookie": adapter.COOKIE + "=" + TOKEN + "; sid=native"},
			{"Cookie": (adapter.COOKIE + "=" + TOKEN + ";") * 2},
			{"Content-Length": "1"},
		):
			self.assertRaises(adapter.Denied, adapter.request_policy, request(headers=headers), config())
		r = request()
		r.environ["RAW_URI"] = "/api/resource/%43ustomer?limit_page_length=10&limit_start=0"
		self.assertRaises(adapter.Denied, adapter.request_policy, r, config())
		r = request()
		r.host = "erp.bravo.example.test"
		self.assertRaises(adapter.Denied, adapter.request_policy, r, config())
		self.assertRaises(adapter.Denied, adapter.request_policy, request(method="POST"), config())
		self.assertRaises(
			adapter.Denied, adapter.request_policy, request("/company-session/logout", "", "POST"), config()
		)
		self.assertEqual(
			adapter.request_policy(
				request("/company-session/logout", "", "POST", {"Origin": config().origin}), config()
			)[0],
			"logout",
		)
		self.assertEqual(
			adapter.request_policy(request(headers={"X-Forwarded-Proto": "https"}), config())[0], "read"
		)

	def test_current_native_identity_and_permission_flow(self):
		frappe = types.ModuleType("frappe")
		identity = types.SimpleNamespace(enabled=1, user_type="System User")
		frappe.db = types.SimpleNamespace(
			get_value=mock.Mock(
				side_effect=lambda doctype, *_args, **_kwargs: identity if doctype == "User" else 0
			)
		)
		frappe.set_user = mock.Mock()
		frappe.get_roles = mock.Mock(return_value=["Sales User"])
		doc = types.SimpleNamespace(
			check_permission=mock.Mock(), apply_fieldlevel_read_permissions=mock.Mock()
		)
		frappe.get_doc = mock.Mock(return_value=doc)
		frappe.get_list = mock.Mock(return_value=[{"name": "CUST-001"}])
		with mock.patch.dict(sys.modules, {"frappe": frappe}):
			adapter.native_user(config(), envelope())
			frappe.set_user.assert_called_once_with("reader@example.test")
			adapter.read_records(("Customer", "CUST-001"), {})
			doc.check_permission.assert_called_once_with("read")
			doc.apply_fieldlevel_read_permissions.assert_called_once_with()
			self.assertEqual(frappe.get_list.call_args.kwargs["filters"], {"name": "CUST-001"})
			self.assertNotIn("ignore_permissions", frappe.get_list.call_args.kwargs)
			# A direct Document pass must not bypass current query row denial.
			frappe.get_list.return_value = []
			self.assertRaises(adapter.Denied, adapter.read_records, ("Customer", "CUST-001"), {})
			identity.enabled = 0
			self.assertRaises(adapter.Denied, adapter.native_user, config(), envelope())
			identity.enabled = 1
			identity.user_type = "Website User"
			self.assertRaises(adapter.Denied, adapter.native_user, config(), envelope())
			identity.user_type = "System User"
			frappe.get_roles.return_value = ["System Manager"]
			self.assertRaises(adapter.Denied, adapter.native_user, config(), envelope())

	def test_private_transport_actual_bounded_local_http(self):
		state = {"status": 200, "value": envelope(), "type": "application/json", "bodies": [], "paths": []}

		class Handler(BaseHTTPRequestHandler):
			def do_POST(self):
				state["paths"].append(self.path)
				state["bodies"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
				assert (
					self.headers["Authorization"]
					== "Basic " + base64.b64encode((config().client_id + ":" + SECRET).encode()).decode()
				)
				assert self.headers.get("Cookie") is None
				if state.get("delay"):
					time.sleep(state["delay"])
				self.send_response(state["status"])
				self.send_header("Content-Type", state["type"])
				self.send_header("Location", "/trap")
				self.end_headers()
				try:
					self.wfile.write(state.get("raw", json.dumps(state["value"]).encode()))
				except BrokenPipeError:
					pass

			def log_message(self, *_):
				pass

		server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		thread = threading.Thread(target=server.serve_forever, daemon=True)
		thread.start()
		try:
			c = dataclasses.replace(config(), authority="http://127.0.0.1:" + str(server.server_port))
			self.assertEqual(adapter.private_call(c, "introspect", TOKEN), envelope())
			self.assertEqual(state["bodies"], [{"session_token": TOKEN}])
			for status in (302, 401, 403, 404, 500, 503):
				state["status"] = status
				self.assertRaises(adapter.Denied, adapter.private_call, c, "introspect", TOKEN)
			self.assertNotIn("/trap", state["paths"])
			state["status"] = 200
			state["delay"] = 2.2
			self.assertRaises(adapter.Denied, adapter.private_call, c, "introspect", TOKEN)
			state.pop("delay")
			self.assertEqual(adapter.private_call(c, "introspect", TOKEN), envelope())
			for raw in (b"not-json", b'{"revoked":true,"revoked":false}', b"x" * 12289):
				state["raw"] = raw
				self.assertRaises(adapter.Denied, adapter.private_call, c, "revoke", TOKEN)
			state.pop("raw")
			state["value"] = {"revoked": True}
			self.assertEqual(adapter.private_call(c, "revoke", TOKEN), {"revoked": True})
		finally:
			server.shutdown()
			server.server_close()
			thread.join()

	def test_direct_realtime_and_entrypoint_start_denials(self):
		env = {**os.environ, "ERP_COMPANY_MODE": "true"}
		for path in ("socketio.js", "realtime/index.js"):
			result = subprocess.run(["node", str(ROOT / path)], env=env, capture_output=True)
			self.assertNotEqual(result.returncode, 0)
			self.assertIn(b"not admitted", result.stderr)
		for command in ("bench", "node", "python"):
			result = subprocess.run(
				["bash", str(ROOT / "entrypoint.sh"), command], env=env, capture_output=True
			)
			self.assertNotEqual(result.returncode, 0)
			self.assertIn(b"read-only gunicorn", result.stderr)

	def test_resolved_endpoint_and_nonroot_contract(self):
		for host in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", str(ipaddress.IPv4Address((100 << 24) | (64 << 16) | 1)), str(ipaddress.IPv4Address((100 << 24) | (127 << 16) | (255 << 8) | 254))):
			self.assertEqual(adapter.resolved_endpoint(host), host)
		for host in (
			"private-native-session",
			"8.8.8.8",
			"0.0.0.0",
			"169.254.0.1",
			"100.128.0.1",
			"127.01.0.1",
			"::1",
		):
			self.assertRaises(ValueError, adapter.resolved_endpoint, host)
		with (
			mock.patch.dict(os.environ, {"ERP_COMPANY_MODE": "true"}, clear=True),
			mock.patch.object(os, "geteuid", return_value=0),
		):
			self.assertRaises(ValueError, adapter.load_config)

	def test_absolute_deadline_slow_headers_body_dns_and_recovery(self):
		state = {"phase": "headers"}
		stop = threading.Event()

		class Handler(BaseHTTPRequestHandler):
			def log_message(self, *_):
				pass

			def do_POST(self):
				self.rfile.read(int(self.headers["Content-Length"]))
				body = json.dumps(envelope()).encode()
				header = (
					b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
					+ str(len(body)).encode()
					+ b"\r\nConnection: close\r\n\r\n"
				)
				try:
					if state["phase"] == "headers":
						for byte in header:
							self.wfile.write(bytes([byte]))
							self.wfile.flush()
							if stop.wait(0.1):
								return
					else:
						self.wfile.write(header)
						self.wfile.flush()
					if state["phase"] == "body":
						for byte in body:
							self.wfile.write(bytes([byte]))
							self.wfile.flush()
							if stop.wait(0.1):
								return
					else:
						self.wfile.write(body)
						self.wfile.flush()
				except (BrokenPipeError, ConnectionResetError):
					pass

		server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		server.daemon_threads = True
		thread = threading.Thread(target=server.serve_forever)
		thread.start()
		c = dataclasses.replace(config(), authority="http://127.0.0.1:" + str(server.server_port))
		baseline = {t.ident for t in threading.enumerate() if isinstance(t, threading.Timer)}
		try:
			with mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS is forbidden")):
				for phase in ("headers", "body"):
					state["phase"] = phase
					start = time.monotonic()
					with self.assertRaises(adapter.Denied) as denial:
						adapter.private_call(c, "introspect", TOKEN)
					self.assertEqual(denial.exception.status, 503)
					self.assertGreaterEqual(time.monotonic() - start, 2.8)
					self.assertLess(time.monotonic() - start, 3.7)
					self.assertEqual(
						{t.ident for t in threading.enumerate() if isinstance(t, threading.Timer)}, baseline
					)
					state["phase"] = "healthy"
					for _ in range(3):
						self.assertEqual(adapter.private_call(c, "introspect", TOKEN), envelope())
			# Even a valid parsed result cannot become positive after the deadline.
			clock, parsed, original_json = time.monotonic, {"late": False}, adapter.exact_json

			def late_json(data):
				result = original_json(data)
				parsed["late"] = True
				return result

			with (
				mock.patch.object(adapter, "exact_json", side_effect=late_json),
				mock.patch.object(
					adapter.time, "monotonic", side_effect=lambda: clock() + (4 if parsed["late"] else 0)
				),
			):
				with self.assertRaises(adapter.Denied):
					adapter.private_call(c, "introspect", TOKEN)
		finally:
			stop.set()
			server.shutdown()
			server.server_close()
			thread.join(2)

	def test_application_logout_failures_preserve_cookie_and_current_authority(self):
		class Response:
			def __init__(self, body, status, content_type):
				self.body, self.status_code, self.headers, self.cookies = body, status, {}, []

			def set_cookie(self, *args, **kwargs):
				self.cookies.append((args, kwargs))

		class Flags(dict):
			def __getattr__(self, key):
				return self.get(key)

			def __setattr__(self, key, value):
				self[key] = value

		frappe = types.ModuleType("frappe")
		frappe.local = types.SimpleNamespace()
		frappe.db = types.SimpleNamespace(
			begin=mock.Mock(),
			rollback=mock.Mock(),
			close=mock.Mock(),
			get_value=mock.Mock(
				side_effect=lambda doctype, *_a, **_kw: types.SimpleNamespace(
					enabled=1, user_type="System User"
				)
				if doctype == "User"
				else 0
			),
		)
		frappe.conf = Flags()
		frappe.set_user, frappe.get_roles, frappe.connect = (
			mock.Mock(),
			mock.Mock(return_value=["Sales User"]),
			mock.Mock(),
		)
		frappe.PermissionError, frappe.DoesNotExistError = (
			type("NativePermission", (Exception,), {}),
			type("NativeMissing", (Exception,), {}),
		)

		def initialize(*_a, **_kw):
			frappe.local.flags = Flags()
			frappe.local.db = frappe.db

		frappe.init = mock.Mock(side_effect=initialize)
		utilities, wrappers = types.ModuleType("frappe.utils"), types.ModuleType("werkzeug.wrappers")
		utilities.CallbackManager = lambda: types.SimpleNamespace(run=mock.Mock())
		wrappers.Response = Response
		with mock.patch.dict(
			sys.modules, {"frappe": frappe, "frappe.utils": utilities, "werkzeug.wrappers": wrappers}
		):
			r = request("/company-session/logout", "", "POST", {"Origin": config().origin})
			for result in ({"revoked": False}, {}, [], {"revoked": True, "extra": 1}, adapter.Denied(503)):
				with mock.patch.object(adapter, "private_call", side_effect=[result]):
					response = adapter.application(r, config(), "fixture-sites")
					self.assertEqual(response.status_code, 503)
					self.assertEqual(response.cookies, [])
			with mock.patch.object(adapter, "private_call", return_value={"revoked": True}):
				response = adapter.application(r, config(), "fixture-sites")
				self.assertEqual(response.status_code, 200)
				args, cookie = response.cookies[0]
				self.assertEqual(args[0], adapter.COOKIE)
				self.assertTrue(cookie["secure"] and cookie["httponly"])
				self.assertNotIn("domain", cookie)
				self.assertEqual(cookie["path"], "/")
				self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
				self.assertEqual(response.headers["Cache-Control"], "no-store")
				self.assertFalse(hasattr(frappe.local, "session_obj"))
				self.assertFalse(hasattr(frappe.local, "login_manager"))
				frappe.db.begin.assert_not_called()
				frappe.connect.assert_not_called()
				frappe.init.assert_not_called()
			with (
				mock.patch.object(adapter, "private_call", side_effect=adapter.Denied(401)),
				mock.patch.object(adapter, "read_records") as read,
			):
				response = adapter.application(request(), config(), "fixture-sites")
				self.assertEqual(response.status_code, 401)
				read.assert_not_called()

			entered, release, results = threading.Event(), threading.Event(), []

			def blocked(*_):
				entered.set()
				if not release.wait(2):
					raise adapter.Denied(503)
				return envelope()

			with (
				mock.patch.object(adapter, "private_call", side_effect=blocked),
				mock.patch.object(adapter, "read_records") as read,
			):
				thread = threading.Thread(
					target=lambda: results.append(adapter.application(request(), config(), "fixture-sites"))
				)
				thread.start()
				self.assertTrue(entered.wait(1))
				frappe.init.assert_not_called()
				frappe.connect.assert_not_called()
				frappe.db.begin.assert_not_called()
				frappe.db.get_value.assert_not_called()
				# Downgrade while private authority is blocked. Current native role
				# must be checked after it returns, not captured beforehand.
				frappe.get_roles.return_value = []
				release.set()
				thread.join(2)
				self.assertFalse(thread.is_alive())
				self.assertEqual(results[0].status_code, 403)
				read.assert_not_called()
			frappe.get_roles.return_value = ["Sales User"]
			# Disabled native identity and unavailable positive introspection cannot
			# block cleanup; only the authenticated audience-local revoke is called.
			frappe.db.get_value.side_effect = lambda *_a, **_kw: types.SimpleNamespace(
				enabled=0, user_type="System User"
			)
			frappe.get_roles.return_value = []
			for native in (frappe.init, frappe.connect, frappe.db.begin, frappe.db.get_value):
				native.reset_mock()

			def revoke_only(_config, operation, _token):
				if operation == "introspect":
					raise adapter.Denied(503)
				return {"revoked": True}

			with mock.patch.object(adapter, "private_call", side_effect=revoke_only) as private:
				response = adapter.application(r, config(), "fixture-sites")
				self.assertEqual(response.status_code, 200)
				self.assertEqual(len(response.cookies), 1)
				private.assert_called_once_with(config(), "revoke", TOKEN)
				for native in (frappe.init, frappe.connect, frappe.db.begin, frappe.db.get_value):
					native.assert_not_called()
			# Logout does not initialize native identity or require introspection.
			with mock.patch.object(adapter, "private_call") as authority:
				response = adapter.application(request("/desk", ""), config(), "fixture-sites")
				self.assertEqual(response.status_code, 404)
				authority.assert_not_called()
			frappe.db.rollback.side_effect = RuntimeError("database unavailable")
			with mock.patch.object(adapter, "private_call", return_value={"revoked": True}):
				response = adapter.application(r, config(), "fixture-sites")
				self.assertEqual(response.status_code, 200)
				self.assertEqual(len(response.cookies), 1)

	def test_shipped_permission_cache_branches_bypass_warm_values_only_in_mode(self):
		class Attr(dict):
			__getattr__ = dict.get
			__setattr__ = dict.__setitem__

		def load(relative, name, globals_):
			tree = ast.parse((ROOT / relative).read_text())
			function = next(
				node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
			)
			function.decorator_list = []
			module = ast.Module(
				body=[
					ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
					function,
				],
				type_ignores=[],
			)
			exec(compile(ast.fix_missing_locations(module), relative, "exec"), globals_)
			return globals_[name]

		frappe = types.SimpleNamespace(
			flags=Attr(company_session=True),
			local=types.SimpleNamespace(),
			session=Attr(user="reader@example.test"),
			_dict=Attr,
		)
		frappe.cache = types.SimpleNamespace(
			get_value=mock.Mock(return_value=[]),
			set_value=mock.Mock(),
			hget=mock.Mock(return_value={"Customer": [{"doc": "OLD"}]}),
			hset=mock.Mock(),
		)
		frappe.client_cache = types.SimpleNamespace(
			get_value=mock.Mock(return_value="STALE_META"),
			set_value=mock.Mock(),
			get_doc=mock.Mock(return_value={"strict": 0}),
		)
		frappe.get_doc = mock.Mock(return_value={"strict": 1})
		get_cached = load(
			"frappe/model/document.py",
			"get_cached_doc",
			{
				"frappe": frappe,
				"get_doc": frappe.get_doc,
				"can_cache_doc": lambda _: "key",
				"get_document_cache_key": lambda *_: "key",
				"_set_document_in_cache": mock.Mock(),
			},
		)
		self.assertEqual(get_cached("System Settings"), {"strict": 1})
		frappe.cache.get_value.assert_not_called()
		get_meta = load(
			"frappe/model/meta.py",
			"get_meta",
			{"frappe": frappe, "Meta": lambda name: Attr(name=name, current=True)},
		)
		self.assertTrue(get_meta("Customer").current)
		frappe.client_cache.get_value.assert_not_called()
		get_settings = load(
			"frappe/core/doctype/system_settings/system_settings.py",
			"get_system_settings",
			{"frappe": frappe},
		)
		self.assertEqual(get_settings("strict"), 1)
		frappe.client_cache.get_doc.assert_not_called()
		get_masks = load("frappe/model/meta.py", "get_masked_fields", {"frappe": frappe})
		meta = types.SimpleNamespace(
			name="Customer",
			fields=[Attr(fieldname="customer_name", mask=1)],
			has_permlevel_access_to=lambda **_: False,
		)
		self.assertEqual(get_masks(meta)[0].fieldname, "customer_name")
		frappe.cache.set_value.assert_not_called()
		# User Permission's shipped builder must query current rows, not warmed
		# Redis, and must not overwrite legacy shared cache with company state.
		frappe.request = None
		frappe.get_all = mock.Mock(
			return_value=[
				Attr(allow="Customer", for_value="NEW", applicable_for=None, is_default=0, hide_descendants=1)
			]
		)
		frappe.get_meta = lambda _: types.SimpleNamespace(is_nested_set=lambda: False)
		frappe.db = types.SimpleNamespace(
			SQLError=type("SQLFailure", (Exception,), {}), is_table_missing=lambda _: True
		)
		get_user_permissions = load(
			"frappe/core/doctype/user_permission/user_permission.py",
			"get_user_permissions",
			{"frappe": frappe},
		)
		self.assertEqual(get_user_permissions()["Customer"][0].doc, "NEW")
		frappe.cache.hget.assert_not_called()
		frappe.cache.hset.assert_not_called()
		frappe.get_all.return_value = []
		self.assertEqual(get_user_permissions(), {})
		frappe.get_all.side_effect = frappe.db.SQLError()
		self.assertRaises(frappe.db.SQLError, get_user_permissions)

		class Query:
			def __getattr__(self, _):
				return self

			def __eq__(self, _):
				return self

			def __and__(self, _):
				return self

			def notin(self, _):
				return self

			def where(self, _):
				return self

			def select(self, _):
				return self

			def run(self, **_):
				return list(current_roles)

		current_roles = ["Sales User"]
		query = Query()
		frappe.qb = types.SimpleNamespace(from_=lambda _: query)
		roles = load(
			"frappe/permissions.py",
			"get_roles",
			{
				"frappe": frappe,
				"DocType": lambda _: query,
				"AUTOMATIC_ROLES": ("All", "Guest", "Desk User"),
				"ALL_USER_ROLE": "All",
				"GUEST_ROLE": "Guest",
				"SYSTEM_USER_ROLE": "Desk User",
				"is_system_user": lambda _: True,
			},
		)
		self.assertEqual(roles(), ["Sales User", "All", "Guest", "Desk User"])
		current_roles.clear()
		self.assertEqual(roles(), ["All", "Guest", "Desk User"])
		frappe.cache.hget.assert_not_called()
		# Off mode returns the existing warmed values, unchanged.
		frappe.flags.company_session = False
		self.assertEqual(get_user_permissions(), {"Customer": [{"doc": "OLD"}]})
		self.assertEqual(get_meta("Customer"), "STALE_META")
		frappe.cache.get_value.return_value = {"strict": 0}
		self.assertEqual(get_cached("System Settings"), {"strict": 0})

	def test_company_entrypoint_never_bootstraps_or_changes_existing_site(self):
		with tempfile.TemporaryDirectory() as temp:
			root = pathlib.Path(temp)
			site = root / "sites" / config().site
			site.mkdir(parents=True)
			(site / "site_config.json").write_text('{"db_name":"owned-fixture"}')
			bin_ = root / "env/bin"
			bin_.mkdir(parents=True)
			gunicorn = bin_ / "gunicorn"
			gunicorn.write_text('#!/bin/sh\nprintf "owned-native-command" > "$FRAPPE_BENCH/observed"\n')
			gunicorn.chmod(0o700)
			env = {
				**os.environ,
				"ERP_COMPANY_MODE": "true",
				"FRAPPE_BENCH": str(root),
				"SITE_NAME": config().site,
				"ADMIN_PASSWORD": "",
			}
			result = subprocess.run(
				["bash", str(ROOT / "entrypoint.sh"), "gunicorn", "frappe.app:application"],
				env=env,
				capture_output=True,
			)
			self.assertEqual(result.returncode, 0)
			self.assertEqual((root / "observed").read_text(), "owned-native-command")
			self.assertEqual((site / "site_config.json").read_text(), '{"db_name":"owned-fixture"}')
			self.assertFalse((root / "sites/currentsite.txt").exists())
			self.assertFalse((site / ".admin_password_hash").exists())

	def test_all_shipped_job_entrypoints_refuse_before_native_effects(self):
		paths = {
			"frappe/utils/background_jobs.py": (
				"enqueue",
				"execute_job",
				"start_worker",
				"start_worker_pool",
			),
			"frappe/utils/scheduler.py": ("start_scheduler",),
			"frappe/utils/task_queue.py": ("enqueue_task", "_execute_task"),
		}
		module = types.ModuleType("frappe.company_session")
		module.refuse_background = adapter.refuse_background
		with (
			mock.patch.dict(os.environ, {"ERP_COMPANY_MODE": "true"}),
			mock.patch.dict(sys.modules, {"frappe.company_session": module}),
		):
			for relative, names in paths.items():
				tree = ast.parse((ROOT / relative).read_text())
				for name in names:
					node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
					body = list(node.body)
					if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
						body = body[1:]
					self.assertIsInstance(body[0], ast.ImportFrom)
					self.assertEqual(body[0].module, "frappe.company_session")
					# Execute actual first import/call, not a source string assertion.
					first = ast.Module(body=body[:2], type_ignores=[])
					with self.assertRaises(RuntimeError):
						exec(compile(first, relative, "exec"), {})

	def test_early_native_dispatch_branch(self):
		# Execute the shipped application AST with decorators removed. Company
		# branch must return before any legacy init/session/auth helper runs.
		tree = ast.parse((ROOT / "frappe/app.py").read_text())
		function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "application")
		function.decorator_list = []
		company = mock.Mock(return_value="isolated")
		legacy = mock.Mock(side_effect=AssertionError("Native session path used"))
		globals_ = {
			"Request": object,
			"_company_config": config(),
			"_sites_path": "fixture-sites",
			"frappe": types.SimpleNamespace(company_session=types.SimpleNamespace(application=company)),
			"init_request": legacy,
		}
		exec(compile(ast.Module(body=[function], type_ignores=[]), "actual-app", "exec"), globals_)
		self.assertEqual(globals_["application"](request()), "isolated")
		legacy.assert_not_called()


if __name__ == "__main__":
	unittest.main()
