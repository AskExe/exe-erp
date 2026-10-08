"""Explicit V2 company principal for existing Desk/session/document services.

No parent JWT, native JWT, permission promotion or tenant selection. Native
permissions remain authoritative; Core write scope is an additional ceiling.
"""
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote

from frappe import company_editor_access as contract
from frappe import company_session as transport

contract.company_access = transport.company_access
Denied = transport.Denied
WRITE_TYPES = frozenset(("Customer", "Sales Invoice"))
# Shared native read capabilities used by Desk forms/list/link controls. Each
# document query still checks native permissions; this is not arbitrary RPC.
READ_METHODS = frozenset((
	"frappe.desk.form.load.getdoctype", "frappe.desk.form.load.getdoc",
	"frappe.desk.reportview.get", "frappe.desk.reportview.get_list",
	"frappe.desk.reportview.get_count", "frappe.desk.reportview.get_sidebar_stats",
	"frappe.client.get_value", "frappe.client.get_list",
))
DESK_METHODS = frozenset((
	"frappe.desk.desktop.get_workspace_sidebar_items", "frappe.desk.desktop.get_desktop_page",
))
LINK_SEARCH = "frappe.desk.search.search_link"
LINK_VALIDATE = "frappe.client.validate_link_and_fetch"
LINK_METHODS = frozenset((LINK_SEARCH, LINK_VALIDATE))
LIST_SETTINGS = "frappe.desk.listview.get_list_settings"
LIST_FLAGS = frozenset(("disable_count", "disable_sidebar_stats", "disable_auto_refresh", "disable_comment_count", "disable_automatic_recency_filters", "disable_scrolling", "show_tags"))
SAVE_METHOD = "frappe.desk.form.save.savedocs"
PARTY_CALCULATOR = "erpnext.accounts.party.get_party_details"
ITEM_CALCULATOR = "erpnext.stock.get_item_details.get_item_details"
PRICE_CALCULATOR = "erpnext.stock.get_item_details.apply_price_list"
RULE_CALCULATOR = "erpnext.accounts.doctype.pricing_rule.pricing_rule.apply_pricing_rule"
SALES_SETTINGS = "frappe.client.get_single_value"
TAX_TEMPLATE = "erpnext.stock.get_item_details.get_item_tax_template"
INVOICE_LINK_QUERIES = frozenset(("erpnext.controllers.queries.item_query", "erpnext.controllers.queries.get_item_uom_query", "erpnext.controllers.queries.get_income_account"))
ROUND_OFF = "erpnext.controllers.taxes_and_totals.get_round_off_applicable_accounts"
ROUNDING_SETTING = "erpnext.controllers.taxes_and_totals.get_rounding_tax_settings"
DIMENSIONS = "erpnext.accounts.doctype.accounting_dimension.accounting_dimension.get_dimensions"
DEFAULT_TAXES = "erpnext.controllers.accounts_controller.get_default_taxes_and_charges"
COMPANY_ADDRESS = "erpnext.setup.doctype.company.company.get_default_company_address"
PARTY_ACCOUNT = "erpnext.accounts.party.get_party_account"
LOYALTY_PROGRAMS = "erpnext.accounts.doctype.sales_invoice.sales_invoice.get_loyalty_programs"
INVOICE_INITIALIZERS = frozenset((ROUND_OFF, ROUNDING_SETTING, DIMENSIONS, DEFAULT_TAXES, COMPANY_ADDRESS, PARTY_ACCOUNT, LOYALTY_PROGRAMS))
CALCULATORS = frozenset((PARTY_CALCULATOR, ITEM_CALCULATOR, PRICE_CALCULATOR, RULE_CALCULATOR))
PARTY_ARGUMENTS = frozenset(("party", "party_type", "company", "posting_date", "price_list", "currency", "doctype", "fetch_payment_terms_template", "company_address"))
ITEM_ARGUMENTS = frozenset(("item_code", "barcode", "serial_no", "batch_no", "set_warehouse", "warehouse", "customer", "quotation_to", "supplier", "currency", "is_internal_supplier", "is_internal_customer", "update_stock", "conversion_rate", "price_list", "price_list_currency", "plc_conversion_rate", "company", "order_type", "is_pos", "is_return", "is_subcontracted", "ignore_pricing_rule", "doctype", "name", "project", "qty", "net_rate", "base_net_rate", "stock_qty", "conversion_factor", "weight_per_unit", "uom", "weight_uom", "manufacturer", "stock_uom", "pos_profile", "cost_center", "tax_category", "item_tax_template", "child_doctype", "child_docname", "use_serial_batch_fields", "serial_and_batch_bundle"))
ITEM_EMPTY = frozenset(("barcode", "serial_no", "batch_no", "set_warehouse", "warehouse", "quotation_to", "supplier", "order_type", "project", "manufacturer", "pos_profile", "tax_category", "item_tax_template", "serial_and_batch_bundle"))
ITEM_ZERO = frozenset(("is_internal_supplier", "is_internal_customer", "update_stock", "is_pos", "is_return", "is_subcontracted", "ignore_pricing_rule", "use_serial_batch_fields"))


# Native Sales Invoice business Check fields are admitted only at stock zero.
BUSINESS_ZERO_FIELDS = frozenset(("ignore_pricing_rule", "ignore_default_payment_terms_template", "ignore_tax_withholding_threshold"))
SELECTORS = frozenset(("site", "site_name", "company_id", "binding_id", "generation_id",
	"subject_id", "native_user", "sid", "cmd", "user", "__newname", "ignore_permissions",
	"ignore_user_permissions", "ignore_csrf", "ignore_links", "ignore_mandatory", "query"))


def editor_envelope(value, config):
	if not config.editor_enabled:
		raise Denied(401)
	checked = contract.company_editor_access(value, {
		"company_id": config.company_id, "product": "erp", "binding_id": config.binding_id,
		"native_id": config.site, "generation_id": config.generation_id, "audience": config.audience,
	})
	if checked is None or checked["entitlement_kind"] != config.editor_entitlement_kind:
		raise Denied(503)
	user = config.subjects.get(checked["subject_id"])
	if not user:
		raise Denied(401)
	return checked, user


def session_binding(config, value, token):
	checked, _ = editor_envelope(value, config)
	if not isinstance(token, str) or not transport.SESSION.fullmatch(token):
		raise Denied(401)
	return {**{key: checked[key] for key in (
		"version", "subject_id", "company_id", "product", "resource_kind", "binding_id",
		"native_id", "generation_id", "authz_epoch", "audience", "scopes", "entitlement_kind",
	)}, "client_id": config.client_id, "origin": config.origin,
		"credential_hash": hashlib.sha256(token.encode("ascii")).hexdigest()}


@dataclass(frozen=True)
class Context:
	config: object
	value: dict
	token: str
	binding: dict
	deadline: float
	operation: str


def current(context):
	if time.monotonic() >= context.deadline:
		raise Denied(503)



def native_zero(value):
	return (type(value) is bool and value is False) or (type(value) is int and value == 0) or (type(value) is str and value in ("0", "false"))


def ingress(request, config):
	"""Before Frappe init/HTTPRequest can select a site, SID or auth scheme."""
	raw = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI")
	if not isinstance(raw, str) or len(raw) > 16384 or raw.split("?", 1)[0] != quote(request.path, safe="/._-~") or "\\" in raw or "#" in raw or any(ord(c) < 32 for c in request.path):
		raise Denied(400)
	if request.host != config.site or request.headers.get("Origin") not in (None, config.origin):
		raise Denied(403)
	if request.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
		if not (request.method == "GET" and request.path == "/desk" and request.headers.get("Sec-Fetch-Mode") == "navigate" and request.headers.get("Sec-Fetch-Dest") == "document"):
			raise Denied(403)
	names = [key.lower() for key, _ in request.headers]
	if len(set(names)) != len(names) or len(names) > 40 or sum(len(k) + len(v) for k, v in request.headers) > 16384:
		raise Denied(400)
	for key, _ in request.headers:
		name = key.lower()
		if name in ("authorization", "proxy-authorization", "x-frappe-site-name", "x-frappe-user", "frappe-authorization-source", "x-frappe-socket-secret", "upgrade", "sec-websocket-protocol") or name.startswith(("x-auth-", "x-exe-", "x-company", "x-org", "x-role", "forwarded")) or (name.startswith("x-forwarded-") and not (name == "x-forwarded-proto" and request.headers.get(key) == "https")):
			raise Denied(401)
	if request.headers.get("Transfer-Encoding") or (request.content_length is not None and not 0 <= request.content_length <= 1024**2):
		raise Denied(400)
	request.max_content_length = 1024**2
	link_request = request.path in {"/api/method/" + method for method in LINK_METHODS}
	def selector(key, value):
		return key in SELECTORS and not (link_request and ((key == "ignore_user_permissions" and native_zero(value)) or (key == "query" and isinstance(value, str) and value in INVOICE_LINK_QUERIES)))
	if link_request and set(request.args) & set(request.form):
		raise Denied(400)
	if request.method != "GET":
		if request.mimetype == "application/json":
			body = transport.exact_json(request.get_data())
			if not isinstance(body, dict) or any(selector(key, value) for key, value in body.items()) or (link_request and bool(set(request.args) & set(body))):
				raise Denied(400)
		elif any(len(request.form.getlist(key)) != 1 or selector(key, request.form.get(key)) for key in request.form):
			raise Denied(400)
	if any(len(request.args.getlist(key)) != 1 or selector(key, request.args.get(key)) for key in request.args):
		raise Denied(400)
	cookies = {}
	for part in request.headers.get("Cookie", "").split(";"):
		if not part.strip():
			continue
		name, sep, value = part.strip().partition("=")
		if not sep or name in cookies or len(value) > 2048 or name not in (transport.COOKIE, transport.FLOW_COOKIE, "sid", "user_id", "full_name", "user_image", "user_lang", "system_user"):
			raise Denied(401)
		cookies[name] = value
	if not transport.SESSION.fullmatch(cookies.get(transport.COOKIE, "")) or not re.fullmatch(r"[A-Za-z0-9]{12,128}", cookies.get("sid", "")):
		raise Denied(401)
	if request.path in ("/company-session/logout", "/api/method/logout") and request.method == "POST":
		operation = "logout"
	elif request.method == "GET" and (request.path == "/desk" or request.path.startswith("/desk/")):
		operation = "desk"
	elif request.path.startswith("/api/method/") and request.method in ("GET", "POST", "PUT"):
		method = request.path[len("/api/method/"):]
		if method == SAVE_METHOD and request.method in ("POST", "PUT"):
			operation = "save"
		elif method in INVOICE_INITIALIZERS and request.method == "POST":
			operation = "invoice-initialize"
		elif method == TAX_TEMPLATE and request.method == "POST":
			operation = "tax-calculate"
		elif method in CALCULATORS and request.method == "POST":
			operation = "calculate"
		elif method in LINK_METHODS and request.method in ("GET", "POST"):
			operation = "link-read"
		elif method == SALES_SETTINGS and request.method in ("GET", "POST"):
			operation = "sales-settings"
		elif method == LIST_SETTINGS and request.method == "POST":
			operation = "list-settings"
		elif method in READ_METHODS | DESK_METHODS:
			operation = "read"
		else:
			raise Denied(404)
	else:
		raise Denied(404)
	if request.method != "GET" and (request.headers.get("Origin") != config.origin or request.content_length is None):
		raise Denied(403)
	return operation, cookies[transport.COOKIE]


def assert_site_configuration():
	import frappe
	# Adopted editor sites must have completed the shipped operator setup;
	# customer requests cannot enter an unsupported administrative wizard.
	apps = frappe.get_all("Installed Application", filters={"app_name": ["in", ["frappe", "erpnext"]]}, fields=["app_name", "is_setup_complete"])
	if len(apps) != 2 or {row.get("app_name") for row in apps} != {"frappe", "erpnext"} or any(type(row.get("is_setup_complete")) is not int or row["is_setup_complete"] != 1 for row in apps):
		raise Denied(503)
	if frappe.conf.maintenance_mode or any(frappe.conf.get(key) for key in (
		"gotrue_url", "gotrue_admin_token", "gotrue_external_url", "exe_admin_token", "exe_erp_admin_token", "auth_token",
		"jwt_secret", "exe_bridge_database_url", "ignore_csrf", "server_script_enabled",
	)):
		raise Denied(503)


def native_budget(deadline):
	import frappe
	remaining = int((deadline - time.monotonic()) * 1000)
	if frappe.conf.db_type != "postgres" or remaining <= 0:
		raise Denied(503)
	frappe.db.sql(f"SET LOCAL statement_timeout = {min(9000, remaining)}")
	frappe.db.sql("SET LOCAL lock_timeout = 1000")
	frappe.db.sql("SET LOCAL idle_in_transaction_session_timeout = 10000")


def initialize_native_request(context):
	import frappe
	from frappe.auth import LoginManager
	current(context)
	frappe.flags.company_session = True
	frappe.flags.company_editor = context
	native_budget(context.deadline)
	assert_site_configuration()
	from frappe.auth import HTTPRequest
	HTTPRequest.__new__(HTTPRequest).set_request_ip()
	if any(key in SELECTORS and not (context.operation == "link-read" and ((key == "ignore_user_permissions" and native_zero(value)) or (key == "query" and isinstance(value, str) and value in INVOICE_LINK_QUERIES))) for key, value in frappe.form_dict.items()):
		raise Denied(400)
	user = transport.native_user(context.config, context.value, fresh_roles=True, set_principal=False)
	# Native cache is not proof of an unrevoked SID: require its actual current row.
	sid = frappe.request.cookies.get("sid")
	row = frappe.db.get_value("Sessions", {"sid": sid}, ["user", "sessiondata", "status"], as_dict=True, order_by="sid")
	if not row or row.user != user or row.status != "Active":
		raise Denied(401)
	data = transport.exact_json(row.sessiondata)
	if data.get("company_binding") != context.binding:
		raise Denied(401)
	frappe.local.login_manager = LoginManager.for_company(user, context.binding)
	if frappe.session.user != user or frappe.session.data.get("company_binding") != context.binding:
		raise Denied(401)
	csrf = frappe.session.data.get("csrf_token")
	if not isinstance(csrf, str) or not 8 <= len(csrf) <= 128:
		raise Denied(401)
	if frappe.request.method != "GET":
		provided = frappe.request.headers.get("X-Frappe-CSRF-Token")
		if not isinstance(provided, str) or not hmac.compare_digest(provided, csrf):
			raise Denied(403)
	if context.operation in ("save", "calculate", "tax-calculate", "invoice-initialize") and context.value["scopes"] != ["erp:read", "erp:write"]:
		raise Denied(403)
	current(context)


def callback_native_session(config, value, token, request, sites_path, deadline):
	import frappe
	from frappe.auth import LoginManager
	frappe.init(config.site, sites_path=sites_path, force=True)
	frappe.local.request = request
	from frappe.utils import CallbackManager
	request.after_response = CallbackManager()
	frappe.connect(set_admin_as_user=False)
	frappe.flags.company_session = True
	native_budget(deadline)
	assert_site_configuration()
	from frappe.auth import HTTPRequest
	HTTPRequest.__new__(HTTPRequest).set_request_ip()
	user = transport.native_user(config, value, fresh_roles=True, set_principal=False)
	frappe.local.login_manager = LoginManager.for_company(user, session_binding(config, value, token), start=True)


def method_guard(method):
	import frappe
	context = frappe.flags.get("company_editor")
	if context is None:
		return
	current(context)
	if frappe.override_whitelisted_method(method) != method:
		raise Denied(403)
	if method not in READ_METHODS | DESK_METHODS | {SAVE_METHOD, LIST_SETTINGS, SALES_SETTINGS, TAX_TEMPLATE} | CALCULATORS | LINK_METHODS | INVOICE_INITIALIZERS:
		raise Denied(404)
	if method in INVOICE_INITIALIZERS:
		invoice_initialization_guard(method)
	elif method in LINK_METHODS:
		link_guard(method)
	elif method == TAX_TEMPLATE:
		tax_template_guard()
	elif method == SALES_SETTINGS:
		sales_settings_guard()
	elif method == LIST_SETTINGS:
		list_settings_guard()
	elif method == SAVE_METHOD:
		if context.operation != "save" or frappe.form_dict.get("action") != "Save":
			raise Denied(403)
		validate_save(frappe.form_dict.get("doc"))
	elif method in READ_METHODS:
		doctype = frappe.form_dict.get("doctype")
		if not isinstance(doctype, str) or len(doctype) > 140 or not frappe.has_permission(doctype, "read") or frappe.get_meta(doctype).is_virtual:
			raise Denied(403)
		if any(key in SELECTORS and key != "cmd" for key in frappe.form_dict) or frappe.form_dict.get("query"):
			raise Denied(400)


def validate_save(raw):
	import frappe
	context = frappe.flags.get("company_editor")
	if context is None:
		return
	if context.value["scopes"] != ["erp:read", "erp:write"] or not isinstance(raw, str) or len(raw.encode()) > 1024**2:
		raise Denied(403)
	doc = transport.exact_json(raw)
	if not isinstance(doc, dict) or doc.get("doctype") not in WRITE_TYPES or doc.get("docstatus", 0) != 0:
		raise Denied(403)
	def controls(value):
		if isinstance(value, dict):
			if any(key in SELECTORS or key == "flags" or (key.startswith("ignore_") and not (value.get("doctype") == "Sales Invoice" and key in BUSINESS_ZERO_FIELDS and type(value[key]) in (int, bool) and value[key] == 0)) for key in value):
				raise Denied(400)
			for child in value.values():
				controls(child)
		elif isinstance(value, list):
			for child in value:
				controls(child)
	controls(doc)
	canonical_document(doc)
	# Native save performs create/write, field-level and User Permission checks.
	# Do not use ignore_permissions or reproduce business validation in this bridge.


def canonical_document(payload):
	"""Check stored native authority before interpreting an existing draft update."""
	import frappe
	kind, name = payload["doctype"], payload.get("name")
	new = bool(payload.get("__islocal")) or not name
	if name is not None and (not isinstance(name, str) or not 1 <= len(name) <= 140):
		raise Denied(400)
	if new:
		if name and not name.startswith("new-" + kind.lower().replace(" ", "-") + "-"):
			raise Denied(403)
		stored = None
		frappe.get_doc(payload).check_permission("create")
	else:
		stored = frappe.get_doc(kind, name)
		stored.check_permission("write")
		if stored.docstatus != 0 or payload.get("owner") != stored.owner:
			raise Denied(403)
	for field in frappe.get_meta(kind).get_table_fields():
		rows = payload.get(field.fieldname, [])
		if not isinstance(rows, list) or len(rows) > 1000:
			raise Denied(400)
		original = {row.name: row for row in stored.get(field.fieldname, [])} if stored else {}
		seen = set()
		for row in rows:
			if not isinstance(row, dict) or row.get("doctype") != field.options:
				raise Denied(400)
			if any(key in row and row[key] != value for key, value in (("parent", name), ("parenttype", kind), ("parentfield", field.fieldname))):
				raise Denied(403)
			child_name = row.get("name")
			if child_name:
				if not isinstance(child_name, str) or len(child_name) > 140 or child_name in seen:
					raise Denied(400)
				seen.add(child_name)
			if row.get("__islocal") or not child_name:
				if "owner" in row and row["owner"] != frappe.session.user:
					raise Denied(403)
				if child_name and not child_name.startswith("new-" + field.options.lower().replace(" ", "-") + "-"):
					raise Denied(403)
			else:
				actual = original.get(child_name)
				if actual is None or row.get("owner") != actual.owner:
					raise Denied(403)
	return stored


def recheck_before_commit(context):
	import frappe
	current(context)
	value = transport.private_call(context.config, "introspect", context.token)
	if session_binding(context.config, value, context.token) != context.binding:
		raise Denied(403)
	# Discard the request snapshot and decisions before fresh native authority.
	frappe.local.request_cache.pop("company_editor_meta", None)
	frappe.local.role_permissions = {}
	transport.native_user(context.config, value, fresh_roles=True, set_principal=False)
	if context.operation == "save":
		for result in frappe.response.docs:
			if result.get("doctype") in WRITE_TYPES and result.get("name"):
				frappe.get_doc(result["doctype"], result["name"]).check_permission("write")
				if result["doctype"] == "Sales Invoice":
					rule_free_admission()
	current(context)
	# A previously authorized request may finish under bounded native transaction
	# semantics. This recheck narrows races; it cannot make two databases atomic.


def boot_ceiling(boot):
	"""Native permission lists remain authoritative, with the Core draft ceiling."""
	import frappe
	context = frappe.flags.get("company_editor")
	if not isinstance(context,Context):
		return
	current(context)
	writable = sorted(WRITE_TYPES) if context.value["scopes"] == ["erp:read", "erp:write"] else []
	for key in ("can_create", "can_write", "in_create"):
		boot["user"][key] = [kind for kind in boot["user"].get(key, []) if kind in writable]
	for key in ("can_delete", "can_submit", "can_cancel", "can_import", "can_export", "can_print", "can_email"):
		boot["user"][key] = []
	boot.setdefault("sysdefaults", {})["use_legacy_js_reactivity"] = 1
	boot["company_editor"] = {"version": 2, "writable_doctypes": writable, "auth_origin": context.config.auth_origin}



def form_meta_ceiling(value):
	"""Hosted draft editors use the supported native full-form Save path."""
	import frappe
	context = frappe.flags.get("company_editor")
	if isinstance(context, Context) and value.get("name") in WRITE_TYPES:
		current(context)
		value["quick_entry"] = 0
	return value


def application(request, config, sites_path):
	from werkzeug.wrappers import Response

	import frappe
	from frappe.app import native_application
	context = None
	deadline = time.monotonic() + 9
	try:
		operation, token = ingress(request, config)
		value = transport.private_call(config, "introspect", token)
		context = Context(config, value, token, session_binding(config, value, token), deadline, operation)
		if operation == "logout":
			frappe.init(config.site, sites_path=sites_path, force=True)
			frappe.local.request = request
			frappe.connect(set_admin_as_user=False)
			frappe.local.form_dict = frappe._dict()
			initialize_native_request(context)
			result = transport.private_call(config, "revoke", token)
			if result != {"revoked": True}:
				raise Denied(503)
			from frappe.sessions import delete_session
			delete_session(frappe.session.sid, reason="Company session logout")
			frappe.db.commit()
			response = Response('{"revoked":true}', content_type="application/json")
			response.set_cookie(transport.COOKIE, "", max_age=0, secure=True, httponly=True, samesite="Lax")
			response.set_cookie("sid", "", max_age=0, secure=True, httponly=True, samesite="Lax")
		else:
			response = native_application(request, context)
	except Exception as error:
		if getattr(frappe.local, "db", None):
			frappe.db.rollback()
		response = Response('{"error":"unavailable"}', status=error.status if isinstance(error, Denied) else (404 if isinstance(error, (frappe.PermissionError, frappe.DoesNotExistError)) else 503), content_type="application/json")
	response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "frame-ancestors 'self'"})
	return response

def bounded_calculator_scalars(args, *, form=False):
	import math
	from datetime import date
	string_keys = PARTY_ARGUMENTS - {"fetch_payment_terms_template"} if form else ITEM_ARGUMENTS - ITEM_ZERO - {"conversion_rate", "plc_conversion_rate", "qty", "net_rate", "base_net_rate", "stock_qty", "conversion_factor", "weight_per_unit"}
	for key in string_keys:
		value = args.get(key)
		if value is not None and (not isinstance(value, str) or len(value.encode()) > (10 if key == "posting_date" else 140) or any(ord(char) < 32 for char in value)):
			raise Denied(400)
	if form:
		if args.get("posting_date"):
			try:
				if date.fromisoformat(args["posting_date"]).isoformat() != args["posting_date"]:
					raise ValueError
			except ValueError:
				raise Denied(400) from None
		if args.get("fetch_payment_terms_template", "1") not in ("1", 1) or isinstance(args.get("fetch_payment_terms_template"), bool):
			raise Denied(400)
	else:
		for key in ITEM_ZERO:
			if args.get(key) is not None and (type(args[key]) is not int or args[key] != 0):
				raise Denied(400)
		for key in ("conversion_rate", "plc_conversion_rate", "qty", "net_rate", "base_net_rate", "stock_qty", "conversion_factor", "weight_per_unit"):
			value = args.get(key)
			if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000000):
				raise Denied(400)
		if type(args.get("qty")) not in (int, float) or not 0 < args["qty"] <= 1000000:
			raise Denied(400)


def calculator_guard(method, supplied=None):
	import frappe
	context = frappe.flags.company_editor
	if context.operation != "calculate" or context.value["scopes"] != ["erp:read", "erp:write"]:
		raise Denied(403)
	args = supplied if supplied is not None else {key: value for key, value in frappe.form_dict.items() if key != "cmd"}
	if method == PARTY_CALCULATOR:
		if set(args) - PARTY_ARGUMENTS or args.get("doctype") != "Sales Invoice" or args.get("party_type") != "Customer" or str(args.get("fetch_payment_terms_template", 1)) != "1":
			raise Denied(400)
		bounded_calculator_scalars(args, form=True)
		if any(not isinstance(args.get(key), str) or not args[key] for key in ("company", "party")):
			raise Denied(400)
		document = frappe.get_doc({"doctype": "Sales Invoice", "customer": args.get("party"), "company": args.get("company")})
		document.check_permission("create")
		child = False
	else:
		if set(args) != {"doc", "ctx"} or not all(isinstance(args[key], str) for key in args):
			raise Denied(400)
		validate_save(args["doc"])
		payload, ctx = transport.exact_json(args["doc"]), transport.exact_json(args["ctx"])
		if payload.get("doctype") != "Sales Invoice" or not isinstance(ctx, dict) or set(ctx) - ITEM_ARGUMENTS:
			raise Denied(400)
		bounded_calculator_scalars(ctx)
		if any(not isinstance(ctx.get(key), str) or not ctx[key] for key in ("company", "customer", "item_code", "name", "child_docname")):
			raise Denied(400)
		bounded_calculator_scalars({"posting_date": payload.get("posting_date")}, form=True)
		if any(ctx.get(key) not in (None, "") for key in ITEM_EMPTY) or any(ctx.get(key) not in (None, "", 0, False) for key in ITEM_ZERO):
			raise Denied(400)
		if any(ctx.get(key) != payload.get(other) for key, other in (("company", "company"), ("customer", "customer"), ("currency", "currency"), ("name", "name"), ("doctype", "doctype"))):
			raise Denied(403)
		if any(payload.get(key, 0) != 0 or ctx.get(key, 0) not in (None, 0) for key in ("is_pos", "is_return", "update_stock", "ignore_pricing_rule")):
			raise Denied(403)
		rows = [row for row in payload.get("items", []) if row.get("name") == ctx.get("child_docname")]
		if len(rows) != 1 or ctx.get("child_doctype") != "Sales Invoice Item" or rows[0].get("item_code") != ctx.get("item_code"):
			raise Denied(403)
		item = frappe.get_doc("Item", ctx.get("item_code"))
		item.check_permission("read")
		if item.is_stock_item or item.has_serial_no or item.has_batch_no:
			raise Denied(403)
		for key in ("uom", "stock_uom", "weight_uom", "cost_center"):
			if ctx.get(key):
				frappe.get_doc({"uom": "UOM", "stock_uom": "UOM", "weight_uom": "UOM", "cost_center": "Cost Center"}[key], ctx[key]).check_permission("read")
		if ctx.get("conversion_rate") != 1 or ctx.get("plc_conversion_rate") != 1:
			raise Denied(403)
		args = ctx
		document = frappe.get_doc(payload)
		child = True
	company = frappe.get_doc("Company", args.get("company"))
	company.check_permission("read")
	customer = frappe.get_doc("Customer", args.get("party") or args.get("customer"))
	customer.check_permission("read")
	if customer.default_currency and customer.default_currency != company.default_currency:
		raise Denied(403)
	if args.get("currency") != company.default_currency or (child and args.get("price_list_currency") != company.default_currency):
		raise Denied(403)
	prices = {args.get("price_list")}
	if not child:
		from erpnext.accounts.party import get_default_price_list

		from frappe.core.doctype.user_permission.user_permission import get_permitted_documents
		frappe.get_doc("Customer Group", customer.customer_group).check_permission("read")
		prices.add(get_default_price_list(customer))
		permitted = get_permitted_documents("Price List")
		if len(permitted) == 1:
			prices.add(permitted[0])
	elif frappe.get_single_value("Selling Settings", "fallback_to_default_price_list"):
		prices.add(frappe.get_single_value("Selling Settings", "selling_price_list"))
	for name in prices - {None, ""}:
		price = frappe.get_doc("Price List", name)
		price.check_permission("read")
		if not price.enabled or not price.selling or price.currency != company.default_currency:
			raise Denied(403)
	if args.get("company_address"):
		frappe.get_doc("Address", args["company_address"]).check_permission("read")
	return document, child



def calculator_projection(result, document, child):
	import frappe
	# Use native field permissions and masking, then return only native form fields.
	kind = "Sales Invoice Item" if child else "Sales Invoice"
	metadata = frappe.get_meta(kind)
	fields = {field.fieldname for field in metadata.fields if field.fieldtype not in ("Table", "Table MultiSelect", "Dynamic Link", "HTML", "Button", "Attach", "Attach Image", "Password")}
	if any(result.get(field.fieldname) for field in metadata.fields if field.fieldtype in ("Table", "Table MultiSelect", "Dynamic Link")):
		raise Denied(403)
	# Two stock text fields encode native links. Check their finite structures
	# and linked ACLs rather than treating serialized data as an unscoped scalar.
	for key, link_kind, container in (("pricing_rules", "Pricing Rule", list), ("item_tax_rate", "Account", dict)):
		if result.get(key):
			if not isinstance(result[key], str) or len(result[key].encode()) > 16384:
				raise Denied(403)
			links = transport.exact_json(result[key])
			if not isinstance(links, container) or len(links) > 100:
				raise Denied(403)
			for name in links:
				if not isinstance(name, str) or not name or len(name.encode()) > 140:
					raise Denied(403)
				frappe.get_doc(link_kind, name).check_permission("read")
	selected = {key: value for key, value in result.items() if key in fields}
	if any(isinstance(value, (dict, list)) for value in selected.values()):
		raise Denied(403)
	for field in frappe.get_meta(kind).fields:
		if field.fieldtype == "Link" and selected.get(field.fieldname):
			frappe.get_doc(field.options, selected[field.fieldname]).check_permission("read")
	if child:
		document.set("items", [{"doctype": kind, **selected}])
	else:
		document.update(selected)
	document.apply_fieldlevel_read_permissions()
	filtered = document.items[0].as_dict() if child else document.as_dict()
	return {key: filtered[key] for key in selected if key in filtered}

def calculate(method):
	"""Exactly two native calculators, without persisting their optional side effects."""
	import frappe
	context = frappe.flags.company_editor
	current(context)
	if method == PRICE_CALCULATOR:
		return apply_prices()
	if method == RULE_CALCULATOR:
		return apply_rules()
	# Authentication has read current native SID. Start a fresh readonly statement
	# before checking native documents; no invoice or pricing writes may commit.
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		document, child = calculator_guard(method)
		currency = transport.exact_json(frappe.form_dict["ctx"])["currency"] if child else frappe.form_dict["currency"]
		frappe.local.request_cache["company_editor_currency"] = (context,currency)
		from erpnext.accounts.party import get_party_details
		from erpnext.stock.get_item_details import get_item_details
		function = get_party_details if method == PARTY_CALCULATOR else get_item_details
		result = frappe.call(function, **{key: value for key, value in frappe.form_dict.items() if key != "cmd"})
		if not isinstance(result, dict):
			raise Denied(503)
		result = calculator_projection(result, document, child)
	finally:
		frappe.local.request_cache.pop("company_editor_currency",None)
		frappe.db.rollback()
	# Fresh Core and native User/roles check before publishing computed values.
	native_budget(context.deadline)
	recheck_before_commit(context)
	document, child = calculator_guard(method)
	if child:
		rule_free_admission()
	return calculator_projection(result, document, child)


def list_settings_guard():
	import frappe
	context = frappe.flags.get("company_editor")
	current(context)
	if context.operation != "list-settings" or frappe.request.method != "POST":
		raise Denied(403)
	if set(frappe.form_dict) - {"cmd", "doctype"}:
		raise Denied(400)
	kind = frappe.form_dict.get("doctype")
	if not isinstance(kind, str) or kind not in WRITE_TYPES:
		raise Denied(400)
	if not frappe.has_permission(kind, "read") or frappe.get_meta(kind).is_virtual:
		raise Denied(403)
	return kind


def list_settings_projection(result, kind):
	import frappe
	from frappe.model import get_permitted_fields
	if result is None:
		return {}
	selected = {}
	for key in LIST_FLAGS:
		value = result.get(key)
		if type(value) is not int or value not in (0, 1):
			raise Denied(503)
		selected[key] = value
	raw = result.get("fields")
	if raw is not None and raw != "":
		if not isinstance(raw, str) or len(raw.encode()) > 16384:
			raise Denied(503)
		fields = transport.exact_json(raw)
		if not isinstance(fields, list) or len(fields) > 100:
			raise Denied(503)
		permitted = set(get_permitted_fields(kind, permission_type="read"))
		seen = set()
		for row in fields:
			if not isinstance(row, dict) or set(row) - {"fieldname", "width"}:
				raise Denied(503)
			name = row.get("fieldname")
			if not isinstance(name, str) or not name or len(name.encode()) > 140 or name in seen:
				raise Denied(503)
			if ("status" if name == "status_field" else name) not in permitted:
				raise Denied(403)
			if "width" in row and (type(row["width"]) is not int or not 40 <= row["width"] <= 1000):
				raise Denied(503)
			seen.add(name)
		selected["fields"] = json.dumps(fields, separators=(",", ":"))
	return selected


def read_list_settings():
	import frappe
	from frappe.desk.listview import get_list_settings
	context = frappe.flags.get("company_editor")
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		kind = list_settings_guard()
		result = list_settings_projection(get_list_settings(kind), kind)
	finally:
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	kind = list_settings_guard()
	# Reapply fresh field permission decisions before publishing the same snapshot.
	return list_settings_projection(result if result else None, kind)


def link_guard(method):
	import frappe
	from frappe.model import get_permitted_fields
	context = frappe.flags.get("company_editor")
	current(context)
	if context.operation != "link-read" or frappe.request.method not in ("GET", "POST"):
		raise Denied(403)
	args = {key: value for key, value in frappe.form_dict.items() if key != "cmd"}
	common = {"doctype", "reference_doctype", "link_fieldname", "filters", "ignore_user_permissions", "txt", "page_length", "query"}
	allowed = common | ({"docname", "fields_to_fetch"} if method == LINK_VALIDATE else set())
	if method not in LINK_METHODS or set(args) - allowed:
		raise Denied(400)
	kind, parent, field = (args.get(key) for key in ("doctype", "reference_doctype", "link_fieldname"))
	if not isinstance(parent, str) or parent not in WRITE_TYPES | {"Sales Invoice Item"} or not isinstance(kind, str) or not isinstance(field, str) or not 1 <= len(field.encode()) <= 140 or (parent,field) not in INVOICE_LINK_FIELDS or INVOICE_LINK_FIELDS[(parent,field)] != kind:
		raise Denied(400)
	owner = "Sales Invoice" if parent == "Sales Invoice Item" else parent
	if not frappe.has_permission(owner, "read") or not frappe.has_permission(kind, "read"):
		raise Denied(403)
	meta = frappe.get_meta(parent)
	if meta.is_virtual:
		raise Denied(403)
	if parent == "Sales Invoice Item":
		parent_meta = frappe.get_meta(owner)
		if not any(df.options == parent and df.permlevel in parent_meta.get_permlevel_access("read") for df in parent_meta.get_table_fields()):
			raise Denied(403)
	df = meta.get_field(field)
	if not df or df.fieldtype != "Link" or df.options != kind or field not in get_permitted_fields(parent, parenttype=owner if parent != owner else None, permission_type="read"):
		raise Denied(403)
	target = frappe.get_meta(kind)
	if target.is_virtual or kind in (frappe.get_hooks().standard_queries or {}):
		raise Denied(403)
	if "ignore_user_permissions" in args and not native_zero(args["ignore_user_permissions"]):
		raise Denied(400)
	args["ignore_user_permissions"] = False
	args = invoice_link_filters(args)
	limit = args.get("page_length", 10)
	if type(limit) is str and re.fullmatch(r"[1-9]|10", limit):
		limit = int(limit)
	if type(limit) is not int or not 1 <= limit <= 10:
		raise Denied(400)
	args["page_length"] = limit
	text = args.get("docname") if method == LINK_VALIDATE else args.get("txt")
	if not isinstance(text, str) or len(text.encode()) > 140 or (method == LINK_VALIDATE and not text) or any(ord(c) < 32 for c in text):
		raise Denied(400)
	if method == LINK_VALIDATE:
		if args.get("txt", text) != text:
			raise Denied(400)
		fields = args.pop("fields_to_fetch", [])
		if isinstance(fields,str):
			if len(fields.encode()) > 1024:
				raise Denied(400)
			fields = transport.exact_json(fields)
		allowed_fetch = CUSTOMER_FETCH_FIELDS if (parent,field,kind) == ("Sales Invoice","customer","Customer") else ITEM_FETCH_FIELDS if (parent,field,kind) == ("Sales Invoice Item","item_code","Item") else COMPANY_FETCH_FIELDS if (parent,field,kind) == ("Sales Invoice","company","Company") else frozenset()
		if not isinstance(fields,list) or len(fields)>len(allowed_fetch) or any(not isinstance(name,str) or name not in allowed_fetch for name in fields) or len(set(fields)) != len(fields):
			raise Denied(400)
		# Native fetch_from names the target field; its parent destination can have
		# a different name (Company.tax_id -> Sales Invoice.company_tax_id).
		permitted_parent = get_permitted_fields(parent,parenttype=owner if parent != owner else None,permission_type="read")
		for name in fields:
			destinations = [df.fieldname for df in frappe.get_meta(parent).fields if df.get("fetch_from") == field + "." + name]
			if not destinations or any(destination not in permitted_parent for destination in destinations):
				raise Denied(403)
		args["fields_to_fetch"] = fields
	return args


def link_projection(result, method, args):
	import frappe
	from frappe.model import get_permitted_fields
	if "name" not in get_permitted_fields(args["doctype"], permission_type="read"):
		raise Denied(403)
	if method == LINK_VALIDATE:
		if not isinstance(result, dict) or set(result) - ({"name"} | set(args.get("fields_to_fetch",[]))):
			raise Denied(503)
		names = [result["name"]] if result else []
		if names and names[0] != args["docname"]:
			raise Denied(503)
	else:
		if not isinstance(result, list) or len(result) > args["page_length"]:
			raise Denied(503)
		names = [row.get("value") if isinstance(row, dict) else None for row in result]
	for name in names:
		if not isinstance(name, str) or not name or len(name.encode()) > 140:
			raise Denied(503)
		frappe.get_doc(args["doctype"], name).check_permission("read")
	if method == LINK_VALIDATE:
		if not names:
			return {}
		document = frappe.get_doc(args["doctype"],names[0])
		permitted = get_permitted_fields(args["doctype"],permission_type="read")
		selected = {"name":names[0]}
		for key in args.get("fields_to_fetch",[]):
			if key not in permitted:
				raise Denied(403)
			df = frappe.get_meta(args["doctype"]).get_field(key)
			if not df:
				raise Denied(403)
			value = document.get(key)
			if args["doctype"] == "Item" and key == "grant_commission" and df.fieldtype == "Check":
				if type(value) is not int or value not in (0,1):
					raise Denied(503)
			elif args["doctype"] == "Item" and key == "image" and df.fieldtype == "Attach Image":
				if value not in (None,""):
					raise Denied(403)
			elif args["doctype"] == "Customer" and key == "is_internal_customer" and df.fieldtype == "Check":
				if type(value) is not int or value != 0:
					raise Denied(403)
			elif df.fieldtype not in ("Data","Link"):
				raise Denied(403)
			elif value is not None and (not isinstance(value,str) or len(value.encode())>140):
				raise Denied(503)
			if df.fieldtype == "Link" and value:
				frappe.get_doc(df.options,value).check_permission("read")
			selected[key] = value
		return selected
	return [{"value":name} for name in names]


def read_link(method):
	import frappe
	from frappe.client import validate_link_and_fetch
	context = frappe.flags.get("company_editor")
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		args = link_guard(method)
		if method == LINK_VALIDATE:
			frappe.local.request_cache["company_editor_link_exact"] = (context,args["doctype"],args["docname"])
		if args["doctype"] in ("Customer Group","Territory") and method == LINK_VALIDATE:
			result = frappe.call(validate_link_and_fetch,**args)
		else:
			names = invoice_link_names(args,method)
			result = ({"name":names[0]} if names else {}) if method == LINK_VALIDATE else [{"value":name} for name in names]
		result = link_projection(result, method, args)
	finally:
		frappe.local.request_cache.pop("company_editor_link_exact",None)
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	args = link_guard(method)
	return link_projection(result, method, args)


PRICE_PARENT_FIELDS = frozenset(("items", "customer", "quotation_to", "customer_group", "territory", "supplier", "supplier_group", "currency", "conversion_rate", "price_list", "price_list_currency", "plc_conversion_rate", "company", "transaction_date", "campaign", "sales_partner", "ignore_pricing_rule", "doctype", "name", "is_return", "update_stock", "conversion_factor", "pos_profile", "coupon_code", "is_internal_supplier", "is_internal_customer"))
PRICE_ITEM_FIELDS = frozenset(("doctype", "name", "child_docname", "item_code", "item_group", "brand", "qty", "stock_qty", "uom", "stock_uom", "parenttype", "parent", "pricing_rules", "is_free_item", "warehouse", "serial_no", "batch_no", "price_list_rate", "conversion_factor", "discount_percentage", "discount_amount", "margin_type", "margin_rate_or_amount"))


def price_list_guard(parameter="ctx"):
	"""The shipped _get_args DTO, bound to exactly the submitted native draft."""
	import math

	import frappe
	context = frappe.flags.company_editor
	current(context)
	if context.operation != "calculate" or context.value["scopes"] != ["erp:read", "erp:write"]:
		raise Denied(403)
	args = {key:value for key,value in frappe.form_dict.items() if key != "cmd"}
	if parameter not in ("ctx","args") or set(args) != {parameter, "doc"} or not all(isinstance(value,str) and len(value.encode()) <= 1024**2 for value in args.values()):
		raise Denied(400)
	validate_save(args["doc"])
	payload, ctx = transport.exact_json(args["doc"]), transport.exact_json(args[parameter])
	if payload.get("doctype") != "Sales Invoice" or not isinstance(ctx,dict) or set(ctx) - PRICE_PARENT_FIELDS:
		raise Denied(400)
	rows = ctx.get("items")
	if not isinstance(rows,list) or len(rows) > 100:
		raise Denied(400)
	zero = {"ignore_pricing_rule","is_return","update_stock","is_internal_supplier","is_internal_customer"}
	empty = {"quotation_to","supplier","supplier_group","campaign","sales_partner","pos_profile","coupon_code"}
	for key,value in ctx.items():
		if key == "items":
			continue
		if key in zero:
			if value is not None and (type(value) is not int or value != 0):
				raise Denied(400)
		elif key in ("conversion_rate","plc_conversion_rate","conversion_factor"):
			if value not in (None,"") and (type(value) not in (int,float) or not math.isfinite(value) or value != 1):
				raise Denied(400)
		elif value is not None and (not isinstance(value,str) or len(value.encode()) > 140 or any(ord(c)<32 for c in value)):
			raise Denied(400)
		if key in empty and value not in (None,""):
			raise Denied(403)
	# The stock new-form callback prices an empty item list before a Customer
	# exists. Permit only that native draft initialization, never an item or an
	# existing invoice without its current Customer authority.
	initial = parameter == "ctx" and bool(payload.get("__islocal")) and ctx.get("customer") in (None,"") and payload.get("customer") in (None,"") and not rows
	if initial and (len(payload.get("items",[])) > 100 or any(row.get("item_code") for row in payload.get("items",[]))):
		raise Denied(403)
	for key,other in (("doctype","doctype"),("name","name"),("company","company"),("customer","customer"),("currency","currency"),("price_list","selling_price_list"),("transaction_date","posting_date")):
		if key == "customer" and initial:
			continue
		if not isinstance(ctx.get(key),str) or not ctx[key] or ctx[key] != payload.get(other):
			raise Denied(403)
	bounded_calculator_scalars({"posting_date":ctx["transaction_date"]},form=True)
	company = frappe.get_doc("Company",ctx["company"])
	company.check_permission("read")
	customer = None if initial else frappe.get_doc("Customer",ctx["customer"])
	if customer:
		customer.check_permission("read")
		if customer.default_currency and customer.default_currency != company.default_currency:
			raise Denied(403)
	if any(payload.get(key,0) != 0 for key in ("is_pos","is_return","update_stock","ignore_pricing_rule","is_internal_customer","is_internal_supplier")):
		raise Denied(403)
	if ctx["currency"] != company.default_currency or ctx.get("price_list_currency") not in (None,"",company.default_currency) or type(payload.get("conversion_rate")) not in (int,float) or payload["conversion_rate"] != 1:
		raise Denied(403)
	for key,target in (("customer_group","Customer Group"),("territory","Territory")):
		if ctx.get(key) not in ((None,"") if initial else (None,"",customer.get(key))):
			raise Denied(403)
		if ctx.get(key):
			frappe.get_doc(target,ctx[key]).check_permission("read")
	prices = {ctx["price_list"]}
	if frappe.get_single_value("Selling Settings","fallback_to_default_price_list"):
		prices.add(frappe.get_single_value("Selling Settings","selling_price_list"))
	for name in prices - {None,""}:
		price = frappe.get_doc("Price List",name)
		price.check_permission("read")
		if not price.enabled or not price.selling or price.currency != company.default_currency:
			raise Denied(403)
	# Native UI clears this factor before recalculation. Exact same-currency
	# verification permits a fixed one, without invoking an exchange provider.
	ctx["price_list_currency"],ctx["plc_conversion_rate"] = company.default_currency,1
	ctx["conversion_rate"] = 1
	document = frappe.get_doc(payload)
	actual_rows = {row.get("name"):row for row in payload.get("items",[])}
	seen = set()
	for row in rows:
		if not isinstance(row,dict) or set(row) - PRICE_ITEM_FIELDS or not isinstance(row.get("name"),str) or not row["name"] or row["name"] in seen:
			raise Denied(400)
		seen.add(row["name"])
		actual = actual_rows.get(row["name"])
		if actual is None or row.get("doctype") != "Sales Invoice Item" or row.get("child_docname") != row["name"]:
			raise Denied(403)
		for key,value in row.items():
			if key == "child_docname":
				continue
			if key in {"qty","stock_qty","price_list_rate","conversion_factor","discount_percentage","discount_amount","margin_rate_or_amount"}:
				if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0 <= value <= 1000000):
					raise Denied(400)
			elif key == "is_free_item":
				if type(value) is not int or value != 0:
					raise Denied(403)
			elif value is not None and (not isinstance(value,str) or len(value.encode()) > 140 or any(ord(c)<32 for c in value)):
				raise Denied(400)
			if value != actual.get(key) and not (key == "conversion_factor" and value == 1 and actual.get(key) in (None,0)):
				raise Denied(403)
		if row.get("pricing_rules") not in (None,"") or row.get("margin_rate_or_amount") not in (None,0) or (row.get("discount_percentage") or 0) > 100:
			raise Denied(403)
		item = frappe.get_doc("Item",row.get("item_code"))
		item.check_permission("read")
		for key,target in (("item_group","Item Group"),("brand","Brand")):
			if row.get(key) not in (None,"",item.get(key)):
				raise Denied(403)
			if row.get(key):
				frappe.get_doc(target,row[key]).check_permission("read")
		if row.get("warehouse"):
			frappe.get_doc("Warehouse",row["warehouse"]).check_permission("read")
		synthetic = {key:value for key,value in row.items() if key in ITEM_ARGUMENTS and key != "warehouse"}
		synthetic.update({key:ctx[key] for key in ("company","customer","currency","price_list_currency","price_list","conversion_rate","plc_conversion_rate")})
		synthetic.update(doctype="Sales Invoice",name=payload["name"],child_doctype="Sales Invoice Item",child_docname=row["name"])
		calculator_guard(ITEM_CALCULATOR,{"doc":args["doc"],"ctx":json.dumps(synthetic)})
	return document,ctx


def price_list_projection(result, document, ctx):
	import frappe
	if not isinstance(result,dict) or set(result) != {"parent","children"} or not isinstance(result["parent"],dict) or not isinstance(result["children"],list) or len(result["children"]) != len(ctx["items"]):
		raise Denied(503)
	parent = result["parent"]
	if set(parent) - {"price_list_currency","price_list_uom_dependant","plc_conversion_rate"} or parent.get("price_list_currency") != ctx["currency"] or type(parent.get("plc_conversion_rate")) not in (int,float) or parent["plc_conversion_rate"] != 1:
		raise Denied(503)
	selected_parent = calculator_projection(parent,document,False)
	children = []
	for result_row,row in zip(result["children"],ctx["items"],strict=True):
		if not isinstance(result_row,dict) or any(result_row.get(key) for key in ("free_item_data","apply_rule_on_other_items")):
			raise Denied(403)
		selected = calculator_projection(result_row,document,True)
		children.append({**selected,"doctype":"Sales Invoice Item","name":row["name"]})
	return {"parent":selected_parent,"children":children}


def apply_prices():
	from erpnext.stock.get_item_details import apply_price_list

	import frappe
	context = frappe.flags.company_editor
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		document,ctx = price_list_guard()
		frappe.local.request_cache["company_editor_currency"] = (context,ctx["currency"])
		result = price_list_projection(frappe.call(apply_price_list,ctx=json.dumps(ctx),doc=frappe.form_dict["doc"]),document,ctx)
	finally:
		frappe.local.request_cache.pop("company_editor_currency",None)
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	document,ctx = price_list_guard()
	rule_free_admission()
	return price_list_projection(result,document,ctx)


def sales_settings_guard():
	import frappe
	from frappe.model import get_permitted_fields
	context = frappe.flags.company_editor
	current(context)
	args = {key:value for key,value in frappe.form_dict.items() if key != "cmd"}
	if context.operation != "sales-settings" or args != {"doctype":"Accounts Settings","field":"fetch_valuation_rate_for_internal_transaction"}:
		raise Denied(400)
	if context.value["scopes"] != ["erp:read","erp:write"] or not frappe.has_permission("Sales Invoice","read") or not frappe.has_permission("Sales Invoice","create") or not native_table_read("Sales Invoice","items","Sales Invoice Item") or "item_code" not in get_permitted_fields("Sales Invoice Item",parenttype="Sales Invoice",permission_type="read"):
		raise Denied(403)


def read_sales_setting():
	import frappe
	context = frappe.flags.company_editor
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		sales_settings_guard()
		value = frappe.db.get_single_value("Accounts Settings","fetch_valuation_rate_for_internal_transaction")
		if type(value) is not int or value not in (0,1):
			raise Denied(503)
	finally:
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	sales_settings_guard()
	return value


ITEM_FETCH_FIELDS = frozenset(("image","grant_commission"))
CUSTOMER_FETCH_FIELDS = frozenset(("customer_name","is_internal_customer","tax_id","language","represents_company","loyalty_program"))
COMPANY_FETCH_FIELDS = frozenset(("tax_id",))
INVOICE_LINK_FIELDS = {
	("Customer","customer_group"):"Customer Group", ("Customer","territory"):"Territory",
	("Sales Invoice","customer"):"Customer", ("Sales Invoice","company"):"Company",
	("Sales Invoice","currency"):"Currency", ("Sales Invoice","price_list_currency"):"Currency",
	("Sales Invoice","selling_price_list"):"Price List", ("Sales Invoice","debit_to"):"Account",
	("Sales Invoice Item","item_code"):"Item", ("Sales Invoice Item","uom"):"UOM",
	("Sales Invoice Item","income_account"):"Account", ("Sales Invoice Item","cost_center"):"Cost Center",
}


def invoice_link_filters(args):
	"""Source-owned field/query choices, never an arbitrary whitelisted query."""
	import frappe
	kind,parent,field = (args[key] for key in ("doctype","reference_doctype","link_fieldname"))
	filters = args.get("filters",{})
	if isinstance(filters,str):
		if len(filters.encode()) > 1024:
			raise Denied(400)
		filters = transport.exact_json(filters)
	if kind in ("Customer Group","Territory"):
		if isinstance(filters,list) and len(filters)==1 and filters[0] == [kind,"is_group","=",0] and type(filters[0][-1]) is int:
			filters = {"is_group":0}
		expected = {"is_group":0}
	elif field == "selling_price_list":
		expected = {"selling":1}
	elif parent == "Sales Invoice Item" and field == "item_code":
		if not isinstance(filters,dict):
			raise Denied(400)
		customer = filters.get("customer")
		if not isinstance(customer,str) or not 1<=len(customer.encode())<=140:
			raise Denied(400)
		frappe.get_doc("Customer",customer).check_permission("read")
		expected = {"is_sales_item":1,"customer":customer,"has_variants":0}
	elif parent == "Sales Invoice Item" and field == "uom":
		if not isinstance(filters,dict) or not isinstance(filters.get("item_code"),str) or not 1<=len(filters["item_code"].encode())<=140:
			raise Denied(400)
		frappe.get_doc("Item",filters["item_code"]).check_permission("read")
		expected = {"item_code":filters["item_code"]}
	elif field in ("income_account","debit_to","cost_center"):
		if not isinstance(filters,dict) or not isinstance(filters.get("company"),str) or not 1<=len(filters["company"].encode())<=140:
			raise Denied(400)
		frappe.get_doc("Company",filters["company"]).check_permission("read")
		expected = {"company":filters["company"],**({"disabled":0} if field=="income_account" else {"is_group":0})}
		if field=="debit_to":
			expected["account_type"] = "Receivable"
	else:
		expected = {}
	if not isinstance(filters,dict) or filters != expected or any(type(filters[key]) is not type(value) for key,value in expected.items()):
		raise Denied(400)
	query = {
		("Sales Invoice Item","item_code"):"erpnext.controllers.queries.item_query",
		("Sales Invoice Item","uom"):"erpnext.controllers.queries.get_item_uom_query",
		("Sales Invoice Item","income_account"):"erpnext.controllers.queries.get_income_account",
	}.get((parent,field))
	if args.get("query") != query and not (query is None and "query" not in args):
		raise Denied(400)
	return {**args,"filters":dict(expected)}


def invoice_link_names(args,method):
	from erpnext.controllers.queries import get_item_uom_query, item_query

	import frappe
	kind,field = args["doctype"],args["link_fieldname"]
	validation = method == LINK_VALIDATE
	text = args["docname"] if validation else args["txt"]
	limit = 1 if validation else args["page_length"]
	filters = dict(args["filters"])
	if kind == "Item":
		if validation:
			filters["name"] = text
		rows = item_query(kind,text,"name",0,limit,filters)
		names = [row[0] for row in rows]
	elif kind == "UOM":
		# Native UOM selection follows its Item conversion setting; ACL is checked
		# per returned UOM below, including when the stock helper uses get_all.
		if frappe.get_single_value("Stock Settings","allow_uom_with_conversion_rate_defined_in_item"):
			from frappe.model import get_permitted_fields
			if not native_table_read("Item","uoms","UOM Conversion Detail") or "uom" not in get_permitted_fields("UOM Conversion Detail",parenttype="Item",permission_type="read"):
				raise Denied(403)
		if validation:
			if frappe.get_single_value("Stock Settings","allow_uom_with_conversion_rate_defined_in_item"):
				rows = frappe.get_all("UOM Conversion Detail",filters={"parent":filters["item_code"],"parenttype":"Item","parentfield":"uoms","uom":text},fields=["uom"],limit_page_length=2)
				if len(rows)>1:
					raise Denied(503)
				if not rows:
					return []
			rows = frappe.get_list("UOM",fields=["name"],filters={"name":text,"enabled":1},limit_page_length=1,ignore_permissions=False,ignore_user_permissions=False,reference_doctype=args["reference_doctype"])
			names = [row.get("name") for row in rows]
		else:
			rows = get_item_uom_query(kind,text,"name",0,limit,filters)
			names = [row[0] for row in rows]
	else:
		metadata = frappe.get_meta(kind)
		if metadata.get_field("enabled"):
			filters["enabled"] = 1
		if metadata.get_field("disabled"):
			filters["disabled"] = 0
		filters["name"] = text if validation else ["like","%"+text+"%"]
		options = {}
		if field == "income_account":
			filters["is_group"] = 0
			options["or_filters"] = [["Account","report_type","=","Profit and Loss"],["Account","account_type","in",["Income Account","Temporary"]]]
		rows = frappe.get_list(kind,fields=["name"],filters=filters,limit_page_length=limit,
			ignore_permissions=False,ignore_user_permissions=False,reference_doctype=args["reference_doctype"],**options)
		names = [row.get("name") for row in rows]
	if len(names)>limit or len(set(names)) != len(names) or (validation and any(name != text for name in names)):
		raise Denied(503)
	return names


def calculator_currency():
	"""A private request-local projection, only after the native DTO guard."""
	import frappe
	context = frappe.flags.get("company_editor")
	projection = frappe.local.request_cache.get("company_editor_currency")
	if not isinstance(context,Context) or context.operation != "calculate" or not isinstance(projection,tuple) or len(projection)!=2 or projection[0] is not context or not isinstance(projection[1],str) or not projection[1]:
		raise Denied(503)
	current(context)
	return projection[1]


def canonical_link_name(kind):
	import frappe
	context = frappe.flags.get("company_editor")
	projection = frappe.local.request_cache.get("company_editor_link_exact")
	if isinstance(context,Context) and context.operation == "link-read" and isinstance(projection,tuple) and len(projection)==3 and projection[0] is context and projection[1]==kind:
		current(context)
		return projection[2]
	return None



def tax_template_guard():
	import math

	import frappe
	from frappe.model import get_permitted_fields
	from frappe.utils.nestedset import get_ancestors_of
	context=frappe.flags.company_editor
	current(context)
	if context.operation != "tax-calculate" or context.value["scopes"] != ["erp:read","erp:write"] or not frappe.has_permission("Sales Invoice","read") or not frappe.has_permission("Sales Invoice","create") or "item_tax_template" not in get_permitted_fields("Sales Invoice Item",parenttype="Sales Invoice",permission_type="read"):
		raise Denied(403)
	args={key:value for key,value in frappe.form_dict.items() if key != "cmd"}
	if set(args) != {"ctx"} or not isinstance(args["ctx"],str) or len(args["ctx"].encode())>4096:
		raise Denied(400)
	ctx=transport.exact_json(args["ctx"])
	allowed={"item_code","company","base_net_rate","tax_category","item_tax_template","posting_date","bill_date","transaction_date"}
	if not isinstance(ctx,dict) or set(ctx)-allowed or any(not isinstance(ctx.get(key),str) or not 1<=len(ctx[key].encode())<=140 for key in ("item_code","company")):
		raise Denied(400)
	rate=ctx.get("base_net_rate")
	if type(rate) not in (int,float) or not math.isfinite(rate) or not 0<=rate<=1000000 or any(ctx.get(key) not in (None,"") for key in ("tax_category","item_tax_template")):
		raise Denied(400)
	for key in ("posting_date","bill_date","transaction_date"):
		bounded_calculator_scalars({"posting_date":ctx.get(key)},form=True)
	native_budget(context.deadline)
	frappe.get_doc("Company",ctx["company"]).check_permission("read")
	native_budget(context.deadline)
	item=frappe.get_doc("Item",ctx["item_code"])
	item.check_permission("read")
	if item.is_stock_item or item.has_serial_no or item.has_batch_no:
		raise Denied(403)
	native_budget(context.deadline)
	groups=[item.item_group,*get_ancestors_of("Item Group",item.item_group)]
	if len(groups)>32:
		raise Denied(403)
	# Small invoice ceiling across the Item and all inherited Item Group rows.
	# Each query keeps the original decreasing request deadline.
	tax_rows = 0
	for name in [None,*groups]:
		native_budget(context.deadline)
		document = item if name is None else frappe.get_doc("Item Group",name)
		document.check_permission("read")
		tax_rows += len(document.taxes)
		if tax_rows > 32:
			raise Denied(403)
		if not native_table_read(document.doctype,"taxes","Item Tax") or "item_tax_template" not in get_permitted_fields("Item Tax",parenttype=document.doctype,permission_type="read"):
			raise Denied(403)
		for row in document.taxes:
			native_budget(context.deadline)
			frappe.get_doc("Item Tax Template",row.item_tax_template).check_permission("read")
	return ctx


def tax_template_projection(result):
	import frappe
	from frappe.model import get_permitted_fields
	if result is None:
		return None
	if not isinstance(result,str) or not 1<=len(result.encode())<=140:
		raise Denied(503)
	if "name" not in get_permitted_fields("Item Tax Template",permission_type="read"):
		raise Denied(403)
	native_budget(frappe.flags.company_editor.deadline)
	frappe.get_doc("Item Tax Template",result).check_permission("read")
	return result


def read_tax_template():
	from erpnext.stock.get_item_details import get_item_tax_template

	import frappe
	context=frappe.flags.company_editor
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		ctx=tax_template_guard()
		result=tax_template_projection(frappe.call(get_item_tax_template,ctx=json.dumps(ctx)))
	finally:
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	tax_template_guard()
	return tax_template_projection(result)


def rule_free_admission():
	"""Hosted beta requires an adopted site with no stored Pricing Rule rows."""
	import frappe
	context = frappe.flags.get("company_editor")
	if context is None:
		return False
	if not isinstance(context,Context) or context.operation not in ("calculate","save"):
		raise Denied(403)
	current(context)
	if context.value["scopes"] != ["erp:read","erp:write"]:
		raise Denied(403)
	native_budget(context.deadline)
	# This existence-only ceiling projects no rule values and intentionally does
	# not let native cached count/condition evaluation decide editor admission.
	if frappe.get_all("Pricing Rule",fields=["name"],limit_page_length=1):
		raise Denied(403)
	return True


def rules_projection(result,document,ctx):
	if not isinstance(result,list) or len(result) != len(ctx["items"]):
		raise Denied(503)
	children=[]
	for value,row in zip(result,ctx["items"],strict=True):
		if not isinstance(value,dict) or any(value.get(key) for key in ("free_item_data","apply_rule_on_other_items","pricing_rules","has_pricing_rule","has_margin")):
			raise Denied(403)
		if any(value.get(key) != expected for key,expected in (("doctype","Sales Invoice Item"),("name",row["name"]),("child_docname",row["name"]),("parent",document.name),("parenttype","Sales Invoice"))):
			raise Denied(503)
		selected=calculator_projection(value,document,True)
		children.append({**selected,"doctype":"Sales Invoice Item","name":row["name"]})
	return children


def apply_rules():
	from erpnext.accounts.doctype.pricing_rule.pricing_rule import apply_pricing_rule

	import frappe
	context=frappe.flags.company_editor
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		document,ctx=price_list_guard("args")
		rule_free_admission()
		result=frappe.call(apply_pricing_rule,args=json.dumps(ctx),doc=frappe.form_dict["doc"])
		# Keep the bounded native addresses private for fresh final projection.
		rules_projection(result,document,ctx)
	finally:
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	document,ctx=price_list_guard("args")
	rule_free_admission()
	return rules_projection(result,document,ctx)


def native_table_read(kind, field, child):
	"""Native scalar field lists omit Table; use its real read permlevel."""
	import frappe
	metadata=frappe.get_meta(kind)
	df=metadata.get_field(field)
	return bool(df and df.fieldtype == "Table" and df.options == child and df.permlevel in metadata.get_permlevel_access("read"))


def invoice_initialization_guard(method):
	"""Closed stock new-Sales-Invoice callbacks; no generic settings or queries."""
	import frappe
	from frappe.model import get_permitted_fields
	context = frappe.flags.company_editor
	current(context)
	if context.operation != "invoice-initialize" or frappe.request.method != "POST" or context.value["scopes"] != ["erp:read", "erp:write"]:
		raise Denied(403)
	if not frappe.has_permission("Sales Invoice", "read") or not frappe.has_permission("Sales Invoice", "create"):
		raise Denied(403)
	fields = get_permitted_fields("Sales Invoice", permission_type="read")
	args = {key:value for key,value in frappe.form_dict.items() if key != "cmd"}
	company = None
	if method == ROUND_OFF:
		if set(args) != {"company", "account_list"}:
			raise Denied(400)
		accounts = args["account_list"]
		if isinstance(accounts, str):
			if len(accounts.encode()) > 16:
				raise Denied(400)
			accounts = transport.exact_json(accounts)
		if accounts != []:
			raise Denied(400)
		company = args["company"]
	elif method == DEFAULT_TAXES:
		if set(args) != {"master_doctype", "tax_template", "company"} or args["master_doctype"] != "Sales Taxes and Charges Template" or not isinstance(args["tax_template"],str) or len(args["tax_template"].encode())>140:
			raise Denied(400)
		df = frappe.get_meta("Sales Invoice").get_field("taxes_and_charges")
		if not df or df.fieldtype != "Link" or df.options != args["master_doctype"] or "taxes_and_charges" not in fields or not native_table_read("Sales Invoice", "taxes", "Sales Taxes and Charges"):
			raise Denied(403)
		company = args["company"]
	elif method == COMPANY_ADDRESS:
		if set(args) != {"name", "existing_address"} or args["existing_address"] != "":
			raise Denied(400)
		df = frappe.get_meta("Sales Invoice").get_field("company_address")
		if not df or df.fieldtype != "Link" or df.options != "Address" or "company_address" not in fields:
			raise Denied(403)
		company = args["name"]
	elif method in (PARTY_ACCOUNT, LOYALTY_PROGRAMS):
		expected = {"company", "party_type", "party"} if method == PARTY_ACCOUNT else {"customer"}
		if set(args) != expected or (method == PARTY_ACCOUNT and args["party_type"] != "Customer"):
			raise Denied(400)
		customer = args["party"] if method == PARTY_ACCOUNT else args["customer"]
		if not isinstance(customer, str) or not 1 <= len(customer.encode()) <= 140 or any(ord(c) < 32 for c in customer):
			raise Denied(400)
		for field, target in (("customer", "Customer"), ("debit_to", "Account")) if method == PARTY_ACCOUNT else (("customer", "Customer"),):
			df = frappe.get_meta("Sales Invoice").get_field(field)
			if not df or df.fieldtype != "Link" or df.options != target or field not in fields:
				raise Denied(403)
		frappe.get_doc("Customer", customer).check_permission("read")
		if method == PARTY_ACCOUNT:
			company = args["company"]
	elif method in (DIMENSIONS, ROUNDING_SETTING):
		if args:
			raise Denied(400)
		if not native_table_read("Sales Invoice", "items", "Sales Invoice Item") or not native_table_read("Sales Invoice", "taxes", "Sales Taxes and Charges"):
			raise Denied(403)
	else:
		raise Denied(404)
	if company is not None:
		if not isinstance(company, str) or not 1 <= len(company.encode()) <= 140 or any(ord(char) < 32 for char in company):
			raise Denied(400)
		if "company" not in fields:
			raise Denied(403)
		frappe.get_doc("Company", company).check_permission("read")
	native_budget(context.deadline)
	return args


def invoice_initialization_value(method, args):
	"""Fresh bounded empty-configuration admission precedes the stock functions.

	Configured dimensions, advanced sales-tax templates, company addresses and
	regional round-off overrides are outside this initial adopted-site subset.
	No row identifiers or dynamic hooks are projected by these checks.
	"""
	import frappe
	context = frappe.flags.company_editor
	current(context)
	native_budget(context.deadline)
	if method in (PARTY_ACCOUNT, LOYALTY_PROGRAMS):
		return invoice_party_value(method, args)
	if method == DIMENSIONS:
		if frappe.get_all("Accounting Dimension", fields=["name"], limit_page_length=1) or frappe.get_all("Accounting Dimension Detail", fields=["name"], limit_page_length=1):
			raise Denied(403)
		return [[], {}]
	if method == ROUNDING_SETTING:
		df = frappe.get_meta("Accounts Settings").get_field("round_row_wise_tax")
		value = frappe.db.get_single_value("Accounts Settings", "round_row_wise_tax")
		if not df or df.fieldtype != "Check" or type(value) is not int or value not in (0,1):
			raise Denied(503)
		return value
	if method == DEFAULT_TAXES:
		return default_native_taxes(args)
	if method == COMPANY_ADDRESS:
		# Exact company relationship, enabled native Address only, bounded to one
		# existence row. Do not call the stock unbounded Dynamic Link lookup.
		rows = frappe.db.sql('SELECT 1 FROM "tabAddress" a JOIN "tabDynamic Link" d ON d.parent=a.name WHERE d.link_doctype=\'Company\' AND d.link_name=%s AND coalesce(a.disabled,0)=0 LIMIT 1', (args["name"],))
		if rows:
			raise Denied(403)
		return None
	if method == ROUND_OFF:
		path = "erpnext.controllers.taxes_and_totals.get_regional_round_off_accounts"
		hooks = frappe.get_hooks("regional_overrides", {})
		if not isinstance(hooks, dict) or any(not isinstance(overrides,dict) or path in overrides for overrides in hooks.values()):
			raise Denied(403)
		from erpnext.controllers.taxes_and_totals import get_regional_round_off_accounts
		# Invoke the shipped empty base only; no region/cache-selected override.
		value = get_regional_round_off_accounts.__wrapped__(args["company"], [])
		if value is not None:
			raise Denied(503)
		return value
	raise Denied(404)



def invoice_party_value(method, args):
	"""Closed native Customer callbacks; no loyalty adoption or posted-ledger fallback."""
	import frappe
	from frappe.model import get_permitted_fields
	customer = frappe.get_doc("Customer", args["party"] if method == PARTY_ACCOUNT else args["customer"])
	customer.check_permission("read")
	if method == LOYALTY_PROGRAMS:
		if "loyalty_program" not in get_permitted_fields("Customer", permission_type="read") or customer.loyalty_program or frappe.get_all("Loyalty Program", fields=["name"], limit_page_length=1):
			raise Denied(403)
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_loyalty_programs
		value = get_loyalty_programs(customer.name)
		if value != []:
			raise Denied(503)
		return value
	company = frappe.get_doc("Company", args["company"])
	company.check_permission("read")
	if not {"default_currency", "default_receivable_account"} <= set(get_permitted_fields("Company", permission_type="read")) or "customer_group" not in get_permitted_fields("Customer", permission_type="read"):
		raise Denied(403)
	group = frappe.get_doc("Customer Group", customer.customer_group)
	group.check_permission("read")
	# Stock Party Account lookups are single-row. Refuse ambiguous matching
	# children and enforce each actual parent Table and child field permission.
	candidate = None
	for parent in (customer, group):
		if not native_table_read(parent.doctype, "accounts", "Party Account") or not {"company", "account"} <= set(get_permitted_fields("Party Account", parenttype=parent.doctype, permission_type="read")):
			raise Denied(403)
		rows = frappe.get_all("Party Account", filters={"parenttype":parent.doctype,"parent":parent.name,"company":company.name}, fields=["account", "parentfield"], limit_page_length=2)
		if len(rows) > 1 or any(row["parentfield"] != "accounts" for row in rows):
			raise Denied(403)
		if candidate is None and rows:
			candidate = rows[0]["account"]
	candidate = candidate or company.default_receivable_account
	if not isinstance(candidate, str) or not 1 <= len(candidate.encode()) <= 140:
		raise Denied(403)
	# No posted ledger selection or alternative party-type fallback in beta.
	if frappe.db.sql('SELECT 1 FROM "tabGL Entry" WHERE docstatus=1 AND company=%s AND party_type=\'Customer\' AND party=%s LIMIT 1',(company.name,customer.name)):
		raise Denied(403)
	account = frappe.get_doc("Account", candidate)
	account.check_permission("read")
	if not {"company", "is_group", "disabled", "account_type", "account_currency"} <= set(get_permitted_fields("Account", permission_type="read")) or account.company != company.name or account.is_group or account.disabled or account.account_type != "Receivable" or (account.account_currency or company.default_currency) != company.default_currency:
		raise Denied(403)
	native_budget(frappe.flags.company_editor.deadline)
	from erpnext.accounts.party import get_party_account
	value = get_party_account("Customer", customer.name, company.name)
	if value != candidate:
		raise Denied(403)
	return value


def default_native_taxes(args):
	"""Stock default template, bounded simple noninclusive net-total tax rows."""
	import math

	from erpnext.controllers.accounts_controller import get_default_taxes_and_charges

	import frappe
	from frappe.model import get_permitted_fields
	context = frappe.flags.company_editor
	native_budget(context.deadline)
	kind, child = "Sales Taxes and Charges Template", "Sales Taxes and Charges"
	candidates = frappe.get_all(kind, filters={"is_default":1,"company":args["company"]}, fields=["name"], limit_page_length=2)
	if len(candidates) > 1:
		raise Denied(403)
	if not candidates:
		if args["tax_template"]:
			raise Denied(403)
		return {"taxes_and_charges":None,"taxes":None}
	name = candidates[0].name
	if args["tax_template"] and args["tax_template"] != name:
		raise Denied(403)
	# This operation uses one REPEATABLE READ/READ ONLY snapshot. The bounded
	# child admission and native get_doc cannot observe different tax row sets.
	bounded = frappe.get_all(child, filters={"parent":name,"parenttype":kind,"parentfield":"taxes"}, fields=["name"], limit_page_length=33)
	if not 1 <= len(bounded) <= 32:
		raise Denied(403)
	native_budget(context.deadline)
	template = frappe.get_doc(kind,name)
	template.check_permission("read")
	parent_fields = get_permitted_fields(kind,permission_type="read")
	if not {"company","is_default","disabled","tax_category"}.issubset(parent_fields) or not native_table_read(kind,"taxes",child) or template.company != args["company"] or template.is_default != 1 or template.disabled != 0 or template.tax_category or len(template.taxes) != len(bounded):
		raise Denied(403)
	company = frappe.get_doc("Company",args["company"])
	company.check_permission("read")
	if "default_currency" not in get_permitted_fields("Company",permission_type="read"):
		raise Denied(403)
	fields = {"charge_type","account_head","cost_center","description","rate","included_in_print_rate","included_in_paid_amount","dont_recompute_tax","set_by_item_tax_template","is_tax_withholding_account"}
	if any(not fields.issubset(get_permitted_fields(child,parenttype=parent,permission_type="read")) for parent in (kind,"Sales Invoice")):
		raise Denied(403)
	value = frappe.call(get_default_taxes_and_charges,**args)
	if args["tax_template"]:
		if value is not None:
			raise Denied(503)
		rows=[row.as_dict() for row in template.taxes]
	else:
		rows=value.get("taxes") if isinstance(value,dict) else None
	if not args["tax_template"] and (not isinstance(value,dict) or set(value) != {"taxes_and_charges","taxes"} or value["taxes_and_charges"] != name or not isinstance(value["taxes"],list) or len(value["taxes"]) != len(bounded)):
		raise Denied(503)
	projected=[]
	for row in rows:
		native_budget(context.deadline)
		if not isinstance(row,dict) or row.get("charge_type") != "On Net Total" or any(row.get(key) not in (None,"",0) for key in ("row_id","project","included_in_print_rate","included_in_paid_amount","dont_recompute_tax","set_by_item_tax_template","is_tax_withholding_account")):
			raise Denied(403)
		rate=row.get("rate")
		if type(rate) not in (int,float) or not math.isfinite(rate) or not 0<=rate<=100 or not isinstance(row.get("description"),str) or len(row["description"].encode())>500 or any(char in row["description"] for char in "<>\x00"):
			raise Denied(403)
		for field,target in (("account_head","Account"),("cost_center","Cost Center")):
			df=frappe.get_meta(child).get_field(field)
			key=row.get(field)
			if not df or df.fieldtype!="Link" or df.options!=target or not isinstance(key,str) or not 1<=len(key.encode())<=140:
				raise Denied(403)
			linked=frappe.get_doc(target,key)
			linked.check_permission("read")
			needed={"company","is_group","disabled"}|({"account_currency","account_type"} if target=="Account" else set())
			if not needed.issubset(get_permitted_fields(target,permission_type="read")) or linked.company!=args["company"] or linked.is_group!=0 or linked.disabled!=0:
				raise Denied(403)
			if target=="Account" and ((linked.account_currency or company.default_currency)!=company.default_currency or linked.account_type!="Tax"):
				raise Denied(403)
		projected.append({"charge_type":"On Net Total","account_head":row["account_head"],"cost_center":row["cost_center"],"description":row["description"],"rate":rate,**{key:0 for key in fields if key not in ("charge_type","account_head","cost_center","description","rate")}})
	return None if args["tax_template"] else {"taxes_and_charges":name,"taxes":projected}


def read_invoice_initialization(method):
	import frappe
	context = frappe.flags.company_editor
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" if method == DEFAULT_TAXES else "SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		args = invoice_initialization_guard(method)
		value = invoice_initialization_value(method, args)
	finally:
		frappe.db.rollback()
	native_budget(context.deadline)
	recheck_before_commit(context)
	# Re-read the bounded configuration/Check at the final permission boundary.
	# A changed native configuration cannot publish the earlier empty projection.
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" if method == DEFAULT_TAXES else "SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		args = invoice_initialization_guard(method)
		fresh = invoice_initialization_value(method, args)
		if fresh != value:
			raise Denied(403)
		return fresh
	finally:
		frappe.db.rollback()
