"""
Unit tests for the PURE cap -> Frappe-role mapping (P3, exe_perms.py).

These tests are deliberately FRAPPE-FREE: they import only
`erpnext.exe_auth.exe_perms` (no `import frappe`, no live site) so they run
under plain `python -m unittest` / `pytest` in CI without bench.

The login-path INTEGRATION (User.add_roles/remove_roles, user_type flip,
managed-deny disable, login_as) lives in `api.py` and must be tested under
bench (frappe.tests) against a live site — see test plan at bottom of file.
"""

import importlib.util
import os
import unittest

# Load the PURE mapping module directly by file path. This avoids importing the
# `erpnext` package (whose __init__ imports frappe), so these tests run under
# plain `python -m unittest` with NO live site. Under bench, importing
# `erpnext.exe_auth.exe_perms` normally works too — same module, same behavior.
_EP_PATH = os.path.join(os.path.dirname(__file__), "exe_perms.py")
_spec = importlib.util.spec_from_file_location("exe_perms_under_test", _EP_PATH)
ep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ep)


class TestErpLevel(unittest.TestCase):
	def testAdminFromErpAdmin(self):
		self.assertEqual(ep.erp_level(["erp:admin"]), ep.LEVEL_ADMIN)

	def testAdminFromOrgAdmin(self):
		self.assertEqual(ep.erp_level(["org:admin"]), ep.LEVEL_ADMIN)

	def testWriteFromErpWrite(self):
		self.assertEqual(ep.erp_level(["erp:write", "erp:read"]), ep.LEVEL_WRITE)

	def testReadFromErpReadOnly(self):
		self.assertEqual(ep.erp_level(["erp:read"]), ep.LEVEL_READ)

	def testNoneFromEmpty(self):
		self.assertEqual(ep.erp_level([]), ep.LEVEL_NONE)

	def testWikiCrmCapsIgnored(self):
		# Only ERP-relevant caps drive the level.
		self.assertEqual(ep.erp_level(["wiki:admin", "crm:write"]), ep.LEVEL_NONE)

	def testHighestLevelWins(self):
		self.assertEqual(ep.erp_level(["erp:read", "erp:write", "erp:admin"]), ep.LEVEL_ADMIN)

	def testCaseInsensitive(self):
		self.assertEqual(ep.erp_level(["ERP:Admin"]), ep.LEVEL_ADMIN)


class TestMapErpRoles(unittest.TestCase):
	def testAdminRolesSet(self):
		d = ep.map_erp_roles(["erp:admin"])
		self.assertIn("System Manager", d["roles"])
		self.assertEqual(d["user_type"], ep.SYSTEM_USER_TYPE)
		self.assertFalse(d["deny"])

	def testWriteRolesSet(self):
		d = ep.map_erp_roles(["erp:write"])
		self.assertEqual(d["roles"], set(ep.DEFAULT_WRITE_ROLES))
		self.assertEqual(d["user_type"], ep.SYSTEM_USER_TYPE)
		self.assertNotIn("System Manager", d["roles"])

	def testReadIsPortalNoDeskRoles(self):
		d = ep.map_erp_roles(["erp:read"])
		self.assertEqual(d["roles"], set())
		self.assertEqual(d["user_type"], ep.WEBSITE_USER_TYPE)
		self.assertFalse(d["deny"])

	def testNoneIsDeny(self):
		d = ep.map_erp_roles([])
		self.assertTrue(d["deny"])
		self.assertEqual(d["roles"], set())

	def testMonotonicAdminSupersetOfWrite(self):
		admin = ep.map_erp_roles(["erp:admin"])["roles"]
		write = ep.map_erp_roles(["erp:write"])["roles"]
		read = ep.map_erp_roles(["erp:read"])["roles"]
		self.assertTrue(write.issubset(admin))
		self.assertTrue(read.issubset(write))

	def testManagedAllowlistCoversTargets(self):
		# Every role the mapping can assign must be in the managed allowlist so
		# reconcile removal never leaves a stray granted role uncontrolled.
		for caps in (["erp:admin"], ["erp:write"], ["erp:read"], []):
			d = ep.map_erp_roles(caps)
			self.assertTrue(d["roles"].issubset(d["managed"]))

	def testCustomRoleConfig(self):
		d = ep.map_erp_roles(
			["erp:admin"], admin_role="Exe Admin", write_roles=["Exe Writer"]
		)
		self.assertEqual(d["roles"], {"Exe Writer", "Exe Admin"})
		self.assertEqual(d["managed"], {"Exe Writer", "Exe Admin"})


class TestManagedRoles(unittest.TestCase):
	def testDefaultManagedAllowlist(self):
		m = ep.managed_roles()
		self.assertEqual(m, set(ep.DEFAULT_WRITE_ROLES) | {ep.DEFAULT_ADMIN_ROLE})

	def testManagedExcludesUnrelatedRoles(self):
		# A hand-assigned role like "Accounts Manager" is NOT managed -> reconcile
		# must never strip it.
		self.assertNotIn("Accounts Manager", ep.managed_roles())


# --- Claim extraction: per-org shape + legacy flat fallback ------------------

PER_ORG_META = {
	"exe_perms": {
		"version": 1,
		"orgs": {
			"acme": {"role": "manager", "caps": ["erp:write", "erp:read"]},
			"other": {"role": "viewer", "caps": ["erp:read"]},
		},
	}
}

LEGACY_FLAT_META = {
	"exe_perms": {"version": 1, "org": "acme", "role": "admin", "caps": ["erp:admin"]}
}


class TestResolveOrgId(unittest.TestCase):
	def testConfiguredOrgWins(self):
		org, status = ep.resolve_org_id(PER_ORG_META, "ACME")
		self.assertEqual(org, "acme")  # normalized lowercase
		self.assertEqual(status, ep.ORG_RESOLVED)

	def testSingleOrgAutoResolves(self):
		meta = {"exe_perms": {"orgs": {"solo": {"caps": ["erp:read"]}}}}
		org, status = ep.resolve_org_id(meta, None)
		self.assertEqual(org, "solo")
		self.assertEqual(status, ep.ORG_RESOLVED)

	def testMultiOrgWithoutConfigIsUnmanaged(self):
		org, status = ep.resolve_org_id(PER_ORG_META, None)
		self.assertIsNone(org)
		self.assertEqual(status, ep.ORG_UNMANAGED_MULTI)

	def testAbsentClaimIsUnmanaged(self):
		org, status = ep.resolve_org_id({}, None)
		self.assertIsNone(org)
		self.assertEqual(status, ep.ORG_UNMANAGED_ABSENT)

	def testLegacyFlatResolvesFromOrg(self):
		org, status = ep.resolve_org_id(LEGACY_FLAT_META, None)
		self.assertEqual(org, "acme")
		self.assertEqual(status, ep.ORG_RESOLVED)


class TestSelectOrgClaim(unittest.TestCase):
	def testPerOrgSelect(self):
		claim = ep.select_org_claim(PER_ORG_META, "acme")
		self.assertEqual(claim["caps"], ["erp:write", "erp:read"])

	def testPerOrgMissingOrg(self):
		self.assertIsNone(ep.select_org_claim(PER_ORG_META, "nope"))

	def testLegacyFlatSelect(self):
		claim = ep.select_org_claim(LEGACY_FLAT_META, "acme")
		self.assertEqual(claim["caps"], ["erp:admin"])

	def testLegacyFlatWrongOrg(self):
		self.assertIsNone(ep.select_org_claim(LEGACY_FLAT_META, "acme2"))


class TestComputeDecision(unittest.TestCase):
	def testManagedPerOrgWrite(self):
		d, status = ep.compute_decision(PER_ORG_META, "acme")
		self.assertEqual(status, ep.ORG_RESOLVED)
		self.assertEqual(d["level"], ep.LEVEL_WRITE)
		self.assertEqual(d["org_id"], "acme")

	def testManagedLegacyAdmin(self):
		d, status = ep.compute_decision(LEGACY_FLAT_META, None)
		self.assertEqual(d["level"], ep.LEVEL_ADMIN)
		self.assertIn("System Manager", d["roles"])

	def testAbsentIsUnmanagedNoDecision(self):
		d, status = ep.compute_decision({}, None)
		self.assertIsNone(d)
		self.assertEqual(status, ep.ORG_UNMANAGED_ABSENT)

	def testNoneAppMetadataUnmanaged(self):
		d, status = ep.compute_decision(None, "acme")
		self.assertIsNone(d)

	def testMultiOrgUnmanaged(self):
		d, status = ep.compute_decision(PER_ORG_META, None)
		self.assertIsNone(d)
		self.assertEqual(status, ep.ORG_UNMANAGED_MULTI)

	def testOrgResolvedButNoClaimIsUnmanaged(self):
		d, status = ep.compute_decision(PER_ORG_META, "nosuchorg")
		self.assertIsNone(d)
		self.assertEqual(status, ep.ORG_UNMANAGED_NO_CLAIM)

	def testRoleNoneIsManagedDeny(self):
		meta = {"exe_perms": {"orgs": {"acme": {"role": "none", "caps": ["erp:admin"]}}}}
		d, status = ep.compute_decision(meta, "acme")
		# role "none" forces deny even if caps somehow present.
		self.assertTrue(d["deny"])
		self.assertEqual(d["level"], ep.LEVEL_NONE)

	def testEmptyErpCapsIsManagedDeny(self):
		meta = {"exe_perms": {"orgs": {"acme": {"role": "member", "caps": ["wiki:write"]}}}}
		d, status = ep.compute_decision(meta, "acme")
		self.assertTrue(d["deny"])


if __name__ == "__main__":
	unittest.main()


# ---------------------------------------------------------------------------
# LOGIN-PATH INTEGRATION TEST PLAN (requires bench + live site; not run here)
# ---------------------------------------------------------------------------
# Run under: `bench --site <site> run-tests --module \
#   erpnext.exe_auth.test_api_gotrue` (frappe.tests.utils.FrappeTestCase).
# Mock `requests.post`/`requests.get` (GoTrue /token, /user) to return a body
# whose app_metadata carries the exe_perms claim; set frappe.conf exe_org_id.
#
# Cases to cover against a live User doctype:
#   1. managed erp:admin  -> User gains "System Manager" + write bundle,
#      user_type == "System User", enabled == 1.
#   2. managed erp:write  -> gains write bundle, NOT "System Manager".
#   3. managed erp:read   -> no desk roles, user_type == "Website User".
#   4. downgrade admin->write on second login -> "System Manager" REMOVED,
#      write bundle retained (remove_roles scoped to managed allowlist).
#   5. hand-assigned unmanaged role (e.g. "Accounts Manager" or "HR User")
#      set outside caps -> SURVIVES reconcile (never stripped).
#   6. managed-deny (role "none" / empty erp caps) -> User.enabled == 0 and
#      login raises AuthenticationError (fail-closed).
#   7. re-grant after deny -> User re-enabled on next login.
#   8. ABSENT exe_perms -> unchanged legacy behavior: first-user +
#      ERP_BOOTSTRAP_MODE path still promotes; _assert_provisioning_allowed
#      still gates provisioning.
#   9. multi-org token with exe_org_id UNSET -> treated as unmanaged (legacy).
