"""Default-off staged company transport. Native UI, jobs and issuance are not admitted.

Configuration is immutable per process. Only a private registered native-client
credential lives here; no parent identity, issuer, signing or commerce secret.
"""

import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlsplit

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
CLIENT = re.compile(r"[a-z][a-z0-9_-]{2,63}\Z")
SESSION = re.compile(r"exs_[A-Za-z0-9_-]{43}\Z")
COOKIE = "__Host-exe_erp_session"
FLOW_COOKIE = "__Host-exe_erp_flow"
CODE = re.compile(r"exc_[A-Za-z0-9_-]{43}\Z")
FLOW_VALUE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}\Z")
FIELDS = frozenset(
	(
		"version",
		"subject_id",
		"company_id",
		"product",
		"resource_kind",
		"binding_id",
		"native_id",
		"generation_id",
		"authz_epoch",
		"audience",
		"scopes",
		"current_role",
		"technical_status",
		"subscription_entitled",
	)
)
READ_FIELDS = {
	"Customer": ("name", "customer_name", "customer_type", "customer_group", "territory"),
	"Item": ("name", "item_name", "item_group", "stock_uom", "disabled"),
}
FORBIDDEN_CONFIG = (
	"GOTRUE_URL",
	"GOTRUE_EXTERNAL_URL",
	"GOTRUE_ADMIN_TOKEN",
	"EXE_ERP_ADMIN_TOKEN",
	"EXE_ADMIN_TOKEN",
	"EXE_BRIDGE_DATABASE_URL",
	"JWT_SECRET",
	"ADMIN_PASSWORD",
	"ERP_ADMIN_PASSWORD",
	"EXE_LICENSE_KEY",
	"STRIPE_SECRET_KEY",
	"COMPANY_SESSION_ENCRYPTION_KEY_FILE",
	"COMPANY_SESSION_ISSUER_SECRET_FILE",
	"FRAPPE_SENTRY_DSN",
	"ENABLE_SENTRY_DB_MONITORING",
	"SENTRY_TRACING_SAMPLE_RATE",
)


class Denied(Exception):
	def __init__(self, status=401):
		self.status = status


def mode_enabled():
	value = os.environ.get("ERP_COMPANY_MODE", "false")
	if value not in ("true", "false"):
		raise ValueError("ERP_COMPANY_MODE must be true or false")
	return value == "true"


def refuse_background():
	if mode_enabled():
		raise RuntimeError("Company mode background/realtime operations are not admitted")


def hostname(value):
	if not isinstance(value, str) or len(value) > 252 or len(value.split(".")) < 2:
		raise ValueError("Canonical DNS hostname required")
	if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")):
		raise ValueError("Canonical DNS hostname required")
	return value


def exact_json(data):
	def pairs(values):
		out = {}
		for key, value in values:
			if key in out:
				raise ValueError("Duplicate JSON field")
			out[key] = value
		return out

	return json.loads(data, object_pairs_hook=pairs)


def private_file(path, maximum):
	if not path or not os.path.isabs(path) or os.path.realpath(path) != path:
		raise ValueError("Canonical private file required")
	fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
	try:
		s = os.fstat(fd)
		if (
			not stat.S_ISREG(s.st_mode)
			or s.st_nlink != 1
			or stat.S_IMODE(s.st_mode) != 0o600
			or s.st_uid != os.geteuid()
			or not 0 < s.st_size <= maximum
		):
			raise ValueError("Owned bounded 0600 private file required")
		with os.fdopen(fd, "rb", closefd=False) as stream:
			data = stream.read(maximum + 1)
		if len(data) > maximum:
			raise ValueError("Private file changed")
		return data
	finally:
		os.close(fd)


@dataclass(frozen=True)
class Config:
	company_id: str
	site: str
	binding_id: str
	generation_id: str
	audience: str
	client_id: str
	origin: str
	authority: str
	secret: str = field(repr=False)
	subjects: object
	binding_digest: str
	browser_enabled: bool = False
	auth_origin: str = ""
	flow_secret: str = field(default="", repr=False)


def load_config():
	if not mode_enabled():
		return None
	if os.geteuid() == 0:
		raise ValueError("Company mode must run as a nonroot native user")
	if any(os.environ.get(key) for key in FORBIDDEN_CONFIG) or any(
		value
		and key.startswith(
			(
				"GOTRUE_",
				"EXE_GOTRUE_",
				"COMPANY_SESSION_",
				"COMPANY_AUTHORITY_",
				"SESSION_BROKER_",
				"STRIPE_",
				"AUTH_",
				"EXE_OS_",
			)
		)
		for key, value in os.environ.items()
	):
		raise ValueError("Company adapter must not receive legacy/central authority credentials")

	def get(key):
		return os.environ.get("ERP_COMPANY_" + key, "")

	allowed = {
		"MODE",
		"ID",
		"SITE",
		"BINDING_ID",
		"GENERATION_ID",
		"AUDIENCE",
		"CLIENT_ID",
		"ORIGIN",
		"AUTHORITY_URL",
		"CLIENT_SECRET_FILE",
		"BINDINGS_FILE",
		"BINDINGS_SHA256",
		"BROWSER_ENABLED",
		"AUTH_ORIGIN",
		"FLOW_SECRET_FILE",
	}
	if any(key.startswith("ERP_COMPANY_") and key[12:] not in allowed for key in os.environ):
		raise ValueError("Unknown company adapter configuration")
	company, binding, generation = (get(key) for key in ("ID", "BINDING_ID", "GENERATION_ID"))
	if any(not UUID.fullmatch(value) for value in (company, binding, generation)):
		raise ValueError("Canonical company registration required")
	site = hostname(get("SITE"))
	if not site.startswith("erp.") or os.environ.get("SITE_NAME") != site:
		raise ValueError("Registered erp-site must equal SITE_NAME")
	hostname(site[4:])
	if not re.fullmatch(r"[a-z]{2,63}", site.split(".")[-1]):
		raise ValueError("Registered erp-site must match core DNS grammar")
	origin = get("ORIGIN")
	if origin != "https://" + site:
		raise ValueError("Exact registered HTTPS ERP origin required")
	audience, client = get("AUDIENCE"), get("CLIENT_ID")
	if not CLIENT.fullmatch(audience) or not CLIENT.fullmatch(client):
		raise ValueError("Registered client/audience required")
	authority = get("AUTHORITY_URL")
	u = urlsplit(authority)
	if (
		u.scheme not in ("http", "https")
		or not u.hostname
		or u.username
		or u.password
		or u.path
		or u.query
		or u.fragment
		or authority != u.scheme + "://" + u.netloc
		or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*(?::[1-9][0-9]{0,4})?", u.netloc)
	):
		raise ValueError("Private native authority must be a bare canonical origin")
	if u.port is not None and not 1 <= u.port <= 65535:
		raise ValueError("Invalid authority port")
	resolved_endpoint(u.hostname)
	secret = private_file(get("CLIENT_SECRET_FILE"), 128).decode("ascii")
	if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", secret):
		raise ValueError("Invalid native-client credential")
	raw = private_file(get("BINDINGS_FILE"), 65536)
	digest = hashlib.sha256(raw).hexdigest()
	if digest != get("BINDINGS_SHA256"):
		raise ValueError("Immutable operator binding digest mismatch")
	doc = exact_json(raw)
	expected = {"version", "company_id", "site", "binding_id", "generation_id", "audience", "subjects"}
	if (
		not isinstance(doc, dict)
		or set(doc) != expected
		or type(doc["version"]) is not int
		or doc["version"] != 1
		or any(
			doc[key] != value
			for key, value in (
				("company_id", company),
				("site", site),
				("binding_id", binding),
				("generation_id", generation),
				("audience", audience),
			)
		)
	):
		raise ValueError("Binding file must match fixed registration")
	if not isinstance(doc["subjects"], list) or not 1 <= len(doc["subjects"]) <= 100:
		raise ValueError("Bounded operator subjects required")
	subjects, users = {}, set()
	for row in doc["subjects"]:
		if not isinstance(row, dict) or set(row) != {"subject_id", "native_user"}:
			raise ValueError("Invalid operator binding")
		subject, user = row["subject_id"], row["native_user"]
		if (
			not isinstance(subject, str)
			or not UUID.fullmatch(subject)
			or subject in subjects
			or not isinstance(user, str)
			or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._+-]{0,139}", user)
			or user in ("Administrator", "Guest")
			or user in users
		):
			raise ValueError("Unique canonical existing native users required")
		subjects[subject] = user
		users.add(user)
	browser = get("BROWSER_ENABLED") or "false"
	if browser not in ("true", "false"):
		raise ValueError("Invalid company browser configuration")
	auth_origin, flow_secret = "", ""
	if browser == "true":
		auth_origin = get("AUTH_ORIGIN")
		if not auth_origin.startswith("https://auth.") or auth_origin == origin or len(auth_origin) > 261 or hostname(auth_origin[8:]) != auth_origin[8:] or not re.fullmatch(r"[a-z]{2,63}", auth_origin.split(".")[-1]):
			raise ValueError("Exact central Auth company origin required")
		flow_secret = private_file(get("FLOW_SECRET_FILE"), 128).decode("ascii")
		if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", flow_secret) or flow_secret == secret:
			raise ValueError("Distinct native flow credential required")
	elif get("AUTH_ORIGIN") or get("FLOW_SECRET_FILE"):
		raise ValueError("Partial disabled company browser configuration")
	return Config(
		company,
		site,
		binding,
		generation,
		audience,
		client,
		origin,
		authority,
		secret,
		MappingProxyType(subjects),
		digest,
		browser == "true",
		auth_origin,
		flow_secret,
	)


def envelope(value, config):
	if (
		not isinstance(value, dict)
		or set(value) != FIELDS
		or type(value["version"]) is not int
		or value["version"] != 1
	):
		raise Denied(503)
	fixed = {
		"company_id": config.company_id,
		"product": "erp",
		"resource_kind": "erp-site",
		"binding_id": config.binding_id,
		"native_id": config.site,
		"generation_id": config.generation_id,
		"audience": config.audience,
		"scopes": ["erp:read"],
		"technical_status": "accepted",
		"subscription_entitled": True,
	}
	if any(type(value[key]) is not type(want) or value[key] != want for key, want in fixed.items()):
		raise Denied(503)
	if (
		not isinstance(value["subject_id"], str)
		or not UUID.fullmatch(value["subject_id"])
		or value["subject_id"] not in config.subjects
		or type(value["current_role"]) is not str
		or value["current_role"] not in ("owner", "member")
		or not isinstance(value["authz_epoch"], str)
		or not re.fullmatch(r"[1-9][0-9]{0,18}", value["authz_epoch"])
	):
		raise Denied()
	return config.subjects[value["subject_id"]]


def resolved_endpoint(host):
	try:
		address = ipaddress.IPv4Address(host)
	except (ValueError, TypeError):
		raise ValueError("Operator-fixed resolved private IPv4 required") from None
	if str(address) != host or not any(
		address in ipaddress.IPv4Network(network)
		# RFC 6598 shared-address range: octets 100/64, prefix length 10.
		for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", ((100 << 24) | (64 << 16), 10))
	):
		raise ValueError("Operator-fixed resolved private IPv4 required")
	return host


class PrivateConnection(http.client.HTTPConnection):
	"""One call, numeric AF_INET only; no resolver or reusable socket."""

	def __init__(self, host, port, context=None):
		super().__init__(resolved_endpoint(host), port, timeout=2)
		self.context = context
		self.lock = threading.Lock()
		self.expired = False
		self.owned_socket = None

	def teardown(self):
		with self.lock:
			self.expired = True
			if self.owned_socket:
				try:
					self.owned_socket.shutdown(socket.SHUT_RDWR)
				except OSError:
					pass
				self.owned_socket.close()

	def connect(self):
		with self.lock:
			if self.expired:
				raise TimeoutError()
			self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			self.owned_socket = self.sock
			self.sock.settimeout(2)
		self.sock.connect((self.host, self.port))
		if self.context:
			with self.lock:
				if self.expired:
					raise TimeoutError()
				self.sock = self.context.wrap_socket(
					self.sock, server_hostname=self.host, do_handshake_on_connect=False
				)
				self.owned_socket = self.sock
			self.sock.do_handshake()


def private_call(config, operation, token):
	return private_json(config, operation, {"session_token": token})


def private_json(config, operation, payload):
	if not isinstance(payload, dict):
		raise Denied(400)
	if operation in ("introspect", "revoke"):
		if set(payload) != {"session_token"} or not isinstance(payload["session_token"], str) or not SESSION.fullmatch(payload["session_token"]):
			raise Denied()
	elif operation == "token" and config.browser_enabled:
		if set(payload) != {"grant_type", "code", "redirect_uri", "code_verifier", "state_hash"} or payload["grant_type"] != "authorization_code" or not isinstance(payload["code"], str) or not CODE.fullmatch(payload["code"]) or payload["redirect_uri"] != config.origin + "/company-session/callback" or not isinstance(payload["code_verifier"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", payload["code_verifier"]) or not isinstance(payload["state_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", payload["state_hash"]):
			raise Denied(400)
	else:
		raise Denied(400)
	u = urlsplit(config.authority)
	connection = PrivateConnection(
		u.hostname,
		u.port or (443 if u.scheme == "https" else 80),
		ssl.create_default_context() if u.scheme == "https" else None,
	)
	deadline = time.monotonic() + 3
	timer = threading.Timer(3, connection.teardown)
	timer.start()
	response = None
	try:
		body = json.dumps(payload, separators=(",", ":"))
		basic = base64.b64encode((config.client_id + ":" + config.secret).encode("ascii")).decode("ascii")
		connection.request(
			"POST",
			"/internal/session-broker/" + operation,
			body,
			{"Content-Type": "application/json", "Authorization": "Basic " + basic, "Connection": "close"},
		)
		transport = connection.sock
		response = connection.getresponse()
		if response.status != 200:
			raise Denied(401 if response.status in (401, 403, 404) else 503)
		if response.getheader("Content-Type") != "application/json" or response.getheader("Content-Encoding"):
			raise Denied(503)
		data = bytearray()
		while True:
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				raise Denied(503)
			if transport:
				transport.settimeout(min(2, remaining))
			chunk = response.read1(4096)
			if not chunk:
				break
			data.extend(chunk)
			if len(data) > 12288:
				raise Denied(503)
			if response.isclosed():
				break
		if time.monotonic() >= deadline:
			raise Denied(503)
		result = exact_json(bytes(data))
		if time.monotonic() >= deadline:
			raise Denied(503)
		return result
	except Denied:
		raise
	except Exception:
		raise Denied(503) from None
	finally:
		timer.cancel()
		timer.join()
		connection.teardown()
		if response is not None:
			response.close()
		connection.close()


def fixed_request_headers(request, config, browser_navigation=False):
	# RAW_URI/REQUEST_URI are supplied by Gunicorn. No reconstruction may turn
	# an encoded/ambiguous path into an accepted finite route.
	raw = request.environ.get("RAW_URI") or request.environ.get("REQUEST_URI")
	try:
		query = request.query_string.decode("ascii")
	except (AttributeError, UnicodeError):
		raise Denied(400) from None
	if (
		not isinstance(raw, str)
		or len(raw) > 1024
		or raw != request.path + ("?" + query if request.query_string else "")
		or any(c in raw for c in ("%", "\\", "#"))
	):
		raise Denied(400)
	if len(list(request.headers)) > 32 or sum(len(k) + len(v) for k, v in request.headers) > 16384:
		raise Denied(400)
	if request.host != config.site or request.headers.get("Origin") not in (None, config.origin):
		raise Denied(403)
	if any(
		name.lower()
		in (
			"authorization",
			"proxy-authorization",
			"x-frappe-site-name",
			"x-frappe-user",
			"frappe-authorization-source",
			"x-frappe-socket-secret",
			"x-exe-user",
			"x-exe-company",
			"x-exe-role",
			"x-user",
			"x-company",
			"x-org",
			"x-role",
			"upgrade",
			"sec-websocket-protocol",
		)
		or (
			name.lower().startswith(("x-forwarded-", "forwarded", "x-auth-"))
			and not (name.lower() == "x-forwarded-proto" and request.headers.get(name) == "https")
		)
		for name, _ in request.headers
	):
		raise Denied()
	if request.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
		if not browser_navigation or request.headers.get("Sec-Fetch-Site") not in ("same-site", "cross-site") or request.headers.get("Sec-Fetch-Mode") not in (None, "navigate") or request.headers.get("Sec-Fetch-Dest") not in (None, "document"):
			raise Denied(403)
	if request.headers.get("Content-Length") not in (None, "0") or request.headers.get("Transfer-Encoding"):
		raise Denied(400)
	return query


def request_policy(request, config):
	# Redirect-chain Fetch Metadata retains the cross-host navigation site.
	# Only the browser completion destination accepts it, with explicit hints.
	status_navigation = (
		config.browser_enabled
		and request.method == "GET"
		and request.path == "/company-session/status"
		and not request.query_string
		and request.headers.get("Sec-Fetch-Mode") == "navigate"
		and request.headers.get("Sec-Fetch-Dest") == "document"
	)
	query = fixed_request_headers(request, config, browser_navigation=status_navigation)
	parts = request.headers.get("Cookie", "").split(";")
	cookies, seen = [], set()
	for part in parts:
		if not part.strip():
			continue
		name, sep, token = part.strip().partition("=")
		if not sep or name in seen:
			raise Denied()
		seen.add(name)
		if name == COOKIE and SESSION.fullmatch(token):
			cookies.append(token)
		elif name == FLOW_COOKIE and config.browser_enabled and len(token) <= 2048 and FLOW_VALUE.fullmatch(token):
			# Flow is bounded protocol state only; it never supplies request identity.
			continue
		else:
			raise Denied()
	if len(cookies) != 1:
		raise Denied()
	path = request.path
	if path == "/company-session/logout":
		if request.method != "POST" or request.query_string or request.headers.get("Origin") != config.origin:
			raise Denied(403)
		return ("logout", None, None, cookies[0])
	if request.method != "GET":
		raise Denied(404)
	if path == "/company-session/status" and not request.query_string:
		return ("status", None, None, cookies[0])
	match = re.fullmatch(r"/api/resource/(Customer|Item)(?:/([A-Za-z0-9][A-Za-z0-9_-]{0,139}))?", path)
	if not match:
		raise Denied(404)
	doctype, name = match.groups()
	if name:
		if query:
			raise Denied(400)
		params = {}
	else:
		params = {}
		for pair in query.split("&"):
			key, sep, value = pair.partition("=")
			if (
				not sep
				or key in params
				or key not in ("limit_page_length", "limit_start")
				or not re.fullmatch(r"0|[1-9][0-9]{0,4}", value)
			):
				raise Denied(400)
			params[key] = int(value)
		if (
			set(params) != {"limit_page_length", "limit_start"}
			or not 1 <= params["limit_page_length"] <= 100
			or params["limit_start"] > 10000
		):
			raise Denied(400)
	return ("read", (doctype, name), params, cookies[0])


def native_user(config, value, fresh_roles=False):
	import frappe

	user = envelope(value, config)
	# Identity-only current lookup. Business records never use this bypass.
	identity = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not identity or identity.enabled != 1 or identity.user_type != "System User":
		raise Denied(403)
	frappe.set_user(user)
	if fresh_roles:
		# Callback-only bounded current Has Role lookup, never Redis membership cache.
		roles = frappe.db.get_values("Has Role", {"parenttype": "User", "parent": user}, "role", pluck=True, cache=False, limit=101)
		if not isinstance(roles, list) or len(roles) > 100 or any(type(role) is not str or not 1 <= len(role) <= 140 for role in roles) or len(set(roles)) != len(roles):
			raise Denied(403)
	else:
		roles = frappe.get_roles(user)
	if (
		not isinstance(roles, list)
		or any(not isinstance(role, str) or not 1 <= len(role) <= 140 for role in roles)
		or any(role in roles for role in ("Administrator", "System Manager", "Script Manager"))
	):
		raise Denied(403)
	explicit = [role for role in roles if role not in ("All", "Guest", "Desk User")]
	if not explicit or any(frappe.db.get_value("Role", role, "disabled") != 0 for role in explicit):
		raise Denied(403)
	return user


def read_records(resource, params):
	import frappe

	doctype, name = resource
	fields = list(READ_FIELDS[doctype])
	if name:
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("read")
		doc.apply_fieldlevel_read_permissions()
		# get_list preserves row/User Permission + field/mask policy even for
		# direct reads. Intersection with the native Document check, no SQL.
		rows = frappe.get_list(doctype, fields=fields, filters={"name": name}, limit_page_length=1)
		if not rows:
			raise Denied(404)
		return rows[0]
	return frappe.get_list(doctype, fields=fields, **params)


def request_current(end_wall, end_mono):
	if time.time() >= end_wall or time.monotonic() >= end_mono:
		raise Denied(503)


def native_read_current(request, config, sites_path, value, operation, resource, params, end_wall, end_mono):
	import frappe
	from frappe.utils import CallbackManager

	request_current(end_wall, end_mono)
	connected = False
	try:
		frappe.init(config.site, sites_path=sites_path, force=True)
		frappe.local.request = request
		frappe.local.request.after_response = CallbackManager()
		frappe.local.flags.company_session = True
		# Never construct HTTPRequest/LoginManager, resume sid, run legacy auth
		# hooks or initialize CookieManager; no native session is persisted.
		if any(
			frappe.conf.get(key)
			for key in (
				"gotrue_url",
				"gotrue_admin_token",
				"gotrue_external_url",
				"exe_admin_token",
				"exe_erp_admin_token",
				"auth_token",
				"jwt_secret",
				"exe_bridge_database_url",
			)
		):
			raise Denied(503)
		frappe.connect(set_admin_as_user=False)
		connected = True
		request_current(end_wall, end_mono)
		frappe.db.begin(read_only=True)
		frappe.local.flags.read_only = True
		if frappe.conf.maintenance_mode:
			raise Denied(503)
		native_user(config, value)
		if operation == "status":
			data = {"authenticated": True, "company_id": config.company_id}
		else:
			data = read_records(resource, params)

		request_current(end_wall, end_mono)
		return data
	finally:
		if connected:
			try:
				frappe.db.rollback()
			finally:
				# Close this connection before force-init resets all request-local ACL/meta state.
				frappe.db.close()
		request_current(end_wall, end_mono)


def application(request, config, sites_path):
	from werkzeug.wrappers import Response

	import frappe
	from frappe.utils import CallbackManager

	# Required even for browser start and pre-native denial by after_response_wrapper.
	frappe.local.request = request
	frappe.local.request.after_response = CallbackManager()
	if request.path in ("/company-session/start", "/company-session/callback") and config.browser_enabled:
		return browser_application(request, config, sites_path)
	status, data, clear = 200, None, False
	end_wall, end_mono = time.time() + 9, time.monotonic() + 9
	try:
		operation, resource, params, token = request_policy(request, config)
		if operation == "logout":
			result = private_call(config, "revoke", token)
			if not isinstance(result, dict) or set(result) != {"revoked"} or result["revoked"] is not True:
				raise Denied(503)
			data, clear = {"revoked": True}, True
		else:
			value = private_call(config, "introspect", token)
			envelope(value, config)
			data = native_read_current(request, config, sites_path, value, operation, resource, params, end_wall, end_mono)
			current = private_call(config, "introspect", token)
			request_current(end_wall, end_mono)
			envelope(current, config)
			if current != value:
				raise Denied(403)
			if operation != "status":
				# Discard the first data. Re-run the same native row/field permission path
				# in a fresh context, then recheck authority after its rollback.
				data = native_read_current(request, config, sites_path, current, operation, resource, params, end_wall, end_mono)
				final = private_call(config, "introspect", token)
				request_current(end_wall, end_mono)
				envelope(final, config)
				if final != value:
					raise Denied(403)

	except Denied as error:
		status = error.status
	except (frappe.PermissionError, frappe.DoesNotExistError):
		status = 404
	except Exception:
		status = 503
	try:
		request_current(end_wall, end_mono)
		payload = json.dumps({"data": data} if status == 200 else {"error": "unavailable"})
		request_current(end_wall, end_mono)
	except Exception:
		status, clear, payload = 503, False, '{"error":"unavailable"}'
	response = Response(payload, status=status, content_type="application/json")
	response.headers.update(
		{
			"Cache-Control": "no-store",
			"Referrer-Policy": "no-referrer",
			"X-Content-Type-Options": "nosniff",
			"Content-Security-Policy": "frame-ancestors 'self'",
		}
	)
	if clear:
		response.set_cookie(COOKIE, "", max_age=0, path="/", secure=True, httponly=True, samesite="Lax")
		if config.browser_enabled:
			response.set_cookie(FLOW_COOKIE, "", max_age=0, path="/", secure=True, httponly=True, samesite="Lax")
	return response


def flow_binding(config):
	return {
		"version": 1, "company_id": config.company_id, "site": config.site,
		"binding_id": config.binding_id, "generation_id": config.generation_id,
		"audience": config.audience, "client_id": config.client_id,
		"callback": config.origin + "/company-session/callback", "auth_origin": config.auth_origin,
	}


def base64url(data):
	return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def browser_request(request, config):
	# Fixed routes bypass only the existing opaque-session prerequisite, not ingress checks.
	if request.method != "GET" or request.path not in ("/company-session/start", "/company-session/callback"):
		raise Denied(404)
	query = fixed_request_headers(request, config, browser_navigation=True)
	names = [name.lower() for name, _ in request.headers]
	if len(set(names)) != len(names):
		raise Denied(400)
	cookies = {}
	for part in request.headers.get("Cookie", "").split(";"):
		if not part.strip():
			continue
		name, sep, value = part.strip().partition("=")
		if not sep or name not in (COOKIE, FLOW_COOKIE) or name in cookies or len(value) > 2048:
			raise Denied(401)
		if name == COOKIE and not SESSION.fullmatch(value):
			raise Denied(401)
		if name == FLOW_COOKIE and not FLOW_VALUE.fullmatch(value):
			raise Denied(401)
		cookies[name] = value
	if request.path == "/company-session/start":
		if query:
			raise Denied(400)
		return {}, cookies
	args = {}
	for part in query.split("&"):
		key, sep, value = part.partition("=")
		if not sep or key not in ("code", "state") or key in args:
			raise Denied(400)
		args[key] = value
	if set(args) != {"code", "state"} or not CODE.fullmatch(args["code"]) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", args["state"]):
		raise Denied(400)
	return args, cookies


def seal_flow(config, state, verifier):
	value = {**flow_binding(config), "state": state, "verifier": verifier, "exp": int(time.time()) + 600}
	payload = base64url(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"))
	signature = base64url(hmac.digest(config.flow_secret.encode("ascii"), b"exe-erp-company-flow-v1\0" + payload.encode("ascii"), "sha256"))
	return payload + "." + signature


def open_flow(config, value, state):
	if not isinstance(value, str) or len(value) > 2048 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}", value):
		raise Denied(401)
	payload, signature = value.split(".")
	expected = base64url(hmac.digest(config.flow_secret.encode("ascii"), b"exe-erp-company-flow-v1\0" + payload.encode("ascii"), "sha256"))
	if not hmac.compare_digest(signature, expected):
		raise Denied(401)
	try:
		decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
		if len(decoded) > 1536 or base64url(decoded) != payload:
			raise Denied(401)
		flow = exact_json(decoded)
	except Exception:
		raise Denied(401) from None
	binding = flow_binding(config)
	if not isinstance(flow, dict) or set(flow) != set(binding) | {"state", "verifier", "exp"} or any(flow[k] != v or type(flow[k]) is not type(v) for k, v in binding.items()) or flow["state"] != state or not isinstance(flow["verifier"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", flow["verifier"]) or type(flow["exp"]) is not int or not time.time() < flow["exp"] <= time.time() + 600:
		raise Denied(401)
	return flow["verifier"]


def callback_native_user(config, value, request, sites_path):
	import frappe
	# Every central network call has completed before native initialization/transaction.
	after_response = request.after_response
	try:
		frappe.init(config.site, sites_path=sites_path, force=True)
	finally:
		# init resets local context; preserve cleanup even on initialization denial.
		frappe.local.request = request
		request.after_response = after_response
	frappe.local.flags.company_session = True
	if any(frappe.conf.get(key) for key in ("gotrue_url", "gotrue_admin_token", "gotrue_external_url", "exe_admin_token", "exe_erp_admin_token", "auth_token", "jwt_secret", "exe_bridge_database_url")):
		raise Denied(503)
	frappe.connect(set_admin_as_user=False)
	try:
		frappe.db.begin(read_only=True)
		frappe.local.flags.read_only = True
		if frappe.conf.maintenance_mode:
			raise Denied(503)
		native_user(config, value, fresh_roles=True)
	finally:
		frappe.db.rollback()


def browser_application(request, config, sites_path):
	from werkzeug.wrappers import Response
	status, location, flow, session, expires = 200, None, None, None, 0
	mono_deadline, wall_deadline = time.monotonic() + 9, time.time() + 9
	def check():
		if time.monotonic() >= mono_deadline or time.time() >= wall_deadline:
			raise Denied(503)
	try:
		check()
		args, cookies = browser_request(request, config)
		if request.path == "/company-session/start":
			state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
			flow = seal_flow(config, state, verifier)
			challenge = base64url(hashlib.sha256(verifier.encode("ascii")).digest())
			location = config.auth_origin + "/company-session/authorize?client_id=" + config.client_id + "&state=" + state + "&code_challenge=" + challenge + "&code_challenge_method=S256"
		else:
			verifier = open_flow(config, cookies.get(FLOW_COOKIE), args["state"])
			result = private_json(config, "token", {"grant_type": "authorization_code", "code": args["code"], "redirect_uri": config.origin + "/company-session/callback", "code_verifier": verifier, "state_hash": hashlib.sha256(args["state"].encode("ascii")).hexdigest()})
			if not isinstance(result, dict) or set(result) != {"session_token", "token_type", "expires_in"} or not isinstance(result["session_token"], str) or not SESSION.fullmatch(result["session_token"]) or result["token_type"] != "Bearer" or type(result["expires_in"]) is not int or not 1 <= result["expires_in"] <= 900:
				raise Denied(503)
			check()
			value = private_call(config, "introspect", result["session_token"])
			check()
			envelope(value, config)
			callback_native_user(config, value, request, sites_path)
			check()
			session, expires, flow = result["session_token"], result["expires_in"], ""
			# Staged producer completion, not a replacement ERP UI or native Desk admission.
			location = config.origin + "/company-session/status"
		check()
	except Denied as error:
		status = error.status
	except Exception:
		status = 503
	response = Response("" if location and status == 200 else '{"error":"unavailable"}', status=303 if location and status == 200 else status, content_type="application/json")
	response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "frame-ancestors 'self'"})
	if status == 200:
		response.headers["Location"] = location
		if session:
			response.set_cookie(COOKIE, session, max_age=expires, path="/", secure=True, httponly=True, samesite="Lax")
		if flow is not None:
			response.set_cookie(FLOW_COOKIE, flow, max_age=600 if flow else 0, path="/", secure=True, httponly=True, samesite="Lax")
	return response
