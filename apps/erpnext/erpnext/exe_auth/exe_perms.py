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

Single-tenant org resolution (this ERP serves ONE org):
  1. site_config `exe_org_id`, else env `EXE_ORG_ID`;
  2. if unset and the token carries exactly one org -> use it;
  3. if unset and the token carries multiple orgs -> UNMANAGED (legacy path,
     backward-compatible — we do not guess which org applies).
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
ORG_UNMANAGED_ABSENT = "unmanaged_absent"      # no exe_perms claim at all
ORG_UNMANAGED_MULTI = "unmanaged_multi"        # multiple orgs, none configured
ORG_UNMANAGED_NO_CLAIM = "unmanaged_no_claim"  # org resolved but no claim for it


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
    """
    if app_metadata is None or _exe_perms_root(app_metadata) is None:
        return None, ORG_UNMANAGED_ABSENT

    if configured_org_id:
        return str(configured_org_id).strip().lower(), ORG_RESOLVED

    orgs = list_claim_orgs(app_metadata)
    if len(orgs) == 1:
        return str(orgs[0]).strip().lower(), ORG_RESOLVED
    if len(orgs) == 0:
        return None, ORG_UNMANAGED_ABSENT
    # Multiple orgs and no site config to disambiguate -> unmanaged (legacy).
    return None, ORG_UNMANAGED_MULTI


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
      - decision is None  => UNMANAGED: caller keeps existing legacy behavior
        (bootstrap / default_gotrue_user_type). Backward compatible.
      - decision is a dict (see map_erp_roles) => MANAGED: caller reconciles.

    A MANAGED-DENY user (role none / empty erp caps) still returns a decision
    with deny=True so the caller fails closed (disable, no usable default).
    """
    org_id, status = resolve_org_id(app_metadata, configured_org_id)
    if status != ORG_RESOLVED:
        return None, status

    claim = select_org_claim(app_metadata, org_id)
    if claim is None:
        # Org resolved but this user has no claim for it -> unmanaged.
        return None, ORG_UNMANAGED_NO_CLAIM

    # role == "none" is an explicit managed-deny even if caps somehow present.
    role = claim.get("role")
    caps = claim.get("caps") or []
    if isinstance(role, str) and role.strip().lower() == "none":
        caps = []

    decision = map_erp_roles(caps, admin_role=admin_role, write_roles=write_roles)
    decision["org_id"] = org_id
    decision["role_preset"] = role
    return decision, ORG_RESOLVED
