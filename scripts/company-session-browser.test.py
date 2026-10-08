"""Complete shipped Python source, controlled boundaries only; no SDK/network/native DB."""
import base64
import dataclasses
import hashlib
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("owned_erp_browser", ROOT / "frappe/company_session.py")
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
IDS = [f"00000000-0000-4000-8000-{n:012d}" for n in range(1, 6)]
TOKEN, CODE, STATE, VERIFIER = "exs_" + "a" * 43, "exc_" + "b" * 43, "c" * 43, "d" * 43


def config():
	return adapter.Config(IDS[0], "erp.alpha.example.test", IDS[1], IDS[2], "erp-alpha", "erp-alpha", "https://erp.alpha.example.test", "http://127.0.0.1:8097", "s" * 43, {IDS[3]: "reader@example.test"}, "0" * 64, True, "https://auth.platform.example.test", "f" * 43)


def envelope():
	c = config()
	return dict(version=1, subject_id=IDS[3], company_id=c.company_id, product="erp", resource_kind="erp-site", binding_id=c.binding_id, native_id=c.site, generation_id=c.generation_id, authz_epoch="1", audience=c.audience, scopes=["erp:read"], current_role="member", technical_status="accepted", subscription_entitled=True)


class Headers(dict):
	def __iter__(self):
		return iter(self.items())


class Response:
	def __init__(self, body, status, content_type):
		self.body, self.status_code, self.headers, self.cookies = body, status, {}, {}

	def set_cookie(self, name, value, **options):
		self.cookies[name] = (value, options)


class Conf(dict):
	maintenance_mode = False


def request(path="/company-session/start", query="", cookie="", **headers):
	return types.SimpleNamespace(method="GET", path=path, query_string=query.encode("ascii"), host=config().site, headers=Headers({"Cookie": cookie, **headers}), environ={"RAW_URI": path + ("?" + query if query else "")})


class BrowserTests(unittest.TestCase):
	def setUp(self):
		self.trace, self.used, self.provider_failure, self.value = [], False, None, envelope()
		self.transaction_active, self.role_queries, self.closed = False, [], 0
		self.identity = types.SimpleNamespace(enabled=1, user_type="System User")
		self.roles = ["All", "Desk User", "Sales User"]
		self.frappe = types.ModuleType("frappe")
		self.frappe.local = types.SimpleNamespace(flags=types.SimpleNamespace())
		self.frappe.conf = Conf()
		def init(site, **kw):
			self.trace.append(("init", site))
			self.frappe.local = types.SimpleNamespace(flags=types.SimpleNamespace())
		self.frappe.init = init
		self.frappe.connect = lambda **kw: self.trace.append(("connect", kw))
		self.frappe.set_user = lambda user: self.trace.append(("user", user))
		self.frappe.get_roles = lambda user: self.roles
		def begin(**kw):
			self.transaction_active = True
			self.trace.append(("begin", kw))
		def rollback():
			self.transaction_active = False
			self.trace.append(("rollback",))
		def get_values(kind, filters, field, **kw):
			self.assertTrue(self.transaction_active)
			self.role_queries.append((kind, filters, field, kw))
			return self.roles
		def close():
			self.assertFalse(self.transaction_active)
			self.closed += 1
		self.frappe.db = types.SimpleNamespace(begin=begin, rollback=rollback, close=close, get_values=get_values, get_value=lambda kind, name, fields, **kw: self.identity if kind == "User" else 0)
		utils = types.ModuleType("frappe.utils")
		class ControlledCallbackManager:
			# SDK boundary, not native wrapper execution: required callable interface.
			def run(inner):
				self.trace.append(("after_response",))
		utils.CallbackManager = ControlledCallbackManager
		wrappers = types.ModuleType("werkzeug.wrappers")
		wrappers.Response = Response
		self.modules = mock.patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": utils, "werkzeug.wrappers": wrappers})
		self.modules.start()
		self.addCleanup(self.modules.stop)

	def provider(self, c, operation, payload):
		self.trace.append(("provider", operation, payload))
		self.assertFalse(self.transaction_active)
		if self.provider_failure:
			raise adapter.Denied(self.provider_failure)
		if operation == "token":
			if self.used:
				raise adapter.Denied(401)
			self.used = True
			self.assertEqual(payload, dict(grant_type="authorization_code", code=CODE, redirect_uri=c.origin + "/company-session/callback", code_verifier=VERIFIER, state_hash=hashlib.sha256(STATE.encode()).hexdigest()))
			return dict(session_token=TOKEN, token_type="Bearer", expires_in=900)
		if operation == "introspect":
			return self.value
		if operation == "revoke":
			return {"revoked": True}
		self.fail("unknown controlled provider operation")

	def call(self, req, c=None):
		with mock.patch.object(adapter, "private_json", side_effect=self.provider):
			result = adapter.application(req, c or config(), "/owned/synthetic/sites")
			self.assertIs(self.frappe.local.request, req)
			self.assertTrue(callable(req.after_response.run))
			return result

	def status_call(self, req, c=None):
		# Controlled fresh authority boundary, not real broker/GoTrue evidence.
		def current_authority(c, operation, token):
			self.assertEqual(token, TOKEN)
			return self.provider(c, operation, {"session_token": token})
		with mock.patch.object(adapter, "private_call", side_effect=current_authority):
			return self.call(req, c)

	def callback(self, flow=None, state=STATE, query=None):
		return request("/company-session/callback", query or "code=" + CODE + "&state=" + state, adapter.FLOW_COOKIE + "=" + (flow or adapter.seal_flow(config(), STATE, VERIFIER)))

	def no_session(self, response):
		self.assertNotEqual(response.status_code, 303)
		self.assertNotIn(adapter.COOKIE, response.cookies)
		self.assertNotIn("Location", response.headers)

	def test_native_wrapper_cleanup_contract_survives_start_callback_and_early_errors(self):
		for req in [request(), self.callback(), self.callback(flow="forged"), request(query="unexpected=selector")]:
			self.used = False
			self.call(req)
			self.frappe.local.request.after_response.run()
			self.assertEqual(self.trace[-1], ("after_response",))
		def failed_init(*args, **kw):
			self.frappe.local = types.SimpleNamespace()
			raise RuntimeError("controlled initialization failure")
		self.used = False
		self.frappe.init = failed_init
		result = self.call(self.callback())
		self.assertEqual(result.status_code, 503)
		self.frappe.local.request.after_response.run()
		self.assertEqual(self.trace[-1], ("after_response",))

	def test_start_is_registered_s256_and_signed_host_only_flow_without_native_work(self):
		result = self.call(request())
		self.assertEqual(result.status_code, 303)
		u = urlsplit(result.headers["Location"])
		self.assertEqual(u.scheme + "://" + u.netloc, config().auth_origin)
		self.assertEqual(u.path, "/company-session/authorize")
		args = parse_qs(u.query)
		self.assertEqual(set(args), {"client_id", "state", "code_challenge", "code_challenge_method"})
		flow, options = result.cookies[adapter.FLOW_COOKIE]
		verifier = adapter.open_flow(config(), flow, args["state"][0])
		self.assertEqual(args["code_challenge"], [adapter.base64url(hashlib.sha256(verifier.encode()).digest())])
		self.assertEqual(args["code_challenge_method"], ["S256"])
		self.assertEqual(options, dict(max_age=600, path="/", secure=True, httponly=True, samesite="Lax"))
		self.assertFalse(self.trace)
		self.assertNotIn(adapter.COOKIE, result.cookies)

	def test_callback_same_protocol_current_native_nonadmin_and_cleanup_before_cookie(self):
		result = self.call(self.callback())
		self.assertEqual(result.status_code, 303)
		self.assertEqual(result.headers["Location"], config().origin + "/company-session/status")
		self.assertEqual([row[0] for row in self.trace], ["provider", "provider", "init", "connect", "begin", "user", "rollback"])
		self.assertEqual(self.trace[3][1], {"set_admin_as_user": False})
		self.assertEqual(result.cookies[adapter.COOKIE], (TOKEN, dict(max_age=900, path="/", secure=True, httponly=True, samesite="Lax")))
		self.assertEqual(result.cookies[adapter.FLOW_COOKIE][1]["max_age"], 0)

	def test_v2_callback_lands_on_fixed_native_customer_list_after_native_acceptance(self):
		c = dataclasses.replace(config(), editor_enabled=True, editor_entitlement_kind="beta")
		self.value = envelope()
		self.value.pop("subscription_entitled")
		self.value.update(version=2, access_entitled=True, entitlement_kind="beta", scopes=["erp:read", "erp:write"])
		module = types.ModuleType("frappe.company_editor")
		# V2 contract/native identity are independently exercised by editor controls.
		module.editor_envelope = mock.Mock(return_value=(self.value, c.subjects[IDS[3]]))
		def native_acceptance(*_args):
			self.frappe.local.cookie_manager = types.SimpleNamespace(flush_cookies=lambda response: response.set_cookie("sid", "controlled-native-sid", path="/"))
		accepted = mock.Mock(side_effect=native_acceptance)
		module.callback_native_session = accepted
		req = request("/company-session/callback", "code=" + CODE + "&state=" + STATE, adapter.FLOW_COOKIE + "=" + adapter.seal_flow(c, STATE, VERIFIER))
		with mock.patch.dict(sys.modules, {"frappe.company_editor": module}):
			result = self.call(req, c)
		self.assertEqual(result.status_code, 303)
		self.assertEqual(result.headers["Location"], c.origin + "/desk/customer")
		accepted.assert_called_once()
		self.assertEqual(accepted.call_args.args[:3], (c, self.value, TOKEN))
		self.assertIs(accepted.call_args.args[3], req)
		self.assertIn(adapter.COOKIE, result.cookies)
		self.assertEqual(result.cookies["sid"][0], "controlled-native-sid")
		self.used = False
		accepted.reset_mock(side_effect=True)
		accepted.side_effect = adapter.Denied(403)
		with mock.patch.dict(sys.modules, {"frappe.company_editor": module}):
			denied = self.call(req, c)
		self.no_session(denied)
		self.assertEqual(denied.status_code, 403)

	def test_replayed_code_denies_without_new_native_work_or_cookies(self):
		self.assertEqual(self.call(self.callback()).status_code, 303)
		self.trace.clear()
		result = self.call(self.callback())
		self.assertEqual(result.status_code, 401)
		self.no_session(result)
		self.assertEqual([row[0] for row in self.trace], ["provider"])

	def test_forged_missing_expired_wrong_state_and_registration_drift_flows(self):
		flow = adapter.seal_flow(config(), STATE, VERIFIER)
		for value, state, c in [(flow[:-1] + ("a" if flow[-1] != "a" else "b"), STATE, config()), ("not-a-flow", STATE, config()), (flow, "z" * 43, config()), (flow, STATE, dataclasses.replace(config(), generation_id=IDS[4]))]:
			with self.subTest(value=value[:10], state=state[:1]):
				self.no_session(self.call(self.callback(value, state), c))
		with mock.patch.object(adapter.time, "time", return_value=1):
			expired = adapter.seal_flow(config(), STATE, VERIFIER)
		self.no_session(self.call(self.callback(expired)))
		self.assertFalse(self.trace)

	def test_ingress_wrong_host_origin_legacy_creds_body_selector_duplicate_query(self):
		cases = [request(Origin="https://other.example.test"), request(Authorization="Bearer a.b.c"), request(cookie="sid=native"), request(cookie="exe_sess=parent"), request(query="company_id=" + IDS[0]), request(**{"Content-Length": "1"}), self.callback(query="code=" + CODE + "&state=" + STATE + "&state=" + STATE)]
		foreign = request();foreign.host = "erp.bravo.example.test";cases.append(foreign)
		for req in cases:
			with self.subTest(path=req.path, headers=str(req.headers)[:20]):
				self.no_session(self.call(req))
		self.assertFalse(self.trace)

	def test_current_wrong_company_subject_generation_and_unaccepted_entitlement_deny(self):
		for key, value in [("company_id", IDS[4]), ("subject_id", IDS[4]), ("generation_id", IDS[4]), ("technical_status", "unverified"), ("subscription_entitled", False)]:
			with self.subTest(key=key):
				self.used, self.trace, self.value = False, [], {**envelope(), key: value}
				self.no_session(self.call(self.callback()))
				self.assertNotIn("init", [row[0] for row in self.trace])

	def test_deleted_disabled_native_user_and_privileged_or_revoked_roles_never_cookie(self):
		for identity, roles in [(None, ["Sales User"]), (types.SimpleNamespace(enabled=0, user_type="System User"), ["Sales User"]), (types.SimpleNamespace(enabled=1, user_type="Website User"), ["Sales User"]), (self.identity, ["System Manager"]), (self.identity, ["All", "Desk User"])]:
			self.used, self.trace, self.identity, self.roles = False, [], identity, roles
			self.no_session(self.call(self.callback()))
			self.assertEqual(self.trace[-1][0], "rollback")

	def test_provider_failure_denies_without_native_or_cookie_and_preserves_status(self):
		for status in [401, 403, 503]:
			self.provider_failure, self.trace = status, []
			result = self.call(self.callback())
			self.assertEqual(result.status_code, status)
			self.no_session(result)
			self.assertEqual([row[0] for row in self.trace], ["provider"])

	def test_invalid_token_result_and_native_rollback_failure_never_positive(self):
		for result in [dict(session_token=TOKEN, token_type="Bearer", expires_in=True), dict(session_token=TOKEN, token_type="Bearer", expires_in=901), dict(session_token=TOKEN, token_type="Bearer", expires_in=900, subject=IDS[3])]:
			with mock.patch.object(adapter, "private_json", return_value=result):
				self.no_session(adapter.application(self.callback(), config(), "/owned/sites"))
		self.frappe.db.rollback = mock.Mock(side_effect=RuntimeError("controlled rollback failure"))
		self.no_session(self.call(self.callback()))

	def test_late_native_ack_does_not_set_cookie_or_redirect(self):
		clock = [10.0]
		def rollback():
			self.transaction_active = False;self.trace.append(("rollback",));clock[0] += 10
		self.frappe.db.rollback = rollback
		with mock.patch.object(adapter.time, "monotonic", side_effect=lambda: clock[0]):
			result = self.call(self.callback())
		self.assertEqual(result.status_code, 503)
		self.no_session(result)

	def test_local_logout_has_no_native_work_clears_only_after_verified_revoke(self):
		req = request("/company-session/logout", cookie=adapter.COOKIE + "=" + TOKEN, Origin=config().origin)
		req.method = "POST"
		result = self.call(req)
		self.assertEqual(result.status_code, 200)
		self.assertEqual(set(result.cookies), {adapter.COOKIE, adapter.FLOW_COOKIE})
		self.assertEqual([row[0] for row in self.trace], ["provider"])
		self.trace.clear();self.provider_failure = 503
		self.assertFalse(self.call(req).cookies)

	def test_real_cross_host_top_level_navigation_is_allowed_but_fetch_is_not(self):
		for site in ("same-site", "cross-site"):
			self.used = False
			req = self.callback();req.headers.update({"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"})
			self.assertEqual(self.call(req).status_code, 303)
			self.used = False;req.headers["Sec-Fetch-Mode"] = "cors"
			self.no_session(self.call(req))
			self.used = False;req.headers.pop("Sec-Fetch-Mode");req.headers.pop("Sec-Fetch-Dest")
			self.assertEqual(self.call(req).status_code, 303)

	def test_callback_redirect_target_completes_same_site_and_cross_site_navigation(self):
		for site in ("same-site", "cross-site"):
			with self.subTest(site=site):
				self.used = False
				callback = self.callback()
				metadata = {"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
				callback.headers.update(metadata)
				result = self.call(callback)
				self.assertEqual(result.status_code, 303)
				target = urlsplit(result.headers["Location"])
				self.assertEqual(target.scheme + "://" + target.netloc, config().origin)
				self.assertEqual((target.path, target.query), ("/company-session/status", ""))
				token = result.cookies[adapter.COOKIE][0]
				self.trace.clear()
				self.closed = 0
				status = self.status_call(request(target.path, cookie=adapter.COOKIE + "=" + token, **metadata))
				self.assertEqual(status.status_code, 200)
				self.assertEqual(json.loads(status.body), {"data": {"authenticated": True, "company_id": config().company_id}})
				self.assertEqual([item[1] for item in self.trace if item[0] == "provider"], ["introspect", "introspect"])
				self.assertIn(("user", "reader@example.test"), self.trace)
				self.assertIn(("begin", {"read_only": True}), self.trace)
				self.assertEqual(self.trace[-2], ("rollback",))
				self.assertEqual(self.trace[-1][0:2], ("provider", "introspect"))
				self.assertEqual(self.closed, 1)
				self.assertFalse(status.cookies)

	def test_status_navigation_requires_explicit_metadata_and_own_session(self):
		base = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
		cases = [
			{**base, "Sec-Fetch-Mode": "cors"}, {**base, "Sec-Fetch-Dest": "iframe"},
			{k: v for k, v in base.items() if k != "Sec-Fetch-Mode"},
			{k: v for k, v in base.items() if k != "Sec-Fetch-Dest"},
			{**base, "Origin": "https://foreign.example.test"},
		]
		for headers in cases:
			with self.subTest(headers=headers):
				self.trace.clear()
				result = self.status_call(request("/company-session/status", cookie=adapter.COOKIE + "=" + TOKEN, **headers))
				self.assertEqual(result.status_code, 403)
				self.assertFalse(self.trace)
		flow = adapter.FLOW_COOKIE + "=" + adapter.seal_flow(config(), STATE, VERIFIER)
		self.trace.clear()
		self.assertNotEqual(self.status_call(request("/company-session/status", cookie=flow, **base)).status_code, 200)
		self.assertFalse(self.trace)
		req = request("/company-session/status", cookie=adapter.COOKIE + "=" + TOKEN, **base)
		req.host = "erp.foreign.example.test"
		self.assertEqual(self.status_call(req).status_code, 403)
		self.assertFalse(self.trace)

	def test_status_navigation_rechecks_current_central_and_native_authority(self):
		metadata = {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
		def status():
			return self.status_call(request("/company-session/status", cookie=adapter.COOKIE + "=" + TOKEN, **metadata))
		for field, value in [("company_id", IDS[4]), ("subject_id", IDS[4]), ("generation_id", IDS[4]), ("subscription_entitled", False)]:
			with self.subTest(field=field):
				self.value = {**envelope(), field: value}
				self.trace.clear()
				self.assertNotEqual(status().status_code, 200)
				self.assertFalse(any(item[0] == "connect" for item in self.trace))
		self.value = envelope()
		for identity, roles in [(None, ["Sales User"]), (types.SimpleNamespace(enabled=0, user_type="System User"), ["Sales User"]), (types.SimpleNamespace(enabled=1, user_type="System User"), ["All", "Desk User"]), (types.SimpleNamespace(enabled=1, user_type="System User"), ["System Manager"])]:
			self.identity, self.roles = identity, roles
			self.assertEqual(status().status_code, 403)
			self.assertFalse(self.transaction_active)

	def test_status_navigation_exception_does_not_extend_native_reads_logout_or_offmode(self):
		metadata = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
		for path, query, method, c in [
			("/api/resource/Customer", "limit_page_length=10&limit_start=0", "GET", config()),
			("/company-session/logout", "", "POST", config()),
			("/company-session/status", "selector=1", "GET", config()),
			("/company-session/status", "", "POST", config()),
			("/company-session/status", "", "GET", dataclasses.replace(config(), browser_enabled=False)),
		]:
			with self.subTest(path=path, query=query, method=method, enabled=c.browser_enabled):
				req = request(path, query, adapter.COOKIE + "=" + TOKEN, Origin=config().origin, **metadata)
				req.method = method
				self.trace.clear()
				self.assertEqual(self.status_call(req, c).status_code, 403)
				self.assertFalse(self.trace)

	def test_session_and_bounded_flow_coexist_for_current_policy_and_local_logout(self):
		flow = adapter.seal_flow(config(), STATE, VERIFIER)
		combined = adapter.COOKIE + "=" + TOKEN + "; " + adapter.FLOW_COOKIE + "=" + flow
		for path, query in [("/company-session/status", ""), ("/api/resource/Customer", "limit_page_length=10&limit_start=0")]:
			req = request(path, query, combined)
			self.assertEqual(adapter.request_policy(req, config())[-1], TOKEN)
		req = request("/company-session/logout", cookie=combined, Origin=config().origin);req.method = "POST"
		result = self.call(req)
		self.assertEqual(result.status_code, 200)
		self.assertEqual(set(result.cookies), {adapter.COOKIE, adapter.FLOW_COOKIE})
		for cookie in [adapter.FLOW_COOKIE + "=" + flow, combined + "; " + adapter.FLOW_COOKIE + "=" + flow, combined + "; sid=native", adapter.COOKIE + "=" + TOKEN + "; " + adapter.FLOW_COOKIE + "=" + "x" * 2049, adapter.COOKIE + "=" + TOKEN + "; " + adapter.FLOW_COOKIE + "=bad"]:
			self.assertRaises(adapter.Denied, adapter.request_policy, request("/company-session/status", cookie=cookie), config())
		self.assertRaises(adapter.Denied, adapter.request_policy, request("/company-session/status", cookie=combined), dataclasses.replace(config(), browser_enabled=False))

	def test_callback_uses_fresh_bounded_role_membership_not_cached_allow(self):
		self.frappe.get_roles = mock.Mock(side_effect=AssertionError("cached roles must not be used"))
		self.assertEqual(self.call(self.callback()).status_code, 303)
		self.assertEqual(self.role_queries, [("Has Role", {"parenttype": "User", "parent": "reader@example.test"}, "role", {"pluck": True, "cache": False, "limit": 101})])
		for roles in [[], ["All", "Guest", "Desk User"], ["Sales User"] * 101, ["Sales User", "Sales User"], [{}], [["Sales User"]], [None]]:
			self.roles, self.used = roles, False
			self.no_session(self.call(self.callback()))
		self.frappe.get_roles.assert_not_called()
		self.assertFalse(self.transaction_active)

	def test_complete_default_off_browser_configuration_and_secret_separation(self):
		c = config()
		bindings = json.dumps(dict(version=1, company_id=c.company_id, site=c.site, binding_id=c.binding_id, generation_id=c.generation_id, audience=c.audience, subjects=[dict(subject_id=IDS[3], native_user="reader@example.test")])).encode()
		env = {"ERP_COMPANY_MODE": "true", "SITE_NAME": c.site, "ERP_COMPANY_ID": c.company_id, "ERP_COMPANY_SITE": c.site, "ERP_COMPANY_ORIGIN": c.origin, "ERP_COMPANY_BINDING_ID": c.binding_id, "ERP_COMPANY_GENERATION_ID": c.generation_id, "ERP_COMPANY_AUDIENCE": c.audience, "ERP_COMPANY_CLIENT_ID": c.client_id, "ERP_COMPANY_AUTHORITY_URL": c.authority, "ERP_COMPANY_CLIENT_SECRET_FILE": "/owned/client", "ERP_COMPANY_BINDINGS_FILE": "/owned/bindings", "ERP_COMPANY_BINDINGS_SHA256": hashlib.sha256(bindings).hexdigest()}
		def private(path, maximum):
			return {"/owned/client": c.secret.encode(), "/owned/bindings": bindings, "/owned/flow": c.flow_secret.encode(), "/owned/same": c.secret.encode()}[path]
		with mock.patch.dict(adapter.os.environ, env, clear=True), mock.patch.object(adapter.os, "geteuid", return_value=1000), mock.patch.object(adapter, "private_file", side_effect=private):
			self.assertFalse(adapter.load_config().browser_enabled)
			adapter.os.environ.update(ERP_COMPANY_BROWSER_ENABLED="true", ERP_COMPANY_AUTH_ORIGIN=c.auth_origin, ERP_COMPANY_FLOW_SECRET_FILE="/owned/flow")
			self.assertEqual(adapter.load_config().auth_origin, c.auth_origin)
			for key, value in [("ERP_COMPANY_BROWSER_ENABLED", "yes"), ("ERP_COMPANY_AUTH_ORIGIN", "https://erp.alpha.example.test"), ("ERP_COMPANY_AUTH_ORIGIN", "https://auth.platform.example.test/path"), ("ERP_COMPANY_FLOW_SECRET_FILE", "/owned/same"), ("ERP_COMPANY_FLOW_SECRET_FILE", "")]:
				with mock.patch.dict(adapter.os.environ, {key: value}):
					self.assertRaises((ValueError, KeyError), adapter.load_config)
			with mock.patch.dict(adapter.os.environ, {"ERP_COMPANY_BROWSER_ENABLED": "false"}):
				self.assertRaises(ValueError, adapter.load_config)

	def test_private_exchange_rejects_payload_selectors_and_offmode_before_transport(self):
		with mock.patch.object(adapter, "PrivateConnection", side_effect=AssertionError("must not allocate")):
			for body in [{"subject_id": IDS[3]}, {"grant_type": "authorization_code", "code": CODE, "redirect_uri": "https://foreign.test/callback", "code_verifier": VERIFIER, "state_hash": "a" * 64}]:
				self.assertRaises(adapter.Denied, adapter.private_json, config(), "token", body)
			self.assertRaises(adapter.Denied, adapter.private_json, dataclasses.replace(config(), browser_enabled=False), "token", {})


if __name__ == "__main__":
	unittest.main()
