"""
Exe ERP — GoTrue SSO Authentication

Provides GoTrue-first login for Exe ERP, matching the pattern used by
exe-crm (gotrue-auth.controller.ts) and exe-wiki (system.js GoTrue block).

Two endpoints:
  - gotrue_login: Validates credentials against GoTrue, auto-provisions Frappe User
  - admin_token: Direct admin access via shared secret (for exe-os daemon/MCP)

Configuration (site_config.json):
  {
    "gotrue_url": "http://gotrue:9999",
    "exe_admin_token": "your-secret-token",
    "gotrue_admin_token": "your-service-role-key"
  }
"""

import hmac
import os
import frappe
import requests
from frappe.website.utils import get_home_page
from frappe.rate_limiter import rate_limit


@frappe.whitelist(allow_guest=True)
@rate_limit(key="gotrue_login", limit=5, seconds=900)
def gotrue_login(
	email: str | None = None,
	password: str | None = None,
	workspace_name: str | None = None,
):
	"""Authenticate via GoTrue, auto-provision Frappe User on first login."""
	if not email or not password:
		frappe.throw("Email and password are required", frappe.AuthenticationError)

	gotrue_url = frappe.conf.get("gotrue_url")
	if not gotrue_url:
		frappe.throw(
			"GoTrue URL not configured. Set gotrue_url in site_config.json",
			frappe.ValidationError,
		)

	# Validate credentials against GoTrue
	try:
		resp = requests.post(
			f"{gotrue_url.rstrip('/')}/token?grant_type=password",
			json={"email": email, "password": password},
			headers={"Content-Type": "application/json"},
			timeout=10,
		)
	except requests.RequestException as e:
		frappe.log_error(
			title="GoTrue Auth Error",
			message=f"GoTrue service unavailable: {e}",
		)
		frappe.throw("Authentication service temporarily unavailable", frappe.AuthenticationError)

	if resp.status_code != 200:
		# Log full error server-side for debugging, never expose to client
		try:
			error_data = resp.json()
			# Redact sensitive fields before logging
			safe_data = {k: v for k, v in error_data.items() if k not in ("access_token", "refresh_token", "password")}
			frappe.log_error(
				title="GoTrue Auth Failure",
				message=f"Status {resp.status_code}: {safe_data}",
			)
		except Exception:
			frappe.log_error(
				title="GoTrue Auth Failure",
				message=f"Status {resp.status_code}: {resp.text[:500]}",
			)
		frappe.throw("Invalid email or password", frappe.AuthenticationError)

	# GoTrue accepted — find or create Frappe User
	if not frappe.db.exists("User", email):
		first_name = email.split("@")[0]
		# Default to System User for internal domains, Website User for external
		allowed_domains = frappe.conf.get("allowed_email_domains", [])
		email_domain = email.split("@")[1] if "@" in email else ""
		if allowed_domains and email_domain not in allowed_domains:
			frappe.throw(
				f"Email domain '{email_domain}' is not allowed. Contact your administrator.",
				frappe.AuthenticationError,
			)
		default_user_type = frappe.conf.get("default_gotrue_user_type", "System User")
		user_doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"enabled": 1,
				"user_type": default_user_type,
			}
		)
		user_doc.flags.ignore_permissions = True
		user_doc.flags.no_welcome_mail = True
		user_doc.insert()

		# First user gets System Manager role ONLY in bootstrap mode.
		# In production (default), first user gets a standard role.
		bootstrap_mode = os.environ.get("ERP_BOOTSTRAP_MODE", "false").lower() == "true"
		user_count = frappe.db.count("User", {"user_type": "System User", "enabled": 1})
		if user_count <= 1 and bootstrap_mode:
			frappe.logger().warning(
				"BOOTSTRAP MODE ACTIVE: Auto-promoting first user %s to System Manager. "
				"Disable ERP_BOOTSTRAP_MODE after initial setup.",
				email,
			)
			user_doc.add_roles("System Manager")
		elif user_count <= 1:
			frappe.logger().info(
				"First user %s created with standard role. "
				"Set ERP_BOOTSTRAP_MODE=true to auto-promote to System Manager.",
				email,
			)

	# Login the user
	frappe.local.login_manager.login_as(email)

	return {
		"success": True,
		"message": "Logged In",
		"user": email,
		"sid": frappe.session.sid,
		"home_page": get_home_page() or "/desk",
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(key="gotrue_callback", limit=10, seconds=900)
def gotrue_login_callback():
	"""Handle redirect from auth.askexe.com with JWT token.

	auth.askexe.com redirects to:
	  /api/method/erpnext.exe_auth.api.gotrue_login_callback?access_token=JWT&refresh_token=REFRESH

	We validate the JWT against GoTrue's /user endpoint, auto-provision
	a Frappe User if needed, log them in, and redirect to /desk.
	"""
	access_token = frappe.form_dict.get("access_token")
	if not access_token:
		frappe.throw("No access token provided", frappe.AuthenticationError)

	gotrue_url = frappe.conf.get("gotrue_url")
	if not gotrue_url:
		frappe.throw(
			"GoTrue URL not configured. Set gotrue_url in site_config.json",
			frappe.ValidationError,
		)

	# Validate the JWT against GoTrue
	try:
		resp = requests.get(
			f"{gotrue_url.rstrip('/')}/user",
			headers={"Authorization": f"Bearer {access_token}"},
			timeout=10,
		)
	except requests.RequestException as e:
		frappe.log_error(
			title="GoTrue SSO Callback Error",
			message=f"GoTrue service unavailable: {e}",
		)
		frappe.throw("Authentication service temporarily unavailable", frappe.AuthenticationError)

	if resp.status_code != 200:
		frappe.log_error(
			title="GoTrue SSO Callback Failure",
			message=f"Status {resp.status_code}: {resp.text[:500]}",
		)
		frappe.throw("Invalid or expired SSO token", frappe.AuthenticationError)

	user_data = resp.json()
	email = user_data.get("email")
	if not email:
		frappe.throw("No email in SSO token", frappe.AuthenticationError)

	# Auto-provision Frappe User if needed (same logic as gotrue_login)
	if not frappe.db.exists("User", email):
		first_name = email.split("@")[0]
		allowed_domains = frappe.conf.get("allowed_email_domains", [])
		email_domain = email.split("@")[1] if "@" in email else ""
		if allowed_domains and email_domain not in allowed_domains:
			frappe.throw(
				f"Email domain '{email_domain}' is not allowed. Contact your administrator.",
				frappe.AuthenticationError,
			)
		default_user_type = frappe.conf.get("default_gotrue_user_type", "System User")
		user_doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"enabled": 1,
				"user_type": default_user_type,
			}
		)
		user_doc.flags.ignore_permissions = True
		user_doc.flags.no_welcome_mail = True
		user_doc.insert()

		bootstrap_mode = os.environ.get("ERP_BOOTSTRAP_MODE", "false").lower() == "true"
		user_count = frappe.db.count("User", {"user_type": "System User", "enabled": 1})
		if user_count <= 1 and bootstrap_mode:
			user_doc.add_roles("System Manager")

	# Login and redirect to desk
	frappe.local.login_manager.login_as(email)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = get_home_page() or "/desk"


@frappe.whitelist(allow_guest=True)
@rate_limit(key="admin_token", limit=5, seconds=900)
def admin_token(token: str | None = None):
	"""Authenticate via shared admin token (for exe-os daemon/MCP access)."""
	if not token:
		frappe.throw("Token is required", frappe.AuthenticationError)

	expected_token = frappe.conf.get("exe_admin_token")
	if not expected_token:
		frappe.throw(
			"Admin token not configured. Set exe_admin_token in site_config.json",
			frappe.ValidationError,
		)

	if not hmac.compare_digest(token.encode(), expected_token.encode()):
		frappe.throw("Invalid admin token", frappe.AuthenticationError)

	# Login as Administrator
	frappe.local.login_manager.login_as("Administrator")

	return {
		"success": True,
		"message": "Logged In",
		"user": "Administrator",
		"sid": frappe.session.sid,
		"home_page": get_home_page() or "/desk",
		"isAdminToken": True,
	}
