"""
Exe ERP — GoTrue SSO Authentication

Same pattern as exe-crm (GoTrue auth controller) and exe-wiki (GoTrue-first auth).
Two whitelisted guest endpoints:
  1. gotrue_login  — validate email+password against GoTrue, auto-provision Frappe User
  2. admin_token   — headless admin access via shared secret (for exe-os orchestration)
"""

import frappe
import requests


@frappe.whitelist(allow_guest=True)
def gotrue_login(email: str, password: str, workspace_name: str | None = None) -> dict:
    """
    Authenticate via GoTrue SSO.

    1. POST to GoTrue /token?grant_type=password
    2. If accepted → find or create Frappe User
    3. Login the user and return session ID
    """
    if not email or not password:
        frappe.throw("Email and password are required", frappe.AuthenticationError)

    gotrue_url = frappe.conf.get("gotrue_url")
    if not gotrue_url:
        frappe.throw(
            "GoTrue URL not configured. Set 'gotrue_url' in site_config.json",
            frappe.ValidationError,
        )

    # ── Validate against GoTrue ──────────────────────────────
    try:
        resp = requests.post(
            f"{gotrue_url}/token?grant_type=password",
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as e:
        frappe.throw(f"GoTrue connection failed: {e}", frappe.AuthenticationError)

    if resp.status_code != 200:
        error_msg = "Invalid credentials"
        try:
            error_data = resp.json()
            error_msg = error_data.get("error_description", error_data.get("msg", error_msg))
        except Exception:
            pass
        frappe.throw(error_msg, frappe.AuthenticationError)

    # ── Find or create Frappe User ───────────────────────────
    user = _ensure_user(email)

    # ── Login ────────────────────────────────────────────────
    frappe.local.login_manager.login_as(email)

    return {
        "success": True,
        "user": email,
        "sid": frappe.session.sid,
        "full_name": user.full_name,
    }


@frappe.whitelist(allow_guest=True)
def admin_token(token: str) -> dict:
    """
    Headless admin login via shared secret.

    Used by exe-os orchestration (configurator, health checks, API calls)
    to access the system without GoTrue credentials.

    Token must match site_config key 'exe_admin_token'.
    """
    if not token:
        frappe.throw("Token is required", frappe.AuthenticationError)

    expected = frappe.conf.get("exe_admin_token")
    if not expected:
        frappe.throw(
            "Admin token not configured. Set 'exe_admin_token' in site_config.json",
            frappe.ValidationError,
        )

    # Constant-time comparison to prevent timing attacks
    import hmac

    if not hmac.compare_digest(str(token), str(expected)):
        frappe.throw("Invalid admin token", frappe.AuthenticationError)

    frappe.local.login_manager.login_as("Administrator")

    return {
        "success": True,
        "user": "Administrator",
        "sid": frappe.session.sid,
        "isAdminToken": True,
    }


def _ensure_user(email: str):
    """Find existing user or create a new one. First user gets System Manager role."""
    if frappe.db.exists("User", email):
        return frappe.get_doc("User", email)

    # Count existing non-guest users to determine if this is the first real user
    user_count = frappe.db.count(
        "User",
        filters={
            "user_type": "System User",
            "enabled": 1,
            "name": ("not in", ["Administrator", "Guest"]),
        },
    )

    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": email.split("@")[0],
            "enabled": 1,
            "user_type": "System User" if user_count == 0 else "Website User",
        }
    )
    user.flags.ignore_permissions = True
    user.flags.no_welcome_mail = True
    user.insert()

    # First user (non-admin) gets System Manager role
    if user_count == 0:
        user.add_roles("System Manager")
        frappe.logger().info(f"Exe Auth: First user {email} granted System Manager role")

    return user
