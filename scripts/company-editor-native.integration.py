"""Owned two-site native Desk/callback/save proof; private Core is controlled HTTP.

No Frappe session/model/ACL/database methods are replaced. Not genuine Core,
GoTrue, customer-browser acceptance or enabled production beta evidence.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

from werkzeug.test import Client
from werkzeug.wrappers import Response

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('native_fixture', ROOT / 'scripts/company-session-native.integration.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
frappe, adapter = fixture.frappe, fixture.adapter
from frappe import company_editor as editor

SECRET = 's' * 43
TOKENS = {'exs_' + char * 43: subject for char, subject in zip('abc', fixture.SUBJECTS, strict=True)}
TOKENS.update({'exs_' + 'd' * 43: fixture.SUBJECTS[0], 'exs_' + 'e' * 43: fixture.SUBJECTS[2]})
TRACE = []
STATE = {'token': None, 'revoked': set(), 'reader': False, 'calls': 0, 'revoke_at': 0, 'errors': [], 'foreign': False, 'permission_at': 0}
WRITER = 'Company Editor Fixture Writer'


def sanitized_trace(frame, event, arg):
	# Owned harness only: no exception text, locals, SQL, headers or credentials.
	if event == 'call':
		return sanitized_trace if (frame.f_code.co_filename.endswith('/company_editor.py') and frame.f_code.co_name in ('application', 'calculator_guard', 'calculator_projection', 'price_list_guard', 'rules_projection', 'apply_rules', 'rule_free_admission', 'read_tax_template', 'tax_template_guard', 'sales_settings_guard')) or (frame.f_code.co_filename.endswith('/model/document.py') and frame.f_code.co_name in ('check_permission', 'load_from_db')) or (frame.f_code.co_filename.endswith('/app.py') and frame.f_code.co_name == 'native_application') else None
	if event == 'exception' and ((Path(frame.f_code.co_filename).name == 'company_editor.py' and frame.f_code.co_name in ('application', 'calculator_guard', 'calculator_projection', 'price_list_guard', 'rules_projection', 'apply_rules', 'rule_free_admission', 'read_tax_template', 'tax_template_guard', 'sales_settings_guard')) or (frame.f_code.co_filename.endswith('/model/document.py') and frame.f_code.co_name in ('check_permission', 'load_from_db')) or (frame.f_code.co_filename.endswith('/app.py') and frame.f_code.co_name == 'native_application')):
		name = arg[0].__name__
		if name.isidentifier() and len(name) <= 64:
			prior_type = next((row for row in TRACE if 'permission_doctype' in row and row.get('class') == name), None)
			TRACE.clear()
			if prior_type:
				TRACE.append(prior_type)
			public_types = {'Customer', 'Sales Invoice', 'Sales Invoice Item', 'Company', 'Item', 'Item Price', 'Price List', 'Account', 'Cost Center', 'UOM', 'Currency', 'Customer Group', 'Territory', 'Payment Terms Template', 'Payment Term', 'Sales Taxes and Charges Template', 'Item Group', 'Warehouse'}
			checked_type = getattr(frame.f_locals.get('self'), 'doctype', None)
			if checked_type in public_types:
				TRACE.append({'permission_doctype': checked_type, 'class': name})
			error, seen = arg[1], set()
			for cause in range(3):
				if error is None or id(error) in seen:
					break
				seen.add(id(error))
				tb, locations = error.__traceback__, []
				while tb and len(locations) < 96:
					location = tb.tb_frame
					if '/frappe-bench/apps/' in location.f_code.co_filename:
						locations.append({'file': Path(location.f_code.co_filename).name, 'function': location.f_code.co_name, 'line': tb.tb_lineno, 'class': type(error).__name__, 'cause': cause})
					tb = tb.tb_next
				TRACE.extend(locations[-12:])
				error = error.__cause__ or error.__context__
	return sanitized_trace


PROFILE = {}
PROFILE_START = {}


def native_profile(frame, event, arg):
	name, filename = frame.f_code.co_name, frame.f_code.co_filename
	key = None
	if name == 'sql' and filename.endswith('/database/database.py'):
		key = 'native_sql'
	elif name == 'get_meta' and filename.endswith('/model/meta.py'):
		key = 'native_meta'
	elif name in ('initialize_native_request', 'recheck_before_commit') and filename.endswith('/company_editor.py'):
		key = name
	elif name == 'savedocs' and filename.endswith('/desk/form/save.py'):
		key = 'native_savedocs'
	elif name == 'get_bootinfo' and filename.endswith('/boot.py'):
		key = 'native_boot'
	if key:
		if event == 'call':
			PROFILE_START[id(frame)] = time.monotonic()
		elif event == 'return':
			started = PROFILE_START.pop(id(frame), None)
			if started is not None:
				item = PROFILE.setdefault(key, {'count': 0, 'seconds': 0})
				item['count'] += 1
				item['seconds'] += time.monotonic() - started


def authority(token):
	value = fixture.authority(CONFIG, TOKENS[token])
	value.pop('subscription_entitled')
	value.update(version=2, access_entitled=True, entitlement_kind='beta', scopes=['erp:read'] if STATE['reader'] else ['erp:read', 'erp:write'])
	if STATE['foreign']:
		value['generation_id'] = '00000000-0000-4000-8000-000000000099'
	return value


class Authority(BaseHTTPRequestHandler):
	def log_message(self, *_):
		pass

	def do_POST(self):
		try:
			self.connection.settimeout(3)
			length = int(self.headers.get('Content-Length', '0'))
			if not 0 < length <= 2048:
				raise ValueError('Bounded controlled request required')
			expected = 'Basic ' + base64.b64encode((CONFIG.client_id + ':' + SECRET).encode()).decode()
			if self.headers.get('Authorization') != expected or self.headers.get('Content-Type') != 'application/json':
				raise ValueError('Fixed client required')
			body = adapter.exact_json(self.rfile.read(length))
			operation = self.path.removeprefix('/internal/session-broker/')
			status = 200
			if operation == 'token':
				if set(body) != {'grant_type', 'code', 'redirect_uri', 'code_verifier', 'state_hash'} or body['redirect_uri'] != CONFIG.origin + '/company-session/callback':
					raise ValueError('Fixed code exchange required')
				payload = {'session_token': STATE['token'], 'token_type': 'Bearer', 'expires_in': 900}
			else:
				if set(body) != {'session_token'} or body['session_token'] not in TOKENS:
					raise ValueError('Known controlled session required')
				token = body['session_token']
				if operation == 'introspect':
					STATE['calls'] += 1
					if STATE['calls'] == STATE['permission_at']:
						fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent': 'Customer', 'role': WRITER}, 'write', 0))
					if STATE['calls'] == STATE['revoke_at']:
						STATE['revoked'].add(token)
					status = 403 if token in STATE['revoked'] else 200
					payload = {'error': 'revoked'} if status != 200 else authority(token)
				elif operation == 'revoke':
					STATE['revoked'].add(token)
					payload = {'revoked': True}
				else:
					raise ValueError('Unknown controlled operation')
			data = json.dumps(payload, separators=(',', ':')).encode()
			self.send_response(status)
			self.send_header('Content-Type', 'application/json')
			self.send_header('Content-Length', str(len(data)))
			self.send_header('Connection', 'close')
			self.end_headers()
			self.wfile.write(data)
		except Exception as error:
			STATE['errors'].append(type(error).__name__)
			self.send_error(500)


def write_setup(site, plane):
	fixture.connect(site)
	try:
		from datetime import date
		year = date.today().year
		frappe.get_doc({'doctype': 'Fiscal Year', 'year': str(year), 'year_start_date': f'{year}-01-01', 'year_end_date': f'{year}-12-31'}).insert()
		if not frappe.db.exists('Warehouse Type', 'Transit'):
			frappe.get_doc({'doctype': 'Warehouse Type', 'name': 'Transit'}).insert()
		frappe.get_doc({'doctype': 'Company', 'company_name': 'Editor ' + plane, 'abbr': 'E' + plane.upper(), 'country': 'United States', 'default_currency': 'USD', 'create_chart_of_accounts_based_on': 'Standard Template', 'chart_of_accounts': 'Standard', 'enable_perpetual_inventory': 0}).insert()
		if not frappe.db.exists('UOM', 'Unit'):
			frappe.get_doc({'doctype': 'UOM', 'uom_name': 'Unit', 'must_be_whole_number': 1}).insert()
		if not frappe.db.exists('Item Group', 'All Item Groups'):
			frappe.get_doc({'doctype': 'Item Group', 'item_group_name': 'All Item Groups', 'is_group': 1}).insert()
		group = frappe.get_doc({'doctype': 'Item Group', 'item_group_name': 'Editor Services', 'parent_item_group': 'All Item Groups'}).insert()
		frappe.get_doc({'doctype': 'Item', 'item_code': 'Editor-Service-' + plane, 'item_name': 'Editor Service', 'item_group': group.name, 'stock_uom': 'Unit', 'is_stock_item': 0, 'is_sales_item': 1, 'is_purchase_item': 0}).insert()
		frappe.get_doc({'doctype': 'Item', 'item_code': 'Editor-Denied-' + plane, 'item_name': 'Editor Denied Item', 'item_group': group.name, 'stock_uom': 'Unit', 'is_stock_item': 0, 'is_sales_item': 1, 'is_purchase_item': 0}).insert()
		price = frappe.get_doc({'doctype': 'Price List', 'price_list_name': 'Editor USD', 'currency': 'USD', 'selling': 1, 'enabled': 1}).insert()
		frappe.get_doc({'doctype': 'Item Price', 'item_code': 'Editor-Service-' + plane, 'price_list': price.name, 'price_list_rate': 100, 'uom': 'Unit'}).insert()
		frappe.get_doc({'doctype': 'Price List', 'price_list_name': 'Editor USD Rollback', 'currency': 'USD', 'selling': 1, 'enabled': 1}).insert()
		frappe.db.set_single_value('Selling Settings', 'selling_price_list', price.name)
		frappe.db.set_single_value('Stock Settings', 'auto_insert_price_list_rate_if_missing', 1)
		frappe.get_doc({'doctype': 'Role', 'role_name': WRITER, 'desk_access': 1}).insert()
		for kind in ('Customer', 'Sales Invoice'):
			fixture.add_permission(kind, WRITER, ptype='read')
			from frappe.permissions import update_permission_property
			for permission in ('write', 'create'):
				update_permission_property(kind, WRITER, 0, permission, 1)
			grant = frappe.db.get_value('Custom DocPerm', {'parent': kind, 'role': WRITER}, ['read', 'write', 'create'], as_dict=True)
			if not grant or any(grant[key] != 1 for key in ('read', 'write', 'create')):
				raise ValueError('Actual native writer grant absent')
		for kind in ('Company', 'Item', 'Item Price', 'Price List', 'Account', 'Cost Center', 'UOM', 'Currency', 'Customer Group', 'Territory', 'Payment Terms Template', 'Payment Term', 'Sales Taxes and Charges Template', 'Item Group', 'Warehouse'):
			fixture.add_permission(kind, WRITER, ptype='read')
		from frappe.permissions import update_permission_property
		update_permission_property('Item Price', WRITER, 0, 'write', 1)
		update_permission_property('Item Price', WRITER, 0, 'create', 1)
		for user in (['aowner', 'amember'] if plane == 'a' else ['bowner']):
			doc = frappe.get_doc('User', user + '@native-acl.example.test')
			doc.append('roles', {'role': WRITER})
			doc.save()
		for index in range(12):
			frappe.get_doc({'doctype':'Customer Group','customer_group_name':'EditorBound-' + plane + '-' + str(index),'parent_customer_group':'All Customer Groups','is_group':0}).insert()
		# Full shipped operator setup sequence; completion flags are set by
		# native successful stages, never by a fixture boot/readiness shim.
		from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
		result = setup_complete({'language': 'English', 'country': 'United States',
			'currency': 'USD', 'timezone': 'UTC', 'company_name': 'Editor ' + plane,
			'company_abbr': 'E' + plane.upper(), 'chart_of_accounts': 'Standard',
			'fy_start_date': f'{year}-01-01', 'fy_end_date': f'{year}-12-31',
			'enable_telemetry': 0, 'setup_demo': 0})
		if result != {'status': 'ok'} or not frappe.is_setup_complete():
			raise ValueError('Native operator setup incomplete')
		# Retain the qualified same-currency native price source after stock
		# setup installs its global defaults.
		frappe.db.set_single_value('Selling Settings', 'selling_price_list', price.name)
		frappe.db.commit()
	finally:
		fixture.close()


def verify_prepared_sites():
	# Post-setup gate: the original verify_sites intentionally requires absent
	# fixture users and is used only before operator preparation.
	if not re.fullmatch(r'[a-f0-9]{32}', ARGS.fixture_id):
		raise ValueError('Random owned fixture ID required')
	databases = set()
	for site, plane in ((ARGS.site_a, 'a'), (ARGS.site_b, 'b')):
		if site != 'erp.acl-' + plane + '-' + ARGS.fixture_id[:12] + '.example.test':
			raise ValueError('Exact prepared site namespace required')
		path = ARGS.sites_path / site / 'site_config.json'
		if path.resolve() != path:
			raise ValueError('Canonical owned config required')
		data = json.loads(path.read_text())
		if data.get('allow_tests') is not True or data.get('company_acl_fixture') != ARGS.fixture_id or data.get('company_editor_fixture_prepared') != ARGS.fixture_id or data.get('db_type') != 'postgres' or not isinstance(data.get('db_name'), str) or not data['db_name']:
			raise ValueError('Owned native preparation receipt absent')
		databases.add(data['db_name'])
	if len(databases) != 2:
		raise ValueError('Distinct prepared native databases required')


class NativeEditor(unittest.TestCase):
	def setUp(self):
		STATE.update(token='exs_' + ('a' if ARGS.plane == 'a' else 'c') * 43, revoked=set(), reader=False, calls=0, revoke_at=0, errors=[], foreign=False, permission_at=0)
		self.client = Client(APP.application, Response, use_cookies=False)
		self.cookies = {}
		self.csrf = None
		self.login()

	def tearDown(self):
		self.assertEqual(STATE['errors'], [])

	def call(self, path, method='GET', data=None, headers=None, host=None):
		cookie = '; '.join(k + '=' + v for k, v in self.cookies.items())
		TRACE.clear()
		PROFILE.clear()
		PROFILE_START.clear()
		started = time.monotonic()
		# Trace is opt-in: an instrumented request is not latency/persistence proof.
		if os.environ.get("OWNED_NATIVE_TRACE") == "true" or ARGS.diagnostic or ARGS.calculator_probe:
			sys.settrace(sanitized_trace)
		if ARGS.diagnostic:
			sys.setprofile(native_profile)
			if path == "/desk":
				sys.settrace(sanitized_trace)
		try:
			response = self.client.open(path, method=method, base_url='https://' + (host or CONFIG.site), data=data,
				headers={'Cookie': cookie, 'Origin': CONFIG.origin, **({'X-Frappe-CSRF-Token': self.csrf} if self.csrf else {}), **(headers or {})},
				environ_overrides={'RAW_URI': path})
		finally:
			sys.settrace(None)
			sys.setprofile(None)
		print('OWNED_REQUEST_TIMING ' + json.dumps({'operation': 'save' if path.endswith('savedocs') else 'native', 'status': response.status_code, 'elapsed_s': round(time.monotonic() - started, 3)}))
		if ARGS.diagnostic:
			print('OWNED_NATIVE_AGGREGATE ' + json.dumps({key: {'count': value['count'], 'seconds': round(value['seconds'], 3)} for key, value in PROFILE.items()}))
		if response.status_code >= 400:
			print('OWNED_SANITIZED_EXCEPTION ' + json.dumps(TRACE, separators=(',', ':')))

		response.get_data()
		for raw in response.headers.getlist('Set-Cookie'):
			parsed = SimpleCookie(raw)
			for key, value in parsed.items():
				self.cookies[key] = value.value
		response.close()
		return response

	def login(self):
		start = self.call('/company-session/start')
		self.assertEqual(start.status_code, 303)
		state = parse_qs(urlsplit(start.headers['Location']).query)['state'][0]
		callback = self.call('/company-session/callback?' + urlencode({'code': 'exc_' + 'b' * 43, 'state': state}))
		self.assertEqual(callback.status_code, 303)
		self.assertEqual(callback.headers['Location'], CONFIG.origin + '/desk/customer')
		self.assertIn('sid', self.cookies)
		fixture.connect(CONFIG.site)
		try:
			row = frappe.db.get_value('Sessions', {'sid': self.cookies['sid']}, ['user', 'sessiondata'], as_dict=True, order_by='sid')
			self.assertEqual(row.user, CONFIG.subjects[TOKENS[STATE['token']]])
			self.data = json.loads(row.sessiondata)
			self.assertEqual(self.data['company_binding']['credential_hash'], hashlib.sha256(STATE['token'].encode()).hexdigest())
			self.assertEqual(self.data['company_binding']['native_id'], CONFIG.site)
			self.csrf = self.data['csrf_token']
		finally:
			fixture.close()

	def save(self, doc, **kw):
		return self.call('/api/method/frappe.desk.form.save.savedocs', 'POST', {'doc': frappe.as_json(doc, indent=None), 'action': 'Save'}, **kw)

	def existing(self, name):
		fixture.connect(CONFIG.site)
		try:
			return frappe.get_doc('Customer', name).as_dict()
		finally:
			fixture.close()

	def test_native_invoice_initialization_empty_configuration_and_permission_ceiling(self):
		company='Editor '+ARGS.plane
		cases=[(editor.ROUND_OFF,{'company':company,'account_list':'[]'},None),(editor.ROUNDING_SETTING,{},0),(editor.DIMENSIONS,{},[[],{}]),(editor.DEFAULT_TAXES,{'company':company,'master_doctype':'Sales Taxes and Charges Template','tax_template':''},{'taxes_and_charges':None,'taxes':None}),(editor.COMPANY_ADDRESS,{'name':company,'existing_address':''},None)]
		for method,args,expected in cases:
			response=self.call('/api/method/'+method,'POST',args)
			self.assertEqual(response.status_code,200)
			if method == editor.ROUNDING_SETTING:
				self.assertIn(response.get_json()['message'],(0,1))
			else:
				self.assertEqual(response.get_json().get('message'),expected)
			self.assertEqual(self.call('/api/method/'+method,'POST',{**args,'extra':'unadmitted'}).status_code,400)
		STATE['reader']=True
		self.cookies,self.csrf={},None
		try:
			self.login()
			for method,args,_ in cases:
				self.assertEqual(self.call('/api/method/'+method,'POST',args).status_code,403)
		finally:
			STATE['reader']=False
			self.cookies,self.csrf={},None
			self.login()
		fixture.mutate(CONFIG.site,lambda:frappe.db.set_value('Custom DocPerm',{'parent':'Sales Invoice','role':WRITER},'create',0))
		try:
			self.assertEqual(self.call('/api/method/'+editor.DIMENSIONS,'POST',{}).status_code,403)
		finally:
			fixture.mutate(CONFIG.site,lambda:frappe.db.set_value('Custom DocPerm',{'parent':'Sales Invoice','role':WRITER},'create',1))
		name='Owned-Dimension-'+ARGS.plane
		fixture.mutate(CONFIG.site,lambda:frappe.db.sql('INSERT INTO "tabAccounting Dimension" (name,disabled) VALUES (%s,1)',(name,)))
		try:
			self.assertEqual(self.call('/api/method/'+editor.DIMENSIONS,'POST',{}).status_code,403)
		finally:
			fixture.mutate(CONFIG.site,lambda:frappe.db.delete('Accounting Dimension',{'name':name}))
		self.assertEqual(self.call('/api/method/'+editor.DIMENSIONS,'POST',{}).get_json()['message'],[[],{}])
		print('OWNED_NATIVE_INVOICE_INITIALIZATION_EMPTY_AND_CURRENT_PERMISSION '+ARGS.plane)

	def test_customer_create_edit_reload_and_desk_boot(self):
		settings = self.call("/api/method/" + editor.LIST_SETTINGS, "POST", {"doctype":"Customer"})
		self.assertEqual(settings.status_code, 200)
		self.assertEqual(settings.json.get("message"), {})
		doc = {'doctype': 'Customer', 'name': 'new-customer-fixture', '__islocal': 1, 'customer_name': 'Editor ' + ARGS.plane, 'customer_type': 'Individual', 'customer_group': 'ACL ' + ARGS.plane, 'territory': 'ACL ' + ARGS.plane}
		created = self.save(doc)
		self.assertEqual(created.status_code, 200)
		actual = created.get_json()['docs'][0]
		actual['customer_name'] = 'Edited ' + ARGS.plane
		self.assertEqual(self.save(actual).status_code, 200)
		reloaded = self.call('/api/method/frappe.desk.form.load.getdoc?' + urlencode({'doctype': 'Customer', 'name': actual['name']}))
		self.assertEqual(reloaded.status_code, 200)
		self.assertEqual(reloaded.get_json()['docs'][0]['customer_name'], 'Edited ' + ARGS.plane)
		self.assertEqual(self.existing(actual['name'])['customer_name'], 'Edited ' + ARGS.plane)
		print('OWNED_CUSTOMER_CREATE_EDIT_RELOAD_PERSISTED ' + ARGS.plane)
		args = {'doctype':'Customer Group','reference_doctype':'Customer','link_fieldname':'customer_group','filters':'{"is_group":0}','ignore_user_permissions':'0','docname':'ACL ' + ARGS.plane,'txt':'ACL ' + ARGS.plane,'fields_to_fetch':'[]','page_length':'10'}
		path = '/api/method/' + editor.LINK_VALIDATE + '?' + urlencode(args)
		self.assertEqual(self.call(path).json['message'], {'name':'ACL ' + ARGS.plane})
		search_args = {key:value for key,value in args.items() if key not in ('docname','fields_to_fetch')}
		search_args['txt'] = 'EditorBound-' + ARGS.plane
		bounded = self.call('/api/method/' + editor.LINK_SEARCH + '?' + urlencode(search_args))
		self.assertEqual(bounded.status_code,200)
		self.assertEqual(len(bounded.json['message']),10)
		self.assertTrue(all(set(row)=={'value'} for row in bounded.json['message']))
		fixture.connect(CONFIG.site)
		try:
			self.assertEqual(frappe.get_meta('Customer Group').translated_doctype,1)
			self.assertEqual(frappe.db.count('Customer Group',{'name':['like','EditorBound-' + ARGS.plane + '-%']}),12)
		finally:
			fixture.close()
		print('OWNED_NATIVE_TRANSLATED_LINK_12_ROWS_LIMIT10 ' + ARGS.plane)

		malformed = {**args, 'ignore_user_permissions':'1'}
		self.assertEqual(self.call('/api/method/' + editor.LINK_VALIDATE + '?' + urlencode(malformed)).status_code,400)
		fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent':'Customer Group','role':WRITER}, 'read', 0))
		try:
			self.assertIn(self.call(path).status_code,(403,404))
		finally:
			fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent':'Customer Group','role':WRITER}, 'read', 1))
		user = CONFIG.subjects[TOKENS[STATE['token']]]
		permission = {}
		def restrict_group():
			doc = frappe.get_doc({'doctype':'User Permission','user':user,'allow':'Customer Group','for_value':'All Customer Groups','hide_descendants':1,'apply_to_all_doctypes':1}).insert()
			permission['name'] = doc.name
		fixture.mutate(CONFIG.site, restrict_group)
		try:
			denied = self.call(path)
			self.assertEqual(denied.status_code,200)
			self.assertEqual(denied.json['message'],{})
			self.assertIn(self.save(actual).status_code,(403,404))
		finally:
			fixture.mutate(CONFIG.site, lambda: frappe.db.delete('User Permission',{'name':permission['name']}))
		self.assertEqual(self.call(path).json['message'], {'name':'ACL ' + ARGS.plane})
		print('OWNED_NATIVE_LINK_ROLE_USER_PERMISSION_DENIAL ' + ARGS.plane)

		meta = self.call('/api/method/frappe.desk.form.load.getdoctype?doctype=Customer')
		self.assertEqual(meta.status_code, 200)
		boot = self.call('/desk')
		self.assertEqual(boot.status_code, 200)
		self.assertIn(CONFIG.subjects[TOKENS[STATE['token']]].encode(), boot.data)

	def test_csrf_side_doors_and_copied_credential_denied(self):
		doc = self.existing('ACL-' + ARGS.plane + '-visible')
		for headers in ({'X-Frappe-CSRF-Token': 'wrong'}, {'Origin': 'https://foreign.example.test'}, {'Authorization': 'token native:key'}, {'X-Frappe-Site-Name': ARGS.site_b if ARGS.plane == 'a' else ARGS.site_a}):
			self.assertIn(self.save(doc, headers=headers).status_code, (401, 403))
		for path in ('/api/method/login', '/api/method/frappe.client.delete', '/api/method/run_doc_method'):
			self.assertEqual(self.call(path, 'POST', {}).status_code, 404)
		self.cookies[adapter.COOKIE] = 'exs_' + ('c' if ARGS.plane == 'a' else 'a') * 43
		self.assertIn(self.call('/desk').status_code, (401, 503))
		self.cookies.pop(adapter.COOKIE)
		self.assertEqual(self.call('/desk').status_code, 401)

	def test_reader_generation_revocation_rollback_and_logout(self):
		doc = self.existing('ACL-' + ARGS.plane + '-visible')
		before = doc['customer_name']
		doc['customer_name'] = 'must rollback'
		STATE['reader'] = True
		self.assertEqual(self.save(doc).status_code, 401)  # immutable scope binding differs
		STATE['reader'] = False
		STATE['foreign'] = True
		self.assertEqual(self.save(doc).status_code, 503)
		STATE['foreign'] = False
		STATE['calls'] = 0
		STATE['revoke_at'] = 2
		self.assertEqual(self.save(doc).status_code, 401)
		self.assertEqual(self.existing(doc['name'])['customer_name'], before)
		STATE['revoked'].clear()
		STATE['revoke_at'] = 0
		sid = self.cookies['sid']
		self.assertEqual(self.call('/api/method/logout', 'POST', '{}', headers={'Content-Type': 'application/json'}).status_code, 200)
		self.assertIn(STATE['token'], STATE['revoked'])
		self.assertEqual(type(frappe.form_dict).__name__, 'LocalProxy')
		fixture.connect(CONFIG.site)
		try:
			self.assertFalse(frappe.db.get_value('Sessions', {'sid': sid}, 'sid', order_by='sid'))
		finally:
			fixture.close()

		# A genuinely new controlled opaque session after logout must not inherit
		# a replaced module proxy or authorize the deleted native SID.
		STATE['token'] = 'exs_' + ('d' if ARGS.plane == 'a' else 'e') * 43
		self.cookies, self.csrf = {}, None
		self.login()
		self.assertNotEqual(self.cookies['sid'], sid)
		fresh = self.existing(doc['name'])
		self.assertEqual(self.save(fresh).status_code, 200)
		self.cookies['sid'] = sid
		self.assertEqual(self.save(self.existing(doc['name'])).status_code, 401)

	def test_writer_revocation_with_surviving_reader_role(self):
		doc = self.existing('ACL-' + ARGS.plane + '-visible')
		self.assertEqual(self.save(doc).status_code, 200)  # warm real session/permission caches
		user = CONFIG.subjects[TOKENS[STATE['token']]]
		removed = {}
		def remove_writer():
			# Do not invalidate Redis: prove current SQL roles drive actual ACL.
			removed.update(frappe.get_doc('Has Role', frappe.db.get_value('Has Role', {'parent': user, 'role': WRITER})).as_dict())
			frappe.db.delete('Has Role', {'parent': user, 'role': WRITER})
		fixture.mutate(CONFIG.site, remove_writer)
		try:
			fixture.connect(CONFIG.site)
			try:
				self.assertTrue(frappe.db.exists('Has Role', {'parent': user, 'role': fixture.ROLE}))
			finally:
				fixture.close()
			doc['customer_name'] = 'revoked writer must deny'
			self.assertIn(self.save(doc).status_code, (403, 404))
			self.assertNotEqual(self.existing(doc['name'])['customer_name'], doc['customer_name'])
		finally:
			def restore_writer():
				frappe.get_doc(removed).db_insert()
			fixture.mutate(CONFIG.site, restore_writer)

	def test_stale_customer_modified_after_concurrent_native_update_denied(self):
		doc = self.existing('ACL-' + ARGS.plane + '-visible')
		def other_update():
			other = frappe.get_doc('Customer', doc['name'])
			other.customer_name = 'Concurrent native update ' + ARGS.plane
			other.save()
		fixture.mutate(CONFIG.site, other_update)
		doc['customer_name'] = 'Stale request must not replace concurrent update'
		os.environ['OWNED_NATIVE_TRACE'] = 'true'
		try:
			self.assertEqual(self.save(doc).status_code, 503)
			self.assertTrue(any('TimestampMismatchError' in str(row) for row in TRACE))
		finally:
			os.environ.pop('OWNED_NATIVE_TRACE', None)
		self.assertEqual(self.existing(doc['name'])['customer_name'], 'Concurrent native update ' + ARGS.plane)

	def test_same_writer_role_permission_reduction_before_commit_rolls_back(self):
		doc = self.existing('ACL-' + ARGS.plane + '-visible')
		self.assertEqual(self.save(doc).status_code, 200)
		doc = self.existing(doc['name'])  # native Save advances modified
		before = doc['customer_name']
		doc['customer_name'] = 'delayed same role permission must rollback'
		STATE['calls'], STATE['permission_at'] = 0, 2
		try:
			self.assertIn(self.save(doc).status_code, (403, 404))
			self.assertEqual(self.existing(doc['name'])['customer_name'], before)
			fixture.connect(CONFIG.site)
			try:
				self.assertTrue(frappe.db.exists('Has Role', {'parent': CONFIG.subjects[TOKENS[STATE['token']]], 'role': WRITER}))
				self.assertEqual(frappe.db.get_value('Custom DocPerm', {'parent': 'Customer', 'role': WRITER}, 'write'), 0)
			finally:
				fixture.close()
		finally:
			STATE['permission_at'] = 0
			fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent': 'Customer', 'role': WRITER}, 'write', 1))


	def test_current_native_user_permissions_with_stale_real_redis(self):
		if ARGS.plane != 'a':
			return  # B has no seeded member; A executes actual row restriction.
		STATE['token'] = 'exs_' + 'b' * 43
		self.cookies = {}
		self.csrf = None
		self.login()
		visible = self.existing('ACL-a-visible')
		self.assertEqual(self.save(visible).status_code, 200)
		user = CONFIG.subjects[TOKENS[STATE['token']]]
		from frappe.core.doctype.user_permission.user_permission import get_user_permissions
		fixture.connect(CONFIG.site)
		try:
			old = get_user_permissions(user)
		finally:
			fixture.close()
		def replace():
			name = frappe.db.get_value('User Permission', {'user': user, 'allow': 'Customer'})
			permission = frappe.get_doc('User Permission', name)
			permission.for_value = 'ACL-a-restricted'
			permission.save()
			frappe.cache.hset('user_permissions', user, old)
		fixture.mutate(CONFIG.site, replace)
		try:
			visible['customer_name'] = 'stale user permission must deny'
			self.assertIn(self.save(visible).status_code, (403, 404))
			self.assertNotEqual(self.existing(visible['name'])['customer_name'], visible['customer_name'])
		finally:
			def restore():
				name = frappe.db.get_value('User Permission', {'user': user, 'allow': 'Customer'})
				permission = frappe.get_doc('User Permission', name)
				permission.for_value = 'ACL-a-visible'
				permission.save()
			fixture.mutate(CONFIG.site, restore)

	def test_native_item_price_optional_write_is_rolled_back_by_readonly_transaction(self):
		# Public calculator rejects rate. Separately execute the exact stock
		# optional writer to prove our PostgreSQL readonly fence, not a mock.
		fixture.connect(CONFIG.site)
		try:
			before = frappe.db.count('Item Price', {'price_list': 'Editor USD Rollback'})
			frappe.set_user(CONFIG.subjects[TOKENS[STATE['token']]])
			frappe.flags.company_session = True
			value = authority(STATE['token'])
			frappe.flags.company_editor = editor.Context(CONFIG, value, STATE['token'], editor.session_binding(CONFIG, value, STATE['token']), time.monotonic() + 9, 'calculate')
			frappe.db.rollback()
			frappe.db.sql('SET TRANSACTION READ ONLY')
			self.assertTrue(frappe.has_permission('Item Price', 'create'))
			self.assertTrue(frappe.has_permission('Item Price', 'write'))
			from erpnext.stock.get_item_details import insert_item_price
			with self.assertRaises(Exception) as refusal:
				insert_item_price(frappe._dict(item_code='Editor-Service-' + ARGS.plane, price_list='Editor USD Rollback', rate=123, currency='USD', stock_uom='Unit', conversion_factor=1))
			chain, error, seen = [], refusal.exception, set()
			for _ in range(3):
				if error is None or id(error) in seen:
					break
				seen.add(id(error))
				chain.append({'class': type(error).__name__, 'sqlstate': getattr(error, 'pgcode', None)})
				error = error.__cause__ or error.__context__
			print('OWNED_ITEM_PRICE_EXCEPTION ' + json.dumps(chain))
			self.assertIsInstance(refusal.exception, frappe.InReadOnlyMode)
			self.assertEqual([row['sqlstate'] for row in chain if row['sqlstate'] is not None], ['25006'])
			frappe.db.rollback()
			self.assertEqual(frappe.db.count('Item Price', {'price_list': 'Editor USD Rollback'}), before)
			print('OWNED_NATIVE_ITEM_PRICE_WRITE_READONLY_25006_ROLLBACK ' + ARGS.plane)
			# Inject only an attempted settings write in the shipped read getter.
			# Real PostgreSQL must reject it inside the actual adapter wrapper.
			before_settings = frappe.db.get_value('List View Settings', 'Customer', 'disable_count')
			frappe.flags.company_editor = editor.Context(CONFIG, value, STATE['token'], editor.session_binding(CONFIG, value, STATE['token']), time.monotonic() + 9, 'list-settings')
			frappe.local.form_dict = frappe._dict(doctype='Customer')
			frappe.local.request = type('OwnedReadRequest', (), {'method':'POST'})()
			def attempted_settings_write(kind):
				frappe.db.set_value('List View Settings', kind, 'disable_count', 1)
			with mock.patch('frappe.desk.listview.get_list_settings', attempted_settings_write):
				with self.assertRaises(frappe.InReadOnlyMode) as denied:
					editor.read_list_settings()
			error, codes, seen = denied.exception, [], set()
			for _ in range(3):
				if error is None or id(error) in seen:
					break
				seen.add(id(error))
				if code := getattr(error, 'pgcode', None):
					codes.append(code)
				error = error.__cause__ or error.__context__
			self.assertEqual(codes, ['25006'])
			self.assertEqual(frappe.db.get_value('List View Settings', 'Customer', 'disable_count'), before_settings)
			print('OWNED_NATIVE_LIST_SETTINGS_WRITE_25006_ROLLBACK ' + ARGS.plane)
			frappe.flags.company_editor = editor.Context(CONFIG, value, STATE['token'], editor.session_binding(CONFIG, value, STATE['token']), time.monotonic() + 9, 'link-read')
			frappe.local.form_dict = frappe._dict(doctype='Customer Group',reference_doctype='Customer',link_fieldname='customer_group',filters='{"is_group":0}',ignore_user_permissions='0',txt='ACL',page_length='10')
			original_get_list = frappe.get_list
			def attempted_translated_link_write(kind, *args, **kwargs):
				if kind == 'Customer Group':
					return attempted_settings_write('Customer')
				return original_get_list(kind, *args, **kwargs)
			with mock.patch('frappe.get_list', attempted_translated_link_write):
				with self.assertRaises(frappe.InReadOnlyMode) as denied:
					editor.read_link(editor.LINK_SEARCH)
			error, codes, seen = denied.exception, [], set()
			for _ in range(3):
				if error is None or id(error) in seen:
					break
				seen.add(id(error))
				if code := getattr(error,'pgcode',None):
					codes.append(code)
				error = error.__cause__ or error.__context__
			self.assertEqual(codes,['25006'])
			self.assertEqual(frappe.db.get_value('List View Settings','Customer','disable_count'),before_settings)
			print('OWNED_NATIVE_LINK_WRITE_25006_ROLLBACK ' + ARGS.plane)
			frappe.flags.company_editor = editor.Context(CONFIG,value,STATE['token'],editor.session_binding(CONFIG,value,STATE['token']),time.monotonic()+9,'sales-settings')
			frappe.local.form_dict = frappe._dict(doctype='Accounts Settings',field='fetch_valuation_rate_for_internal_transaction')
			original_single = frappe.db.get_single_value
			def attempted_boolean_write(kind,field,*args,**kwargs):
				if (kind,field)==('Accounts Settings','fetch_valuation_rate_for_internal_transaction'):
					return attempted_settings_write('Customer')
				return original_single(kind,field,*args,**kwargs)
			with mock.patch.object(frappe.db,'get_single_value',attempted_boolean_write):
				with self.assertRaises(frappe.InReadOnlyMode) as denied:
					editor.read_sales_setting()
			self.assertEqual(getattr(denied.exception.__cause__ or denied.exception.__context__,'pgcode',None),'25006')
			self.assertEqual(frappe.db.get_value('List View Settings','Customer','disable_count'),before_settings)
			print('OWNED_NATIVE_ACCOUNTS_BOOLEAN_WRITE_25006_ROLLBACK ' + ARGS.plane)
			from datetime import date
			payload={'doctype':'Sales Invoice','name':'new-sales-invoice-readonly','__islocal':1,'company':'Editor '+ARGS.plane,'customer':'ACL-'+ARGS.plane+'-visible','currency':'USD','conversion_rate':1,'selling_price_list':'Editor USD','posting_date':date.today().isoformat(),'items':[]}
			price_ctx={'doctype':'Sales Invoice','name':payload['name'],'company':payload['company'],'customer':payload['customer'],'currency':'USD','conversion_rate':1,'price_list':'Editor USD','price_list_currency':'USD','plc_conversion_rate':1,'transaction_date':payload['posting_date'],'items':[]}
			frappe.flags.company_editor=editor.Context(CONFIG,value,STATE['token'],editor.session_binding(CONFIG,value,STATE['token']),time.monotonic()+9,'calculate')
			frappe.local.form_dict=frappe._dict(doc=json.dumps(payload),ctx=json.dumps(price_ctx))
			def attempted_native_price_write(**_args):
				return insert_item_price(frappe._dict(item_code='Editor-Service-'+ARGS.plane,price_list='Editor USD Rollback',rate=123,currency='USD',stock_uom='Unit',conversion_factor=1))
			with mock.patch('erpnext.stock.get_item_details.apply_price_list',attempted_native_price_write):
				with self.assertRaises(frappe.InReadOnlyMode) as denied:
					editor.apply_prices()
			error,codes,seen=denied.exception,[],set()
			for _ in range(3):
				if error is None or id(error) in seen:
					break
				seen.add(id(error))
				if code:=getattr(error,'pgcode',None):
					codes.append(code)
				error=error.__cause__ or error.__context__
			self.assertEqual(codes,['25006'])
			self.assertEqual(frappe.db.count('Item Price',{'price_list':'Editor USD Rollback'}),before)
			self.assertNotIn('company_editor_currency',frappe.local.request_cache)
			print('OWNED_NATIVE_APPLY_PRICE_WRITE_25006_ROLLBACK ' + ARGS.plane)
			for operation,method,form,entry in (
				('invoice-initialize','frappe.company_editor.invoice_initialization_value',{},lambda:editor.read_invoice_initialization(editor.DIMENSIONS)),
				('calculate','erpnext.accounts.doctype.pricing_rule.pricing_rule.apply_pricing_rule',{'doc':json.dumps(payload),'args':json.dumps(price_ctx)},editor.apply_rules),
				('tax-calculate','erpnext.stock.get_item_details.get_item_tax_template',{'ctx':json.dumps({'item_code':'Editor-Service-'+ARGS.plane,'company':'Editor '+ARGS.plane,'base_net_rate':100,'posting_date':date.today().isoformat()})},editor.read_tax_template),
			):
				frappe.flags.company_editor=editor.Context(CONFIG,value,STATE['token'],editor.session_binding(CONFIG,value,STATE['token']),time.monotonic()+9,operation)
				frappe.local.form_dict=frappe._dict(form)
				with mock.patch(method,lambda *_pos,**_args:attempted_settings_write('Customer')):
					with self.assertRaises(frappe.InReadOnlyMode) as denied:
						entry()
				error,codes,seen=denied.exception,[],set()
				for _ in range(3):
					if error is None or id(error) in seen:
						break
					seen.add(id(error))
					if code:=getattr(error,'pgcode',None):
						codes.append(code)
					error=error.__cause__ or error.__context__
				self.assertEqual(codes,['25006'])
				self.assertEqual(frappe.db.get_value('List View Settings','Customer','disable_count'),before_settings)
				print('OWNED_NATIVE_'+operation.upper()+'_WRITE_25006_ROLLBACK '+ARGS.plane)





		finally:
			fixture.close()

	def test_draft_sales_invoice_native_calculations_save_edit_reload(self):
		from datetime import date
		party = {'party': 'ACL-' + ARGS.plane + '-visible', 'party_type': 'Customer', 'company': 'Editor ' + ARGS.plane, 'posting_date': date.today().isoformat(), 'price_list': 'Editor USD', 'currency': 'USD', 'doctype': 'Sales Invoice', 'fetch_payment_terms_template': '1'}
		calculated = self.call('/api/method/' + editor.PARTY_CALCULATOR, 'POST', party)
		self.assertEqual(calculated.status_code, 200)
		doc = {'doctype': 'Sales Invoice', 'name': 'new-sales-invoice-fixture', '__islocal': 1, 'docstatus': 0, 'company': party['company'], 'customer': party['party'], 'posting_date': party['posting_date'], 'due_date': party['posting_date'], 'currency': 'USD', 'conversion_rate': 1, 'selling_price_list': 'Editor USD', 'price_list_currency': 'USD', 'plc_conversion_rate': 1, 'is_pos': 0, 'is_return': 0, 'update_stock': 0, 'items': [{'doctype': 'Sales Invoice Item', 'name': 'new-sales-invoice-item-fixture', 'parent':'new-sales-invoice-fixture','parenttype':'Sales Invoice','parentfield':'items', '__islocal': 1, 'item_code': 'Editor-Service-' + ARGS.plane, 'qty': 1, 'uom': 'Unit', 'stock_uom': 'Unit', 'conversion_factor': 1}]}
		doc.update(calculated.get_json()['message'])
		ctx = {'doctype': 'Sales Invoice', 'name': doc['name'], 'child_doctype': 'Sales Invoice Item', 'child_docname': doc['items'][0]['name'], 'company': party['company'], 'customer': party['party'], 'item_code': doc['items'][0]['item_code'], 'qty': 1, 'currency': 'USD', 'conversion_rate': 1, 'price_list': 'Editor USD', 'price_list_currency': 'USD', 'plc_conversion_rate': 1, 'stock_uom': 'Unit', 'uom': 'Unit', 'conversion_factor': 1, 'is_pos': 0, 'is_return': 0, 'update_stock': 0, 'ignore_pricing_rule': 0}
		items = self.call('/api/method/' + editor.ITEM_CALCULATOR, 'POST', {'doc': frappe.as_json(doc, indent=None), 'ctx': json.dumps(ctx)})
		self.assertEqual(items.status_code, 200)
		# Stock setup supplies a default Warehouse even for service items.
		# Linked-result native read remains mandatory after the operator grant.
		fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent': 'Warehouse', 'role': WRITER}, 'read', 0))
		try:
			self.assertEqual(self.call('/api/method/' + editor.ITEM_CALCULATOR, 'POST', {'doc': frappe.as_json(doc, indent=None), 'ctx': json.dumps(ctx)}).status_code, 404)
		finally:
			fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent': 'Warehouse', 'role': WRITER}, 'read', 1))
		for value in (None, True, float('inf'), -1):
			bad = {**ctx, 'qty': value}
			self.assertEqual(self.call('/api/method/' + editor.ITEM_CALCULATOR, 'POST', {'doc': frappe.as_json(doc, indent=None), 'ctx': json.dumps(bad)}).status_code, 400)
		self.assertEqual(self.call('/api/method/' + editor.ITEM_CALCULATOR, 'POST', {'doc': frappe.as_json(doc, indent=None), 'ctx': json.dumps({**ctx, 'rate': 123})}).status_code, 400)
		fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent': 'Item', 'role': WRITER}, 'read', 0))
		try:
			self.assertIn(self.call('/api/method/' + editor.ITEM_CALCULATOR, 'POST', {'doc': frappe.as_json(doc, indent=None), 'ctx': json.dumps(ctx)}).status_code, (403, 404))
		finally:
			fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('Custom DocPerm', {'parent': 'Item', 'role': WRITER}, 'read', 1))

		restriction = []
		def restrict_item():
			permission = frappe.get_doc({'doctype': 'User Permission', 'user': CONFIG.subjects[TOKENS[STATE['token']]], 'allow': 'Item', 'for_value': 'Editor-Denied-' + ARGS.plane, 'apply_to_all_doctypes': 1}).insert()
			restriction.append(permission.name)
		fixture.mutate(CONFIG.site, restrict_item)
		try:
			self.assertIn(self.call('/api/method/' + editor.ITEM_CALCULATOR, 'POST', {'doc': frappe.as_json(doc, indent=None), 'ctx': json.dumps(ctx)}).status_code, (403, 404))
		finally:
			fixture.mutate(CONFIG.site, lambda: frappe.db.delete('User Permission', {'name': restriction[0]}))
			fixture.connect(CONFIG.site)
			try:
				self.assertFalse(frappe.db.exists('User Permission', restriction[0]))
			finally:
				fixture.close()
		doc['items'][0].update(items.get_json()['message'])
		price_ctx = {key:doc.get(other) for key,other in (('doctype','doctype'),('name','name'),('company','company'),('customer','customer'),('currency','currency'),('price_list','selling_price_list'),('price_list_currency','price_list_currency'),('conversion_rate','conversion_rate'),('plc_conversion_rate','plc_conversion_rate'),('transaction_date','posting_date'))}
		price_ctx['items'] = [{**{key:value for key,value in doc['items'][0].items() if key in editor.PRICE_ITEM_FIELDS}, 'child_docname':doc['items'][0]['name']}]
		price_args = {'doc':frappe.as_json(doc,indent=None),'ctx':json.dumps(price_ctx)}
		prices = self.call('/api/method/' + editor.PRICE_CALCULATOR,'POST',price_args)
		self.assertEqual(prices.status_code,200)
		priced = prices.get_json()['message']
		self.assertEqual(priced['parent']['plc_conversion_rate'],1)
		self.assertEqual(priced['parent']['price_list_currency'],'USD')
		self.assertEqual(priced['children'][0]['name'],doc['items'][0]['name'])
		from erpnext.stock.get_item_details import apply_price_list as actual_apply_price_list
		def concurrently_change_price_currency(**args):
			errors=[]
			def change():
				try:
					fixture.mutate(CONFIG.site,lambda:frappe.db.set_value('Price List','Editor USD','currency','EUR'))
				except Exception as error:
					errors.append(type(error).__name__)
				finally:
					if getattr(frappe.local,'db',None):
						fixture.close()
			worker=threading.Thread(target=change,daemon=True)
			worker.start()
			worker.join(2)
			if worker.is_alive() or errors:
				raise ValueError('Owned concurrent currency change did not close')
			return actual_apply_price_list(**args)
		try:
			with mock.patch('erpnext.stock.get_item_details.apply_price_list',concurrently_change_price_currency),mock.patch('erpnext.stock.get_item_details.get_exchange_rate',side_effect=AssertionError('Exchange helper must not run')) as exchange:
				self.assertEqual(self.call('/api/method/'+editor.PRICE_CALCULATOR,'POST',price_args).status_code,403)
				exchange.assert_not_called()
		finally:
			fixture.mutate(CONFIG.site,lambda:frappe.db.set_value('Price List','Editor USD','currency','USD'))
		print('OWNED_NATIVE_CHANGED_CURRENCY_REFUSED_NO_EXCHANGE ' + ARGS.plane)

		doc['items'][0].update({key:value for key,value in priced['children'][0].items() if key not in ('name','doctype')})
		tax_ctx={'item_code':ctx['item_code'],'company':party['company'],'base_net_rate':100,'posting_date':party['posting_date'],'tax_category':None,'item_tax_template':None}
		tax_args={'ctx':json.dumps(tax_ctx)}
		tax=self.call('/api/method/'+editor.TAX_TEMPLATE,'POST',tax_args)
		self.assertEqual(tax.status_code,200)
		self.assertIsNone(tax.get_json().get('message'))
		self.assertEqual(self.call('/api/method/'+editor.TAX_TEMPLATE,'POST',{'ctx':json.dumps({**tax_ctx,'base_net_rate':True})}).status_code,400)
		rules_args={'doc':frappe.as_json(doc,indent=None),'args':json.dumps(price_ctx)}
		rules=self.call('/api/method/'+editor.RULE_CALCULATOR,'POST',rules_args)
		self.assertEqual(rules.status_code,200)
		self.assertEqual(rules.get_json()['message'][0]['name'],doc['items'][0]['name'])
		# A minimal owned catalog row is enough to prove the deliberately strict
		# existence ceiling; this is not a supported business Pricing Rule setup.
		rule_name='owned-editor-rule-ceiling-'+ARGS.plane
		fixture.mutate(CONFIG.site,lambda:frappe.db.sql('INSERT INTO "tabPricing Rule" (name,disable) VALUES (%s,1)',(rule_name,)))
		try:
			for method,args in ((editor.RULE_CALCULATOR,rules_args),(editor.PRICE_CALCULATOR,price_args),(editor.ITEM_CALCULATOR,{'doc':frappe.as_json(doc,indent=None),'ctx':json.dumps(ctx)})):
				self.assertEqual(self.call('/api/method/'+method,'POST',args).status_code,403)
			self.assertEqual(self.save(doc).status_code,403)
		finally:
			fixture.mutate(CONFIG.site,lambda:frappe.db.delete('Pricing Rule',{'name':rule_name}))
		print('OWNED_NATIVE_ALL_PRICING_AND_SAVE_RULE_ROW_REFUSED '+ARGS.plane)


		settings = {'doctype':'Accounts Settings','field':'fetch_valuation_rate_for_internal_transaction'}
		boolean = self.call('/api/method/'+editor.SALES_SETTINGS+'?'+urlencode(settings))
		self.assertEqual(boolean.status_code,200)
		self.assertIs(type(boolean.get_json()['message']),int)
		self.assertIn(boolean.get_json()['message'],(0,1))
		self.assertEqual(self.call('/api/method/'+editor.SALES_SETTINGS+'?'+urlencode({**settings,'field':'arbitrary-setting'})).status_code,400)
		link_cases = [
			('Sales Invoice','customer','Customer',party['party'],{},None),
			('Sales Invoice','company','Company',party['company'],{},None),
			('Sales Invoice','currency','Currency','USD',{},None),
			('Sales Invoice','selling_price_list','Price List','Editor USD',{'selling':1},None),
			('Sales Invoice','debit_to','Account',doc['debit_to'],{'company':party['company'],'is_group':0,'account_type':'Receivable'},None),
			('Sales Invoice Item','item_code','Item',ctx['item_code'],{'is_sales_item':1,'has_variants':0,'customer':party['party']},'erpnext.controllers.queries.item_query'),
			('Sales Invoice Item','uom','UOM','Unit',{'item_code':ctx['item_code']},'erpnext.controllers.queries.get_item_uom_query'),
			('Sales Invoice Item','income_account','Account',doc['items'][0]['income_account'],{'company':party['company'],'disabled':0},'erpnext.controllers.queries.get_income_account'),
			('Sales Invoice Item','cost_center','Cost Center',doc['items'][0]['cost_center'],{'company':party['company'],'is_group':0},None),
		]
		for parent,field,target,name,filters,query in link_cases:
			args={'reference_doctype':parent,'link_fieldname':field,'doctype':target,'docname':name,'txt':name,'fields_to_fetch':'[]','filters':json.dumps(filters),'ignore_user_permissions':'0'}
			if query:
				args['query']=query
			response=self.call('/api/method/'+editor.LINK_VALIDATE+'?'+urlencode(args))
			self.assertEqual(response.status_code,200,(field,response.status_code))
			self.assertEqual(response.get_json()['message'],{'name':name})
			if target=='Item':
				args['fields_to_fetch']=json.dumps(['image','grant_commission'])
				fetched=self.call('/api/method/'+editor.LINK_VALIDATE+'?'+urlencode(args))
				self.assertEqual(fetched.status_code,200)
				self.assertIn(fetched.get_json()['message']['image'],(None,''))
				self.assertIs(type(fetched.get_json()['message']['grant_commission']),int)
				self.assertIn(fetched.get_json()['message']['grant_commission'],(0,1))

		STATE['reader']=True
		# Issue a read-scoped SID: changing an existing writer binding correctly returns 401.
		self.cookies, self.csrf = {}, None
		self.login()
		try:
			self.assertEqual(self.call('/api/method/'+editor.RULE_CALCULATOR,'POST',rules_args).status_code,403)
			self.assertEqual(self.call('/api/method/'+editor.TAX_TEMPLATE,'POST',tax_args).status_code,403)
			self.assertEqual(self.call('/api/method/'+editor.PRICE_CALCULATOR,'POST',price_args).status_code,403)
			self.assertEqual(self.call('/api/method/'+editor.SALES_SETTINGS+'?'+urlencode(settings)).status_code,403)
		finally:
			STATE['reader']=False
			self.cookies, self.csrf = {}, None
			self.login()
		fixture.mutate(CONFIG.site,lambda:frappe.db.set_value('Custom DocPerm',{'parent':'Item','role':WRITER},'read',0))
		try:
			self.assertIn(self.call('/api/method/'+editor.PRICE_CALCULATOR,'POST',price_args).status_code,(403,404))
		finally:
			fixture.mutate(CONFIG.site,lambda:frappe.db.set_value('Custom DocPerm',{'parent':'Item','role':WRITER},'read',1))
		print('OWNED_NATIVE_GROUPED_PRICE_SETTINGS_LINK_POSITIVE_DENIAL ' + ARGS.plane)
		created = self.save(doc)
		self.assertEqual(created.status_code, 200)
		actual = created.get_json()['docs'][0]
		self.assertEqual(actual['docstatus'], 0)
		actual['items'][0]['qty'] = 2
		quantity_ctx={**price_ctx,'name':actual['name'],'items':[{**{key:value for key,value in actual['items'][0].items() if key in editor.PRICE_ITEM_FIELDS},'child_docname':actual['items'][0]['name']}]}
		quantity=self.call('/api/method/'+editor.RULE_CALCULATOR,'POST',{'doc':frappe.as_json(actual,indent=None),'args':json.dumps(quantity_ctx)})
		self.assertEqual(quantity.status_code,200)
		self.assertEqual(quantity.get_json()['message'][0]['name'],actual['items'][0]['name'])
		edited = self.save(actual)
		self.assertEqual(edited.status_code, 200)
		reloaded = self.call('/api/method/frappe.desk.form.load.getdoc?' + urlencode({'doctype': 'Sales Invoice', 'name': actual['name']}))
		self.assertEqual(reloaded.status_code, 200)
		persisted = reloaded.get_json()['docs'][0]
		self.assertEqual(persisted['docstatus'], 0)
		self.assertEqual(persisted['items'][0]['qty'], 2)
		fixture.connect(CONFIG.site)
		try:
			stored = frappe.get_doc('Sales Invoice', actual['name'])
			self.assertEqual(stored.docstatus, 0)
			self.assertEqual(stored.items[0].qty, 2)
			self.assertEqual(stored.items[0].item_code, 'Editor-Service-' + ARGS.plane)
			self.assertEqual(stored.company, party['company'])
		finally:
			fixture.close()
		for change in ({'owner': None}, {'owner': 'foreign@example.test'}, {'__newname': 'caller-rename'}, {'ignore_permissions': False}):
			forged = {**persisted, **change}
			self.assertIn(self.save(forged).status_code, (400, 403))
		omitted = {key: value for key, value in persisted.items() if key != 'owner'}
		self.assertEqual(self.save(omitted).status_code, 403)
		for key, value in (('owner', 'foreign@example.test'), ('__newname', 'caller-child-rename'), ('name', 'foreign-child-id')):
			forged = {**persisted, 'items': [{**persisted['items'][0], key: value}]}
			self.assertIn(self.save(forged).status_code, (400, 403))
		print('OWNED_DRAFT_INVOICE_CALCULATE_CREATE_EDIT_RELOAD_PERSISTED ' + ARGS.plane)

	def test_disabled_current_user_and_foreign_document_cannot_write(self):
		user = CONFIG.subjects[TOKENS[STATE['token']]]
		def disable():
			frappe.db.set_value('User', user, 'enabled', 0)
		fixture.mutate(CONFIG.site, disable)
		try:
			self.assertEqual(self.call('/desk').status_code, 403)
		finally:
			fixture.mutate(CONFIG.site, lambda: frappe.db.set_value('User', user, 'enabled', 1))
		foreign = self.call('/api/method/frappe.desk.form.load.getdoc?' + urlencode({'doctype': 'Customer', 'name': 'ACL-' + ('b' if ARGS.plane == 'a' else 'a') + '-visible'}))
		self.assertEqual(foreign.status_code, 200)
		self.assertEqual(foreign.get_json().get('docs', []), [])


if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	for flag in ('sites-path', 'site-a', 'site-b', 'fixture-id', 'plane'):
		parser.add_argument('--' + flag, required=True)
	parser.add_argument('--diagnostic', action='store_true')
	parser.add_argument('--browser-serve', action='store_true')
	parser.add_argument('--calculator-probe', action='store_true')
	parser.add_argument('--prepare-only', action='store_true')
	parser.add_argument('--prepared', action='store_true')
	ARGS = parser.parse_args()
	if ARGS.browser_serve:
		def startup_error(_kind, error, _traceback):
			# Owned startup diagnostics only; never exception text/SQL/values.
			rows, seen = [], set()
			for cause in range(3):
				if error is None or id(error) in seen:
					break
				seen.add(id(error))
				tb, frames = error.__traceback__, []
				while tb:
					code = tb.tb_frame.f_code
					frames.append({'file': Path(code.co_filename).name, 'function': code.co_name, 'line': tb.tb_lineno})
					tb = tb.tb_next
				rows.append({'class': type(error).__name__, 'cause': cause, 'frames': frames[-12:]})
				error = error.__cause__ or error.__context__
			print(json.dumps({'owned_startup_failure': rows}), file=sys.stderr)
		sys.excepthook = startup_error
	ARGS.sites_path = Path(ARGS.sites_path).resolve(strict=True)
	fixture.ARGS = ARGS
	if ARGS.prepare_only:
		if ARGS.prepared or ARGS.browser_serve or ARGS.calculator_probe:
			raise ValueError('Closed native preparation only')
		fixture.verify_sites()
		for site, plane in ((ARGS.site_a, 'a'), (ARGS.site_b, 'b')):
			fixture.setup(site, plane)
			write_setup(site, plane)
			path = ARGS.sites_path / site / 'site_config.json'
			data = json.loads(path.read_text())
			data['company_editor_fixture_prepared'] = ARGS.fixture_id
			path.write_text(json.dumps(data))
		print('Owned supported native operator setup completed for both sites')
		sys.exit(0)
	if ARGS.prepared:
		verify_prepared_sites()
	elif ARGS.plane == 'a':
		fixture.verify_sites()
		for site, plane in ((ARGS.site_a, 'a'), (ARGS.site_b, 'b')):
			fixture.setup(site, plane)
			write_setup(site, plane)
	else:
		for site in (ARGS.site_a, ARGS.site_b):
			data = json.loads((ARGS.sites_path / site / 'site_config.json').read_text())
			if data.get('company_acl_fixture') != ARGS.fixture_id or data.get('allow_tests') is not True:
				raise ValueError('Owned fixture required')
	server = HTTPServer(('127.0.0.1', 0), Authority)
	thread = threading.Thread(target=server.serve_forever, daemon=True)
	with tempfile.TemporaryDirectory(prefix='erp-editor-authority-') as private:
		c = fixture.config(ARGS.plane)
		binding = {'version': 1, 'company_id': c.company_id, 'site': c.site, 'binding_id': c.binding_id, 'generation_id': c.generation_id, 'audience': c.audience, 'subjects': [{'subject_id': subject, 'native_user': user} for subject, user in c.subjects.items()]}
		paths = {name: Path(private) / name for name in ('secret', 'bindings', 'flow')}
		paths['secret'].write_text(SECRET)
		paths['bindings'].write_text(json.dumps(binding))
		paths['flow'].write_text('f' * 43)
		for path in paths.values():
			path.chmod(0o600)
		os.environ.update(ERP_COMPANY_MODE='true', ERP_COMPANY_EDITOR_ENABLED='true', ERP_COMPANY_EDITOR_ENTITLEMENT_KIND='beta', ERP_COMPANY_BROWSER_ENABLED='true', ERP_COMPANY_AUTH_ORIGIN='https://auth.platform.example.test', ERP_COMPANY_FLOW_SECRET_FILE=str(paths['flow']), SITE_NAME=c.site, SITES_PATH=str(ARGS.sites_path), ERP_COMPANY_ID=c.company_id, ERP_COMPANY_SITE=c.site, ERP_COMPANY_BINDING_ID=c.binding_id, ERP_COMPANY_GENERATION_ID=c.generation_id, ERP_COMPANY_AUDIENCE=c.audience, ERP_COMPANY_CLIENT_ID=c.client_id, ERP_COMPANY_ORIGIN=c.origin, ERP_COMPANY_AUTHORITY_URL='http://127.0.0.1:' + str(server.server_port), ERP_COMPANY_CLIENT_SECRET_FILE=str(paths['secret']), ERP_COMPANY_BINDINGS_FILE=str(paths['bindings']), ERP_COMPANY_BINDINGS_SHA256=hashlib.sha256(paths['bindings'].read_bytes()).hexdigest())
		if ARGS.browser_serve:
			STATE['token'] = 'exs_' + ('a' if ARGS.plane == 'a' else 'c') * 43
		thread.start()
		try:
			import frappe.app as APP
			CONFIG = APP._company_config
			if CONFIG is None or not CONFIG.editor_enabled:
				raise ValueError('Explicit editor required')
			if ARGS.browser_serve:
				from werkzeug.middleware.shared_data import SharedDataMiddleware
				from werkzeug.serving import WSGIRequestHandler, make_server
				class Quiet(WSGIRequestHandler):
					def log_request(self, *args):
						pass  # Never log callback nonces/cookies/query strings.
				static = SharedDataMiddleware(APP.application, {'/assets': str(ARGS.sites_path / 'assets')})
				def owned_https(environ, start_response):
					# Dedicated browser forwarding preserves original host/origin and
					# HTTPS semantics; this wrapper exists only in the owned fixture.
					environ['wsgi.url_scheme'] = 'https'
					return static(environ, start_response)
				web = make_server('0.0.0.0', 8000 if ARGS.plane == 'a' else 8001, owned_https, threaded=True, request_handler=Quiet)
				ready = Path('/tmp/owned-browser-' + ARGS.plane + '.json')
				ready.write_text(json.dumps({'origin': CONFIG.origin, 'scope': 'actual native browser; controlled HTTP Core'}))
				ready.chmod(0o600)
				timer = threading.Timer(115, web.shutdown)
				timer.start()
				try:
					web.serve_forever()
				finally:
					timer.cancel()
					web.server_close()
			else:
				selected = unittest.TestSuite([NativeEditor('test_draft_sales_invoice_native_calculations_save_edit_reload')]) if ARGS.calculator_probe else unittest.defaultTestLoader.loadTestsFromTestCase(NativeEditor)
				result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
		finally:
			server.shutdown()
			server.server_close()
			thread.join(2)
			if thread.is_alive():
				raise RuntimeError('Owned authority failed closure')
		print('Scope: native callback/Desk/Customer and draft invoice persistence; controlled HTTP Core; plane ' + ARGS.plane)
		sys.exit(0 if ARGS.browser_serve or (result.wasSuccessful() and result.testsRun == (1 if ARGS.calculator_probe else 11)) else 1)
