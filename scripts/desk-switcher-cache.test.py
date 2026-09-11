#!/usr/bin/env python3
"""The real desk asset version must invalidate cached switcher code on updates."""

import ast
import unittest
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frappe/www/desk.py"
TREE = ast.parse(SOURCE.read_text())
FUNCTION = next(
	node
	for node in TREE.body
	if isinstance(node, ast.FunctionDef) and node.name == "get_service_switcher_version"
)


class SwitcherCacheVersion(unittest.TestCase):
	def test_version_tracks_bytes_and_not_file_timestamps(self):
		with TemporaryDirectory() as directory:
			path = Path(directory) / "switcher.js"
			path.write_bytes(b"old component")
			framework = SimpleNamespace(get_app_path=lambda *parts: path)
			scope = {"frappe": framework, "Path": Path, "sha256": sha256}
			exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(SOURCE), "exec"), scope)
			version = scope["get_service_switcher_version"]
			old = version()
			self.assertRegex(old, r"^[a-f0-9]{16}$")
			path.touch()
			self.assertEqual(version(), old)
			path.write_bytes(b"new component")
			self.assertNotEqual(version(), old)
			self.assertEqual(version(), sha256(path.read_bytes()).hexdigest()[:16])

	def test_desk_context_and_script_use_the_content_version(self):
		context = next(
			node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == "get_context"
		)
		pairs = [
			(key, value)
			for node in ast.walk(context)
			if isinstance(node, ast.Dict)
			for key, value in zip(node.keys, node.values, strict=True)
		]
		value = next(
			value
			for key, value in pairs
			if isinstance(key, ast.Constant) and key.value == "service_switcher_version"
		)
		self.assertEqual(ast.unparse(value), "get_service_switcher_version()")
		template = (ROOT / "frappe/www/desk.html").read_text()
		self.assertIn(
			'src="/assets/frappe/js/exe-service-switcher.js?v={{ service_switcher_version }}"', template
		)
		self.assertNotIn('src="/assets/frappe/js/exe-service-switcher.js"', template)


if __name__ == "__main__":
	unittest.main()
