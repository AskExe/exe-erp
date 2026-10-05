"""Complete application/read_records controlled checks; not native DB or GoTrue proof."""
import importlib.util
import json
import pathlib
import sys
import threading
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("postread_source_fixture", ROOT / "scripts/company-session-read.test.py")
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
spec.loader.exec_module(fixture)
a = fixture.adapter


class Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class Response:
	def __init__(self, body, status, content_type):
		self.body, self.status_code, self.headers, self.cookies = body, status, {}, []

	def set_cookie(self, *args, **kwargs):
		self.cookies.append((args, kwargs))


class Tests(unittest.TestCase):
	def setUp(self):
		self.state = {"roles": ["Sales User"], "enabled": 1, "visible": True, "field": True}
		self.authority = fixture.envelope()
		self.introspections = 0
		self.reads = 0
		self.f = types.ModuleType("frappe")
		self.f.local = types.SimpleNamespace()
		self.f.conf = Flags()
		self.f.PermissionError = type("NativePermission", (Exception,), {})
		self.f.DoesNotExistError = type("NativeMissing", (Exception,), {})
		self.f.db = types.SimpleNamespace(begin=mock.Mock(), rollback=mock.Mock(), close=mock.Mock(), get_value=self.get_value)
		self.f.init = mock.Mock(side_effect=self.initialize)
		self.f.connect = mock.Mock()
		self.f.set_user = mock.Mock()
		self.f.get_roles = self.get_roles
		self.f.get_list = self.get_list
		utils = types.ModuleType("frappe.utils")
		utils.CallbackManager = lambda: types.SimpleNamespace(run=mock.Mock())
		wrappers = types.ModuleType("werkzeug.wrappers")
		wrappers.Response = Response
		self.modules = mock.patch.dict(sys.modules, {"frappe": self.f, "frappe.utils": utils, "werkzeug.wrappers": wrappers})
		self.modules.start()
		self.private = mock.patch.object(a, "private_call", side_effect=self.private_call)
		self.private.start()
		self.block_phase = None
		self.entered, self.release = threading.Event(), threading.Event()

	def tearDown(self):
		self.private.stop()
		self.modules.stop()

	def initialize(self, *_args, **_kw):
		self.f.local.flags = Flags()
		self.f.local.role_permissions = {}
		self.f.local.request_cache = {}
		self.f.local.db = self.f.db

	def get_roles(self, user):
		self.assertEqual(user, fixture.config().subjects[fixture.IDS[3]])
		return list(self.state["roles"])

	def get_value(self, doctype, *_args, **_kw):
		return types.SimpleNamespace(enabled=self.state["enabled"], user_type="System User") if doctype == "User" else 0

	def block(self, phase):
		if self.block_phase == phase:
			self.entered.set()
			if not self.release.wait(2):
				raise a.Denied(503)

	def private_call(self, _c, operation, _token):
		if operation == "revoke":
			return {"revoked": True}
		self.introspections += 1
		self.block("authority" + str(self.introspections))
		if isinstance(self.authority, Exception):
			raise self.authority
		return dict(self.authority)

	def get_list(self, doctype, fields, **params):
		self.reads += 1
		visible, field = self.state["visible"], self.state["field"]
		self.block("read" + str(self.reads))
		if not visible:
			raise self.f.PermissionError()
		row = {"name": "customer-" + str(self.reads)}
		if field:
			row["customer_name"] = "private-field"
		return [row]

	def call(self, r=None):
		return a.application(r or fixture.request(), fixture.config(), "fixture-sites")

	def blocked(self, phase, change):
		self.block_phase = phase
		results, errors = [], []
		def run():
			try:
				results.append(self.call())
			except BaseException as e:
				errors.append(type(e).__name__)
		thread = threading.Thread(target=run)
		thread.start()
		try:
			self.assertTrue(self.entered.wait(1))
			change()
		finally:
			self.release.set()
			thread.join(2)
		self.assertFalse(thread.is_alive())
		self.assertEqual(errors, [])
		self.assertEqual(len(results), 1)
		return results[0]

	def denied(self, response):
		self.assertNotEqual(response.status_code, 200)
		self.assertNotIn("data", json.loads(response.body))
		self.assertNotIn("private-field", response.body)

	def test_only_second_current_native_result_is_published(self):
		r = self.call()
		self.assertEqual(r.status_code, 200)
		self.assertEqual(json.loads(r.body)["data"][0]["name"], "customer-2")
		self.assertEqual((self.reads, self.introspections), (2, 3))
		self.assertEqual(self.f.init.call_count, 2)
		self.assertEqual(self.f.db.rollback.call_count, 2)
		self.assertEqual(self.f.db.close.call_count, 2)

	def test_central_revocation_while_first_read_is_blocked_denies(self):
		self.denied(self.blocked("read1", lambda: setattr(self, "authority", a.Denied(401))))
		self.assertEqual(self.reads, 1)

	def test_epoch_drift_while_second_read_is_blocked_denies(self):
		self.denied(self.blocked("read2", lambda: self.authority.update(authz_epoch="2")))

	def test_subject_drift_while_final_authority_is_blocked_denies(self):
		self.denied(self.blocked("authority3", lambda: self.authority.update(subject_id=fixture.IDS[4])))

	def test_entitlement_or_registration_drift_denies_after_read(self):
		for delta in ({"subscription_entitled": False}, {"generation_id": fixture.IDS[4]}, {"binding_id": fixture.IDS[4]}, {"company_id": fixture.IDS[4]}, {"current_role": "owner"}):
			with self.subTest(delta=delta):
				self.authority = fixture.envelope()
				self.reads = self.introspections = 0
				self.entered.clear(); self.release.clear()
				self.denied(self.blocked("read1", lambda: self.authority.update(delta)))

	def test_native_disabled_or_role_revoked_after_first_read_denies(self):
		for delta in ({"enabled": 0}, {"roles": []}, {"roles": ["System Manager"]}):
			with self.subTest(delta=delta):
				self.state.update(enabled=1, roles=["Sales User"])
				self.reads = self.introspections = 0
				self.entered.clear(); self.release.clear()
				self.denied(self.blocked("read1", lambda: self.state.update(delta)))

	def test_same_native_row_and_field_path_rechecks_current_acl(self):
		self.denied(self.blocked("read1", lambda: self.state.update(visible=False)))
		self.state.update(visible=True, field=True)
		self.reads = self.introspections = 0
		self.entered.clear(); self.release.clear()
		r = self.blocked("read1", lambda: self.state.update(field=False))
		self.assertEqual(r.status_code, 200)
		self.assertNotIn("customer_name", json.loads(r.body)["data"][0])
		self.assertNotIn("private-field", r.body)

	def test_status_has_one_native_pass_and_logout_none(self):
		r = self.call(fixture.request("/company-session/status", ""))
		self.assertEqual(r.status_code, 200)
		self.assertEqual((self.reads, self.introspections, self.f.init.call_count), (0, 2, 1))
		r = self.call(fixture.request("/company-session/logout", "", "POST", {"Origin": fixture.config().origin}))
		self.assertEqual(r.status_code, 200)
		self.assertEqual(self.f.init.call_count, 1)

	def test_rollback_failure_never_publishes_success(self):
		self.f.db.rollback.side_effect = RuntimeError("controlled")
		self.denied(self.call())
		self.assertEqual(self.f.db.close.call_count, 1)


if __name__ == "__main__":
	unittest.main()
