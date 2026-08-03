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
from frappe.rate_limiter import rate_limit
from frappe.website.utils import get_home_page

from erpnext.exe_auth import exe_perms as _exe_perms


def _assert_provisioning_allowed(email: str) -> None:
	"""Fail-closed tenant/domain gate for SSO auto-provisioning.

	bug 7b4bbe12: ERP SSO must NOT auto-provision arbitrary valid GoTrue users.
	A valid GoTrue credential proves identity, not tenant membership — without a
	binding to this ERP tenant any GoTrue user (including cross-tenant ones) would
	otherwise get a Frappe account here.

	Provisioning is therefore REFUSED unless one of the following is configured in
	site_config.json:
	  - allowed_email_domains: list of domains authorized for this tenant (preferred)
	  - gotrue_allow_all_domains: true to explicitly opt into open provisioning
	    (single-tenant deployments where every GoTrue user is trusted)

	When allowed_email_domains is set, the email's domain must be on the list.
	"""
	allowed_domains = frappe.conf.get("allowed_email_domains") or []
	allow_all = frappe.conf.get("gotrue_allow_all_domains", False)
	email_domain = email.split("@")[1] if "@" in email else ""

	if not allowed_domains and not allow_all:
		frappe.throw(
			"User auto-provisioning is disabled: no tenant domain allowlist is "
			"configured. Set allowed_email_domains in site_config.json (or "
			"gotrue_allow_all_domains=true to explicitly allow all domains).",
			frappe.AuthenticationError,
		)

	if allowed_domains and email_domain not in allowed_domains:
		frappe.throw(
			f"Email domain '{email_domain}' is not allowed. Contact your administrator.",
			frappe.AuthenticationError,
		)


# ---------------------------------------------------------------------------
# P3 — Unified permissions: map the GoTrue exe_perms claim to Frappe roles.
# See UNIFIED-PERMISSIONS-DESIGN.md §3 and exe_auth/exe_perms.py.
# ---------------------------------------------------------------------------


def _configured_org_id() -> str | None:
	"""This single-tenant ERP's org id: site_config `exe_org_id`, env fallback."""
	return frappe.conf.get("exe_org_id") or os.environ.get("EXE_ORG_ID") or None


def _role_config():
	"""(admin_role, write_roles) overridable via site_config for white-label."""
	return (
		frappe.conf.get("exe_erp_admin_role"),
		frappe.conf.get("exe_erp_write_roles"),
	)


class GoTrueUserFetchError(Exception):
	"""GoTrue /user could not be fetched or returned a non-200 / bad body.

	Signals "identity may be proven but authoritative perms are UNKNOWN". On a
	managed-enforcement tenant the caller MUST fail closed on this rather than
	logging in with unknown/stale roles (see gotrue_login)."""


def _fetch_gotrue_user(gotrue_url: str, access_token: str) -> dict:
	"""Fetch authoritative user (incl. app_metadata) from GoTrue /user.

	GoTrue /user returns app_metadata in its JSON — the authoritative source for
	the exe_perms claim on the password-grant path (the callback path hits /user
	itself). Returns the user dict on success.

	RAISES GoTrueUserFetchError on ANY failure (no token, network error,
	non-200, or unparseable body). It deliberately does NOT swallow errors into
	{}: a fetch failure means perms are UNKNOWN, which is NOT the same as "no
	claim" (unmanaged). The caller decides fail-open vs fail-closed based on
	whether the tenant runs managed enforcement (exe_org_id configured).
	"""
	if not access_token:
		raise GoTrueUserFetchError("no access token from password grant")
	try:
		resp = requests.get(
			f"{gotrue_url.rstrip('/')}/user",
			headers={"Authorization": f"Bearer {access_token}"},
			timeout=10,
		)
	except requests.RequestException as e:
		frappe.log_error(title="GoTrue exe_perms fetch error", message=str(e))
		raise GoTrueUserFetchError(f"request failed: {e}") from e
	if resp.status_code != 200:
		raise GoTrueUserFetchError(f"non-200 from /user: {resp.status_code}")
	try:
		return resp.json() or {}
	except ValueError as e:
		raise GoTrueUserFetchError("unparseable /user body") from e


def _apply_managed_roles(email: str, app_metadata: dict) -> bool:
	"""Reconcile a user's Frappe roles to the caps in their exe_perms claim.

	Returns True if the user is MANAGED (a decision was applied), False if the
	claim is ABSENT/unmanaged (caller keeps existing bootstrap behavior — fully
	backward compatible).

	MANAGED path:
	  - Reconcile roles to EXACTLY the mapped set: add missing, and REMOVE only
	    managed-owned roles (decision["managed"]) that are no longer granted.
	    Roles outside the managed allowlist are never touched (design R5).
	  - Flip user_type (System User <-> Website User).
	  - MANAGED-DENY (role `none` / empty erp caps): disable the Frappe user
	    (enabled=0) and deny login. Fail closed — never leave a usable default.

	STALENESS BOUND (P2, documented): reconcile happens ONLY at login/callback.
	An already-authenticated Frappe session slides on its own TTL and is NOT
	re-checked against GoTrue mid-session, so a cap change made in the control
	plane takes effect for an active user only on their NEXT login (or when
	their session expires) — EXCEPT managed-deny, which kills existing sessions
	immediately (see deny branch below). Worst-case stale window for a
	downgrade-without-deny is therefore one session TTL.

	PROPAGATION / ACTIVE FAN-OUT SEAM: to close that window for non-deny
	downgrades, a later phase adds a control-plane -> ERP role-sync endpoint
	that (a) re-runs this reconcile without a login and (b) kills the affected
	user's Sessions so new roles apply at once. It would call
	`_apply_managed_roles(email, fetched_app_metadata)` directly. Hook it here.
	We deliberately do NOT build that fan-out in this module.
	"""
	admin_role, write_roles = _role_config()
	decision, status = _exe_perms.compute_decision(
		app_metadata,
		_configured_org_id(),
		admin_role=admin_role,
		write_roles=write_roles,
	)
	if decision is None:
		# Unmanaged: absent claim, multi-org-without-config, or no claim for
		# this org. Leave existing behavior untouched.
		frappe.logger().debug("exe_perms: user %s unmanaged (%s)", email, status)
		return False

	user_doc = frappe.get_doc("User", email)

	# MANAGED-DENY -> fail closed: disable and refuse login.
	#
	# PERSISTENCE (P1): frappe.throw raises AuthenticationError, and Frappe
	# ROLLS BACK the request transaction on that exception. Without an explicit
	# commit the enabled=0 write is UNDONE, so a downgraded user would keep
	# getting fresh sids with stale roles. We therefore commit the disable AND
	# the session-kill BEFORE raising, so deny durably sticks in the DB.
	if decision["deny"]:
		if user_doc.enabled:
			user_doc.enabled = 0
			user_doc.flags.ignore_permissions = True
			user_doc.save(ignore_permissions=True)
		# Kill any existing Frappe sessions so deny takes effect IMMEDIATELY
		# (not just on next login) — an already-authenticated sid is revoked.
		try:
			frappe.db.delete("Sessions", {"user": email})
		except Exception as e:  # noqa: BLE001 — never let cleanup mask the deny
			frappe.log_error(
				title="exe_perms deny: session-kill failed", message=str(e)
			)
		# Durably persist BEFORE the raise-triggered rollback can undo it.
		frappe.db.commit()
		frappe.logger().warning(
			"exe_perms: managed-deny for %s (role=%s) — access disabled",
			email,
			decision.get("role_preset"),
		)
		frappe.throw(
			"Your account has no ERP access in this organization.",
			frappe.AuthenticationError,
		)

	target = decision["roles"]
	managed = decision["managed"]
	current = {r.role for r in user_doc.get("roles")}

	to_add = target - current
	# Scope removal to managed-owned roles only — never strip roles an ERP
	# admin assigned by hand outside the caps model.
	to_remove = (current & managed) - target

	if to_add:
		user_doc.add_roles(*sorted(to_add))
	if to_remove:
		user_doc.remove_roles(*sorted(to_remove))

	changed = False
	if decision["user_type"] and user_doc.user_type != decision["user_type"]:
		user_doc.user_type = decision["user_type"]
		changed = True
	# Re-enable if a prior managed-deny disabled this user and access returned.
	if not user_doc.enabled:
		user_doc.enabled = 1
		changed = True
	if changed:
		user_doc.flags.ignore_permissions = True
		user_doc.save(ignore_permissions=True)

	frappe.logger().info(
		"exe_perms: reconciled %s -> level=%s roles+%s roles-%s",
		email,
		decision["level"],
		sorted(to_add),
		sorted(to_remove),
	)
	return True


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

	# P3: obtain authoritative app_metadata (incl. exe_perms) via GoTrue /user,
	# using the access_token the password grant just issued.
	try:
		token_data = resp.json()
	except ValueError:
		token_data = {}

	# FAIL-CLOSED ON /user ERROR (P1): the password grant already PROVED
	# identity. If we then cannot fetch authoritative app_metadata, perms are
	# UNKNOWN. On a managed-enforcement tenant (exe_org_id configured) we must
	# NOT fall back to "no claim" and log in with STALE roles — a downgraded
	# user could otherwise get a fresh sid with old roles during a brief /user
	# outage. So we DENY this login. TRADEOFF: a GoTrue /user outage blocks
	# managed logins — acceptable vs. granting stale privilege. The callback
	# path already fails closed on /user non-200; this makes password consistent.
	#
	# A genuinely UNMANAGED / legacy tenant (no exe_org_id configured) is NOT
	# doing managed enforcement, so a /user outage there must not block logins:
	# we degrade to the legacy no-claim path only in that case.
	try:
		gotrue_user = _fetch_gotrue_user(gotrue_url, token_data.get("access_token"))
	except GoTrueUserFetchError as e:
		if _configured_org_id():
			frappe.log_error(
				title="GoTrue exe_perms unavailable — failing closed",
				message=f"{email}: {e}",
			)
			frappe.throw(
				"Unable to verify your access right now. Please try again.",
				frappe.AuthenticationError,
			)
		# Unmanaged/legacy tenant: /user outage must not block login.
		gotrue_user = {}

	# SUBJECT BINDING (P2): the /user response is authoritative for identity.
	# We provision/log in the SUBMITTED email, so verify /user's email matches
	# before applying its roles — never apply org caps from a body that
	# describes a different identity. (The callback path derives email from
	# /user; this makes the password path consistent.)
	gotrue_email = (gotrue_user.get("email") or "").strip().lower()
	if gotrue_email and gotrue_email != (email or "").strip().lower():
		frappe.log_error(
			title="GoTrue subject-binding mismatch",
			message=f"submitted={email} /user={gotrue_email}",
		)
		frappe.throw("Authentication identity mismatch", frappe.AuthenticationError)

	app_metadata = gotrue_user.get("app_metadata")
	managed = _exe_perms.compute_decision(app_metadata, _configured_org_id(), *_role_config())[0] is not None

	# GoTrue accepted — find or create Frappe User
	if not frappe.db.exists("User", email):
		# SECURITY (bug 7b4bbe12): fail closed — require a tenant/domain allowlist
		# (or an explicit allow-all opt-in) before auto-provisioning any user.
		_assert_provisioning_allowed(email)
		first_name = email.split("@")[0]
		default_user_type = frappe.conf.get("default_gotrue_user_type", "Website User")
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

		# First-user/bootstrap heuristic runs ONLY for UNMANAGED users (no
		# exe_perms claim). When caps are present, _apply_managed_roles below is
		# the single source of truth for roles/user_type.
		if not managed:
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

	# P3: reconcile Frappe roles from the exe_perms claim (managed users only).
	# Fails closed (throws) on managed-deny, so login below never runs for a
	# denied user.
	_apply_managed_roles(email, app_metadata)

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
	"""Handle redirect from the Exe SSO auth domain with JWT token.

	The auth domain (e.g. auth.acme.com) redirects to:
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

	# P3: /user already returns app_metadata — read the exe_perms claim from it.
	app_metadata = user_data.get("app_metadata")
	managed = _exe_perms.compute_decision(app_metadata, _configured_org_id(), *_role_config())[0] is not None

	# Auto-provision Frappe User if needed (same logic as gotrue_login)
	if not frappe.db.exists("User", email):
		# SECURITY (bug 7b4bbe12): fail closed — require a tenant/domain allowlist
		# (or an explicit allow-all opt-in) before auto-provisioning any user.
		_assert_provisioning_allowed(email)
		first_name = email.split("@")[0]
		default_user_type = frappe.conf.get("default_gotrue_user_type", "Website User")
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

		# Bootstrap heuristic runs ONLY for UNMANAGED users (no exe_perms claim).
		if not managed:
			bootstrap_mode = os.environ.get("ERP_BOOTSTRAP_MODE", "false").lower() == "true"
			user_count = frappe.db.count("User", {"user_type": "System User", "enabled": 1})
			if user_count <= 1 and bootstrap_mode:
				user_doc.add_roles("System Manager")

	# P3: reconcile Frappe roles from the exe_perms claim (managed users only).
	# Fails closed (throws) on managed-deny, so login below never runs.
	_apply_managed_roles(email, app_metadata)

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
