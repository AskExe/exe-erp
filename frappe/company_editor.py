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
	"frappe.desk.search.search_link", "frappe.client.get_value", "frappe.client.get_list",
))
DESK_METHODS = frozenset((
	"frappe.desk.desktop.get_workspace_sidebar_items", "frappe.desk.desktop.get_desktop_page",
))
SAVE_METHOD = "frappe.desk.form.save.savedocs"
PARTY_CALCULATOR = "erpnext.accounts.party.get_party_details"
ITEM_CALCULATOR = "erpnext.stock.get_item_details.get_item_details"
CALCULATORS = frozenset((PARTY_CALCULATOR, ITEM_CALCULATOR))
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
	if request.method != "GET":
		if request.mimetype == "application/json":
			body = transport.exact_json(request.get_data())
			if not isinstance(body, dict) or any(key in SELECTORS for key in body):
				raise Denied(400)
		elif any(len(request.form.getlist(key)) != 1 or key in SELECTORS for key in request.form):
			raise Denied(400)
	if any(len(request.args.getlist(key)) != 1 or key in SELECTORS for key in request.args):
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
		elif method in CALCULATORS and request.method == "POST":
			operation = "calculate"
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
	if any(key in SELECTORS for key in frappe.form_dict):
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
	if context.operation in ("save", "calculate") and context.value["scopes"] != ["erp:read", "erp:write"]:
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
	if method not in READ_METHODS | DESK_METHODS | {SAVE_METHOD} | CALCULATORS:
		raise Denied(404)
	if method == SAVE_METHOD:
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
	current(context)
	# A previously authorized request may finish under bounded native transaction
	# semantics. This recheck narrows races; it cannot make two databases atomic.


def boot_ceiling(boot):
	"""Native permission lists remain authoritative, with the Core draft ceiling."""
	import frappe
	context = frappe.flags.get("company_editor")
	if context is None:
		return
	writable = sorted(WRITE_TYPES) if context.value["scopes"] == ["erp:read", "erp:write"] else []
	for key in ("can_create", "can_write", "in_create"):
		boot["user"][key] = [kind for kind in boot["user"].get(key, []) if kind in writable]
	for key in ("can_delete", "can_submit", "can_cancel", "can_import", "can_export", "can_print", "can_email"):
		boot["user"][key] = []
	boot["company_editor"] = {"version": 2, "writable_doctypes": writable, "auth_origin": context.config.auth_origin}


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


def calculator_guard(method):
	import frappe
	context = frappe.flags.company_editor
	if context.operation != "calculate" or context.value["scopes"] != ["erp:read", "erp:write"]:
		raise Denied(403)
	args = {key: value for key, value in frappe.form_dict.items() if key != "cmd"}
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
	# Authentication has read current native SID. Start a fresh readonly statement
	# before checking native documents; no invoice or pricing writes may commit.
	frappe.db.rollback()
	frappe.db.sql("SET TRANSACTION READ ONLY")
	native_budget(context.deadline)
	try:
		document, child = calculator_guard(method)
		from erpnext.accounts.party import get_party_details
		from erpnext.stock.get_item_details import get_item_details
		function = get_party_details if method == PARTY_CALCULATOR else get_item_details
		result = frappe.call(function, **{key: value for key, value in frappe.form_dict.items() if key != "cmd"})
		if not isinstance(result, dict):
			raise Denied(503)
		result = calculator_projection(result, document, child)
	finally:
		frappe.db.rollback()
	# Fresh Core and native User/roles check before publishing computed values.
	native_budget(context.deadline)
	recheck_before_commit(context)
	document, child = calculator_guard(method)
	return calculator_projection(result, document, child)
