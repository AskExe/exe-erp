# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE


import os
from urllib.parse import urljoin, urlparse

import frappe
import frappe.utils
from frappe import _
from frappe.apps import get_default_path
from frappe.auth import LoginManager
from frappe.core.doctype.navbar_settings.navbar_settings import get_app_logo
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, get_url
from frappe.utils.data import escape_html
from frappe.utils.html_utils import get_icon_html
from frappe.utils.jinja import guess_is_path
from frappe.utils.oauth import get_oauth2_authorize_url, get_oauth_keys, redirect_post_login
from frappe.utils.password import get_decrypted_password
from frappe.website.utils import get_home_page

no_cache = True


def get_context(context):
	redirect_to = frappe.local.request.args.get("redirect-to")
	redirect_to = sanitize_redirect(redirect_to)

	if frappe.session.user != "Guest":
		if not redirect_to:
			if frappe.session.data.user_type == "Website User":
				redirect_to = get_default_path() or get_home_page()
			else:
				redirect_to = get_default_path() or "/desk"

		if redirect_to != "login":
			frappe.local.flags.redirect_location = redirect_to
			raise frappe.Redirect

	context.no_header = True
	context.for_test = "login.html"
	context["title"] = "Login"
	context["hide_login"] = True  # dont show login link on login page again.
	context["provider_logins"] = []
	context["disable_signup"] = cint(frappe.get_website_settings("disable_signup"))
	context["show_footer_on_login"] = cint(frappe.get_website_settings("show_footer_on_login"))
	context["disable_user_pass_login"] = cint(frappe.get_system_settings("disable_user_pass_login"))
	context["gotrue_login_enabled"] = bool(frappe.conf.get("gotrue_url"))
	context["exe_auth_url"] = get_exe_auth_url()
	context["logo"] = get_app_logo()
	context["app_name"] = (
		frappe.get_website_settings("app_name") or frappe.get_system_settings("app_name") or _("Exe ERP")
	)

	signup_form_template = frappe.get_hooks("signup_form_template")
	if signup_form_template and len(signup_form_template):
		path = signup_form_template[-1]
		if not guess_is_path(path):
			path = frappe.get_attr(signup_form_template[-1])()
	else:
		path = "frappe/templates/signup.html"

	if path:
		context["signup_form_template"] = frappe.get_template(path).render()

	providers = frappe.get_all(
		"Social Login Key",
		filters={"enable_social_login": 1},
		fields=["name", "client_id", "base_url", "provider_name", "icon"],
		order_by="name",
	)

	for provider in providers:
		client_secret = get_decrypted_password(
			"Social Login Key", provider.name, "client_secret", raise_exception=False
		)
		if not client_secret:
			continue

		icon = None
		if provider.icon:
			if provider.provider_name == "Custom":
				icon = get_icon_html(provider.icon, small=True)
			else:
				icon = f"<img src={escape_html(provider.icon)!r} alt={escape_html(provider.provider_name)!r}>"

		if provider.client_id and provider.base_url and get_oauth_keys(provider.name):
			context.provider_logins.append(
				{
					"name": provider.name,
					"provider_name": provider.provider_name,
					"auth_url": get_oauth2_authorize_url(provider.name, redirect_to),
					"icon": icon,
				}
			)
			context["social_login"] = True

	if cint(frappe.db.get_value("LDAP Settings", "LDAP Settings", "enabled")):
		from frappe.integrations.doctype.ldap_settings.ldap_settings import LDAPSettings

		context["ldap_settings"] = LDAPSettings.get_ldap_client_settings()

	login_label = [_("Email")]

	if frappe.utils.cint(frappe.get_system_settings("allow_login_using_mobile_number")):
		login_label.append(_("Mobile"))

	if frappe.utils.cint(frappe.get_system_settings("allow_login_using_user_name")):
		login_label.append(_("Username"))

	context["login_label"] = f" {_('or')} ".join(login_label)

	context["login_with_email_link"] = frappe.get_system_settings("login_with_email_link")
	context["login_with_frappe_cloud_url"] = None  # Frappe Cloud removed — Exe ERP fork

	return context


def get_exe_auth_url() -> str:
	"""Resolve the Exe SSO base URL for THIS customer's deployment.

	Never hardcode auth.askexe.com — a customer ERP at erp.acme.com must send
	users to acme.com's own auth tenant, or SSO tokens fail validation against
	the customer's GoTrue.

	Resolution order (first match wins):
	  1. Explicit override — site_config "exe_auth_url" or env EXE_AUTH_URL
	     (full URL, e.g. https://auth.acme.com). Lets operators point anywhere.
	  2. Explicit auth domain — site_config "auth_domain" or env AUTH_DOMAIN
	     (bare host, e.g. auth.acme.com → https://auth.acme.com).
	  3. Derived from the request host — replace the leading label with "auth"
	     (erp.acme.com → auth.acme.com), preserving scheme. Single-label hosts
	     (e.g. "localhost") are prefixed (auth.localhost).
	  4. Last resort — derive from SITE_NAME env (erp.acme.com → auth.acme.com).
	     Falls back to https://auth.localhost when nothing is configured.
	"""
	# 1. Full URL override
	explicit = frappe.conf.get("exe_auth_url") or os.environ.get("EXE_AUTH_URL")
	if explicit:
		return explicit.rstrip("/")

	# 2. Auth domain (bare host) → https scheme
	auth_domain = frappe.conf.get("auth_domain") or os.environ.get("AUTH_DOMAIN")
	if auth_domain:
		auth_domain = auth_domain.strip()
		if "://" in auth_domain:
			return auth_domain.rstrip("/")
		return f"https://{auth_domain.rstrip('/')}"

	# 3. Derive from the request host (erp.acme.com → auth.acme.com)
	try:
		parsed = urlparse(frappe.local.request.url)
		host = parsed.hostname
		scheme = parsed.scheme or "https"
		if host:
			labels = host.split(".")
			if len(labels) >= 2:
				auth_host = ".".join(["auth"] + labels[1:])
			else:
				auth_host = f"auth.{host}"
			# Preserve a non-default port if present (e.g. dev on :8080)
			port = f":{parsed.port}" if parsed.port else ""
			return f"{scheme}://{auth_host}{port}"
	except Exception:
		# Request context may be unavailable in some render paths; fall through.
		pass

	# 4. Last-resort default — derive from SITE_NAME env (customer domains)
	site_name = os.environ.get("SITE_NAME", "")
	if site_name:
		labels = site_name.split(".")
		if len(labels) >= 2:
			return f"https://auth.{'.'.join(labels[1:])}"
		return f"https://auth.{site_name}"
	return "https://auth.localhost"


@frappe.whitelist(allow_guest=True)
def login_via_token(login_token: str):
	sid = frappe.cache.get_value(f"login_token:{login_token}", expires=True)
	if not sid:
		frappe.respond_as_web_page(_("Invalid Request"), _("Invalid Login Token"), http_status_code=417)
		return

	frappe.local.form_dict.sid = sid
	frappe.local.login_manager = LoginManager()

	redirect_post_login(
		desk_user=frappe.db.get_value("User", frappe.session.user, "user_type") == "System User"
	)


def get_login_with_email_link_ratelimit() -> int:
	return frappe.get_system_settings("rate_limit_email_link_login") or 5


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=get_login_with_email_link_ratelimit, seconds=60 * 60)
def send_login_link(email: str):
	if not frappe.get_system_settings("login_with_email_link"):
		return

	try:
		expiry = frappe.get_system_settings("login_with_email_link_expiry") or 10
		link = _generate_temporary_login_link(email, expiry)

		app_name = (
			frappe.get_website_settings("app_name") or frappe.get_system_settings("app_name") or _("Exe ERP")
		)

		subject = _("Login To {0}").format(app_name)

		frappe.sendmail(
			subject=subject,
			recipients=email,
			template="login_with_email_link",
			args={"link": link, "minutes": expiry, "app_name": app_name},
			now=True,
		)
	except frappe.DoesNotExistError:
		frappe.clear_messages()
	except frappe.OutgoingEmailError:
		frappe.clear_messages()
		frappe.log_error(title="Login link email could not be sent", message=frappe.get_traceback())
	except Exception:
		frappe.clear_messages()
		frappe.log_error(title="Login link generation failed unexpectedly", message=frappe.get_traceback())


def _generate_temporary_login_link(email: str, expiry: int):
	assert isinstance(email, str)

	if not frappe.db.exists("User", email):
		frappe.throw(_("User with email address {0} does not exist").format(email), frappe.DoesNotExistError)
	key = frappe.generate_hash()
	frappe.cache.set_value(f"one_time_login_key:{key}", email, expires_in_sec=expiry * 60)

	return get_url(f"/api/method/frappe.www.login.login_via_key?key={key}", allow_header_override=False)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=get_login_with_email_link_ratelimit, seconds=60 * 60)
def login_via_key(key: str):
	cache_key = f"one_time_login_key:{key}"
	email = frappe.cache.get_value(cache_key)

	if email:
		frappe.cache.delete_value(cache_key)
		frappe.local.login_manager.login_as(email)

		redirect_post_login(
			desk_user=frappe.db.get_value("User", frappe.session.user, "user_type") == "System User"
		)
	else:
		frappe.respond_as_web_page(
			_("Not Permitted"),
			_("The link you trying to login is invalid or expired."),
			http_status_code=403,
			indicator_color="red",
		)


def sanitize_redirect(redirect: str | None) -> str | None:
	"""Only allow redirect on same domain.

	Allowed redirects:
	- Same host e.g. https://frappe.localhost/path
	- Just path e.g. /app gets converted to https://frappe.localhost/app
	"""
	if not redirect:
		return redirect

	parsed_redirect = urlparse(redirect)

	parsed_request_host = urlparse(frappe.local.request.url)
	output_parsed_url = parsed_redirect._replace(
		netloc=parsed_request_host.netloc, scheme=parsed_request_host.scheme
	)
	if parsed_redirect.netloc:
		if parsed_request_host.netloc != parsed_redirect.netloc:
			output_parsed_url = output_parsed_url._replace(path="/desk")
		else:
			output_parsed_url = output_parsed_url._replace(path=parsed_redirect.path)

	return output_parsed_url.geturl()
