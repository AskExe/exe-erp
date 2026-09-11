#!/usr/bin/env python3
"""Exercise the real boot home-page loader without a database or bench install."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

# Isolate the production function from unrelated imports requiring a running bench.
source = Path(__file__).resolve().parents[1] / "frappe" / "boot.py"
tree = ast.parse(source.read_text())
loader = next(
	node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "add_home_page"
)


class Boot(dict):
	def __setattr__(self, key, value):
		self[key] = value


class TestDeskHomePage(unittest.TestCase):
	def run_loader(self, home="setup-wizard", complete=True, error=None, user="qa@example.invalid"):
		page = SimpleNamespace(name="desktop" if complete and home == "setup-wizard" else home)
		framework = SimpleNamespace(
			session=SimpleNamespace(user=user),
			db=SimpleNamespace(get_default=Mock(return_value=home)),
			is_setup_complete=Mock(return_value=complete),
			get_hooks=Mock(return_value=["setup.bundle.js"]),
			desk=SimpleNamespace(desk_page=SimpleNamespace(get=Mock(return_value=page, side_effect=error))),
			DoesNotExistError=LookupError,
			PermissionError=PermissionError,
			clear_last_message=Mock(),
		)
		scope = {"frappe": framework}
		exec(compile(ast.Module(body=[loader], type_ignores=[]), str(source), "exec"), scope)
		boot, docs = Boot(), []
		scope["add_home_page"](boot, docs)
		return framework, boot, docs

	def test_completed_setup_ignores_stale_wizard_home(self):
		framework, boot, docs = self.run_loader()
		framework.desk.desk_page.get.assert_called_once_with("desktop")
		self.assertEqual(boot["home_page"], "desktop")
		self.assertEqual(docs[0].name, "desktop")
		framework.get_hooks.assert_not_called()

	def test_incomplete_setup_keeps_wizard_and_assets(self):
		framework, boot, _docs = self.run_loader(complete=False)
		framework.desk.desk_page.get.assert_called_once_with("setup-wizard")
		self.assertEqual(boot["home_page"], "setup-wizard")
		self.assertEqual(boot["setup_wizard_requires"], ["setup.bundle.js"])

	def test_custom_home_is_preserved(self):
		framework, boot, _docs = self.run_loader(home="custom-dashboard")
		framework.desk.desk_page.get.assert_called_once_with("custom-dashboard")
		self.assertEqual(boot["home_page"], "custom-dashboard")

	def test_missing_and_forbidden_home_keep_safe_fallback(self):
		for error in (LookupError(), PermissionError()):
			with self.subTest(error=type(error)):
				framework, boot, docs = self.run_loader(home="restricted", error=error)
				self.assertEqual(boot["home_page"], "desktop")
				self.assertEqual(docs, [])
				framework.clear_last_message.assert_called_once()

	def test_guest_does_not_load_desk(self):
		framework, boot, docs = self.run_loader(user="Guest")
		framework.db.get_default.assert_not_called()
		self.assertEqual(boot, {})
		self.assertEqual(docs, [])


if __name__ == "__main__":
	unittest.main()
