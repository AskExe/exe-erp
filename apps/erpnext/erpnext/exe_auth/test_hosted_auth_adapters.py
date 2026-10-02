"""Adapter-level regressions for hosted central auth and provisioning."""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
API_PY = os.path.join(HERE, "api.py")
EXE_PERMS_PY = os.path.join(HERE, "exe_perms.py")


def load_path(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


class AuthenticationError(Exception):
	pass


class Redirect(Exception):
	pass


class AdapterDriver:
	def __init__(self, conf=None, path="/", command=None):
		self.frappe = types.ModuleType("frappe")
		self.frappe.conf = dict(conf or {})
		self.frappe.local = types.SimpleNamespace(
			request=types.SimpleNamespace(path=path),
			flags=types.SimpleNamespace(redirect_location=None),
		)
		self.frappe.form_dict = {"cmd": command} if command else {}
		self.frappe.AuthenticationError = AuthenticationError
		self.frappe.Redirect = Redirect
		self.frappe.whitelist = lambda *args, **kwargs: lambda fn: fn

		def throw(message, exc=Exception):
			raise exc(message)

		self.frappe.throw = throw
		rate_limiter = types.ModuleType("frappe.rate_limiter")
		rate_limiter.rate_limit = lambda *args, **kwargs: lambda fn: fn
		website_utils = types.ModuleType("frappe.website.utils")
		website_utils.get_home_page = lambda: "/desk"
		login = types.ModuleType("frappe.www.login")
		login.get_exe_auth_url = lambda: "https://auth.example.com"

		exe_perms = load_path("hosted_adapter_exe_perms", EXE_PERMS_PY)
		erpnext = types.ModuleType("erpnext")
		exe_auth = types.ModuleType("erpnext.exe_auth")
		exe_auth.exe_perms = exe_perms
		erpnext.exe_auth = exe_auth

		self.modules = {
			"frappe": self.frappe,
			"frappe.rate_limiter": rate_limiter,
			"frappe.website": types.ModuleType("frappe.website"),
			"frappe.website.utils": website_utils,
			"frappe.www": types.ModuleType("frappe.www"),
			"frappe.www.login": login,
			"erpnext": erpnext,
			"erpnext.exe_auth": exe_auth,
		}

	def load_api(self):
		with mock.patch.dict(sys.modules, self.modules):
			return load_path("hosted_adapter_api", API_PY)

	def call(self, name, *args):
		with mock.patch.dict(sys.modules, self.modules):
			return getattr(self.load_api(), name)(*args)


class TestProvisioningAdmissionAdapter(unittest.TestCase):
	def metadata(self, org="askexe", caps=None):
		if caps is None:
			caps = ["erp:read"]
		return {"exe_perms": {"orgs": {org: {"caps": caps}}}}

	def testPositiveConfiguredGrantBypassesDifferentEmailDomain(self):
		driver = AdapterDriver({"exe_org_id": "askexe", "allowed_email_domains": ["askexe.com"]})
		driver.call("_assert_provisioning_allowed", "person@gmail.com", self.metadata())

	def testWrongOrgDeniedAndAbsentMetadataStillUseLegacyDomainGate(self):
		driver = AdapterDriver({"exe_org_id": "askexe", "allowed_email_domains": ["askexe.com"]})
		for metadata in (None, self.metadata(org="other"), self.metadata(caps=[])):
			with self.subTest(metadata=metadata), self.assertRaises(AuthenticationError):
				driver.call("_assert_provisioning_allowed", "person@gmail.com", metadata)


class TestHostedRouteAdapter(unittest.TestCase):
	def testHtmlRecoveryRouteRedirectsToConfiguredCentralAuth(self):
		driver = AdapterDriver({"gotrue_url": "http://gotrue"}, path="/update-password")
		with self.assertRaises(Redirect):
			driver.call("enforce_central_auth_routes")
		self.assertEqual(driver.frappe.local.flags.redirect_location, "https://auth.example.com")

	def testAllApiShapesAndPasswordlessEndpointsReject(self):
		paths = (
			"/api/method/frappe.www.login.login_via_key",
			"/api/v1/method/frappe.www.login.send_login_link",
			"/api/v2/method/frappe.www.login.login_via_token",
			"/api/v2/method/User/reset_password",
			"/api/v2/method/User/update_password",
		)
		for path in paths:
			with self.subTest(path=path):
				driver = AdapterDriver({"gotrue_url": "http://gotrue"}, path=path)
				with self.assertRaises(AuthenticationError):
					driver.call("enforce_central_auth_routes")

	def testBeforeLoginRejectsHostedButLeavesStandaloneAlone(self):
		with self.assertRaises(AuthenticationError):
			AdapterDriver({"gotrue_url": "http://gotrue"}).call("reject_local_password_login")
		AdapterDriver({}).call("reject_local_password_login")



class TestPublicSyntheticAdmission(unittest.TestCase):
	def setUp(self):
		self.policy = load_path("public_demo_perms", EXE_PERMS_PY)
		self.conf = {"exe_hosted_site_mode": "synthetic_demo", "exe_org_id": "demo",
			"exe_erp_public_demo": True, "exe_erp_readonly_desk": True}
		self.user = {"id": "10000000-0000-4000-8000-000000000001", "email": "visitor@example.com",
			"email_confirmed_at": "2026-01-01T00:00:00Z", "app_metadata": {}}

	def testOnlyFreshConfirmedUnmanagedUserAdmitted(self):
		self.assertTrue(self.policy.public_demo_viewer_allowed(self.user, self.conf))
		for key in ("id", "email", "email_confirmed_at"):
			with self.subTest(key=key):
				u = dict(self.user)
				u.pop(key)
				self.assertFalse(self.policy.public_demo_viewer_allowed(u, self.conf))

	def testPrivateSitesAndPartialPublicConfigRefused(self):
		for key in self.conf:
			with self.subTest(key=key):
				c = dict(self.conf)
				c.pop(key)
				self.assertFalse(self.policy.public_demo_viewer_allowed(self.user, c))
		self.assertFalse(self.policy.public_demo_viewer_allowed(self.user, {}))

	def testNeverOverridesManagedDeniedOrOtherOrgClaims(self):
		for claim in (None, {}, {"orgs": {"other": {"caps": ["erp:admin"]}}},
			{"orgs": {"demo": {"role": "none", "caps": []}}}):
			with self.subTest(claim=claim):
				u = {**self.user, "app_metadata": {"exe_perms": claim}}
				self.assertFalse(self.policy.public_demo_viewer_allowed(u, self.conf))

	def testBannedDeletedAnonymousMalformedRefused(self):
		for patch in ({"banned_until": "2999-01-01T00:00:00Z"}, {"banned_until": "invalid"},
			{"deleted_at": "2026-01-01"}, {"is_anonymous": True}, {"id": "invalid"},
			{"app_metadata": "invalid"}):
			with self.subTest(patch=patch):
				self.assertFalse(self.policy.public_demo_viewer_allowed({**self.user, **patch}, self.conf))


class TestPublicViewerRoleAdapter(unittest.TestCase):
	def make(self, roles, enabled=1):
		driver = AdapterDriver()
		doc = types.SimpleNamespace(enabled=enabled, flags=types.SimpleNamespace(), user_type="Website User")
		doc.get = lambda field: [types.SimpleNamespace(role=r) for r in roles]
		doc.append = mock.Mock()
		doc.save = mock.Mock()
		driver.frappe.get_doc = lambda *args: doc
		return driver, doc

	def testNewVisitorGetsOnlyViewerAndNoBootstrapPromotion(self):
		driver, doc = self.make([])
		driver.call("_apply_public_demo_viewer", "visitor@example.com")
		self.assertEqual(doc.user_type, "System User")
		doc.append.assert_called_once_with("roles", {"role": "Exe ERP Viewer"})
		doc.save.assert_called_once_with(ignore_permissions=True)

	def testDisabledAndPrivilegedExistingUsersAreNeverRewritten(self):
		for roles, enabled in (([], 0), (["System Manager"], 1), (["Accounts User"], 1)):
			with self.subTest(roles=roles, enabled=enabled):
				driver, doc = self.make(roles, enabled)
				with self.assertRaises(AuthenticationError):
					driver.call("_apply_public_demo_viewer", "visitor@example.com")
				doc.save.assert_not_called()
				doc.append.assert_not_called()


class TestPublicCallbackAdapter(unittest.TestCase):
	def make(self, user):
		driver = AdapterDriver({"gotrue_url": "http://gotrue", "exe_org_id": "demo",
			"exe_hosted_site_mode": "synthetic_demo", "exe_erp_public_demo": True,
			"exe_erp_readonly_desk": True})
		f = driver.frappe
		f.form_dict = {"state": "initiating-state"}
		f.request = types.SimpleNamespace(cookies={"exe_sso_state": "initiating-state", "exe_sess": "test-central-token"})
		f.local.cookie_manager = types.SimpleNamespace(delete_cookie=mock.Mock())
		f.local.response = {}
		f.local.login_manager = types.SimpleNamespace(login_as=mock.Mock())
		f.log_error = mock.Mock()
		f.db = types.SimpleNamespace(exists=lambda *args: False)
		doc = types.SimpleNamespace(flags=types.SimpleNamespace(), insert=mock.Mock())
		f.get_doc = mock.Mock(return_value=doc)
		api = driver.load_api()
		api.requests = types.SimpleNamespace(get=mock.Mock(return_value=types.SimpleNamespace(
			status_code=200, json=lambda: user)), RequestException=RuntimeError)
		api._try_bootstrap_first_admin = mock.Mock()
		api._apply_public_demo_viewer = mock.Mock()
		api._apply_managed_roles = mock.Mock()
		return driver, api, doc

	def testFreshConfirmedPublicCallbackCreatesViewerWithoutAdminBootstrap(self):
		driver, api, doc = self.make({"id": "10000000-0000-4000-8000-000000000001",
			"email": "visitor@example.com", "email_confirmed_at": "2026-01-01T00:00:00Z", "app_metadata": {}})
		api.gotrue_login_callback()
		doc.insert.assert_called_once()
		api._apply_public_demo_viewer.assert_called_once_with("visitor@example.com")
		api._try_bootstrap_first_admin.assert_not_called()
		api._apply_managed_roles.assert_not_called()
		driver.frappe.local.login_manager.login_as.assert_called_once_with("visitor@example.com")
		self.assertEqual(driver.frappe.local.response["location"], "/desk")

	def testUnverifiedPublicCallbackCannotProvisionOrMintNativeSession(self):
		driver, api, doc = self.make({"id": "10000000-0000-4000-8000-000000000001",
			"email": "visitor@example.com", "app_metadata": {}})
		with self.assertRaises(AuthenticationError):
			api.gotrue_login_callback()
		doc.insert.assert_not_called()
		api._apply_public_demo_viewer.assert_not_called()
		driver.frappe.local.login_manager.login_as.assert_not_called()

if __name__ == "__main__":
	unittest.main()
