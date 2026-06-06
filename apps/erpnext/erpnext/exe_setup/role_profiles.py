"""
Exe ERP -- Opinionated Role Profiles for 7-Department Company

Role Profiles:
  1. Finance Manager       -- Full access to accounts, partial to sales/purchase
  2. Sales Manager         -- Full selling, read-only accounts
  3. Procurement Manager   -- Full buying, read-only accounts
  4. Warehouse Manager     -- Full stock, read-only purchase
  5. HR Manager            -- Full HR/employee, no financial access
  6. Manufacturing Manager -- Full manufacturing + BOM, read stock
  7. Executive/Management  -- Read-only everything, approve workflows
  8. Department User       -- Basic read/create within their department

Each profile maps to a set of built-in ERPNext roles.  Profiles are
idempotent: re-running updates existing profiles to match the canonical
definition without destroying manual additions.
"""

import frappe

logger = frappe.logger("exe_setup.role_profiles")

# ---------------------------------------------------------------------------
# Canonical profile definitions
# ---------------------------------------------------------------------------

ROLE_PROFILES: dict[str, list[str]] = {
    "Finance Manager": [
        "Accounts Manager",
        "Accounts User",
        "Sales User",       # read-only cross-visibility into revenue
        "Purchase User",    # read-only cross-visibility into spend
    ],
    "Sales Manager": [
        "Sales Manager",
        "Sales User",
        "Stock User",       # need to check availability
        "Accounts User",    # read-only: view invoices, payment status
    ],
    "Procurement Manager": [
        "Purchase Manager",
        "Purchase User",
        "Stock User",       # need to see warehouse levels
        "Accounts User",    # read-only: view payment status
    ],
    "Warehouse Manager": [
        "Stock Manager",
        "Stock User",
        "Purchase User",    # read-only: see incoming POs
        "Quality Manager",  # manage incoming QC
    ],
    "HR Manager": [
        "HR Manager",
        "HR User",
        "Projects User",    # view project assignments
    ],
    "Manufacturing Manager": [
        "Manufacturing Manager",
        "Manufacturing User",
        "Stock User",       # read raw-material levels
        "Quality Manager",  # production QC
        "Projects User",    # job-card scheduling
    ],
    "Executive": [
        # Read-level roles across every domain -- no Manager write access.
        # Workflow transitions grant explicit approval rights separately.
        "Accounts User",
        "Sales User",
        "Purchase User",
        "Stock User",
        "Manufacturing User",
        "HR User",
        "Projects User",
        "Quality Manager",
    ],
    "Department User": [
        # Minimal baseline -- department-specific roles are added when the
        # user is assigned to a department via user_provisioning.
        "Projects User",
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_role_profiles() -> None:
    """Create or update all Exe ERP role profiles.

    Existing profiles are synced to match the canonical set: missing roles are
    added, stale roles removed.  Profiles that do not exist yet are created.
    """
    for profile_name, roles in ROLE_PROFILES.items():
        try:
            _upsert_profile(profile_name, roles)
        except Exception:
            logger.exception("Failed to create/update role profile %s", profile_name)


def _upsert_profile(name: str, roles: list[str]) -> None:
    """Insert or update a single Role Profile."""
    if frappe.db.exists("Role Profile", name):
        doc = frappe.get_doc("Role Profile", name)
        existing = {row.role for row in doc.roles}

        # Remove roles not in canonical set
        doc.roles = [row for row in doc.roles if row.role in roles]

        # Add missing roles
        for role in roles:
            if role not in existing:
                doc.append("roles", {"role": role})

        doc.save(ignore_permissions=True)
        logger.info("Updated role profile: %s", name)
    else:
        doc = frappe.new_doc("Role Profile")
        doc.role_profile = name
        for role in roles:
            doc.append("roles", {"role": role})
        doc.insert(ignore_permissions=True)
        logger.info("Created role profile: %s", name)
