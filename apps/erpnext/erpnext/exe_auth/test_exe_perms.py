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

	def testSingleOrgWithoutConfigDeniesFail(self):
		# SECURITY: a single-org token must NOT auto-resolve when exe_org_id is
		# unset — inferring org X here would grant org-X caps on tenant Y.
		meta = {"exe_perms": {"orgs": {"solo": {"caps": ["erp:read"]}}}}
		org, status = ep.resolve_org_id(meta, None)
		self.assertIsNone(org)
		self.assertEqual(status, ep.ORG_DENY_UNRESOLVED)

	def testMultiOrgWithoutConfigDeniesFail(self):
		# Present claim + no configured org -> fail closed, never guess.
		org, status = ep.resolve_org_id(PER_ORG_META, None)
		self.assertIsNone(org)
		self.assertEqual(status, ep.ORG_DENY_UNRESOLVED)

	def testAbsentClaimIsUnmanaged(self):
		org, status = ep.resolve_org_id({}, None)
		self.assertIsNone(org)
		self.assertEqual(status, ep.ORG_UNMANAGED_ABSENT)

	def testLegacyFlatWithoutConfigDeniesFail(self):
		# Legacy flat shape is no longer allowed to self-resolve its org.
		org, status = ep.resolve_org_id(LEGACY_FLAT_META, None)
		self.assertIsNone(org)
		self.assertEqual(status, ep.ORG_DENY_UNRESOLVED)

	def testLegacyFlatResolvesWithConfig(self):
		org, status = ep.resolve_org_id(LEGACY_FLAT_META, "acme")
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
		d, status = ep.compute_decision(LEGACY_FLAT_META, "acme")
		self.assertEqual(status, ep.ORG_RESOLVED)
		self.assertEqual(d["level"], ep.LEVEL_ADMIN)
		self.assertIn("System Manager", d["roles"])

	def testAbsentIsUnmanagedNoDecision(self):
		d, status = ep.compute_decision({}, None)
		self.assertIsNone(d)
		self.assertEqual(status, ep.ORG_UNMANAGED_ABSENT)

	def testNoneAppMetadataUnmanaged(self):
		d, _status = ep.compute_decision(None, "acme")
		self.assertIsNone(d)

	def testMultiOrgWithoutConfigDeniesFail(self):
		# Present claim + exe_org_id unset -> DENY decision (fail closed),
		# not unmanaged/stale.
		d, status = ep.compute_decision(PER_ORG_META, None)
		self.assertIsNotNone(d)
		self.assertTrue(d["deny"])
		self.assertEqual(status, ep.ORG_DENY_UNRESOLVED)

	def testOrgConfiguredNoClaimDeniesFail(self):
		# Configured org, but this user has NO claim for it -> DENY (a removed
		# org claim is a downgrade, not a bypass to stale legacy roles).
		d, status = ep.compute_decision(PER_ORG_META, "nosuchorg")
		self.assertIsNotNone(d)
		self.assertTrue(d["deny"])
		self.assertEqual(status, ep.ORG_DENY_NO_CLAIM)

	def testRoleNoneIsManagedDeny(self):
		meta = {"exe_perms": {"orgs": {"acme": {"role": "none", "caps": ["erp:admin"]}}}}
		d, _status = ep.compute_decision(meta, "acme")
		# role "none" forces deny even if caps somehow present.
		self.assertTrue(d["deny"])
		self.assertEqual(d["level"], ep.LEVEL_NONE)

	def testEmptyErpCapsIsManagedDeny(self):
		meta = {"exe_perms": {"orgs": {"acme": {"role": "member", "caps": ["wiki:write"]}}}}
		d, _status = ep.compute_decision(meta, "acme")
		self.assertTrue(d["deny"])


class TestDenyDecision(unittest.TestCase):
	"""The fail-closed decision shape used for org cases that cannot bind."""

	def testDenyShapeIsFailClosed(self):
		d = ep.deny_decision()
		self.assertTrue(d["deny"])
		self.assertEqual(d["roles"], set())
		self.assertEqual(d["level"], ep.LEVEL_NONE)
		self.assertEqual(d["user_type"], ep.WEBSITE_USER_TYPE)

	def testDenyManagedAllowlistPreserved(self):
		# managed set still names the roles we own, so the disable path can
		# reason about removal scope consistently.
		d = ep.deny_decision()
		self.assertEqual(d["managed"], ep.managed_roles())


class TestFailClosedOrgScoping(unittest.TestCase):
	"""The 3-case org model (mirrors the wiki fix): absent->unmanaged,
	present+configured+claim->managed, everything-else->deny (fail closed)."""

	def testCase1AbsentIsUnmanagedLegacy(self):
		# No claim at all -> unmanaged (None), legacy behavior preserved.
		d, status = ep.compute_decision({}, "acme")
		self.assertIsNone(d)
		self.assertEqual(status, ep.ORG_UNMANAGED_ABSENT)

	def testCase2PresentConfiguredClaimedIsManaged(self):
		d, status = ep.compute_decision(PER_ORG_META, "acme")
		self.assertEqual(status, ep.ORG_RESOLVED)
		self.assertFalse(d["deny"])
		self.assertEqual(d["level"], ep.LEVEL_WRITE)

	def testCase3aPresentUnconfiguredDeniesFail(self):
		# The wrong-org hole: single-org token, exe_org_id unset -> DENY.
		meta = {"exe_perms": {"orgs": {"attacker": {"caps": ["erp:admin"]}}}}
		d, status = ep.compute_decision(meta, None)
		self.assertTrue(d["deny"])
		self.assertEqual(status, ep.ORG_DENY_UNRESOLVED)

	def testCase3bWrongOrgAdminDoesNotGrantHereFail(self):
		# erp:admin for org X must NOT yield admin on tenant Y (configured=Y).
		meta = {"exe_perms": {"orgs": {"orgx": {"caps": ["erp:admin"]}}}}
		d, status = ep.compute_decision(meta, "orgy")
		self.assertTrue(d["deny"])
		self.assertNotIn(ep.DEFAULT_ADMIN_ROLE, d["roles"])
		self.assertEqual(status, ep.ORG_DENY_NO_CLAIM)

	def testDowngradeRemovingOrgClaimIsDenyNotStaleFail(self):
		# A user whose org claim was REVOKED (present claim, but not for this
		# tenant) must be denied — never fall through to stale roles.
		meta = {"exe_perms": {"orgs": {"former": {"caps": ["erp:read"]}}}}
		d, status = ep.compute_decision(meta, "current")
		self.assertTrue(d["deny"])
		self.assertEqual(status, ep.ORG_DENY_NO_CLAIM)


class TestManagedDenyPersistenceIntent(unittest.TestCase):
	"""Pure-layer intent behind the api.py managed-deny persistence fix.

	The PURE mapping guarantees a deny DECISION is produced for every
	fail-closed case; api.py then commits enabled=0 + kills sessions BEFORE
	raising (that DB durability is bench-only — see plan at bottom)."""

	def testEveryFailClosedCaseYieldsDenyDecision(self):
		role_none = {"exe_perms": {"orgs": {"acme": {"role": "none", "caps": []}}}}
		empty_caps = {"exe_perms": {"orgs": {"acme": {"role": "m", "caps": ["wiki:x"]}}}}
		no_claim = PER_ORG_META  # configured org absent from claim
		for meta, org in ((role_none, "acme"), (empty_caps, "acme"), (no_claim, "zzz")):
			d, _ = ep.compute_decision(meta, org)
			self.assertIsNotNone(d)
			self.assertTrue(d["deny"])
			self.assertEqual(d["roles"], set())


class TestSubjectBinding(unittest.TestCase):
	"""P2: /user body email must be PRESENT and MATCH the submitted email
	before its roles are applied (fail closed on missing OR mismatched)."""

	def testMatchingEmailOk(self):
		self.assertTrue(ep.subject_binding_ok("user@acme.com", "user@acme.com"))

	def testCaseAndWhitespaceInsensitiveOk(self):
		self.assertTrue(ep.subject_binding_ok("  User@Acme.com ", "user@acme.com"))

	def testMismatchDeniedFail(self):
		self.assertFalse(ep.subject_binding_ok("attacker@acme.com", "victim@acme.com"))

	def testMissingGotrueEmailDeniedFail(self):
		# Malformed successful /user body: app_metadata present but NO email.
		self.assertFalse(ep.subject_binding_ok(None, "user@acme.com"))
		self.assertFalse(ep.subject_binding_ok("", "user@acme.com"))
		self.assertFalse(ep.subject_binding_ok("   ", "user@acme.com"))

	def testMissingSubmittedEmailDeniedFail(self):
		self.assertFalse(ep.subject_binding_ok("user@acme.com", None))


class TestShouldReenable(unittest.TestCase):
	"""P1: re-enable a disabled user ONLY when the managed system disabled
	them — never override a manual admin disable."""

	def testManagedDisabledIsReenabled(self):
		# Disabled by managed-deny (marker set), access re-granted -> re-enable.
		self.assertTrue(ep.should_reenable(currently_enabled=0, disabled_by_managed=True))

	def testManualDisableNotReenabledFail(self):
		# Disabled by an admin by hand (NO marker) -> must stay disabled.
		self.assertFalse(ep.should_reenable(currently_enabled=0, disabled_by_managed=False))

	def testEnabledUserNotTouched(self):
		# Already enabled -> no re-enable action regardless of marker.
		self.assertFalse(ep.should_reenable(currently_enabled=1, disabled_by_managed=True))
		self.assertFalse(ep.should_reenable(currently_enabled=1, disabled_by_managed=False))


class TestOAuthStateMatches(unittest.TestCase):
	"""P1 login-CSRF: SSO callback state must match the signed nonce cookie."""

	def testMatchingStateOk(self):
		self.assertTrue(ep.oauth_state_matches("abc123", "abc123"))

	def testMismatchDeniedFail(self):
		self.assertFalse(ep.oauth_state_matches("attacker", "victim"))

	def testMissingReceivedDeniedFail(self):
		self.assertFalse(ep.oauth_state_matches(None, "cookie"))
		self.assertFalse(ep.oauth_state_matches("", "cookie"))

	def testMissingExpectedDeniedFail(self):
		# No cookie present (e.g. cross-site GET / link-scanner) -> fail closed.
		self.assertFalse(ep.oauth_state_matches("urlstate", None))
		self.assertFalse(ep.oauth_state_matches("urlstate", ""))

	def testBothEmptyDeniedFail(self):
		self.assertFalse(ep.oauth_state_matches("", ""))
		self.assertFalse(ep.oauth_state_matches(None, None))


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
#      user_type == "System User", enabled == 1. NOTE: the reconcile runs while
#      the actor is still Guest, so _apply_managed_roles sets
#      user_doc.flags.ignore_permissions=True BEFORE add_roles/remove_roles (whose
#      internal User.save() would otherwise be permission-checked as Guest and
#      FAIL). Assert first login of a fresh managed user actually GAINS the roles
#      (regression guard for the Guest-context role-grant failure).
#   2. managed erp:write  -> gains write bundle, NOT "System Manager".
#   3. managed erp:read   -> no desk roles, user_type == "Website User".
#   4. downgrade admin->write on second login -> "System Manager" REMOVED,
#      write bundle retained (remove_roles scoped to managed allowlist).
#   5. hand-assigned unmanaged role (e.g. "Accounts Manager" or "HR User")
#      set outside caps -> SURVIVES reconcile (never stripped).
#   6. managed-deny (role "none" / empty erp caps) -> User.enabled == 0 AND it
#      PERSISTS after the AuthenticationError (frappe.db.commit before raise;
#      the request rollback must NOT re-enable the user). Verify enabled == 0
#      by reloading the doc in a fresh transaction.
#   7. re-grant after deny -> User re-enabled on next login.
#   8. ABSENT exe_perms -> unchanged legacy behavior: first-user +
#      ERP_BOOTSTRAP_MODE path still promotes; _assert_provisioning_allowed
#      still gates provisioning.
#   9. present exe_perms with exe_org_id UNSET -> DENY (fail closed), NOT legacy
#      login with stale roles (the wrong-org hole is closed).
#  10. exe_org_id configured but token's exe_perms has NO claim for it -> DENY
#      (removing a user's org claim is a downgrade, not a bypass).
#  11. FAIL-CLOSED ON /user ERROR: /token 200 but /user 5xx/network-error, with
#      exe_org_id CONFIGURED -> login raises AuthenticationError (no stale sid).
#      With exe_org_id UNSET (legacy tenant) -> login still succeeds.
#  12. SUBJECT BINDING: /user returns a DIFFERENT email, OR a successful body
#      with app_metadata but NO email -> login raises AuthenticationError; no
#      roles applied. (subject_binding_ok is unit-tested; the api.py wiring +
#      the gotrue_fetched skip on the legacy fail-open path need bench.)
#  13. managed-deny SESSION KILL (needs live Redis backend): a user with an
#      existing CACHED Frappe session who is then denied -> after deny, both
#      the Sessions DB row AND the cached session (frappe.cache hget "session"
#      <sid>) are gone, so resume() cannot revive it. Verify via
#      clear_sessions(user, force=True) against real Redis — a bare DB delete
#      would leave the cached session usable until cache expiry.
#  14. MANUAL-DISABLE OVERRIDE (P1): admin sets User.enabled=0 by hand (no
#      managed marker). Managed login whose caps still grant access -> user is
#      NOT re-enabled (should_reenable False). Contrast: managed-denied user
#      (marker set) then re-granted -> IS re-enabled and marker cleared.
#  15. BOOTSTRAP RACE (P1): two concurrent first-logins (ERP_BOOTSTRAP_MODE=1,
#      allow-all domains) -> exactly ONE gets System Manager. The filelock +
#      one-shot _BOOTSTRAP_FLAG (committed inside the lock) serialize the
#      claim. Multi-server needs a shared lock / DB unique constraint.
#  16. CSRF STATE (P1 login-CSRF): callback with no/mismatched `state` vs the
#      exe_sso_state cookie -> AuthenticationError (when
#      gotrue_require_callback_state is on). gotrue_login_start sets the cookie
#      + redirects with matching state; happy path logs in. NOTE: token-in-URL
#      transport + session-outlives-JWT still need auth-flow coordination.
