"""Fresh-site provisioning guards, without connecting to a live bench or DB."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location("hosted_site_test_subject", Path(__file__).with_name("hosted_site.py"))
subject = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(subject)


class TestHostedSitePlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = Path(self.tmp.name) / "sites"
        self.sites.mkdir()
        self.base = self.sites / "erp.example.com"
        self.base.mkdir()
        self.config = {"db_name": "private_erp", "db_user": "private_erp", "exe_org_id": "private", "db_password": "fixture-only-not-a-secret"}
        (self.base / "site_config.json").write_text(json.dumps(self.config))
        self.spec = {"site": "erp-demo.example.com", "base_site": "erp.example.com", "database": "demo_erp", "org_id": "demo", "auth_url": "https://auth.example.com"}

    def tearDown(self):
        self.tmp.cleanup()

    def testNewSiteLoggerWorksFromWebAndBenchWorkingDirectories(self):
        from logging.handlers import RotatingFileHandler

        site = self.sites / self.spec["site"]
        site.mkdir()
        (site / "site_config.json").write_text("{}")
        before = (self.base / "site_config.json").read_bytes()
        subject.prepare_site_log_directories(self.sites, self.spec["site"])
        for root in (self.sites, self.sites.parent):
            handler = RotatingFileHandler(root / self.spec["site"] / "logs" / "frappe.log")
            handler.close()
        subject.prepare_site_log_directories(self.sites, self.spec["site"])
        self.assertEqual((self.base / "site_config.json").read_bytes(), before)
        self.assertFalse((self.sites.parent / self.spec["base_site"]).exists())

    def testMissingOrSymlinkSiteNeverCreatesRuntimeDirectories(self):
        with self.assertRaises(ValueError):
            subject.prepare_site_log_directories(self.sites, self.spec["site"])
        (self.sites / self.spec["site"]).symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            subject.prepare_site_log_directories(self.sites, self.spec["site"])
        self.assertFalse((self.sites.parent / self.spec["site"]).exists())

    def testDryPlanPreservesExistingSiteAndDoesNotEmitCredentials(self):
        before = (self.base / "site_config.json").read_bytes()
        result, digest = subject.plan(self.spec, self.sites)
        self.assertEqual(before, (self.base / "site_config.json").read_bytes())
        self.assertEqual(list(self.sites.iterdir()), [self.base])
        self.assertNotIn("fixture-only-not-a-secret", json.dumps(result))
        self.assertEqual(len(digest), 64)
        self.assertEqual(subject.plan(self.spec, self.sites), (result, digest))

    def testProtectedBaseChangeInvalidatesReviewedPlan(self):
        _, before = subject.plan(self.spec, self.sites)
        (self.base / "site_config.json").write_text(json.dumps({**self.config, "exe_org_id": "renamed"}))
        _, after = subject.plan(self.spec, self.sites)
        self.assertNotEqual(before, after)

    def testExistingTargetOrSymlinkIsNeverReplaced(self):
        target = self.sites / self.spec["site"]
        target.mkdir()
        with self.assertRaises(ValueError):
            subject.plan(self.spec, self.sites)
        target.rmdir()
        target.symlink_to(self.sites / "does-not-exist")
        with self.assertRaises(ValueError):
            subject.plan(self.spec, self.sites)

    def testAnyExistingDatabaseOrLoginCannotBeReused(self):
        for key in ("db_name", "db_user"):
            another = self.sites / "another.example.com"
            another.mkdir(exist_ok=True)
            (another / "site_config.json").write_text(json.dumps({key: "demo_erp"}))
            with self.assertRaises(ValueError):
                subject.plan(self.spec, self.sites)

    def testSameOrgCannotBeNewDemoTenant(self):
        with self.assertRaises(ValueError):
            subject.plan({**self.spec, "org_id": "private"}, self.sites)

    def testUnboundBaseTenantCannotBeUsed(self):
        (self.base / "site_config.json").write_text(json.dumps({"db_name": "private_erp"}))
        with self.assertRaises(ValueError):
            subject.plan(self.spec, self.sites)

    def testUnsafeIdentityAndExternalAuthAreRefused(self):
        invalid = (
            {"site": "../erp.example.com"}, {"site": "ERP-demo.example.com"},
            {"site": "erp.example.com"}, {"site": "erp-demo.attacker.com"},
            {"site": "auth.example.com"}, {"site": "a" * 64 + ".example.com"},
            {"database": "private_erp; DROP DATABASE anything"}, {"org_id": ".."},
            {"auth_url": "http://auth.example.com"}, {"auth_url": "https://auth.attacker.com"},
            {"auth_url": "https://user@auth.example.com"}, {"auth_url": "https://auth.example.com:443"},
            {"auth_url": "https://auth.example.com/path"}, {"password": "unexpected"},
        )
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                subject.plan({**self.spec, **fields}, self.sites)

    def testViewerAllowlistExcludesCredentialAndControlRecords(self):
        denied = {"User", "Role", "DocPerm", "Custom DocPerm", "System Settings", "File", "Communication"}
        self.assertFalse(denied & set(subject.VIEWER_DOCTYPES))
        self.assertIn("create", subject.WRITE_PERMISSIONS)
        self.assertIn("delete", subject.WRITE_PERMISSIONS)
        self.assertIn("submit", subject.WRITE_PERMISSIONS)


class TestFreshPostgresCreation(unittest.TestCase):
    def load_setup(self, existing=False, race=None):
        import sys
        import types
        from unittest import mock

        class Collision(Exception):
            pass

        class Connection:
            def __init__(self):
                self.statements = []
                self.created_user = False
                self.created_db = False

            def commit(self):
                pass

            def close(self):
                pass

            def sql(self, query, values=None, **kwargs):
                self.statements.append((query, values))
                if query.startswith("SELECT 1 FROM pg_database"):
                    return [(1,)] if existing else []
                if query.startswith("CREATE USER"):
                    if race == "role":
                        raise Collision("Concurrent login already exists")
                    self.created_user = True
                if query.startswith("CREATE DATABASE"):
                    if race == "database":
                        raise Collision("Concurrent database already exists")
                    self.created_db = True
                if query.startswith("SHOW server_version_num"):
                    return [{"server_version_num": "150001"}]
                return []

        root, child = Connection(), Connection()
        frappe = types.ModuleType("frappe")
        frappe.conf = types.SimpleNamespace(db_name="demo_erp", db_user="demo_erp", db_password="fixture-password")
        frappe.database = types.ModuleType("frappe.database")
        frappe.database.get_db = lambda **kwargs: child
        frappe.conf.db_socket = None
        frappe.conf.db_host = "localhost"
        frappe.conf.db_port = 5432
        frappe.flags = types.SimpleNamespace(root_login="operator", root_password="fixture-root")
        child.db_schema = "public"

        def throw(message):
            raise Collision(message)

        frappe.throw = throw
        db_manager = types.ModuleType("frappe.database.db_manager")
        db_manager.DbManager = object
        utils = types.ModuleType("frappe.utils")
        utils.cint = int
        modules = {"frappe": frappe, "frappe.database": frappe.database,
                   "frappe.database.db_manager": db_manager, "frappe.utils": utils}
        source = Path(__file__).parents[4] / "frappe" / "database" / "postgres" / "setup_db.py"
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location("fresh_postgres_under_test", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        module.get_root_connection = lambda: root
        return module, root, child, Collision

    def testFreshCreationNeverDropsOrRotatesAnExistingLogin(self):
        module, root, _, _ = self.load_setup()
        module.setup_database(require_new_database=True)
        self.assertTrue(root.created_user)
        self.assertTrue(root.created_db)
        self.assertFalse(any(q.startswith(("DROP ", "ALTER USER")) for q, _ in root.statements))
        create_user = next((q, values) for q, values in root.statements if q.startswith("CREATE USER"))
        self.assertNotIn("fixture-password", create_user[0])
        self.assertEqual(create_user[1], ("fixture-password",))

    def testExistingDatabaseOrRoleRefusesBeforeAnyDdl(self):
        module, root, _, collision = self.load_setup(existing=True)
        with self.assertRaises(collision):
            module.setup_database(require_new_database=True)
        self.assertFalse(root.created_user or root.created_db)
        self.assertFalse(any(q.startswith(("DROP ", "ALTER ", "CREATE ")) for q, _ in root.statements))

    def testConcurrentCollisionNeverDeletesOrChangesForeignDatabaseLogin(self):
        for race in ("role", "database"):
            module, root, _, collision = self.load_setup(race=race)
            with self.subTest(race=race), self.assertRaises(collision):
                module.setup_database(require_new_database=True)
            self.assertFalse(root.created_db)
            self.assertFalse(any(q.startswith(("DROP ", "ALTER USER")) for q, _ in root.statements))


class TestAutomaticRoleRestrictions(unittest.TestCase):
    def fixture(self):
        import types

        class Permission(dict):
            def set(self, key, value):
                self[key] = value

            def save(self):
                pass

        permissions = {
            "todo-all": Permission(parent="ToDo", role="All", read=1, create=1, write=1, delete=1),
            "file-all": Permission(parent="File", role="All", read=1, create=1, write=1, delete=1),
            "desk-auto": Permission(parent="Desktop Layout", role="Desk User", read=1, write=1),
            "todo-admin": Permission(parent="ToDo", role="System Manager", read=1, create=1, write=1),
        }
        frappe = types.ModuleType("frappe")
        frappe.conf = {"exe_hosted_site_mode": "synthetic_demo", "exe_org_id": "demo", "gotrue_url": "http://gotrue:9999"}
        frappe.session = types.SimpleNamespace(user="Administrator")

        def get_all(doctype, filters, pluck):
            allowed = filters["role"][1]
            rows = [(name, p) for name, p in permissions.items()
                    if p["role"] in allowed and ("parent" not in filters or p["parent"] == filters["parent"])]
            return [name if pluck == "name" else p[pluck] for name, p in rows]

        frappe.get_all = get_all
        frappe.get_doc = lambda doctype, name: permissions[name]
        native_permissions = types.ModuleType("frappe.permissions")
        native_permissions.setup_custom_perms = lambda parent: None
        return frappe, native_permissions, permissions

    def testInheritedWritesAndFileAccessDeniedWithoutRemovingOwnerPrivileges(self):
        import sys
        from unittest import mock

        frappe, native_permissions, rows = self.fixture()
        owner_before = dict(rows["todo-admin"])
        with mock.patch.dict(sys.modules, {"frappe": frappe, "frappe.permissions": native_permissions}):
            subject.restrict_automatic_permissions()
        for name in ("todo-all", "file-all", "desk-auto"):
            self.assertFalse(any(rows[name].get(flag) for flag in subject.WRITE_PERMISSIONS))
        self.assertEqual(rows["file-all"]["read"], 0)
        self.assertEqual(rows["todo-all"]["read"], 1)
        self.assertEqual(rows["desk-auto"]["read"], 1)
        self.assertEqual(dict(rows["todo-admin"]), owner_before)

    def testPrivateSiteOrNonAdministratorRefusesBeforePermissionChanges(self):
        import sys
        from unittest import mock

        for mode, user in ((None, "Administrator"), ("synthetic_demo", "viewer@example.com")):
            frappe, native_permissions, rows = self.fixture()
            frappe.conf["exe_hosted_site_mode"] = mode
            frappe.session.user = user
            before = {name: dict(p) for name, p in rows.items()}
            with mock.patch.dict(sys.modules, {"frappe": frappe, "frappe.permissions": native_permissions}), self.assertRaises(ValueError):
                subject.restrict_automatic_permissions()
            self.assertEqual({name: dict(p) for name, p in rows.items()}, before)
