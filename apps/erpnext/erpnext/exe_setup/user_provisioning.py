"""
Exe ERP -- Bulk User Provisioning API

Endpoint:
    POST /api/method/erpnext.exe_setup.user_provisioning.bulk_create_users

Accepts a JSON array of user definitions and creates both a Frappe User and
a GoTrue user for each entry.  Designed for onboarding batches of 10-500
employees at a time.

Request body:
    {
        "users": [
            {
                "email": "jane@company.com",
                "first_name": "Jane",
                "last_name": "Doe",
                "department": "Finance & Accounting",
                "role_profile": "Finance Manager"
            },
            ...
        ]
    }

Response:
    {
        "created": 48,
        "failed": 2,
        "errors": [
            {"email": "bad@company.com", "error": "Invalid email format"},
            ...
        ]
    }

Configuration (site_config.json):
    {
        "gotrue_url": "http://gotrue:9999",
        "gotrue_admin_token": "your-service-role-key"
    }
"""

import json
from typing import Any

import frappe
import requests

logger = frappe.logger("exe_setup.user_provisioning")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def bulk_create_users(users: str | list | None = None) -> dict:
    """Create Frappe + GoTrue users in bulk.

    Args:
        users: JSON string or list of user dicts.  Each dict must contain
               ``email`` and ``first_name``.  Optional keys: ``last_name``,
               ``department``, ``role_profile``.

    Returns:
        Dict with ``created``, ``failed`` counts and ``errors`` list.
    """
    _assert_admin()

    if users is None:
        frappe.throw("'users' parameter is required", frappe.ValidationError)

    if isinstance(users, str):
        try:
            users = json.loads(users)
        except json.JSONDecodeError:
            frappe.throw("'users' must be valid JSON", frappe.ValidationError)

    if not isinstance(users, list):
        frappe.throw("'users' must be an array of user objects", frappe.ValidationError)

    created = 0
    failed = 0
    errors: list[dict[str, str]] = []

    for entry in users:
        try:
            _validate_entry(entry)
            _create_single_user(entry)
            created += 1
        except Exception as exc:
            failed += 1
            email = entry.get("email", "<missing>") if isinstance(entry, dict) else "<invalid>"
            error_msg = str(exc)
            errors.append({"email": email, "error": error_msg})
            logger.warning("Failed to provision user %s: %s", email, error_msg)

    frappe.db.commit()

    result = {"created": created, "failed": failed, "errors": errors}
    logger.info(
        "Bulk provisioning complete: %d created, %d failed out of %d total",
        created, failed, len(users),
    )
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_admin() -> None:
    """Only System Managers or Administrators can provision users."""
    if "System Manager" not in frappe.get_roles():
        frappe.throw("Only System Managers can provision users", frappe.PermissionError)


def _validate_entry(entry: Any) -> None:
    """Raise if entry is missing required fields or has bad data."""
    if not isinstance(entry, dict):
        frappe.throw("Each user entry must be an object")

    email = entry.get("email", "").strip()
    if not email or "@" not in email:
        frappe.throw(f"Invalid or missing email: {email!r}")

    if not entry.get("first_name", "").strip():
        frappe.throw(f"first_name is required for {email}")

    # Validate referenced docs exist
    department = entry.get("department", "").strip()
    if department and not frappe.db.exists("Department", {"department_name": department}):
        # Also try the "Dept - ABBR" format
        if not frappe.db.exists("Department", department):
            frappe.throw(f"Department not found: {department!r}")

    role_profile = entry.get("role_profile", "").strip()
    if role_profile and not frappe.db.exists("Role Profile", role_profile):
        frappe.throw(f"Role Profile not found: {role_profile!r}")


def _create_single_user(entry: dict) -> None:
    """Create a Frappe User and optionally a GoTrue user."""
    email = entry["email"].strip()
    first_name = entry["first_name"].strip()
    last_name = entry.get("last_name", "").strip()
    department = entry.get("department", "").strip()
    role_profile = entry.get("role_profile", "").strip()

    # --- Frappe User ------------------------------------------------------
    if frappe.db.exists("User", email):
        logger.info("Frappe user already exists, updating profile: %s", email)
        user_doc = frappe.get_doc("User", email)
        _update_user_fields(user_doc, first_name, last_name, department, role_profile)
        user_doc.save(ignore_permissions=True)
    else:
        user_doc = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "enabled": 1,
            "user_type": "System User",
            "send_welcome_email": 0,
        })

        if role_profile:
            user_doc.role_profile_name = role_profile

        if department:
            user_doc.department = department

        user_doc.flags.ignore_mandatory = True
        user_doc.flags.ignore_permissions = True
        user_doc.insert()
        logger.info("Created Frappe user: %s", email)

    # --- GoTrue User ------------------------------------------------------
    _create_gotrue_user(email, first_name, last_name)


def _update_user_fields(
    user_doc: Any,
    first_name: str,
    last_name: str,
    department: str,
    role_profile: str,
) -> None:
    """Update mutable fields on an existing user doc."""
    if first_name:
        user_doc.first_name = first_name
    if last_name:
        user_doc.last_name = last_name
    if department:
        user_doc.department = department
    if role_profile:
        user_doc.role_profile_name = role_profile


def _create_gotrue_user(email: str, first_name: str, last_name: str) -> None:
    """Create a GoTrue user via the admin API.

    Requires ``gotrue_url`` and ``gotrue_admin_token`` in site_config.json.
    If GoTrue is not configured, this step is silently skipped -- the user
    can still log in via standard Frappe auth.
    """
    gotrue_url = frappe.conf.get("gotrue_url")
    admin_token = frappe.conf.get("gotrue_admin_token")

    if not gotrue_url or not admin_token:
        logger.debug("GoTrue not configured, skipping GoTrue user creation for %s", email)
        return

    # Check if GoTrue user already exists by listing with email filter
    try:
        check_resp = requests.get(
            f"{gotrue_url.rstrip('/')}/admin/users",
            params={"filter": f"email eq {email}"},
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if check_resp.status_code == 200:
            data = check_resp.json()
            existing = data.get("users", []) if isinstance(data, dict) else data
            if existing:
                logger.debug("GoTrue user already exists: %s", email)
                return
    except requests.RequestException:
        # If we can't check, try creating anyway -- the API will reject dupes
        pass

    # Create GoTrue user via admin endpoint
    try:
        resp = requests.post(
            f"{gotrue_url.rstrip('/')}/admin/users",
            json={
                "email": email,
                "email_confirm": True,
                "user_metadata": {
                    "first_name": first_name,
                    "last_name": last_name,
                },
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        if resp.status_code in (200, 201):
            logger.info("Created GoTrue user: %s", email)
        elif resp.status_code == 422:
            # User already exists (duplicate)
            logger.debug("GoTrue user already exists (422): %s", email)
        else:
            error_detail = ""
            try:
                error_detail = resp.json().get("msg", resp.text[:200])
            except Exception:
                error_detail = resp.text[:200]
            logger.warning(
                "GoTrue user creation returned %d for %s: %s",
                resp.status_code, email, error_detail,
            )

    except requests.RequestException as exc:
        logger.warning("GoTrue unreachable when creating %s: %s", email, exc)
        # Non-fatal: Frappe user is already created, GoTrue can be synced later
