"""Real two-site Frappe/ERPnext ACL regression; central envelopes are controlled.

Run only in a disposable Bench with the exact reviewed source installed. No
Frappe, database, permission, or model methods are replaced. The caller owns
and removes both sites/databases, Redis and their storage after this command.
This is not a GoTrue, private broker, browser or native write-scope proof.
"""

import argparse
import json
import re
import sys
import time
import unittest
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import frappe
from frappe import company_session as adapter
from frappe.permissions import add_permission


SUBJECTS = (
	"00000000-0000-4000-8000-000000000011",
	"00000000-0000-4000-8000-000000000012",
	"00000000-0000-4000-8000-000000000013",
)
COMPANIES = ("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
ROLE = "Company ACL Fixture Reader"


def connect(site):
	frappe.init(site, sites_path=str(ARGS.sites_path), force=True)
	frappe.connect(set_admin_as_user=False)
	frappe.set_user("Administrator")


def close():
	try:
		if getattr(frappe.local, "db", None):
			frappe.db.rollback()
	finally:
		frappe.destroy()


def verify_sites():
	# A site name or allow_tests alone is not authority to modify a real site.
	# The caller must prebind the same random fixture marker in each fresh config.
	if not re.fullmatch(r"[a-f0-9]{32}", ARGS.fixture_id):
		raise ValueError("Random fixture ID required")
	if ARGS.site_a == ARGS.site_b:
		raise ValueError("Two separate native sites required")
	databases = set()
	for site in (ARGS.site_a, ARGS.site_b):
		if not re.fullmatch(r"erp\.acl-[ab]-[a-f0-9]{12}\.example\.test", site):
			raise ValueError("Explicit disposable native site required")
		path = ARGS.sites_path / site / "site_config.json"
		if path.resolve() != path:
			raise ValueError("Canonical fixture config required")
		with path.open() as stream:
			config = json.load(stream)
		if config.get("allow_tests") is not True or config.get("company_acl_fixture") != ARGS.fixture_id:
			raise ValueError("Site is not bound to this disposable fixture")
		if config.get("db_type") != "postgres" or not config.get("db_name"):
			raise ValueError("Actual PostgreSQL native site required")
		databases.add(config["db_name"])
		connect(site)
		try:
			if "erpnext" not in frappe.get_installed_apps():
				raise ValueError("Real ERPnext installation required")
			if frappe.db.count("User", {"email": ["like", "%@native-acl.example.test"]}):
				raise ValueError("Fixture users already exist")
		finally:
			close()
	if len(databases) != 2:
		raise ValueError("Sites must use distinct native databases")


def setup(site, plane):
	connect(site)
	try:
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert()
		add_permission("Customer", ROLE, ptype="read")
		frappe.clear_cache(doctype="Customer")
		users = ["aowner", "amember"] if plane == "a" else ["bowner"]
		for user in users:
			frappe.get_doc({"doctype": "User", "email": user + "@native-acl.example.test",
				"first_name": user, "enabled": 1, "user_type": "System User",
				"send_welcome_email": 0, "roles": [{"role": ROLE}]}).insert()
		# Fresh install-app has not run the interactive setup wizard. Create its
		# two required tree roots through the real ORM before fixture leaves.
		for doctype, field, name in (("Customer Group", "customer_group_name", "All Customer Groups"),
			("Territory", "territory_name", "All Territories")):
			if not frappe.db.exists(doctype, name):
				frappe.get_doc({"doctype": doctype, field: name, "is_group": 1}).insert()
		group = frappe.get_doc({"doctype": "Customer Group", "customer_group_name": "ACL " + plane,
			"parent_customer_group": "All Customer Groups", "is_group": 0}).insert()
		territory = frappe.get_doc({"doctype": "Territory", "territory_name": "ACL " + plane,
			"parent_territory": "All Territories", "is_group": 0}).insert()
		for suffix in ("visible", "restricted"):
			frappe.get_doc({"doctype": "Customer", "customer_name": plane + "-" + suffix,
				"customer_type": "Individual", "customer_group": group.name,
				"territory": territory.name}).insert(set_name="ACL-" + plane + "-" + suffix)
		if plane == "a":
			frappe.get_doc({"doctype": "User Permission", "user": "amember@native-acl.example.test",
				"allow": "Customer", "for_value": "ACL-a-visible", "apply_to_all_doctypes": 1}).insert()
		frappe.db.commit()
	finally:
		close()


def config(plane):
	index = 0 if plane == "a" else 1
	users = {SUBJECTS[0]: "aowner@native-acl.example.test", SUBJECTS[1]: "amember@native-acl.example.test"} if plane == "a" else {SUBJECTS[2]: "bowner@native-acl.example.test"}
	return adapter.Config(COMPANIES[index], ARGS.site_a if plane == "a" else ARGS.site_b,
		"00000000-0000-4000-8000-000000000021", "00000000-0000-4000-8000-000000000022",
		"erp-" + plane, "erp-" + plane, "https://" + (ARGS.site_a if plane == "a" else ARGS.site_b),
		"http://127.0.0.1", "fixture-not-used", MappingProxyType(users), "0" * 64)


def authority(c, subject):
	# Explicit controlled central state, never represented as a broker response.
	return dict(version=1, subject_id=subject, company_id=c.company_id, product="erp",
		resource_kind="erp-site", binding_id=c.binding_id, native_id=c.site,
		generation_id=c.generation_id, authz_epoch="1", audience=c.audience,
		scopes=["erp:read"], current_role="owner" if subject in (SUBJECTS[0], SUBJECTS[2]) else "member",
		technical_status="accepted", subscription_entitled=True)


def read(plane, subject, name=None):
	c = config(plane)
	try:
		return adapter.native_read_current(SimpleNamespace(), c, str(ARGS.sites_path), authority(c, subject),
			"read", ("Customer", name), {} if name else {"limit_page_length": 100, "limit_start": 0},
			time.time() + 9, time.monotonic() + 9)
	finally:
		close()


def mutate(site, operation):
	connect(site)
	try:
		operation()
		frappe.db.commit()
	finally:
		close()


class NativeACL(unittest.TestCase):
	def test_owner_and_member_use_actual_row_permissions(self):
		self.assertEqual({r["name"] for r in read("a", SUBJECTS[0])}, {"ACL-a-visible", "ACL-a-restricted"})
		self.assertEqual({r["name"] for r in read("a", SUBJECTS[1])}, {"ACL-a-visible"})
		self.assertEqual(read("a", SUBJECTS[1], "ACL-a-visible")["name"], "ACL-a-visible")
		with self.assertRaises((adapter.Denied, frappe.PermissionError)):
			read("a", SUBJECTS[1], "ACL-a-restricted")

	def test_separate_database_and_cross_company_principal(self):
		self.assertEqual({r["name"] for r in read("b", SUBJECTS[2])}, {"ACL-b-visible", "ACL-b-restricted"})
		for plane, subject in (("a", SUBJECTS[2]), ("b", SUBJECTS[0]), ("b", SUBJECTS[1])):
			with self.assertRaises(adapter.Denied):
				read(plane, subject)
		with self.assertRaises(frappe.DoesNotExistError):
			read("b", SUBJECTS[2], "ACL-a-visible")

	def test_warmed_native_role_revocation_is_current(self):
		read("a", SUBJECTS[1], "ACL-a-visible")
		def remove_role():
			user = frappe.get_doc("User", "amember@native-acl.example.test")
			user.set("roles", [])
			user.save()
		mutate(ARGS.site_a, remove_role)
		try:
			with self.assertRaises(adapter.Denied):
				read("a", SUBJECTS[1])
			self.assertEqual(read("a", SUBJECTS[0], "ACL-a-visible")["name"], "ACL-a-visible")
		finally:
			def restore():
				user = frappe.get_doc("User", "amember@native-acl.example.test")
				user.append("roles", {"role": ROLE})
				user.save()
			mutate(ARGS.site_a, restore)

	def test_current_disabled_user_is_denied(self):
		read("a", SUBJECTS[1], "ACL-a-visible")
		mutate(ARGS.site_a, lambda: frappe.db.set_value("User", "amember@native-acl.example.test", "enabled", 0))
		try:
			with self.assertRaises(adapter.Denied):
				read("a", SUBJECTS[1])
		finally:
			mutate(ARGS.site_a, lambda: frappe.db.set_value("User", "amember@native-acl.example.test", "enabled", 1))

	def test_current_user_permission_ignores_stale_real_redis(self):
		from frappe.core.doctype.user_permission.user_permission import get_user_permissions

		connect(ARGS.site_a)
		try:
			old = get_user_permissions("amember@native-acl.example.test")
		finally:
			close()
		def replace_permission():
			name = frappe.db.get_value("User Permission", {"user": "amember@native-acl.example.test", "allow": "Customer"})
			permission = frappe.get_doc("User Permission", name)
			permission.for_value = "ACL-a-restricted"
			permission.save()
			# Actual Redis stale value, not a substituted cache/database implementation.
			frappe.cache.hset("user_permissions", "amember@native-acl.example.test", old)
		mutate(ARGS.site_a, replace_permission)
		try:
			self.assertEqual({r["name"] for r in read("a", SUBJECTS[1])}, {"ACL-a-restricted"})
			with self.assertRaises((adapter.Denied, frappe.PermissionError)):
				read("a", SUBJECTS[1], "ACL-a-visible")
		finally:
			def restore():
				name = frappe.db.get_value("User Permission", {"user": "amember@native-acl.example.test", "allow": "Customer"})
				permission = frappe.get_doc("User Permission", name)
				permission.for_value = "ACL-a-visible"
				permission.save()
			mutate(ARGS.site_a, restore)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--sites-path", type=Path, required=True)
	parser.add_argument("--site-a", required=True)
	parser.add_argument("--site-b", required=True)
	parser.add_argument("--fixture-id", required=True)
	ARGS = parser.parse_args()
	ARGS.sites_path = ARGS.sites_path.absolute()
	if ARGS.sites_path.resolve() != ARGS.sites_path or not ARGS.sites_path.is_dir():
		raise ValueError("Canonical private sites directory required")
	verify_sites()
	setup(ARGS.site_a, "a")
	setup(ARGS.site_b, "b")
	result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NativeACL))
	print("Scope: actual ERPnext/PostgreSQL native ACL; controlled central envelopes; caller must remove both fixture sites.")
	sys.exit(0 if result.wasSuccessful() and result.testsRun == 5 and not result.skipped
		and not result.expectedFailures and not result.unexpectedSuccesses else 1)
