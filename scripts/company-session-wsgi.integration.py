"""Real ERP WSGI/native ACL with a controlled private loopback authority.

No production handler, private_call, permission, database or model is replaced.
Run once for plane A (creates fixture records), then once for plane B.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from werkzeug.test import Client
from werkzeug.wrappers import Response

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("native_fixture", ROOT / "scripts/company-session-native.integration.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
frappe, adapter = fixture.frappe, fixture.adapter
TOKENS = {"exs_" + char * 43: subject for char, subject in zip("abc", fixture.SUBJECTS, strict=True)}
SECRET = "s" * 43
STATE = {"calls": 0, "at": 0, "action": None, "errors": [], "foreign_company": False}


class Authority(BaseHTTPRequestHandler):
	def log_message(self, *_):
		pass

	def do_POST(self):
		try:
			self.connection.settimeout(3)
			length = int(self.headers.get("Content-Length", "0"))
			if not 0 < length <= 256:
				raise ValueError("Bounded authority body required")
			if self.path != "/internal/session-broker/introspect":
				raise ValueError("Only exact introspection admitted")
			expected = "Basic " + base64.b64encode((CONFIG.client_id + ":" + SECRET).encode()).decode()
			if self.headers.get("Authorization") != expected or self.headers.get("Content-Type") != "application/json":
				raise ValueError("Exact native-client headers required")
			if self.headers.get("Host") != "127.0.0.1:" + str(self.server.server_port):
				raise ValueError("Exact loopback authority Host required")
			body = adapter.exact_json(self.rfile.read(length))
			if not isinstance(body, dict) or set(body) != {"session_token"} or body["session_token"] not in TOKENS:
				raise ValueError("Fixed controlled session required")
			STATE["calls"] += 1
			status = 200
			if STATE["calls"] == STATE["at"] and STATE["action"]:
				if STATE["action"] == "revoke":
					status = 403
				else:
					# This is an actual separate PostgreSQL connection/commit between
					# request reads, not a replacement of any native application method.
					fixture.mutate(CONFIG.site, STATE["action"])
			payload = fixture.authority(CONFIG, TOKENS[body["session_token"]]) if status == 200 else {"error": "revoked"}
			if status == 200 and STATE["foreign_company"]:
				payload["company_id"] = fixture.COMPANIES[1 if ARGS.plane == "a" else 0]
			data = json.dumps(payload, separators=(",", ":")).encode()
			self.send_response(status)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(data)))
			self.send_header("Connection", "close")
			self.end_headers()
			self.wfile.write(data)
		except Exception as error:
			STATE["errors"].append(type(error).__name__)
			self.send_error(500)


class WSGIACL(unittest.TestCase):
	def setUp(self):
		STATE.update(calls=0, at=0, action=None, errors=[], foreign_company=False)
		self.client = Client(APP.application, Response, use_cookies=False)

	def tearDown(self):
		self.assertEqual(STATE["errors"], [])

	def read(self, token="a", name=None, headers=None, host=None):
		path = "/api/resource/Customer" + ("/" + name if name else "?limit_page_length=100&limit_start=0")
		response = self.client.get(path, base_url="https://" + (host or CONFIG.site),
			headers={"Cookie": adapter.COOKIE + "=exs_" + token * 43, "Origin": CONFIG.origin, **(headers or {})},
			environ_overrides={"RAW_URI": path})
		response.get_data()
		response.close()  # actual after_response_wrapper/ClosingIterator cleanup
		self.assertEqual(response.headers.get("Cache-Control"), "no-store")
		return response

	def unavailable(self, response, status):
		self.assertEqual(response.status_code, status)
		self.assertEqual(response.get_json(), {"error": "unavailable"})

	def test_own_owner_member_reads(self):
		owner = "a" if ARGS.plane == "a" else "c"
		response = self.read(owner)
		self.assertEqual(response.status_code, 200)
		self.assertEqual({row["name"] for row in response.get_json()["data"]},
			{"ACL-" + ARGS.plane + "-visible", "ACL-" + ARGS.plane + "-restricted"})
		self.assertEqual(STATE["calls"], 3)
		if ARGS.plane == "a":
			STATE["calls"] = 0
			response = self.read("b")
			self.assertEqual(response.status_code, 200)
			self.assertEqual([row["name"] for row in response.get_json()["data"]], ["ACL-a-visible"])
			self.unavailable(self.read("b", "ACL-a-restricted"), 404)

	def test_foreign_principal_and_company_denied(self):
		foreign = ("c",) if ARGS.plane == "a" else ("a", "b")
		for token in foreign:
			self.unavailable(self.read(token), 401)
			self.assertEqual(STATE["calls"], 1)
			STATE["calls"] = 0
		other = ARGS.site_b if ARGS.plane == "a" else ARGS.site_a
		self.unavailable(self.read("a" if ARGS.plane == "a" else "c", host=other), 403)
		self.assertEqual(STATE["calls"], 0)
		STATE["foreign_company"] = True
		self.unavailable(self.read("a" if ARGS.plane == "a" else "c"), 503)
		self.assertEqual(STATE["calls"], 1)

	def test_host_origin_and_identity_headers_fail_before_authority(self):
		for headers, status in (({"Origin": "https://foreign.example.test"}, 403), ({"X-Frappe-User": "Administrator"}, 401),
			({"Authorization": "Bearer fixture"}, 401), ({"X-Frappe-Site-Name": ARGS.site_b}, 401)):
			self.unavailable(self.read(headers=headers), status)
			self.assertEqual(STATE["calls"], 0)

	def test_central_revocation_before_publication_discards_data(self):
		STATE.update(at=3, action="revoke")
		self.unavailable(self.read("a" if ARGS.plane == "a" else "c"), 401)
		self.assertEqual(STATE["calls"], 3)

	def test_native_role_revoked_between_reads_discards_data(self):
		retained = []
		def remove():
			filters = {"parent": "amember@native-acl.example.test", "parenttype": "User", "role": fixture.ROLE}
			name = frappe.db.get_value("Has Role", filters, "name")
			if not name:
				raise ValueError("Actual fixture role row required")
			retained.append(frappe.get_doc("Has Role", name).as_dict())
			# User.save enqueues create_contact, forbidden in company mode. Mutate
			# only this owned native child row; reads still use current real ACLs.
			frappe.db.delete("Has Role", {"name": name})
		STATE.update(at=2, action=remove)
		try:
			self.unavailable(self.read("b", "ACL-a-visible"), 403)
			self.assertEqual(STATE["calls"], 2)
		finally:
			def restore():
				for row in retained:
					frappe.get_doc(row).db_insert()
			fixture.mutate(ARGS.site_a, restore)

	def test_native_permission_changed_between_reads_discards_data(self):
		def change(value):
			name = frappe.db.get_value("User Permission", {"user": "amember@native-acl.example.test", "allow": "Customer"})
			doc = frappe.get_doc("User Permission", name)
			doc.for_value = value
			doc.save()
		STATE.update(at=2, action=lambda: change("ACL-a-restricted"))
		try:
			self.unavailable(self.read("b", "ACL-a-visible"), 404)
			self.assertEqual(STATE["calls"], 2)
		finally:
			fixture.mutate(ARGS.site_a, lambda: change("ACL-a-visible"))


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description=__doc__)
	for name in ("sites-path", "site-a", "site-b", "fixture-id"):
		parser.add_argument("--" + name, required=True)
	parser.add_argument("--plane", choices=("a", "b"), required=True)
	ARGS = parser.parse_args()
	ARGS.sites_path = Path(ARGS.sites_path).resolve(strict=True)
	fixture.ARGS = ARGS
	if ARGS.plane == "a":
		fixture.verify_sites()
		fixture.setup(ARGS.site_a, "a")
		fixture.setup(ARGS.site_b, "b")
	else:
		# Revalidate the same disposable marker without the first-run empty-user check.
		for site in (ARGS.site_a, ARGS.site_b):
			doc = json.loads((ARGS.sites_path / site / "site_config.json").read_text())
			if doc.get("company_acl_fixture") != ARGS.fixture_id or doc.get("allow_tests") is not True:
				raise ValueError("Bound owned fixture required")
	server = HTTPServer(("127.0.0.1", 0), Authority)
	server.timeout = 1
	thread = threading.Thread(target=server.serve_forever, daemon=True)
	with tempfile.TemporaryDirectory(prefix="erp-wsgi-authority-") as private:
		c = fixture.config(ARGS.plane)
		binding = {"version": 1, "company_id": c.company_id, "site": c.site, "binding_id": c.binding_id,
			"generation_id": c.generation_id, "audience": c.audience,
			"subjects": [{"subject_id": subject, "native_user": user} for subject, user in c.subjects.items()]}
		secret, bindings = Path(private) / "secret", Path(private) / "bindings.json"
		secret.write_text(SECRET)
		bindings.write_text(json.dumps(binding))
		for path in (secret, bindings):
			path.chmod(0o600)
		os.environ.update(ERP_COMPANY_MODE="true", SITE_NAME=c.site, SITES_PATH=str(ARGS.sites_path),
			ERP_COMPANY_ID=c.company_id, ERP_COMPANY_SITE=c.site, ERP_COMPANY_BINDING_ID=c.binding_id,
			ERP_COMPANY_GENERATION_ID=c.generation_id, ERP_COMPANY_AUDIENCE=c.audience,
			ERP_COMPANY_CLIENT_ID=c.client_id, ERP_COMPANY_ORIGIN=c.origin,
			ERP_COMPANY_AUTHORITY_URL="http://127.0.0.1:" + str(server.server_port),
			ERP_COMPANY_CLIENT_SECRET_FILE=str(secret), ERP_COMPANY_BINDINGS_FILE=str(bindings),
			ERP_COMPANY_BINDINGS_SHA256=hashlib.sha256(bindings.read_bytes()).hexdigest())
		thread.start()
		try:
			import frappe.app as APP
			CONFIG = APP._company_config
			if CONFIG is None or CONFIG.browser_enabled:
				raise ValueError("Explicit staged company mode required; no browser admission")
			names = ("test_own_owner_member_reads", "test_foreign_principal_and_company_denied") if ARGS.plane == "b" else (
				"test_own_owner_member_reads", "test_foreign_principal_and_company_denied",
				"test_host_origin_and_identity_headers_fail_before_authority",
				"test_central_revocation_before_publication_discards_data",
				"test_native_role_revoked_between_reads_discards_data",
				"test_native_permission_changed_between_reads_discards_data")
			result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(WSGIACL(name) for name in names))
		finally:
			server.shutdown()
			server.server_close()
			thread.join(2)
			if thread.is_alive():
				raise RuntimeError("Owned loopback authority did not stop")
		print("Scope: actual ERP WSGI/private HTTP/native ACL; controlled central authority; plane " + ARGS.plane)
		sys.exit(0 if result.wasSuccessful() and result.testsRun == len(names) and not result.skipped
			and not result.expectedFailures and not result.unexpectedSuccesses else 1)
