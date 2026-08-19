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
	raw_allowed = frappe.conf.get("allowed_email_domains") or []
	# HARDENING (substring-match hole): allowed_email_domains MUST be a list for
	# exact membership. entrypoint.sh writes a JSON list, but an operator editing
	# site_config.json by hand could set a raw CSV STRING — and `"cme.com" in
	# "acme.com,partner.org"` is a substring match, letting cme.com pass an
	# acme.com allowlist. Coerce a str CSV to a list, and normalize case/space so
	# the check is exact and domain-case-insensitive (matches entrypoint.sh).
	if isinstance(raw_allowed, str):
		raw_allowed = raw_allowed.split(",")
	allowed_domains = [d.strip().lower() for d in raw_allowed if d and d.strip()]
	allow_all = frappe.conf.get("gotrue_allow_all_domains", False)
	email_domain = (email.split("@")[1] if "@" in email else "").strip().lower()

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

# --- Managed-disable marker + one-shot bootstrap flag (P1) -------------------
# Durable, schema-free markers in Frappe's DefaultValue store (no migration).
_MANAGED_DISABLED_PREFIX = "exe_managed_disabled::"
_BOOTSTRAP_FLAG = "exe_bootstrap_admin_granted"
# CSRF state cookie for the SSO callback (P1 login-CSRF).
_OAUTH_STATE_COOKIE = "exe_sso_state"


def _mark_managed_disabled(email: str) -> None:
	"""Record that the MANAGED system (not an admin) disabled this user."""
	frappe.db.set_default(f"{_MANAGED_DISABLED_PREFIX}{email}", "1")


def _clear_managed_disabled(email: str) -> None:
	frappe.db.set_default(f"{_MANAGED_DISABLED_PREFIX}{email}", "0")


def _is_managed_disabled(email: str) -> bool:
	return frappe.db.get_default(f"{_MANAGED_DISABLED_PREFIX}{email}") == "1"


def _try_bootstrap_first_admin(user_doc, email: str) -> None:
	"""Atomically grant System Manager to the FIRST user, at most ONCE.

	RACE FIX (P1): the old `user_count <= 1` check-then-act let two concurrent
	first-logins both read count<=1 (before either committed) and BOTH
	self-promote to System Manager. We serialize on a global filelock and gate
	on a durable single-use flag committed INSIDE the lock, so only one user
	can ever bootstrap. LIVE-BENCH / MULTI-SERVER: filelock is per-bench; a
	multi-node deployment needs a shared lock or a DB unique constraint on the
	bootstrap flag (see bench plan).
	"""
	from frappe.utils.file_lock import LockTimeoutError
	from frappe.utils.synchronization import filelock

	bootstrap_mode = os.environ.get("ERP_BOOTSTRAP_MODE", "false").lower() == "true"
	try:
		with filelock("exe_bootstrap_admin", timeout=30, is_global=True):
			if frappe.db.get_default(_BOOTSTRAP_FLAG) == "1":
				return  # already claimed by another first-login
			user_count = frappe.db.count("User", {"user_type": "System User", "enabled": 1})
			if user_count > 1:
				return
			if bootstrap_mode:
				frappe.logger().warning(
					"BOOTSTRAP MODE ACTIVE: Auto-promoting first user %s to System "
					"Manager. Disable ERP_BOOTSTRAP_MODE after initial setup.",
					email,
				)
				user_doc.add_roles("System Manager")
				# Claim the one-shot slot durably, inside the lock, before release.
				frappe.db.set_default(_BOOTSTRAP_FLAG, "1")
				frappe.db.commit()
			else:
				frappe.logger().info(
					"First user %s created with standard role. Set "
					"ERP_BOOTSTRAP_MODE=true to auto-promote to System Manager.",
					email,
				)
	except LockTimeoutError:
		frappe.log_error(title="exe bootstrap: lock timeout", message=email)


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
	# SYSTEM-DRIVEN ROLE SYNC (P1): this reconcile runs during the login request
	# while the actor is still Guest. User.add_roles()/remove_roles() call
	# User.save() WITHOUT ignore_permissions (frappe user.py:725/737), so a
	# Guest-context save is permission-checked and would FAIL — first login for a
	# managed erp:write/erp:admin user could not be granted their roles, and a
	# downgrade needing role removal would fail closed the wrong way. These roles
	# come from a VERIFIED GoTrue exe_perms claim, not user input, so we set the
	# ignore_permissions flag on the doc up front; the internal saves in
	# add_roles/remove_roles (and the explicit saves below) then run with system
	# privileges. The managed-DENY disable path already saves ignore_permissions.
	user_doc.flags.ignore_permissions = True

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
		# Record that the MANAGED system disabled this user (durable marker), so a
		# later re-grant may re-enable them — WITHOUT ever overriding a manual
		# admin disable (offboarding/incident), which carries no marker.
		_mark_managed_disabled(email)
		# Kill any existing Frappe sessions so deny takes effect IMMEDIATELY
		# (not just on next login). We MUST use frappe.sessions.clear_sessions,
		# NOT a bare `frappe.db.delete("Sessions", ...)`: Frappe resumes sessions
		# from the CACHE (frappe.cache.hget("session", sid)) BEFORE the DB
		# lookup, so deleting only the DB row leaves a cached active session
		# usable until cache expiry. clear_sessions -> delete_session clears BOTH
		# the DB row AND frappe.cache.hdel("session", sid) for every sid, so
		# revocation is truly immediate. force=True bypasses the
		# simultaneous_sessions offset so ALL of the user's sessions go.
		# LIVE-BENCH: the cache side must be confirmed against the real Redis
		# session backend (the pure unit tests here cannot exercise cache/DB).
		try:
			from frappe.sessions import clear_sessions
			clear_sessions(user=email, keep_current=False, force=True)
		except Exception as e:
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
	# Re-enable ONLY if the MANAGED system is what disabled this user (marker
	# set on managed-deny). A user disabled by hand by an ERP admin carries no
	# marker and is NEVER silently revived on their next GoTrue login (P1).
	if _exe_perms.should_reenable(user_doc.enabled, _is_managed_disabled(email)):
		user_doc.enabled = 1
		_clear_managed_disabled(email)
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
		gotrue_fetched = True
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
		# Unmanaged/legacy tenant: /user outage must not block login. This is
		# NOT an authoritative body, so subject binding below is skipped.
		gotrue_user = {}
		gotrue_fetched = False

	# SUBJECT BINDING (P2): the /user response is authoritative for identity.
	# We provision/log in the SUBMITTED email, so when we have an authoritative
	# body its email must be PRESENT and MATCH before we apply its roles — never
	# apply org caps from a body that is missing an email or describes a
	# different identity. (Mirrors the callback path, which rejects "No email in
	# SSO token" and derives email from /user.) Skipped only on the legacy
	# fail-open path above, where gotrue_fetched is False and no roles apply.
	if gotrue_fetched and not _exe_perms.subject_binding_ok(
		gotrue_user.get("email"), email
	):
		gotrue_email = (gotrue_user.get("email") or "").strip().lower()
		frappe.log_error(
			title="GoTrue subject-binding mismatch",
			message=f"submitted={email} /user={gotrue_email or '<missing>'}",
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
			# First user MAY be promoted to System Manager — atomically, ONCE
			# (race-safe; see _try_bootstrap_first_admin).
			_try_bootstrap_first_admin(user_doc, email)

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
@rate_limit(key="gotrue_login_start", limit=10, seconds=900)
def gotrue_login_start():
	"""Begin the SSO login flow WITH CSRF protection.

	Generates a random `state` nonce, stores it in an httpOnly SameSite=Lax
	cookie, and redirects the browser to this customer's auth domain carrying
	that `state`. The auth domain MUST echo `state` back to
	gotrue_login_callback, which verifies it against this cookie (double-submit)
	to defeat login-CSRF. This is the SUPPORTED entry point for the SSO flow —
	the login page links HERE (never straight to the callback), so with the
	secure default (gotrue_require_callback_state=True) the callback always has a
	state cookie to verify.

	The provider URL is built the same way the login page derives it
	(login.py:get_exe_auth_url — customer auth domain, never hardcoded), plus the
	`product` tag and our callback `redirect`, so a stock deploy needs NO extra
	config. An explicit `gotrue_auth_redirect_url` in site_config still overrides
	the whole target for operators who front SSO with a custom URL.
	"""
	import secrets
	from urllib.parse import quote

	state = secrets.token_urlsafe(32)

	auth_redirect = frappe.conf.get("gotrue_auth_redirect_url")
	if auth_redirect:
		# Operator-provided full target (may already carry product/redirect params).
		target = auth_redirect
	else:
		# Derive the customer auth domain + attach product tag and our callback.
		from frappe.www.login import get_exe_auth_url

		callback_url = frappe.utils.get_url(
			"/api/method/erpnext.exe_auth.api.gotrue_login_callback"
		)
		target = (
			f"{get_exe_auth_url().rstrip('/')}/login"
			f"?product=ERP&redirect={quote(callback_url, safe='')}"
		)

	frappe.local.cookie_manager.set_cookie(
		_OAUTH_STATE_COOKIE, state, httponly=True, samesite="Lax", max_age=600
	)
	sep = "&" if "?" in target else "?"
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = f"{target}{sep}state={state}"


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(key="gotrue_callback", limit=10, seconds=900)
def gotrue_login_callback():
	"""Handle redirect from the Exe SSO auth domain with JWT token.

	The auth domain (e.g. auth.acme.com) redirects back with `state` (echoed
	from gotrue_login_start) and the access token. We verify the CSRF state,
	validate the JWT against GoTrue's /user endpoint, auto-provision a Frappe
	User if needed, log them in, and redirect to /desk.

	SECURITY — CSRF state is enforced here (see below). TWO transport issues
	remain and need broader auth-flow coordination (NOT fixed here, flagged):
	  (1) the access token still arrives in the URL QUERY on a GET, so it can
	      land in nginx/Frappe access logs and Referer headers (replay risk);
	      moving it to a URL fragment or a POST one-time-code exchange changes
	      the shared GoTrue redirect contract.
	  (2) the issued Frappe session outlives the ~1h JWT TTL; binding session
	      expiry to the JWT `exp` is a follow-up.
	"""
	# CSRF STATE (P1 login-CSRF): require a `state` nonce that matches the signed
	# cookie set by gotrue_login_start. Without it, a GET callback carrying an
	# attacker's token could bind a victim's browser to the attacker's account,
	# and email link-scanners would auto-detonate the callback. Gate is on by
	# default; operators mid-rollout (auth domain not yet echoing state) may set
	# gotrue_require_callback_state=false in site_config while they migrate.
	require_state = frappe.conf.get("gotrue_require_callback_state", True)
	received_state = frappe.form_dict.get("state")
	cookie_state = (
		frappe.request.cookies.get(_OAUTH_STATE_COOKIE) if frappe.request else None
	)
	# One-time use: always drop the state cookie once we have read it.
	if cookie_state and getattr(frappe.local, "cookie_manager", None):
		frappe.local.cookie_manager.delete_cookie(_OAUTH_STATE_COOKIE)
	if _exe_perms.oauth_state_decision(received_state, cookie_state, require_state) == "reject":
		frappe.log_error(
			title="GoTrue SSO callback: CSRF state rejected",
			message="missing/non-matching state and no initiating state cookie on SSO callback",
		)
		frappe.throw("Invalid or missing login state", frappe.AuthenticationError)

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
			# Atomic, single-use first-admin promotion (race-safe).
			_try_bootstrap_first_admin(user_doc, email)

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
