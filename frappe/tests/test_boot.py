from unittest.mock import patch

import frappe
from frappe.boot import get_user_pages_or_reports
from frappe.desk.doctype.note.note import _get_unseen_notes, get_unseen_notes, mark_as_seen
from frappe.sessions import get_default_desk_theme
from frappe.tests import IntegrationTestCase, UnitTestCase


class TestBootData(IntegrationTestCase):
	def test_get_unseen_notes(self):
		frappe.db.delete("Note")
		frappe.db.delete("Note Seen By")
		note = frappe.get_doc(
			{
				"doctype": "Note",
				"title": "Test Note",
				"notify_on_login": 1,
				"content": "Test Note 1",
				"public": 1,
			}
		)
		note.insert()

		frappe.set_user("test@example.com")
		_get_unseen_notes()
		unseen_notes = [d.title for d in get_unseen_notes()]
		self.assertListEqual(unseen_notes, ["Test Note"])

		mark_as_seen(note.name)
		unseen_notes = [d.title for d in get_unseen_notes()]
		self.assertListEqual(unseen_notes, [])


class TestPermissionQueries(IntegrationTestCase):
	@classmethod
	def setUpClass(cls) -> None:
		cls.enterClassContext(cls.enable_safe_exec())
		return super().setUpClass()

	def test_get_user_pages_or_reports_with_permission_query(self):
		# Create a ToDo custom report with admin user
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Report",
				"ref_doctype": "ToDo",
				"report_name": "Test Admin Report",
				"report_type": "Report Builder",
				"is_standard": "No",
			}
		).insert()

		# Add permission query such that each user can only see their own custom reports
		frappe.get_doc(
			doctype="Server Script",
			name="test_report_permission_query",
			script_type="Permission Query",
			reference_doctype="Report",
			script="""conditions = f"(`tabReport`.is_standard = 'Yes' or `tabReport`.owner = '{frappe.session.user}')"
				""",
		).insert()

		# Create a ToDo custom report with test user
		frappe.set_user("test@example.com")
		frappe.get_doc(
			{
				"doctype": "Report",
				"ref_doctype": "ToDo",
				"report_name": "Test User Report",
				"report_type": "Report Builder",
				"is_standard": "No",
			}
		).insert(ignore_permissions=True)

		get_user_pages_or_reports("Report")
		allowed_reports = frappe.cache.get_value("has_role:Report", user=frappe.session.user)

		# Test user must not see admin user's report
		self.assertNotIn("Test Admin Report", allowed_reports)
		self.assertIn("Test User Report", allowed_reports)


class TestDefaultDeskTheme(UnitTestCase):
	"""The desk defaults to the brand (dark) theme for users who never picked one."""

	def test_default_is_dark(self):
		with patch.dict(frappe.conf, {}, clear=False):
			frappe.conf.pop("default_desk_theme", None)
			self.assertEqual(get_default_desk_theme(), "Dark")

	def test_site_config_overrides_default(self):
		with patch.dict(frappe.conf, {"default_desk_theme": "Light"}):
			self.assertEqual(get_default_desk_theme(), "Light")

	def test_unknown_value_falls_back_to_default(self):
		with patch.dict(frappe.conf, {"default_desk_theme": "Neon"}):
			self.assertEqual(get_default_desk_theme(), "Dark")
