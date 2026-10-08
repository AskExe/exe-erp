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

	def test_hosted_full_form_ceiling_keeps_ordinary_metadata_and_native_role_ceiling(self):
		ordinary = {'name':'Customer', 'quick_entry':1}
		frappe.flags = {}
		self.assertEqual(editor.form_meta_ceiling(dict(ordinary)), ordinary)
		for scopes in (['erp:read'], ['erp:read', 'erp:write']):
			context = dataclasses.replace(self.context, value={**self.value,'scopes':scopes})
			frappe.flags = {'company_editor':context}
			self.assertEqual(editor.form_meta_ceiling(dict(ordinary))['quick_entry'], 0)
			other = {'name':'Item', 'quick_entry':1}
			self.assertEqual(editor.form_meta_ceiling(dict(other)), other)
			boot = {'user':{'can_create':['Customer','Item'], 'can_write':['Customer','Item'], 'in_create':['Customer'], 'can_delete':['Customer']}}
			editor.boot_ceiling(boot)
			self.assertEqual(boot['user']['can_create'], ['Customer'] if 'erp:write' in scopes else [])
			self.assertEqual(boot['user']['can_delete'], [])
		frappe.flags = {'company_editor':dataclasses.replace(self.context,deadline=time.monotonic()-1)}
		self.denied(lambda: editor.form_meta_ceiling(dict(ordinary)),503)

	def test_link_ingress_zero_only_and_mixed_duplicate_selectors_deny(self):
		path = '/api/method/' + editor.LINK_SEARCH
		for value in (0, False, '0', 'false'):
			req = request(path)
			req.args = Values(ignore_user_permissions=value)
			self.assertEqual(editor.ingress(req, self.c)[0], 'link-read')
		for value in (1, True, '1', 'true', '', None, 0.0):
			req = request(path)
			req.args = Values(ignore_user_permissions=value)
			self.denied(lambda: editor.ingress(req,self.c),400)
		req = request(path, 'POST', Origin=self.c.origin)
		req.args, req.form = Values(doctype='Customer Group'), Values(doctype='Customer Group')
		self.denied(lambda: editor.ingress(req,self.c),400)

	def test_link_guard_native_field_target_and_closed_arguments(self):
		model = types.ModuleType('frappe.model')
		model.get_permitted_fields = mock.Mock(return_value=['name','customer_group'])
		frappe.flags['company_editor'] = dataclasses.replace(self.context, operation='link-read')
		frappe.request = request('/api/method/' + editor.LINK_VALIDATE)
		frappe.has_permission = mock.Mock(return_value=True)
		field = types.SimpleNamespace(fieldtype='Link',options='Customer Group')
		metadata = types.SimpleNamespace(get_field=lambda name:field,is_virtual=False,translated_doctype=False)
		frappe.get_meta = mock.Mock(return_value=metadata)
		frappe.get_hooks = mock.Mock(return_value=types.SimpleNamespace(standard_queries={}))
		base = dict(doctype='Customer Group',reference_doctype='Customer',link_fieldname='customer_group',filters='{"is_group":0}',ignore_user_permissions='0',docname='ACL a',txt='ACL a',fields_to_fetch='[]',page_length='10')
		frappe.form_dict = dict(base)
		with mock.patch.dict(sys.modules, {'frappe.model':model}):
			self.assertIs(editor.link_guard(editor.LINK_VALIDATE)['ignore_user_permissions'],False)
			for key,value in [('query',''),('ignore_permissions',0),('fields_to_fetch','["private"]'),('page_length','11'),('docname',''),('txt','different'),('filters','{"is_group":true}'),('doctype',{}),('reference_doctype',{})]:
				frappe.form_dict = {**base,key:value}
				self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),400)
			frappe.form_dict = dict(base)
			model.get_permitted_fields.return_value = ['name']
			self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),403)
			model.get_permitted_fields.return_value = ['name','customer_group']
			frappe.has_permission.return_value = False
			self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),403)
			frappe.has_permission.return_value = True
			for key in ('is_virtual',):
				setattr(metadata,key,True)
				self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),403)
				setattr(metadata,key,False)
			frappe.get_hooks.return_value.standard_queries = {'Customer Group':['custom']}
			self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),403)

	def test_link_projection_checks_each_real_native_document_and_omits_descriptions(self):
		model = types.ModuleType('frappe.model')
		model.get_permitted_fields = mock.Mock(return_value=['name'])
		document = types.SimpleNamespace(check_permission=mock.Mock())
		frappe.get_doc = mock.Mock(return_value=document)
		args = {'doctype':'Customer Group','page_length':10,'docname':'ACL a'}
		with mock.patch.dict(sys.modules, {'frappe.model':model}):
			self.assertEqual(editor.link_projection([{'value':'ACL a','description':'private','html':'unsafe'}],editor.LINK_SEARCH,args),[{'value':'ACL a'}])
			document.check_permission.assert_called_once_with('read')
			document.check_permission.side_effect = editor.Denied(403)
			self.denied(lambda:editor.link_projection({'name':'ACL a'},editor.LINK_VALIDATE,args),403)
			self.denied(lambda:editor.link_projection({'name':'wrong'},editor.LINK_VALIDATE,args),503)

	def test_list_settings_projection_is_closed_and_fresh_field_permission_bound(self):
		model = types.ModuleType('frappe.model')
		model.get_permitted_fields = mock.Mock(return_value=['name', 'customer_name', 'status'])
		flags = {key: 0 for key in editor.LIST_FLAGS}
		with mock.patch.dict(sys.modules, {'frappe.model': model}):
			self.assertEqual(editor.list_settings_projection(None, 'Customer'), {})
			value = {**flags, 'allow_edit': 1, 'fields_html': '<script>', 'owner': 'private', 'fields': '[{"fieldname":"customer_name","width":100},{"fieldname":"status_field"}]'}
			result = editor.list_settings_projection(value, 'Customer')
			self.assertEqual(set(result), editor.LIST_FLAGS | {'fields'})
			model.get_permitted_fields.return_value = ['name']
			self.denied(lambda: editor.list_settings_projection(value, 'Customer'), 403)
			model.get_permitted_fields.return_value = ['customer_name']
			for fields in ([{'fieldname':'customer_name','width':True}], [{'fieldname':'customer_name','width':39}], [{'fieldname':'customer_name','width':1001}], [{'fieldname':'customer_name','extra':'x'}], [{'fieldname':'customer_name'}, {'fieldname':'customer_name'}], [{'fieldname':{}}], [{}], [0], [dict(fieldname='customer_name')] * 101):
				self.denied(lambda: editor.list_settings_projection({**flags, 'fields': json.dumps(fields)}, 'Customer'), 503)
			for flag in (True, 2, None, '0'):
				self.denied(lambda: editor.list_settings_projection({**flags, 'disable_count':flag}, 'Customer'), 503)

	def test_list_settings_exact_post_arguments_and_settings_writes_deny(self):
		frappe.flags['company_editor'] = dataclasses.replace(self.context, operation='list-settings')
		frappe.request = request('/api/method/' + editor.LIST_SETTINGS, 'POST', Origin=self.c.origin)
		frappe.has_permission = mock.Mock(return_value=True)
		frappe.get_meta = mock.Mock(return_value=types.SimpleNamespace(is_virtual=False))
		frappe.form_dict = {'cmd':editor.LIST_SETTINGS, 'doctype':'Customer'}
		self.assertEqual(editor.list_settings_guard(), 'Customer')
		frappe.form_dict['extra'] = 'x'
		self.denied(editor.list_settings_guard, 400)
		frappe.form_dict = {'doctype':'User'}
		self.denied(editor.list_settings_guard, 400)
		frappe.form_dict = {'doctype':'Customer'}
		frappe.has_permission.return_value = False
		self.denied(editor.list_settings_guard, 403)
		for method in ('frappe.desk.listview.set_list_settings', 'frappe.desk.doctype.list_view_settings.list_view_settings.save_listview_settings'):
			self.denied(lambda: editor.ingress(request('/api/method/' + method, 'POST', Origin=self.c.origin), self.c), 404)
		self.denied(lambda: editor.ingress(request('/api/method/' + editor.LIST_SETTINGS), self.c), 404)

	def test_list_settings_readonly_failure_always_rolls_back(self):
		module = types.ModuleType('frappe.desk.listview')
		failure = RuntimeError('controlled readonly refusal')
		module.get_list_settings = mock.Mock(side_effect=failure)
		frappe.flags = {'company_editor':dataclasses.replace(self.context, operation='list-settings')}
		frappe.db = types.SimpleNamespace(rollback=mock.Mock(), sql=mock.Mock())
		with mock.patch.dict(sys.modules, {'frappe.desk.listview':module}), mock.patch.object(editor, 'native_budget'), mock.patch.object(editor, 'list_settings_guard', return_value='Customer'):
			with self.assertRaises(RuntimeError) as raised:
				editor.read_list_settings()
		self.assertIs(raised.exception, failure)
		self.assertEqual(frappe.db.rollback.call_count, 2)
		frappe.db.sql.assert_called_once_with('SET TRANSACTION READ ONLY')

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

	def test_hosted_boot_uses_native_legacy_calculators_without_changing_ordinary_setting(self):
		boot = {'user':{},'sysdefaults':{'use_legacy_js_reactivity':0}}
		frappe.flags = {}
		editor.boot_ceiling(boot)
		self.assertEqual(boot['sysdefaults']['use_legacy_js_reactivity'],0)
		frappe.flags = {'company_editor':self.context}
		editor.boot_ceiling(boot)
		self.assertEqual(boot['sysdefaults']['use_legacy_js_reactivity'],1)

	def test_invoice_link_query_is_bound_to_native_field_and_exact_filters(self):
		args = dict(doctype='Item',reference_doctype='Sales Invoice Item',link_fieldname='item_code',query='erpnext.controllers.queries.item_query',filters={'is_sales_item':1,'customer':'current-customer','has_variants':0})
		frappe.get_doc = mock.Mock(return_value=types.SimpleNamespace(check_permission=mock.Mock()))
		self.assertEqual(editor.invoice_link_filters(args)['filters'],args['filters'])
		for changed in ({'query':'erpnext.controllers.queries.get_income_account'},{'filters':{'is_sales_item':True,'customer':'current-customer','has_variants':0}},{'filters':{'is_sales_item':1,'customer':'current-customer','has_variants':0,'ignore_permissions':1}}):
			self.denied(lambda:editor.invoice_link_filters({**args,**changed}),400)
		frappe.get_doc.return_value.check_permission.side_effect = editor.Denied(403)
		self.denied(lambda:editor.invoice_link_filters(args),403)
		req=request('/api/method/'+editor.LINK_SEARCH)
		req.args=Values(query='erpnext.controllers.queries.item_query')
		self.assertEqual(editor.ingress(req,self.c)[0],'link-read')
		req.args=Values(query='custom.query')
		self.denied(lambda:editor.ingress(req,self.c),400)

	def test_income_account_native_lookup_is_bounded_with_stock_predicates(self):
		queries=types.ModuleType('erpnext.controllers.queries')
		queries.get_item_uom_query=mock.Mock()
		queries.item_query=mock.Mock()
		frappe.get_meta=lambda _:types.SimpleNamespace(get_field=lambda _:None)
		frappe.get_list=mock.Mock(return_value=[{'name':'income-account'}])
		args=dict(doctype='Account',link_fieldname='income_account',reference_doctype='Sales Invoice Item',filters={'company':'native-company','disabled':0},page_length=10,txt='income',docname='income-account')
		with mock.patch.dict(sys.modules,{'erpnext.controllers.queries':queries}):
			self.assertEqual(editor.invoice_link_names(args,editor.LINK_VALIDATE),['income-account'])
		kw=frappe.get_list.call_args.kwargs
		self.assertEqual(kw['limit_page_length'],1)
		self.assertEqual(kw['filters'],{'company':'native-company','disabled':0,'is_group':0,'name':'income-account'})
		self.assertEqual(kw['or_filters'],[['Account','report_type','=','Profit and Loss'],['Account','account_type','in',['Income Account','Temporary']]])
		self.assertIs(kw['ignore_permissions'],False)
		self.assertIs(kw['ignore_user_permissions'],False)

	def test_customer_fetch_projection_rechecks_target_fields_and_link_permissions(self):
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=mock.Mock(return_value=['name','tax_id','language'])
		fields={'tax_id':types.SimpleNamespace(fieldtype='Data'),'language':types.SimpleNamespace(fieldtype='Link',options='Language')}
		frappe.get_meta=lambda _:types.SimpleNamespace(get_field=lambda key:fields[key])
		customer=types.SimpleNamespace(check_permission=mock.Mock(),get=lambda key:{'tax_id':'public-tax','language':'en'}[key])
		language=types.SimpleNamespace(check_permission=mock.Mock())
		frappe.get_doc=lambda kind,name:customer if kind=='Customer' else language
		args=dict(doctype='Customer',docname='current-customer',fields_to_fetch=['tax_id','language'])
		with mock.patch.dict(sys.modules,{'frappe.model':model}):
			self.assertEqual(editor.link_projection({'name':'current-customer'},editor.LINK_VALIDATE,args),{'name':'current-customer','tax_id':'public-tax','language':'en'})
			language.check_permission.assert_called_with('read')
			model.get_permitted_fields.return_value=['name','language']
			self.denied(lambda:editor.link_projection({'name':'current-customer'},editor.LINK_VALIDATE,args),403)

	def price_fixture(self):
		class Dot(dict):
			__getattr__=dict.get
		frappe.flags=Dot(company_editor=dataclasses.replace(self.context,operation='calculate'))
		payload=dict(doctype='Sales Invoice',name='new-sales-invoice-one',__islocal=1,company='native-company',customer='native-customer',currency='USD',conversion_rate=1,selling_price_list='native-price',posting_date='2026-10-09',items=[])
		ctx=dict(doctype=payload['doctype'],name=payload['name'],company=payload['company'],customer=payload['customer'],currency='USD',conversion_rate=1,price_list='native-price',price_list_currency='USD',plc_conversion_rate='',transaction_date=payload['posting_date'],items=[])
		frappe.form_dict={'ctx':json.dumps(ctx),'doc':json.dumps(payload)}
		frappe.get_doc=lambda *_:Dot(check_permission=lambda _:None,default_currency='USD',enabled=1,selling=1,currency='USD')
		frappe.get_single_value=lambda *_:0
		return payload,ctx

	def test_price_list_dto_no_fx_no_stock_and_no_caller_child_retarget(self):
		payload,ctx=self.price_fixture()
		_,checked=editor.price_list_guard()
		self.assertEqual(checked['plc_conversion_rate'],1)
		for change in ({'plc_conversion_rate':True},{'conversion_rate':2},{'supplier':'foreign'},{'unknown':0},{'customer':'foreign'}):
			frappe.form_dict={'doc':json.dumps(payload),'ctx':json.dumps({**ctx,**change})}
			self.denied(editor.price_list_guard,400 if set(change)&{'plc_conversion_rate','conversion_rate','unknown'} else 403)
		frappe.form_dict={'doc':json.dumps({**payload,'update_stock':1}),'ctx':json.dumps(ctx)}
		self.denied(editor.price_list_guard,403)
		frappe.form_dict={'doc':json.dumps(payload),'ctx':json.dumps({**ctx,'items':[{'doctype':'Sales Invoice Item','name':'foreign-child','child_docname':'foreign-child'}]})}
		self.denied(editor.price_list_guard,403)

	def test_price_list_projection_cannot_retarget_children_or_return_nested_discount_actions(self):
		ctx={'items':[{'name':'verified-child'}],'currency':'USD'}
		result={'parent':{'price_list_currency':'USD','plc_conversion_rate':1},'children':[{'price_list_rate':10,'name':'forged'}]}
		with mock.patch.object(editor,'calculator_projection',side_effect=lambda value,*_: {key:val for key,val in value.items() if key!='name'}):
			value=editor.price_list_projection(result,object(),ctx)
			self.assertEqual(value['children'][0]['name'],'verified-child')
			for change in ({'free_item_data':[{'private':'x'}]},{'apply_rule_on_other_items':'["foreign"]'}):
				self.denied(lambda:editor.price_list_projection({**result,'children':[change]},object(),ctx),403)
			self.denied(lambda:editor.price_list_projection({**result,'parent':{'price_list_currency':'EUR','plc_conversion_rate':1}},object(),ctx),503)

	def test_sales_settings_is_one_current_workflow_boolean_without_singleton_role_promotion(self):
		class Dot(dict):
			__getattr__=dict.get
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=mock.Mock(side_effect=lambda kind,**_:['items'] if kind=='Sales Invoice' else ['item_code'])
		frappe.flags=Dot(company_editor=dataclasses.replace(self.context,operation='sales-settings'))
		frappe.form_dict={'doctype':'Accounts Settings','field':'fetch_valuation_rate_for_internal_transaction'}
		frappe.has_permission=mock.Mock(return_value=True)
		with mock.patch.dict(sys.modules,{'frappe.model':model}), mock.patch.object(editor,'native_table_read',return_value=True):
			editor.sales_settings_guard()
			self.assertEqual([call.args[0] for call in frappe.has_permission.call_args_list],['Sales Invoice','Sales Invoice'])
			frappe.form_dict['field']='arbitrary-private-field'
			self.denied(editor.sales_settings_guard,400)
			frappe.form_dict['field']='fetch_valuation_rate_for_internal_transaction'
			frappe.flags.company_editor=dataclasses.replace(self.context,operation='sales-settings',value={**self.value,'scopes':['erp:read']})
			self.denied(editor.sales_settings_guard,403)
			frappe.flags.company_editor=dataclasses.replace(self.context,operation='sales-settings')
			model.get_permitted_fields.return_value=[]
			model.get_permitted_fields.side_effect=None
			self.denied(editor.sales_settings_guard,403)

	def test_actual_price_list_details_avoids_stale_currency_cache_only_for_editor_context(self):
		class Dot(dict):
			__getattr__=dict.get
		node=next(n for n in ast.parse((ROOT/'apps/erpnext/erpnext/stock/doctype/price_list/price_list.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='get_price_list_details')
		namespace={'frappe':frappe,'throw':lambda _:None,'_':lambda value:value}
		exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-price-list-details>','exec'),namespace)
		cache=types.SimpleNamespace(hget=mock.Mock(return_value={'currency':'EUR','enabled':1}),hset=mock.Mock())
		frappe.cache=lambda:cache
		frappe._dict=Dot
		context=dataclasses.replace(self.context,operation="calculate")
		frappe.flags=Dot(company_editor=context)
		frappe.local.request_cache["company_editor_currency"]=(context,"USD")
		frappe.get_doc=lambda *_:Dot(enabled=1,currency='USD',price_not_uom_dependent=0,check_permission=mock.Mock())
		self.assertEqual(namespace['get_price_list_details']('native-price')['currency'],'USD')
		cache.hget.assert_not_called()
		frappe.flags=Dot()
		self.assertEqual(namespace['get_price_list_details']('native-price')['currency'],'EUR')
		cache.hget.assert_called_once()

	def test_changed_native_price_currency_refuses_before_actual_exchange_helper(self):
		class Dot(dict):
			__getattr__=dict.get
			__setattr__=dict.__setitem__
		context=dataclasses.replace(self.context,operation='calculate')
		frappe.flags=Dot(company_editor=context)
		frappe.local.request_cache['company_editor_currency']=(context,'USD')
		frappe._dict=Dot
		price=Dot(enabled=1,currency='EUR',check_permission=lambda _:None)
		frappe.get_doc=lambda *_:price
		path=ROOT/'apps/erpnext/erpnext/stock/doctype/price_list/price_list.py'
		node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='get_price_list_details')
		space={'frappe':frappe,'throw':lambda _:None,'_':lambda value:value}
		exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-price-list-read>','exec'),space)
		path=ROOT/'apps/erpnext/erpnext/stock/get_item_details.py'
		node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='get_price_list_currency_and_exchange_rate')
		exchange=mock.Mock(return_value=99)
		space.update(ItemDetailsCtx=Dot,get_price_list_details=space['get_price_list_details'],get_company_currency=lambda _: 'USD',get_exchange_rate=exchange)
		exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-native-exchange-selection>','exec'),space)
		ctx=Dot(price_list='native-price',doctype='Sales Invoice',company='native-company',price_list_currency='USD',plc_conversion_rate=1)
		self.denied(lambda:space['get_price_list_currency_and_exchange_rate'](ctx),403)
		exchange.assert_not_called()
		price.currency='USD'
		self.assertEqual(space['get_price_list_currency_and_exchange_rate'](ctx)['plc_conversion_rate'],1)
		exchange.assert_not_called()
		frappe.local.request_cache['company_editor_currency']=(dataclasses.replace(context), 'USD')
		self.denied(editor.calculator_currency,503)

	def test_calculator_trusted_currency_projection_clears_after_native_failure(self):
		class Dot(dict):
			__getattr__=dict.get
		frappe.flags=Dot(company_editor=dataclasses.replace(self.context,operation='calculate'))
		frappe.form_dict={'currency':'USD'}
		frappe.db=types.SimpleNamespace(rollback=mock.Mock(),sql=mock.Mock())
		frappe.call=mock.Mock(side_effect=editor.Denied(403))
		party=types.ModuleType('erpnext.accounts.party')
		party.get_party_details=mock.Mock()
		items=types.ModuleType('erpnext.stock.get_item_details')
		items.get_item_details=mock.Mock()
		with mock.patch.dict(sys.modules,{'erpnext.accounts.party':party,'erpnext.stock.get_item_details':items}),mock.patch.object(editor,'native_budget'),mock.patch.object(editor,'calculator_guard',return_value=(object(),False)):
			self.denied(lambda:editor.calculate(editor.PARTY_CALCULATOR),403)
		self.assertNotIn('company_editor_currency',frappe.local.request_cache)
		self.assertEqual(frappe.db.rollback.call_count,2)

	def test_uom_validation_exact_predicate_precedes_bound_and_native_relationship(self):
		queries=types.ModuleType('erpnext.controllers.queries')
		queries.get_item_uom_query=mock.Mock(side_effect=AssertionError('LIKE helper forbidden for validation'))
		queries.item_query=mock.Mock()
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=lambda kind,**_: ['uoms'] if kind=='Item' else ['uom']
		frappe.get_single_value=lambda *_:1
		frappe.get_all=mock.Mock(return_value=[{'uom':'Unit'}])
		frappe.get_list=mock.Mock(return_value=[{'name':'Unit'}])
		args=dict(doctype='UOM',link_fieldname='uom',reference_doctype='Sales Invoice Item',filters={'item_code':'native-item'},page_length=10,txt='Unit',docname='Unit')
		with mock.patch.dict(sys.modules,{'erpnext.controllers.queries':queries,'frappe.model':model}), mock.patch.object(editor,'native_table_read',return_value=True):
			self.assertEqual(editor.invoice_link_names(args,editor.LINK_VALIDATE),['Unit'])
			self.assertEqual(frappe.get_all.call_args.kwargs['filters'],{'parent':'native-item','parenttype':'Item','parentfield':'uoms','uom':'Unit'})
			self.assertEqual(frappe.get_list.call_args.kwargs['filters'],{'name':'Unit','enabled':1})
			self.assertEqual(frappe.get_list.call_args.kwargs['limit_page_length'],1)
			frappe.get_all.return_value=[{'uom':'Unit'},{'uom':'Unit'}]
			self.denied(lambda:editor.invoice_link_names(args,editor.LINK_VALIDATE),503)
			model.get_permitted_fields=lambda *_a,**_kw:[]
			self.denied(lambda:editor.invoice_link_names(args,editor.LINK_VALIDATE),403)
		queries.get_item_uom_query.assert_not_called()

	def test_actual_item_query_adds_independent_exact_fence_after_party_filters(self):
		class Dot(dict):
			__getattr__=dict.get
		context=dataclasses.replace(self.context,operation='link-read')
		frappe.flags=Dot(company_editor=context)
		frappe.local.request_cache['company_editor_link_exact']=(context,'Item','exact-native-item')
		query=mock.Mock()
		query.where.return_value=query
		class Name:
			def __eq__(self,value):
				return ('exact-native-name',value)
		path=ROOT/'apps/erpnext/erpnext/controllers/queries.py'
		node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='item_query')
		fence=node.body[-2]
		self.assertIsInstance(fence,ast.If)
		space={'frappe':frappe,'query':query,'item':types.SimpleNamespace(name=Name()),'filters':{'name':['not in',['restricted-other-item']]}}
		exec(compile(ast.Module(body=[fence],type_ignores=[]),'<actual-native-item-final-fence>','exec'),space)
		query.where.assert_called_once_with(('exact-native-name','exact-native-item'))
		self.assertEqual(space['filters']['name'],['not in',['restricted-other-item']])
		frappe.local.request_cache['company_editor_link_exact']=(dataclasses.replace(context),'Item','other')
		self.assertIsNone(editor.canonical_link_name('Item'))

	def test_exact_item_fetch_checks_native_scalar_and_never_publishes_attachment(self):
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=mock.Mock(return_value=['name','grant_commission','image'])
		values={'grant_commission':1,'image':None}
		fields={'grant_commission':types.SimpleNamespace(fieldtype='Check'),'image':types.SimpleNamespace(fieldtype='Attach Image')}
		frappe.get_meta=lambda _:types.SimpleNamespace(get_field=lambda key:fields[key])
		frappe.get_doc=lambda *_:types.SimpleNamespace(check_permission=lambda _:None,get=lambda key:values[key])
		args=dict(doctype='Item',docname='native-item',fields_to_fetch=['grant_commission','image'])
		with mock.patch.dict(sys.modules,{'frappe.model':model}):
			self.assertEqual(editor.link_projection({'name':'native-item'},editor.LINK_VALIDATE,args),{'name':'native-item','grant_commission':1,'image':None})
			values['grant_commission']=True
			self.denied(lambda:editor.link_projection({'name':'native-item'},editor.LINK_VALIDATE,args),503)
			values['grant_commission']=0
			values['image']='/private/attachment'
			self.denied(lambda:editor.link_projection({'name':'native-item'},editor.LINK_VALIDATE,args),403)
			values['image']=None
			model.get_permitted_fields.return_value=['name','image']
			self.denied(lambda:editor.link_projection({'name':'native-item'},editor.LINK_VALIDATE,args),403)

	def test_tax_template_guard_has_closed_native_workflow_acl_and_finite_rate(self):
		class Dot(dict):
			__getattr__=dict.get
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=lambda kind,**_: ['item_tax_template'] if kind in ('Sales Invoice Item','Item Tax') else ['taxes']
		nested=types.ModuleType('frappe.utils.nestedset')
		nested.get_ancestors_of=lambda *_:['root-group']
		frappe.flags=Dot(company_editor=dataclasses.replace(self.context,operation='tax-calculate'))
		frappe.has_permission=lambda *_:True
		ctx=dict(item_code='native-item',company='native-company',base_net_rate=100,posting_date='2026-10-09')
		frappe.form_dict={'ctx':json.dumps(ctx)}
		def document(kind,name):
			return Dot(doctype=kind,name=name,check_permission=mock.Mock(),item_group='native-group',taxes=[],is_stock_item=0,has_serial_no=0,has_batch_no=0)
		frappe.get_doc=document
		with mock.patch.dict(sys.modules,{'frappe.model':model,'frappe.utils.nestedset':nested}), mock.patch.object(editor,'native_budget') as budget, mock.patch.object(editor,'native_table_read',return_value=True):
			self.assertEqual(editor.tax_template_guard(),ctx)
			for change in ({'base_net_rate':True},{'base_net_rate':float('inf')},{'posting_date':'2026-02-30'},{'ignore_permissions':False},{'tax_category':'caller-category'}):
				frappe.form_dict={'ctx':json.dumps({**ctx,**change})}
				self.denied(editor.tax_template_guard,400)
			frappe.form_dict={'ctx':json.dumps(ctx)}
			budget.assert_any_call(frappe.flags.company_editor.deadline)
			def taxed(kind,name):
				doc=document(kind,name)
				doc.taxes=[Dot(item_tax_template='native-template')]*17 if kind in ('Item','Item Group') else []
				return doc
			frappe.get_doc=taxed
			self.denied(editor.tax_template_guard,403)
			frappe.get_doc=document
			budget.side_effect=editor.Denied(503)
			self.denied(editor.tax_template_guard,503)
			budget.side_effect=None
			model.get_permitted_fields=lambda *_a,**_kw:[]
			self.denied(editor.tax_template_guard,403)

	def test_tax_template_projection_returns_only_current_native_template_name(self):
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=mock.Mock(return_value=['name'])
		permission=mock.Mock()
		frappe.flags=types.SimpleNamespace(company_editor=self.context)
		frappe.get_doc=lambda *_:types.SimpleNamespace(check_permission=permission)
		with mock.patch.dict(sys.modules,{'frappe.model':model}), mock.patch.object(editor,'native_budget'):
			self.assertIsNone(editor.tax_template_projection(None))
			self.assertEqual(editor.tax_template_projection('native-template'),'native-template')
			permission.assert_called_once_with('read')
			self.denied(lambda:editor.tax_template_projection({'name':'private'}),503)
			permission.side_effect=editor.Denied(403)
			self.denied(lambda:editor.tax_template_projection('native-template'),403)

	def test_rule_free_native_ceiling_bypasses_cache_and_refuses_rules_or_late_deadline(self):
		frappe.flags={'company_editor':dataclasses.replace(self.context,operation='calculate')}
		with mock.patch.object(editor,'native_budget') as budget, mock.patch.object(frappe,'get_all',return_value=[]) as query:
			self.assertTrue(editor.rule_free_admission())
			query.assert_called_once_with('Pricing Rule',fields=['name'],limit_page_length=1)
			query.return_value=[{'name':'unsupported-rule'}]
			self.denied(editor.rule_free_admission,403)
			query.reset_mock()
			budget.side_effect=editor.Denied(503)
			self.denied(editor.rule_free_admission,503)
			query.assert_not_called()
		frappe.flags={}
		self.assertFalse(editor.rule_free_admission())

	def test_actual_native_pricing_callee_never_evaluates_stored_rules_in_editor(self):
		path=ROOT/'apps/erpnext/erpnext/accounts/doctype/pricing_rule/utils.py'
		node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='get_pricing_rules')
		space={'frappe':frappe}
		exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual-native-rules>','exec'),space)
		frappe.flags={'company_editor':self.context}
		frappe.db=types.SimpleNamespace(count=mock.Mock(side_effect=AssertionError('cached count must not run')))
		with mock.patch.object(editor,'rule_free_admission',return_value=True) as guard:
			self.assertEqual(space['get_pricing_rules']({},{}),[])
			guard.assert_called_once_with()
			frappe.db.count.assert_not_called()
		frappe.flags={}
		frappe.db.count=mock.Mock(return_value=0)
		self.assertIsNone(space['get_pricing_rules']({},{}))
		frappe.db.count.assert_called_once_with('Pricing Rule',cache=True)

	def test_quantity_pricing_projection_keeps_only_exact_own_child_no_rule_branches(self):
		row={'name':'own-child'}
		doc=types.SimpleNamespace(name='own-draft')
		value=dict(doctype='Sales Invoice Item',name='own-child',child_docname='own-child',parent='own-draft',parenttype='Sales Invoice',has_margin=False,free_item_data=[])
		with mock.patch.object(editor,'calculator_projection',return_value={'name':'own-child'}):
			self.assertEqual(editor.rules_projection([value],doc,{'items':[row]}),[{'doctype':'Sales Invoice Item','name':'own-child'}])
			for change in ({'name':'foreign-child'},{'parent':'foreign-draft'},{'pricing_rules':'["rule"]'},{'free_item_data':[{}]},{'apply_rule_on_other_items':'other'}):
				self.denied(lambda:editor.rules_projection([{**value,**change}],doc,{'items':[row]}),503 if set(change)&{'name','parent'} else 403)
			self.denied(lambda:editor.rules_projection([],doc,{'items':[row]}),503)

	def test_native_applied_rule_shortcut_is_denied_before_cached_lookup(self):
		path=ROOT/'apps/erpnext/erpnext/accounts/doctype/pricing_rule/pricing_rule.py'
		node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='get_pricing_rule_for_item')
		frappe.flags={'company_editor':self.context}
		space={'frappe':frappe,'args':{'pricing_rules':'["cached-rule"]'}}
		with mock.patch.object(editor,'rule_free_admission',return_value=True) as admission:
			self.denied(lambda:exec(compile(ast.Module(body=[node.body[0]],type_ignores=[]),'<native-applied-rule-ceiling>','exec'),space),403)
			admission.assert_called_once_with()

	def test_quantity_readonly_rollback_and_late_authority_failure_never_publish(self):
		module=types.ModuleType('erpnext.accounts.doctype.pricing_rule.pricing_rule')
		module.apply_pricing_rule=mock.Mock()
		context=dataclasses.replace(self.context,operation='calculate')
		frappe.flags=types.SimpleNamespace(company_editor=context)
		frappe.form_dict={'doc':'{}'}
		frappe.db=types.SimpleNamespace(rollback=mock.Mock(),sql=mock.Mock())
		frappe.call=mock.Mock(return_value=[])
		with mock.patch.dict(sys.modules,{module.__name__:module}), mock.patch.object(editor,'native_budget'), mock.patch.object(editor,'price_list_guard',return_value=(types.SimpleNamespace(),{'items':[]})), mock.patch.object(editor,'rule_free_admission',return_value=True), mock.patch.object(editor,'recheck_before_commit',side_effect=editor.Denied(403)):
			self.denied(editor.apply_rules,403)
			self.assertEqual(frappe.db.rollback.call_count,2)
			frappe.db.sql.assert_called_once_with('SET TRANSACTION READ ONLY')

	def test_table_field_permission_uses_exact_native_table_options_and_current_permlevel(self):
		field=types.SimpleNamespace(fieldtype='Table',options='Item Tax',permlevel=1)
		levels=[0,1]
		frappe.get_meta=lambda _:types.SimpleNamespace(get_field=lambda _:field,get_permlevel_access=lambda permission:levels if permission=='read' else [])
		self.assertTrue(editor.native_table_read('Item','taxes','Item Tax'))
		levels.remove(1)
		self.assertFalse(editor.native_table_read('Item','taxes','Item Tax'))
		levels.append(1)
		self.assertFalse(editor.native_table_read('Item','taxes','wrong-child'))
		field.fieldtype='Data'
		self.assertFalse(editor.native_table_read('Item','taxes','Item Tax'))
		# Actual shipped native scalar API explicitly filters Table fields.
		model=ast.parse((ROOT/'frappe/model/__init__.py').read_text())
		node=next(n for n in model.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='no_value_fields' for t in n.targets))
		self.assertIn('Table',ast.literal_eval(node.value))

	def test_quantity_final_projection_rechecks_raw_addresses_without_projected_roundtrip(self):
		module=types.ModuleType('erpnext.accounts.doctype.pricing_rule.pricing_rule')
		module.apply_pricing_rule=mock.Mock()
		context=dataclasses.replace(self.context,operation='calculate')
		frappe.flags=types.SimpleNamespace(company_editor=context)
		frappe.form_dict={'doc':'{}'}
		frappe.db=types.SimpleNamespace(rollback=mock.Mock(),sql=mock.Mock())
		raw=[dict(doctype='Sales Invoice Item',name='own-child',child_docname='own-child',parent='own-draft',parenttype='Sales Invoice')]
		frappe.call=mock.Mock(return_value=raw)
		def project(value,*_):
			if value is not raw or value[0].get('child_docname')!='own-child':
				raise editor.Denied(503)
			return [{'doctype':'Sales Invoice Item','name':'own-child'}]
		with mock.patch.dict(sys.modules,{module.__name__:module}), mock.patch.object(editor,'native_budget'), mock.patch.object(editor,'price_list_guard',return_value=(types.SimpleNamespace(),{'items':[]})), mock.patch.object(editor,'rule_free_admission',return_value=True), mock.patch.object(editor,'recheck_before_commit') as final, mock.patch.object(editor,'rules_projection',side_effect=project) as projection:
			self.assertEqual(editor.apply_rules(),[{'doctype':'Sales Invoice Item','name':'own-child'}])
			self.assertEqual(projection.call_count,2)
			final.assert_called_once_with(context)
			self.assertEqual(frappe.db.rollback.call_count,2)


	def test_initial_price_callback_only_accepts_new_empty_item_draft(self):
		payload,ctx=self.price_fixture()
		payload.pop('customer')
		ctx.pop('customer')
		frappe.form_dict={'ctx':json.dumps(ctx),'doc':json.dumps(payload)}
		_,checked=editor.price_list_guard()
		self.assertEqual(checked['plc_conversion_rate'],1)
		self.denied(lambda:editor.price_list_guard('args'),400)
		for change in ({'__islocal':0},{'items':[{'doctype':'Sales Invoice Item','__islocal':1,'item_code':'concealed-item'}]}):
			frappe.form_dict={'ctx':json.dumps(ctx),'doc':json.dumps({**payload,**change})}
			self.denied(editor.price_list_guard,403)

	def test_company_fetch_uses_native_destination_permission_not_target_name_on_parent(self):
		class Dot(dict):
			__getattr__=dict.get
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=mock.Mock(return_value=['name','company','company_tax_id'])
		frappe.flags=Dot(company_editor=dataclasses.replace(self.context,operation='link-read'))
		frappe.request=request('/api/method/'+editor.LINK_VALIDATE)
		frappe.has_permission=mock.Mock(return_value=True)
		destination=Dot(fieldname='company_tax_id',fetch_from='company.tax_id')
		metadata=Dot(get_field=lambda _:Dot(fieldtype='Link',options='Company'),fields=[destination],is_virtual=False,translated_doctype=False)
		frappe.get_meta=mock.Mock(return_value=metadata)
		frappe.get_hooks=mock.Mock(return_value=types.SimpleNamespace(standard_queries={}))
		frappe.form_dict=dict(doctype='Company',reference_doctype='Sales Invoice',link_fieldname='company',filters='{}',ignore_user_permissions='0',docname='native-company',fields_to_fetch='["tax_id"]')
		with mock.patch.dict(sys.modules,{'frappe.model':model}):
			self.assertEqual(editor.link_guard(editor.LINK_VALIDATE)['fields_to_fetch'],['tax_id'])
			model.get_permitted_fields.return_value=['name','company','tax_id']
			self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),403)
			model.get_permitted_fields.return_value=['name','company','company_tax_id']
			destination['fetch_from']='another_link.tax_id'
			self.denied(lambda:editor.link_guard(editor.LINK_VALIDATE),403)

	def initialization_fixture(self,method,args):
		class Dot(dict):
			__getattr__=dict.get
		frappe.flags=Dot(company_editor=dataclasses.replace(self.context,operation='invoice-initialize'))
		frappe.request=request('/api/method/'+method,'POST')
		frappe.form_dict=dict(args)
		frappe.has_permission=mock.Mock(return_value=True)
		model=types.ModuleType('frappe.model')
		model.get_permitted_fields=mock.Mock(return_value=['company','company_address','taxes_and_charges'])
		fields={'company_address':Dot(fieldtype='Link',options='Address'),'taxes_and_charges':Dot(fieldtype='Link',options='Sales Taxes and Charges Template'),'round_row_wise_tax':Dot(fieldtype='Check')}
		frappe.get_meta=lambda _:Dot(get_field=lambda key:fields.get(key))
		return model

	def test_invoice_initialization_requires_exact_stock_args_writer_and_parent_field_access(self):
		cases=[(editor.ROUND_OFF,{'company':'native-company','account_list':'[]'}),(editor.COMPANY_ADDRESS,{'name':'native-company','existing_address':''}),(editor.DEFAULT_TAXES,{'company':'native-company','master_doctype':'Sales Taxes and Charges Template','tax_template':''}),(editor.DIMENSIONS,{}),(editor.ROUNDING_SETTING,{})]
		for method,args in cases:
			model=self.initialization_fixture(method,args)
			with mock.patch.dict(sys.modules,{'frappe.model':model}),mock.patch.object(editor,'native_budget'),mock.patch.object(editor,'native_table_read',return_value=True):
				editor.invoice_initialization_guard(method)
				frappe.form_dict={**args,'ignore_permissions':1}
				self.denied(lambda:editor.invoice_initialization_guard(method),400)
				frappe.form_dict=dict(args)
				frappe.flags.company_editor=dataclasses.replace(frappe.flags.company_editor,value={**self.value,'scopes':['erp:read']})
				self.denied(lambda:editor.invoice_initialization_guard(method),403)
		model=self.initialization_fixture(editor.COMPANY_ADDRESS,{'name':'native-company','existing_address':''})
		with mock.patch.dict(sys.modules,{'frappe.model':model}),mock.patch.object(editor,'native_budget'):
			model.get_permitted_fields.return_value=['company']
			self.denied(lambda:editor.invoice_initialization_guard(editor.COMPANY_ADDRESS),403)

	def test_configured_dimensions_and_rounding_nonboolean_do_not_publish_empty_shapes(self):
		self.initialization_fixture(editor.DIMENSIONS,{})
		frappe.get_all=mock.Mock(return_value=[])
		frappe.db=types.SimpleNamespace(get_single_value=mock.Mock(return_value=0))
		with mock.patch.object(editor,'native_budget'):
			self.assertEqual(editor.invoice_initialization_value(editor.DIMENSIONS,{}),[[],{}])
			self.assertEqual(frappe.get_all.call_args.kwargs['limit_page_length'],1)
			frappe.get_all.return_value=[{'name':'private-config'}]
			self.denied(lambda:editor.invoice_initialization_value(editor.DIMENSIONS,{}),403)
			self.assertEqual(editor.invoice_initialization_value(editor.ROUNDING_SETTING,{}),0)
			for value in (True,None,'0',2):
				frappe.db.get_single_value.return_value=value
				self.denied(lambda:editor.invoice_initialization_value(editor.ROUNDING_SETTING,{}),503)

	def test_initialization_rolls_back_refusal_and_rechecks_fresh_configuration(self):
		self.initialization_fixture(editor.DIMENSIONS,{})
		frappe.db=types.SimpleNamespace(rollback=mock.Mock(),sql=mock.Mock())
		with mock.patch.object(editor,'native_budget'),mock.patch.object(editor,'invoice_initialization_guard',return_value={}),mock.patch.object(editor,'invoice_initialization_value',side_effect=[[[],{}],[[],{}]]),mock.patch.object(editor,'recheck_before_commit') as current:
			self.assertEqual(editor.read_invoice_initialization(editor.DIMENSIONS),[[],{}])
			current.assert_called_once()
			self.assertEqual(frappe.db.rollback.call_count,4)
			self.assertEqual(frappe.db.sql.call_args_list,[mock.call('SET TRANSACTION READ ONLY')]*2)
		with mock.patch.object(editor,'native_budget'),mock.patch.object(editor,'invoice_initialization_guard',return_value={}),mock.patch.object(editor,'invoice_initialization_value',side_effect=[[[],{}],[['changed'],{}]]),mock.patch.object(editor,'recheck_before_commit'):
			self.denied(lambda:editor.read_invoice_initialization(editor.DIMENSIONS),403)
		with mock.patch.object(editor,'native_budget'),mock.patch.object(editor,'invoice_initialization_guard',return_value={}),mock.patch.object(editor,'invoice_initialization_value',side_effect=RuntimeError('controlled native failure')):
			with self.assertRaises(RuntimeError):
				editor.read_invoice_initialization(editor.DIMENSIONS)


	def test_stock_default_tax_projection_uses_actual_helper_with_native_acl_and_bounds(self):
		class Doc(types.SimpleNamespace):
			def check_permission(value,kind):
				self.assertEqual(kind,'read')
				if value.denied:
					raise editor.Denied(403)
		class Row(dict):
			def as_dict(value):
				return dict(value)
		fields={'company','is_default','disabled','tax_category','default_currency','charge_type','account_head','cost_center','description','rate','included_in_print_rate','included_in_paid_amount','dont_recompute_tax','set_by_item_tax_template','is_tax_withholding_account','is_group','account_currency','account_type'}
		state={}
		def reset():
			row=Row(charge_type='On Net Total',account_head='TaxAccount',cost_center='Center',description='ST 6% @ 6.0',rate=6)
			state.update(candidates=[types.SimpleNamespace(name='StockDefault')],children=[{'name':'native-row'}],row=row,fields=set(fields),calls=[],selected='')
			state['docs']={'StockDefault':Doc(denied=False,company='native-company',is_default=1,disabled=0,tax_category=None,taxes=[row]),'native-company':Doc(denied=False,default_currency='USD'),'TaxAccount':Doc(denied=False,company='native-company',is_group=0,disabled=0,account_currency='USD',account_type='Tax'),'Center':Doc(denied=False,company='native-company',is_group=0,disabled=0)}
		model=self.initialization_fixture(editor.DEFAULT_TAXES,{'company':'native-company','master_doctype':'Sales Taxes and Charges Template','tax_template':''})
		model.get_permitted_fields=lambda *args,**kwargs:state['fields']
		def all_(kind,**kwargs):
			state['calls'].append((kind,kwargs))
			return state['candidates'] if kind=='Sales Taxes and Charges Template' else state['children']
		frappe.get_all=all_
		frappe.get_doc=lambda kind,name:state['docs'][name]
		frappe.get_meta=lambda kind:types.SimpleNamespace(get_field=lambda field:types.SimpleNamespace(fieldtype='Link',options='Account' if field=='account_head' else 'Cost Center'))
		frappe.call=lambda fn,**kwargs:None if kwargs['tax_template'] else {'taxes_and_charges':'StockDefault','taxes':[state['row']]}
		modules={name:types.ModuleType(name) for name in ['erpnext','erpnext.controllers','erpnext.controllers.accounts_controller']}
		modules['frappe.model']=model
		modules['erpnext.controllers.accounts_controller'].get_default_taxes_and_charges=mock.Mock()
		def run():
			return editor.default_native_taxes({'company':'native-company','master_doctype':'Sales Taxes and Charges Template','tax_template':state['selected']})
		with mock.patch.dict(sys.modules,modules),mock.patch.object(editor,'native_budget'),mock.patch.object(editor,'native_table_read',return_value=True):
			reset()
			result=run()
			self.assertEqual(result['taxes'][0]['rate'],6)
			self.assertNotIn('name',result['taxes'][0])
			self.assertEqual([call[1]['limit_page_length'] for call in state['calls']],[2,33])
			for empty in (None,''):
				state['docs']['TaxAccount'].account_currency=empty
				self.assertEqual(run()['taxes'][0]['rate'],6)
			reset()
			state['selected']='StockDefault'
			self.assertIsNone(run())
			state['selected']='foreign'
			self.denied(run,403)
			reset()
			state['candidates']=[]
			self.assertEqual(run(),{'taxes_and_charges':None,'taxes':None})
			mutations=[lambda:state.update(candidates=state['candidates']*2),lambda:state.update(children=[{}]*33),lambda:setattr(state['docs']['StockDefault'],'company','CompanyB'),lambda:setattr(state['docs']['TaxAccount'],'denied',True),lambda:setattr(state['docs']['TaxAccount'],'account_currency','EUR'),lambda:setattr(state['docs']['TaxAccount'],'account_type','Receivable'),lambda:state.update(fields=state['fields']-{'rate'})]
			for mutate in mutations:
				reset()
				mutate()
				self.denied(run,403)
			for key,value in [('charge_type','On Previous Row Total'),('included_in_print_rate',1),('project','foreign'),('row_id','1'),('rate',True),('rate',float('nan')),('rate',101),('description','<script>')]:
				with self.subTest(key=key,value=value):
					reset()
					state['row'][key]=value
					self.denied(run,403)

if __name__ == '__main__':
	unittest.main()
