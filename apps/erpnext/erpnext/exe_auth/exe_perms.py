"""
Exe ERP — Unified Permissions (P3): GoTrue exe_perms claim -> Frappe roles.

This module is intentionally FRAPPE-FREE (no top-level `import frappe`) so the
pure cap->role mapping can be unit-tested without a live Frappe site. All Frappe
interaction (reading site_config, reconciling User roles) lives in
`erpnext.exe_auth.api`; this module only computes decisions from data.

Canonical claim (per UNIFIED-PERMISSIONS-DESIGN.md §1, §2). The authoritative
shape is PER-ORG:

    app_metadata.exe_perms.orgs[org_id] = {
        "role": "manager",          # preset name, UI only — apps ignore it
        "caps": ["erp:write", ...],  # implication-closed capability strings
        "version": 1,
        "updated_at": "...",
        "updated_by": "<admin_sub>",
    }

A LEGACY FLAT shape is also accepted as a fallback (what P2/wiki shipped first):

    app_metadata.exe_perms = {"org": "acme", "role": "...", "caps": [...], ...}

ERP-relevant capabilities: erp:read | erp:write | erp:admin, plus the
cross-cutting org:admin. wiki:* / crm:* are ignored here.

Single-tenant org resolution (this ERP serves ONE org). Managed enforcement
requires exe_org_id (site_config `exe_org_id`, else env `EXE_ORG_ID`) to be
EXPLICITLY configured. We NEVER infer the tenant's org from the token: a
single-org token for org X must not silently grant org-X caps on tenant Y
(the "wrong-org" hole). Three cases, mirroring the wiki fix:

  1. exe_perms ABSENT                        -> UNMANAGED (legacy, back-compat).
  2. exe_perms PRESENT, exe_org_id configured AND this org is claimed
                                             -> MANAGED (reconcile roles).
  3. exe_perms PRESENT but (a) exe_org_id UNSET/unresolvable, or (b) configured
     but this user has NO claim for it       -> DENY (fail closed — never fall
     through to stale/legacy roles; removing a user's org claim is a downgrade,
     not a bypass).
"""

from __future__ import annotations

# --- Capability vocabulary (ERP slice of the canonical §1.2 vocabulary) -------
CAP_ERP_READ = "erp:read"
CAP_ERP_WRITE = "erp:write"
CAP_ERP_ADMIN = "erp:admin"
CAP_ORG_ADMIN = "org:admin"

# --- Default Frappe role mapping ---------------------------------------------
# These role names are REAL ERPNext roles (verified against
# erpnext/setup/install.py DEFAULT_ROLE_PROFILES and Frappe core). "System
# Manager" is Frappe core; the write bundle are standard ERPNext desk roles.
# All are overridable via site_config for white-label / tenant-specific setups.
DEFAULT_ADMIN_ROLE = "System Manager"
DEFAULT_WRITE_ROLES = ("Sales User", "Purchase User", "Stock User", "Accounts User")

SYSTEM_USER_TYPE = "System User"
WEBSITE_USER_TYPE = "Website User"

# Access levels (monotonic: admin > write > read > none)
LEVEL_ADMIN = "admin"
LEVEL_WRITE = "write"
LEVEL_READ = "read"
LEVEL_NONE = "none"

# Org-resolution status
ORG_RESOLVED = "resolved"
ORG_UNMANAGED_ABSENT = "unmanaged_absent"  # no exe_perms claim at all -> legacy
# Fail-closed (DENY) statuses: a claim EXISTS but we cannot safely bind it to
# THIS tenant. These must NEVER fall through to stale/legacy roles.
ORG_DENY_UNRESOLVED = "deny_unresolved"    # claim present but exe_org_id UNSET
ORG_DENY_NO_CLAIM = "deny_no_claim"        # org configured but no claim for it


def role_config(admin_role=None, write_roles=None):
    """Normalize role config into (admin_role, tuple(write_roles)).

    Callers pass values read from site_config; None falls back to defaults.
    """
    admin = admin_role or DEFAULT_ADMIN_ROLE
    if write_roles:
        write = tuple(write_roles)
    else:
        write = DEFAULT_WRITE_ROLES
    return admin, write


def managed_roles(admin_role=None, write_roles=None):
    """The FIXED allowlist of Frappe roles this system OWNS.

    Role REMOVAL during reconcile is scoped to exactly this set (design R5): we
    only ever strip roles we could have granted, never roles an ERP admin
    assigned by hand outside the caps model.
    """
    admin, write = role_config(admin_role, write_roles)
    return set(write) | {admin}


def _norm_caps(caps):
    """Coerce caps into a lowercase string set; tolerate junk defensively."""
    if not caps:
        return set()
    out = set()
    for c in caps:
        if isinstance(c, str):
            out.add(c.strip().lower())
    return out


def erp_level(caps):
    """Collapse a capability set to the highest ERP access level.

    org:admin and erp:admin both imply admin (org admins manage everything).
    Monotonic: admin implies write implies read.
    """
    cap_set = _norm_caps(caps)
    if CAP_ERP_ADMIN in cap_set or CAP_ORG_ADMIN in cap_set:
        return LEVEL_ADMIN
    if CAP_ERP_WRITE in cap_set:
        return LEVEL_WRITE
    if CAP_ERP_READ in cap_set:
        return LEVEL_READ
    return LEVEL_NONE


def map_erp_roles(caps, admin_role=None, write_roles=None):
    """Pure cap -> Frappe-role decision.

    Returns a dict:
      {
        "level":     "admin" | "write" | "read" | "none",
        "roles":     set[str],   # target Frappe roles to hold (managed subset)
        "user_type": "System User" | "Website User" | None,
        "deny":      bool,       # managed-deny -> disable/deny login (fail-closed)
        "managed":   set[str],   # allowlist this system owns (removal scope)
      }

    Monotonic role SETS (admin superset of write superset of read):
      read  -> {}                     (portal only)   user_type Website User
      write -> write_roles            desk            user_type System User
      admin -> write_roles + {admin}  desk            user_type System User
      none  -> {} + deny              (fail-closed, disable the Frappe user)
    """
    admin, write = role_config(admin_role, write_roles)
    write_set = set(write)
    managed = write_set | {admin}
    level = erp_level(caps)

    if level == LEVEL_ADMIN:
        return {
            "level": level,
            "roles": write_set | {admin},
            "user_type": SYSTEM_USER_TYPE,
            "deny": False,
            "managed": managed,
        }
    if level == LEVEL_WRITE:
        return {
            "level": level,
            "roles": set(write_set),
            "user_type": SYSTEM_USER_TYPE,
            "deny": False,
            "managed": managed,
        }
    if level == LEVEL_READ:
        return {
            "level": level,
            "roles": set(),
            "user_type": WEBSITE_USER_TYPE,
            "deny": False,
            "managed": managed,
        }
    # LEVEL_NONE — managed-deny: provisioned but no ERP access granted.
    return {
        "level": level,
        "roles": set(),
        "user_type": WEBSITE_USER_TYPE,
        "deny": True,
        "managed": managed,
    }


def should_reenable(currently_enabled, disabled_by_managed):
    """Whether the allow path may re-enable a currently-disabled Frappe user.

    P1 (manual-disable override): the reconcile allow path used to blindly flip
    enabled=1 for any disabled user whose caps still grant access — silently
    reviving a user an ERP admin disabled FOR CAUSE (offboarding / incident) on
    their next GoTrue login. We now re-enable ONLY when the MANAGED system is
    what disabled them (a durable marker set on managed-deny). A user disabled
    manually by an admin carries no marker and is NEVER auto-revived.
    """
    return (not currently_enabled) and bool(disabled_by_managed)


def oauth_state_matches(received_state, expected_state):
    """Constant-time double-submit check for the SSO callback CSRF state.

    Both the URL `state` param and the state stored in the browser's signed
    nonce cookie must be present and equal. An attacker cannot both set a
    victim's cookie and know the random nonce, so a matching pair proves the
    callback was initiated by this browser (login-CSRF defense). Empty/missing
    values never match (fail closed).
    """
    import hmac

    if not received_state or not expected_state:
        return False
    return hmac.compare_digest(str(received_state), str(expected_state))


def oauth_state_decision(received_state, cookie_state, require_state):
    """Decide whether an SSO callback passes the login-CSRF state check.

    Returns "ok" to proceed with login, or "reject" to fail closed. This is the
    pure policy behind gotrue_login_callback, split out so it is testable without
    a live site (bug adf77179 — robust to auth domains that don't echo `state`).

    Policy:
      * If the auth domain echoed a `state`, it MUST match the nonce cookie set
        by gotrue_login_start (strong double-submit check). Enforced whenever a
        state is present, regardless of `require_state` — a present state is
        never trusted blindly.
      * If NO state was echoed (some auth domains legitimately don't), fall back
        to the presence of the state COOKIE, which still proves THIS browser
        initiated the flow; a blind forged callback (no prior login_start, hence
        no cookie) is still rejected. Enforced only when `require_state` is on.
      * `require_state=False` is the operator rollout opt-out: when no state is
        echoed, skip the cookie fallback too.
    """
    if received_state:
        return "ok" if oauth_state_matches(received_state, cookie_state) else "reject"
    if not require_state:
        return "ok"
    return "ok" if cookie_state else "reject"


def subject_binding_ok(gotrue_email, submitted_email):
    """True iff the authoritative /user email is PRESENT and MATCHES.

    Subject binding (P2): the password path provisions/logs-in the SUBMITTED
    email but reads roles from GoTrue /user's app_metadata. Before applying
    those roles the /user body's own email must match the authenticated
    identity. Case-insensitive, whitespace-trimmed. A missing/empty
    gotrue_email is NOT ok (fail closed) — a malformed body carrying
    app_metadata but no email must not have its roles applied.
    """
    g = (gotrue_email or "").strip().lower()
    s = (submitted_email or "").strip().lower()
    return bool(g) and g == s


def deny_decision(admin_role=None, write_roles=None):
    """A fail-closed decision: no roles, deny login, disable the Frappe user.

    Used when a claim EXISTS but cannot be safely bound to this tenant (org
    unconfigured/unresolvable, or configured but no claim for it). Shaped like
    map_erp_roles' LEVEL_NONE result so the caller's managed-deny path handles
    it uniformly (disable + fail closed).
    """
    admin, write = role_config(admin_role, write_roles)
    return {
        "level": LEVEL_NONE,
        "roles": set(),
        "user_type": WEBSITE_USER_TYPE,
        "deny": True,
        "managed": set(write) | {admin},
        "org_id": None,
        "role_preset": None,
    }


# --- Claim extraction --------------------------------------------------------

def _exe_perms_root(app_metadata):
    """Return the exe_perms object from app_metadata, or None."""
    if not isinstance(app_metadata, dict):
        return None
    perms = app_metadata.get("exe_perms")
    if not isinstance(perms, dict):
        return None
    return perms


def list_claim_orgs(app_metadata):
    """List org ids present in the exe_perms claim (both shapes).

    Per-org shape -> keys of `orgs`. Legacy flat shape -> [org] if present.
    """
    perms = _exe_perms_root(app_metadata)
    if perms is None:
        return []
    orgs = perms.get("orgs")
    if isinstance(orgs, dict):
        return [str(k) for k in orgs.keys()]
    org = perms.get("org")
    if isinstance(org, str) and org:
        return [org]
    return []


def resolve_org_id(app_metadata, configured_org_id):
    """Resolve which org id applies for this single-tenant ERP.

    Returns (org_id_or_None, status). See module docstring for the rules.

    SECURITY: we do NOT infer the org from a single-org token when exe_org_id is
    unset — that was the "wrong-org" hole (org X's erp:admin granting System
    Manager on tenant Y). Managed enforcement REQUIRES exe_org_id. With a claim
    present but exe_org_id unset we DENY (fail closed) rather than guess.
    """
    if app_metadata is None or _exe_perms_root(app_metadata) is None:
        # No claim at all -> genuinely unmanaged, keep legacy behavior.
        return None, ORG_UNMANAGED_ABSENT

    if configured_org_id:
        return str(configured_org_id).strip().lower(), ORG_RESOLVED

    # Claim present but no configured tenant org: we cannot classify this user
    # for THIS tenant. Fail closed instead of inferring from the token.
    return None, ORG_DENY_UNRESOLVED


def select_org_claim(app_metadata, org_id):
    """Extract the {role, caps, ...} claim for `org_id` from app_metadata.

    Handles the per-org shape (`exe_perms.orgs[org_id]`) and the legacy flat
    shape (`exe_perms` itself, matched on its `org`). Returns the claim dict or
    None if there is no claim for this org.
    """
    perms = _exe_perms_root(app_metadata)
    if perms is None or not org_id:
        return None
    target = str(org_id).strip().lower()

    orgs = perms.get("orgs")
    if isinstance(orgs, dict):
        # Case-insensitive org key match.
        for k, v in orgs.items():
            if str(k).strip().lower() == target and isinstance(v, dict):
                return v
        return None

    # Legacy flat shape.
    flat_org = perms.get("org")
    if isinstance(flat_org, str) and flat_org.strip().lower() == target:
        return perms
    return None


def compute_decision(app_metadata, configured_org_id, admin_role=None, write_roles=None):
    """Top-level: from raw app_metadata to a role decision.

    Returns (decision_or_None, status).
      - decision is None  => UNMANAGED (status ORG_UNMANAGED_ABSENT only):
        caller keeps existing legacy behavior (bootstrap /
        default_gotrue_user_type). Backward compatible.
      - decision is a dict (see map_erp_roles) => MANAGED: caller reconciles,
        or FAIL-CLOSED when decision["deny"] is True.

    Three-case fail-closed model (see module docstring):
      * absent claim                 -> (None, ORG_UNMANAGED_ABSENT)  [legacy]
      * present + configured + claim -> (managed decision, ORG_RESOLVED)
      * present + unconfigured org   -> (deny decision, ORG_DENY_UNRESOLVED)
      * present + configured, no claim for this org -> (deny, ORG_DENY_NO_CLAIM)

    A MANAGED-DENY user (role none / empty erp caps, OR either fail-closed org
    case) returns a decision with deny=True so the caller disables and fails
    closed — never a usable default and never stale/legacy roles.
    """
    org_id, status = resolve_org_id(app_metadata, configured_org_id)
    if status == ORG_UNMANAGED_ABSENT:
        # Genuinely no claim -> unmanaged, legacy behavior.
        return None, status
    if status != ORG_RESOLVED:
        # Claim present but org unconfigured/unresolvable -> fail closed.
        return deny_decision(admin_role, write_roles), status

    claim = select_org_claim(app_metadata, org_id)
    if claim is None:
        # Org configured but this user has NO claim for it -> fail closed.
        # (Removing a user's org claim must be a DOWNGRADE, not a bypass to
        # stale legacy roles.)
        d = deny_decision(admin_role, write_roles)
        d["org_id"] = org_id
        return d, ORG_DENY_NO_CLAIM

    # role == "none" is an explicit managed-deny even if caps somehow present.
    role = claim.get("role")
    caps = claim.get("caps") or []
    if isinstance(role, str) and role.strip().lower() == "none":
        caps = []

    decision = map_erp_roles(caps, admin_role=admin_role, write_roles=write_roles)
    decision["org_id"] = org_id
    decision["role_preset"] = role
    return decision, ORG_RESOLVED
