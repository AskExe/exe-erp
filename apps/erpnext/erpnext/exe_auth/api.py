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
    "exe_admin_token": "your-secret-token"
  }
"""

import frappe
import requests


@frappe.whitelist(allow_guest=True)
def gotrue_login(email=None, password=None, workspace_name=None):
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
		frappe.throw(f"GoTrue service unavailable: {e}", frappe.AuthenticationError)

	if resp.status_code != 200:
		error_msg = "Invalid credentials"
		try:
			error_data = resp.json()
			error_msg = error_data.get("error_description") or error_data.get("msg") or error_msg
		except Exception:
			pass
		frappe.throw(error_msg, frappe.AuthenticationError)

	# GoTrue accepted — find or create Frappe User
	if not frappe.db.exists("User", email):
		first_name = email.split("@")[0]
		user_doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"enabled": 1,
				"user_type": "Website User",
			}
		)
		user_doc.flags.ignore_permissions = True
		user_doc.flags.no_welcome_mail = True
		user_doc.insert()

		# First user gets System Manager role (admin)
		user_count = frappe.db.count("User", {"user_type": "System User", "enabled": 1})
		if user_count <= 1:
			user_doc.add_roles("System Manager")

	# Login the user
	frappe.local.login_manager.login_as(email)

	return {
		"success": True,
		"user": email,
		"sid": frappe.session.sid,
	}


@frappe.whitelist(allow_guest=True)
def admin_token(token=None):
	"""Authenticate via shared admin token (for exe-os daemon/MCP access)."""
	if not token:
		frappe.throw("Token is required", frappe.AuthenticationError)

	expected_token = frappe.conf.get("exe_admin_token")
	if not expected_token:
		frappe.throw(
			"Admin token not configured. Set exe_admin_token in site_config.json",
			frappe.ValidationError,
		)

	if token != expected_token:
		frappe.throw("Invalid admin token", frappe.AuthenticationError)

	# Login as Administrator
	frappe.local.login_manager.login_as("Administrator")

	return {
		"success": True,
		"user": "Administrator",
		"sid": frappe.session.sid,
		"isAdminToken": True,
	}
