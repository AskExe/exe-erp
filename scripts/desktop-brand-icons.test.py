"""Bundled desktop artwork uses Exe colors; customer images remain separate."""

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesktopBrandTests(unittest.TestCase):
	def test_all_bundled_variants_have_contrasting_brand_colors(self):
		files = []
		for directory in ("frappe/public", "apps/erpnext/erpnext/public"):
			files.extend((ROOT / directory / "icons/desktop_icons").glob("*/*.svg"))
		self.assertGreater(len(files), 50)
		for path in files:
			with self.subTest(icon=path):
				svg = ET.parse(path).getroot()
				paints = {
					value.upper()
					for node in svg.iter()
					for key, value in node.attrib.items()
					if key in ("fill", "stroke") and value != "none"
				}
				expected = {"#F5D76E", "#0F0E1A" if path.parent.name == "solid" else "#1A1832"}
				self.assertEqual(paints, expected)
				self.assertFalse(any("fill-opacity" in node.attrib for node in svg.iter()))

	def test_product_logos_share_the_canonical_exe_intelligence_artwork(self):
		canonical = (ROOT / "frappe/public/images/exe-icons/exe-favicon.svg").read_bytes()
		for relative in (
			"apps/erpnext/erpnext/public/images/erpnext-logo.svg",
			"frappe/public/images/frappe-framework-logo.svg",
			"frappe/public/images/exe-erp-logo.svg",
		):
			with self.subTest(logo=relative):
				self.assertEqual((ROOT / relative).read_bytes(), canonical)

	def test_custom_image_branch_is_not_filtered_or_recolored(self):
		template = (ROOT / "frappe/public/js/frappe/ui/desktop_icon.html").read_text()
		self.assertIn(
			'src="{{ frappe.utils.desktop_brand_image_url(icon.logo_url || icon.icon_image) }}"', template
		)
		css = (ROOT / "frappe/desk/page/desktop/desktop.css").read_text()
		self.assertNotIn("filter:", css.split("/* Exe desktop palette:")[1])
		self.assertIn(".icon-container:has(.desktop-alphabet)", css)


if __name__ == "__main__":
	unittest.main()
