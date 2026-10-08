"""Actual editor boundary functions; controlled request/Core/native adapters only."""
import ast
import dataclasses
import hashlib
import json
import pathlib
import re
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
frappe = types.ModuleType("frappe")
frappe.__path__ = [str(ROOT / "frappe")]
sys.modules["frappe"] = frappe
from frappe import company_editor as editor
from frappe import company_session as transport

IDS = [f"00000000-0000-4000-8000-{n:012d}" for n in range(1, 6)]
TOKEN = "exs_" + "a" * 43


def config():
	return transport.Config(IDS[0], "erp.alpha.example.test", IDS[1], IDS[2], "erp-alpha", "erp-alpha", "https://erp.alpha.example.test", "http://127.0.0.1:8097", "s" * 43, {IDS[3]: "writer@example.test"}, "0" * 64, True, "https://auth.platform.example.test", "f" * 43, True, "beta")


def envelope():
	c = config()
	return dict(version=2, subject_id=IDS[3], company_id=c.company_id, product="erp", resource_kind="erp-site", binding_id=c.binding_id, native_id=c.site, generation_id=c.generation_id, authz_epoch="1", audience=c.audience, scopes=["erp:read", "erp:write"], current_role="member", technical_status="accepted", access_entitled=True, entitlement_kind="beta")


class Values(dict):
	def getlist(self, key):
		value = self[key]
		return value if isinstance(value, list) else [value]


class Headers(dict):
	def __iter__(self):
		return iter(self.items())


def request(path="/desk", method="GET", **headers):
	return types.SimpleNamespace(path=path, method=method, host=config().site,
		headers=Headers({"Cookie": transport.COOKIE + "=" + TOKEN + "; sid=" + "x" * 32, **headers}),
		environ={"RAW_URI": path}, args=Values(), form=Values(), mimetype="application/x-www-form-urlencoded", content_length=0)


class EditorControls(unittest.TestCase):
	def setUp(self):
		self.c, self.value = config(), envelope()
		self.context = editor.Context(self.c, self.value, TOKEN, editor.session_binding(self.c, self.value, TOKEN), time.monotonic() + 9, "save")
		frappe.flags = {"company_editor": self.context}
		frappe.form_dict = {}
		frappe.local = types.SimpleNamespace(role_permissions={}, request_cache={})
		frappe.session = types.SimpleNamespace(user="writer@example.test")
		def installed_apps(kind, **_kwargs):
			if kind != 'Installed Application':
				raise AssertionError('Unexpected controlled native query')
			return [{'app_name': app, 'is_setup_complete': 1} for app in ('frappe', 'erpnext')]
		frappe.get_all = installed_apps
		frappe.get_meta = lambda _: types.SimpleNamespace(get_table_fields=lambda: [])
		frappe.get_doc = lambda *_: types.SimpleNamespace(check_permission=lambda _: None)

	def denied(self, call, status):
		with self.assertRaises(transport.Denied) as result:
			call()
		self.assertEqual(result.exception.status, status)

	def test_adopted_site_requires_both_actual_native_setup_completions(self):
		complete = [{'app_name': app, 'is_setup_complete': 1} for app in ('frappe', 'erpnext')]
		with mock.patch.object(frappe, 'conf', types.SimpleNamespace(maintenance_mode=False, get=lambda _: None), create=True), mock.patch.object(frappe, 'get_all', return_value=complete, create=True) as query:
			editor.assert_site_configuration()
			query.assert_called_once_with('Installed Application', filters={'app_name': ['in', ['frappe', 'erpnext']]}, fields=['app_name', 'is_setup_complete'])
			for rows in ([], complete[:1], [complete[0], complete[0]], [complete[0], {'app_name': 'erpnext', 'is_setup_complete': 0}], [complete[0], {'app_name': 'erpnext', 'is_setup_complete': True}]):
				query.return_value = rows
				self.denied(editor.assert_site_configuration, 503)

	def test_actual_owned_post_setup_gate_accepts_only_prepared_distinct_sites(self):
		module = ast.parse((ROOT / 'scripts/company-editor-native.integration.py').read_text())
		function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'verify_prepared_sites')
		with tempfile.TemporaryDirectory() as directory:
			base = pathlib.Path(directory).resolve()
			marker = 'a' * 32
			sites = ['erp.acl-' + plane + '-' + marker[:12] + '.example.test' for plane in ('a', 'b')]
			args = types.SimpleNamespace(fixture_id=marker, sites_path=base, site_a=sites[0], site_b=sites[1])
			configs = []
			for index, site in enumerate(sites):
				path = base / site / 'site_config.json'
				path.parent.mkdir()
				data = {'allow_tests': True, 'company_acl_fixture': marker, 'company_editor_fixture_prepared': marker, 'db_type': 'postgres', 'db_name': 'owned_' + str(index)}
				path.write_text(json.dumps(data))
				configs.append((path, data))
			namespace = {'ARGS': args, 're': re, 'json': json}
			exec(compile(ast.Module(body=[function], type_ignores=[]), '<actual-owned-fixture-gate>', 'exec'), namespace)
			check = namespace['verify_prepared_sites']
			check()
			path, data = configs[1]
			for key, value in [('allow_tests', False), ('company_acl_fixture', 'b' * 32), ('company_editor_fixture_prepared', None), ('db_type', 'mariadb'), ('db_name', configs[0][1]['db_name'])]:
				path.write_text(json.dumps({**data, key: value}))
				with self.assertRaises(ValueError):
					check()
			path.write_text(json.dumps(data))
			args.site_b = 'erp.acl-b-foreign.example.test'
			with self.assertRaises(ValueError):
				check()

	def test_default_off_and_v1_never_promote(self):
		self.denied(lambda: editor.editor_envelope(self.value, dataclasses.replace(self.c, editor_enabled=False)), 401)
		v1 = dict(self.value, version=1, subscription_entitled=True)
		v1.pop("access_entitled")
		v1.pop("entitlement_kind")
		self.assertIsNone(transport.company_access(self.value))
		self.denied(lambda: editor.editor_envelope(v1, self.c), 503)

	def test_exact_approved_companion(self):
		manifest = json.loads((ROOT / "company-editor-access.manifest.json").read_text())
		self.assertFalse(manifest["enabled"])
		self.assertEqual(hashlib.sha256((ROOT / 'frappe/company_editor_access.py').read_bytes()).hexdigest(), manifest['artifacts']['company-editor-access.py']['sha256'])
		self.assertEqual(manifest['v1PolicySha256'], '790e6bc8d9b78c31d33fa4fce3b57717c952d225b078eef442726daf6c9ec85c')

	def test_binding_hash_and_all_tenant_identity_dimensions(self):
		binding = self.context.binding
		self.assertNotIn(TOKEN, json.dumps(binding))
		self.assertEqual(binding["credential_hash"], hashlib.sha256(TOKEN.encode()).hexdigest())
		for key in ("company_id", "binding_id", "generation_id", "native_id", "audience", "product"):
			changed = dict(self.value, **{key: IDS[4]})
			self.denied(lambda: editor.session_binding(self.c, changed, TOKEN), 503)
		self.assertNotEqual(binding, editor.session_binding(self.c, self.value, "exs_" + "b" * 43))

	def test_reader_and_owner_have_no_implicit_write(self):
		reader = dict(self.value, current_role="owner", scopes=["erp:read"])
		frappe.flags["company_editor"] = dataclasses.replace(self.context, value=reader)
		self.denied(lambda: editor.validate_save('{"doctype":"Customer"}'), 403)

	def test_fixed_site_before_native_selection(self):
		r = request()
		r.host = "erp.other.example.test"
		self.denied(lambda: editor.ingress(r, self.c), 403)
		for header in ("Authorization", "X-Frappe-Site-Name", "X-Frappe-User", "X-Company-ID", "X-Forwarded-Host"):
			self.denied(lambda: editor.ingress(request(**{header: "foreign"}), self.c), 401)

	def test_sid_only_and_duplicate_cookie_denied(self):
		for cookie in ("sid=" + "x" * 32, transport.COOKIE + "=" + TOKEN,
			transport.COOKIE + "=" + TOKEN + "; sid=" + "x" * 32 + "; sid=" + "x" * 32):
			self.denied(lambda: editor.ingress(request(Cookie=cookie), self.c), 401)

	def test_payload_and_query_tenant_selectors(self):
		for key in editor.SELECTORS:
			r = request('/api/method/' + editor.SAVE_METHOD, "POST", Origin=self.c.origin)
			r.form[key] = "foreign"
			self.denied(lambda: editor.ingress(r, self.c), 400)
			r = request()
			r.args[key] = "foreign"
			self.denied(lambda: editor.ingress(r, self.c), 400)

	def test_write_origin_and_fixed_operation_ceiling(self):
		path = '/api/method/' + editor.SAVE_METHOD
		self.denied(lambda: editor.ingress(request(path, "POST"), self.c), 403)
		self.assertEqual(editor.ingress(request(path, "POST", Origin=self.c.origin), self.c)[0], "save")
		for path in ('/api/resource/Customer', '/api/method/login', '/api/method/frappe.client.delete', '/api/method/run_doc_method', '/api/method/frappe.client.submit'):
			self.denied(lambda: editor.ingress(request(path, "POST", Origin=self.c.origin), self.c), 404)

	def test_native_draft_shape_and_nested_bypass_rejection(self):
		for doctype in ('Customer', 'Sales Invoice'):
			editor.validate_save(json.dumps({"doctype": doctype, "docstatus": 0, "items": [{"doctype": "Sales Invoice Item", "item_code": "fixed"}]}))
		for doc in ({"doctype": "User"}, {"doctype": "Sales Invoice", "docstatus": 1}, {"doctype": "Customer", "items": [{"flags": {"ignore_permissions": True}}]}, {"doctype": "Customer", "ignore_links": True}):
			self.denied(lambda: editor.validate_save(json.dumps(doc)), 400 if ('items' in doc or 'ignore_links' in doc) else 403)

	def test_before_commit_rechecks_current_core_and_native(self):
		frappe.response = types.SimpleNamespace(docs=[{"doctype": "Customer", "name": "owned"}])
		trace = []
		frappe.local.role_permissions = {('Customer', 'writer@example.test', False): {'write': 1}}
		frappe.local.request_cache['company_editor_meta'] = {'Customer': 'stale'}
		frappe.get_doc = lambda *args: types.SimpleNamespace(check_permission=lambda permission: trace.append((args, permission)))
		with mock.patch.object(transport, 'private_call', return_value=self.value) as call, mock.patch.object(transport, 'native_user', return_value='writer@example.test') as user:
			editor.recheck_before_commit(self.context)
			call.assert_called_once_with(self.c, 'introspect', TOKEN)
			user.assert_called_once_with(self.c, self.value, fresh_roles=True, set_principal=False)
		self.assertEqual(trace, [(('Customer', 'owned'), 'write')])
		self.assertEqual(frappe.local.role_permissions, {})
		self.assertNotIn("company_editor_meta", frappe.local.request_cache)
		with mock.patch.object(transport, 'private_call', return_value=dict(self.value, scopes=['erp:read'])):
			self.denied(lambda: editor.recheck_before_commit(self.context), 403)
		self.denied(lambda: editor.recheck_before_commit(dataclasses.replace(self.context, deadline=0)), 503)

	def native_request_stubs(self, binding=None, csrf='c' * 32):
		class Dot(dict):
			__getattr__ = dict.get
			__setattr__ = dict.__setitem__
		frappe.flags = Dot(frappe.flags)
		frappe.conf = Dot(db_type='postgres', maintenance_mode=False)
		frappe.local = types.SimpleNamespace()
		frappe.request = types.SimpleNamespace(method='POST', cookies={'sid': 'x' * 32}, headers=Headers({'X-Frappe-CSRF-Token': csrf}))
		frappe.session = types.SimpleNamespace(user='writer@example.test', data={'company_binding': self.context.binding, 'csrf_token': 'c' * 32})
		def session_row(kind, filters, fields, **kw):
			self.assertEqual((kind, filters, fields), ('Sessions', {'sid': 'x' * 32}, ['user', 'sessiondata', 'status']))
			self.assertEqual(kw, {'as_dict': True, 'order_by': 'sid'})
			return types.SimpleNamespace(user='writer@example.test', status='Active', sessiondata=json.dumps({'company_binding': binding or self.context.binding}))
		frappe.db = types.SimpleNamespace(sql=lambda *_: None, get_value=session_row)
		module = types.ModuleType('frappe.auth')
		module.HTTPRequest = type('HTTPRequest', (), {'set_request_ip': lambda _: None})
		module.LoginManager = types.SimpleNamespace(for_company=lambda *_: types.SimpleNamespace())
		return mock.patch.dict(sys.modules, {'frappe.auth': module})

	def test_native_sid_requires_actual_current_row_and_complete_binding(self):
		with self.native_request_stubs(), mock.patch.object(transport, 'native_user', return_value='writer@example.test'):
			statements = []
			frappe.db.sql = lambda statement: statements.append(statement)
			def ready_query(kind, **kwargs):
				self.assertEqual(kind, 'Installed Application')
				self.assertEqual(len(statements), 3)
				self.assertTrue(statements[0].startswith('SET LOCAL statement_timeout = '))
				self.assertEqual(statements[1:], ['SET LOCAL lock_timeout = 1000', 'SET LOCAL idle_in_transaction_session_timeout = 10000'])
				return [{'app_name': app, 'is_setup_complete': 1} for app in ('frappe', 'erpnext')]
			with mock.patch.object(frappe, 'get_all', side_effect=ready_query):
				editor.initialize_native_request(self.context)
		for key in self.context.binding:
			changed = dict(self.context.binding)
			changed[key] = 'foreign'
			with self.native_request_stubs(changed), mock.patch.object(transport, 'native_user', return_value='writer@example.test'):
				self.denied(lambda: editor.initialize_native_request(self.context), 401)
		with self.native_request_stubs(), mock.patch.object(transport, 'native_user', return_value='writer@example.test'):
			frappe.db.get_value = lambda *_a, **_k: None
			self.denied(lambda: editor.initialize_native_request(self.context), 401)

	def test_native_csrf_cannot_be_replaced_by_config_or_referrer(self):
		with self.native_request_stubs(csrf='wrong'), mock.patch.object(transport, 'native_user', return_value='writer@example.test'):
			frappe.request.headers['Referer'] = self.c.origin
			self.denied(lambda: editor.initialize_native_request(self.context), 403)
		with self.native_request_stubs(), mock.patch.object(transport, 'native_user', return_value='writer@example.test'):
			frappe.conf['ignore_csrf'] = True
			self.denied(lambda: editor.initialize_native_request(self.context), 503)

	def test_native_permission_failure_is_not_promoted(self):
		frappe.response = types.SimpleNamespace(docs=[{'doctype': 'Customer', 'name': 'owned'}])
		def refused(_):
			raise transport.Denied(403)
		frappe.get_doc = lambda *_: types.SimpleNamespace(check_permission=refused)
		with mock.patch.object(transport, 'private_call', return_value=self.value), mock.patch.object(transport, 'native_user', return_value='writer@example.test'):
			self.denied(lambda: editor.recheck_before_commit(self.context), 403)

	def test_native_save_dispatch_preserves_action_and_override_ceiling(self):
		frappe.override_whitelisted_method = lambda method: method
		frappe.form_dict = {'action': 'Submit', 'doc': '{"doctype":"Sales Invoice"}'}
		self.denied(lambda: editor.method_guard(editor.SAVE_METHOD), 403)
		frappe.form_dict['action'] = 'Save'
		editor.method_guard(editor.SAVE_METHOD)
		frappe.override_whitelisted_method = lambda _: 'private.custom.override'
		self.denied(lambda: editor.method_guard(editor.SAVE_METHOD), 403)

	def test_actual_set_user_destruction_is_skipped_by_editor_identity_validation(self):
		class Dot(dict):
			__getattr__ = dict.get
			__setattr__ = dict.__setitem__
		node = next(n for n in ast.parse((ROOT / 'frappe/__init__.py').read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'set_user')
		local = Dot(session=Dot(user='writer@example.test', sid='actual-native-sid', data=Dot(company_binding=self.context.binding)), form_dict=Dot(doc='static-doc', action='Save', doctype='Customer'))
		namespace = {'local': local, '_dict': Dot}
		exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual-native-set-user>', 'exec'), namespace)
		frappe.set_user = namespace['set_user']
		frappe.db = types.SimpleNamespace(get_value=lambda kind, *_args, **_kw: types.SimpleNamespace(enabled=1, user_type='System User') if kind == 'User' else 0, get_values=lambda *_args, **_kw: ['Reader', 'Writer'])
		transport.native_user(self.c, self.value, fresh_roles=True, set_principal=False)
		self.assertEqual(local.form_dict, {'doc': 'static-doc', 'action': 'Save', 'doctype': 'Customer'})
		self.assertEqual(local.session.sid, 'actual-native-sid')
		self.assertEqual(local.session.data.company_binding, self.context.binding)
		# Same shipped set_user is destructive in the unchanged V1/default path.
		transport.native_user(self.c, self.value, fresh_roles=True)
		self.assertEqual(local.form_dict, {})
		self.assertEqual(local.session.sid, 'writer@example.test')
		self.assertEqual(local.session.data, {})

	def test_actual_native_session_slots_and_binding_before_persistence(self):
		class Dot(dict):
			__getattr__ = dict.get
			__setattr__ = dict.__setitem__
		node = next(n for n in ast.parse((ROOT / 'frappe/sessions.py').read_text()).body if isinstance(n, ast.ClassDef) and n.name == 'Session')
		namespace = {'frappe': frappe, 'cstr': str, 'unquote': lambda x: x, 'get_expiry_period': lambda: '06:00:00'}
		module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
		exec(compile(ast.fix_missing_locations(module), '<actual-native-session>', 'exec'), namespace)
		Session = namespace['Session']
		frappe._dict = Dot
		frappe.local = Dot(request_ip='127.0.0.1')
		frappe.form_dict = Dot(doc='static-doc', action='Save')
		frappe.request = types.SimpleNamespace(cookies={}, headers=Headers())
		frappe.generate_hash = lambda: 'n' * 32
		frappe.utils = types.SimpleNamespace(now=lambda: '2026-10-08 00:00:00')
		Session.validate_user = lambda _: None
		original_start = Session.start
		Session.start = lambda *_args, **_kw: None
		plain = Session('writer@example.test')
		bound = Session('writer@example.test', company_binding=self.context.binding)
		self.assertIsNone(plain.company_binding)
		self.assertEqual(bound.company_binding, self.context.binding)
		Session.start = original_start
		captured = []
		def persistence(self):
			captured.append(dict(self.data.data))
			raise RuntimeError('controlled persistence boundary')
		Session.insert_session_record = persistence
		with self.assertRaisesRegex(RuntimeError, 'controlled persistence boundary'):
			bound.start()
		self.assertEqual(captured[0]['company_binding'], self.context.binding)
		self.assertEqual(captured[0]['csrf_token'], 'n' * 32)
		self.assertEqual(bound.sid, 'n' * 32)
		self.assertEqual(frappe.form_dict, {'doc': 'static-doc', 'action': 'Save'})

	def test_business_default_zero_is_not_permission_bypass(self):
		for field in editor.BUSINESS_ZERO_FIELDS:
			editor.validate_save(json.dumps({'doctype': 'Sales Invoice', field: 0}))
			self.denied(lambda: editor.validate_save(json.dumps({'doctype': 'Sales Invoice', field: 1})), 400)
		for field in ('ignore_permissions', 'ignore_user_permissions', 'ignore_csrf', 'ignore_links', 'ignore_mandatory'):
			self.denied(lambda: editor.validate_save(json.dumps({'doctype': 'Sales Invoice', field: 0})), 400)

	def test_canonical_owner_and_child_parent_before_native_save(self):
		trace = []
		child = types.SimpleNamespace(name='owned-child', owner='stored-owner')
		stored = types.SimpleNamespace(owner='stored-owner', docstatus=0, check_permission=lambda p: trace.append(p), get=lambda *_: [child])
		frappe.get_doc = lambda *_: stored
		frappe.get_meta = lambda _: types.SimpleNamespace(get_table_fields=lambda: [types.SimpleNamespace(fieldname='items', options='Sales Invoice Item')])
		payload = {'doctype': 'Sales Invoice', 'name': 'owned-parent', 'owner': 'stored-owner', 'items': [{'doctype': 'Sales Invoice Item', 'name': 'owned-child', 'parent': 'owned-parent', 'parenttype': 'Sales Invoice', 'parentfield': 'items', 'owner': 'stored-owner'}]}
		editor.canonical_document(payload)
		self.assertEqual(trace, ['write'])
		self.denied(lambda: editor.canonical_document(dict(payload, owner='forged-owner')), 403)
		self.denied(lambda: editor.canonical_document(dict(payload, owner=None)), 403)
		self.denied(lambda: editor.canonical_document({k: v for k, v in payload.items() if k != "owner"}), 403)
		self.denied(lambda: editor.validate_save(json.dumps(dict(payload, __newname="foreign-parent"))), 400)
		self.denied(lambda: editor.validate_save(json.dumps(dict(payload, items=[dict(payload["items"][0], __newname="foreign-child")]))), 400)
		self.denied(lambda: editor.canonical_document(dict(payload, items=[dict(payload["items"][0], name="new-sales-invoice-item-fixed", __islocal=1, owner="forged-owner")])), 403)
		for key, value in (('name', 'foreign-child'), ('parent', 'foreign-parent'), ('parenttype', 'Customer'), ('parentfield', 'other'), ('owner', 'forged-owner')):
			bad = dict(payload, items=[dict(payload['items'][0], **{key: value})])
			self.denied(lambda: editor.canonical_document(bad), 403)
		self.denied(lambda: editor.canonical_document(dict(payload, __islocal=1)), 403)
		stored.check_permission = lambda _: (_ for _ in ()).throw(transport.Denied(403))
		self.denied(lambda: editor.canonical_document(payload), 403)

	def test_calculator_numeric_date_and_bypass_scalar_boundaries(self):
		editor.bounded_calculator_scalars({'posting_date': '2026-10-09', 'party': 'Customer', 'fetch_payment_terms_template': '1'}, form=True)
		editor.bounded_calculator_scalars({'qty': 1, 'conversion_rate': 1, 'plc_conversion_rate': 1, 'update_stock': 0})
		for args, form in [({'party': 'x' * 141}, True), ({'party': 'line\nbreak'}, True), ({'posting_date': '2026-13-09'}, True), ({'posting_date': '20261009'}, True), ({'fetch_payment_terms_template': True}, True), ({'fetch_payment_terms_template': '0'}, True), ({'qty': None}, False), ({'qty': True}, False), ({'qty': float('nan')}, False), ({'qty': float('inf')}, False), ({'qty': -1}, False), ({'qty': 1000001}, False), ({'qty': 0}, False), ({'qty': 1, 'net_rate': True}, False), ({'qty': 1, 'net_rate': float('inf')}, False), ({'qty': 1, 'update_stock': False}, False), ({'qty': 1, 'update_stock': 1}, False), ({'qty': 1, 'item_code': []}, False)]:
			self.denied(lambda: editor.bounded_calculator_scalars(args, form=form), 400)


	def test_actual_native_metadata_snapshot_is_request_scoped_and_v1_unchanged(self):
		module = ast.parse((ROOT / 'frappe/model/meta.py').read_text())
		function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'get_meta')
		frappe.local = types.SimpleNamespace(request_cache={})
		frappe.flags = {'company_session': True, 'company_editor': self.context}
		trace = []
		def construct(kind):
			trace.append(kind)
			return types.SimpleNamespace(name=kind, revision=len(trace))
		frappe.client_cache = types.SimpleNamespace(get_value=lambda *_: self.fail('shared metadata used'), set_value=lambda *_: self.fail('shared metadata written'))
		scope = {'frappe': frappe, 'Meta': construct}
		exec(compile(ast.Module(body=[function], type_ignores=[]), 'native-meta-snapshot', 'exec'), scope)
		get = scope['get_meta']
		first = get('Customer')
		self.assertIs(get('Customer'), first)
		self.assertIsNot(get('Customer', cached=False), first)
		frappe.local.request_cache = {}  # native init(force=True) resets this each request
		self.assertIsNot(get('Customer'), first)
		frappe.flags.pop('company_editor')
		self.assertIsNot(get('Customer'), get('Customer'))  # original V1 no shared/snapshot cache
		self.assertEqual(len(trace), 5)

	def test_actual_calculator_projection_retains_child_kind_and_checks_serialized_links(self):
		fields = [types.SimpleNamespace(fieldname='currency', fieldtype='Link', options='Currency'), types.SimpleNamespace(fieldname='pricing_rules', fieldtype='Small Text'), types.SimpleNamespace(fieldname='item_tax_rate', fieldtype='Small Text')]
		lookups, links = [], []
		def metadata(kind):
			lookups.append(kind)
			self.assertEqual(kind, 'Sales Invoice Item')
			return types.SimpleNamespace(fields=fields)
		frappe.get_meta = metadata
		frappe.get_doc = lambda kind, name: types.SimpleNamespace(check_permission=lambda right: links.append((kind, name, right)))
		selected = {'currency': 'USD', 'pricing_rules': '["current-rule"]', 'item_tax_rate': '{"current-tax-account":5}'}
		doc = types.SimpleNamespace(set=lambda key, value: setattr(doc, key, [types.SimpleNamespace(as_dict=lambda: value[0])]), apply_fieldlevel_read_permissions=lambda: None)
		self.assertEqual(editor.calculator_projection(selected, doc, True), selected)
		self.assertIn(('Pricing Rule', 'current-rule', 'read'), links)
		self.assertIn(('Account', 'current-tax-account', 'read'), links)
		self.assertIn(('Currency', 'USD', 'read'), links)
		self.assertEqual(lookups, ['Sales Invoice Item', 'Sales Invoice Item'])

	def test_real_postgres_connection_isolation_is_editor_only(self):
		# Execute the actual native connection factory, with only network connect
		# replaced. A truthy flag alone must never alter standalone isolation.
		tree = ast.parse((ROOT / 'frappe/database/postgres/database.py').read_text())
		cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'PostgresDatabase')
		factory = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'get_connection')
		module = ast.Module(body=[factory], type_ignores=[])
		ast.fix_missing_locations(module)
		trace = []
		connection = types.SimpleNamespace(set_isolation_level=trace.append)
		space = {'frappe': frappe, 'psycopg2': types.SimpleNamespace(connect=lambda **_: connection), 'ISOLATION_LEVEL_REPEATABLE_READ': 2, 'ISOLATION_LEVEL_READ_COMMITTED': 1}
		exec(compile(module, 'actual-native-get-connection', 'exec'), space)
		database = types.SimpleNamespace(cur_db_name='owned', user='native', host='postgres', socket=None, password=None, port=5432)
		for flag, expected in ((None, 2), (True, 2), (types.SimpleNamespace(), 2), (self.context, 1)):
			frappe.flags['company_editor'] = flag
			self.assertIs(space['get_connection'](database), connection)
			self.assertEqual(trace[-1], expected)


if __name__ == '__main__':
	unittest.main()
