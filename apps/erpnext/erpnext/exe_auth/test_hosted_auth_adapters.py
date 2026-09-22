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


if __name__ == "__main__":
	unittest.main()
