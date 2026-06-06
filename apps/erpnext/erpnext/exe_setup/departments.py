"""
Exe ERP -- Default Departments for 7-Department Company

Departments:
  1. Finance & Accounting
  2. Sales & Marketing
  3. Procurement & Supply Chain
  4. Warehouse & Logistics
  5. Human Resources
  6. Manufacturing & Production
  7. Management & Administration

Each department is created under the root company node.  If a company does
not exist yet, departments are created without a parent (they will be
re-parented when the setup wizard creates the company).

Idempotent: existing departments are skipped.
"""

import frappe

logger = frappe.logger("exe_setup.departments")

# ---------------------------------------------------------------------------
# Canonical department list
# ---------------------------------------------------------------------------

DEPARTMENTS: list[dict[str, str]] = [
    {
        "name": "Finance & Accounting",
        "description": "General ledger, AP/AR, treasury, tax compliance, and financial reporting.",
    },
    {
        "name": "Sales & Marketing",
        "description": "Revenue generation, customer acquisition, CRM, and brand strategy.",
    },
    {
        "name": "Procurement & Supply Chain",
        "description": "Vendor management, purchase orders, sourcing, and supply planning.",
    },
    {
        "name": "Warehouse & Logistics",
        "description": "Inventory management, shipping, receiving, and distribution.",
    },
    {
        "name": "Human Resources",
        "description": "Recruitment, payroll, benefits, training, and employee relations.",
    },
    {
        "name": "Manufacturing & Production",
        "description": "Production planning, BOM management, work orders, and quality control.",
    },
    {
        "name": "Management & Administration",
        "description": "Executive leadership, corporate governance, and cross-functional oversight.",
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_departments() -> None:
    """Create the 7 default departments.

    Uses the first Company in the system as the parent.  If no company exists
    yet (pre-setup-wizard), departments are created without a company link --
    the setup wizard will re-parent them.
    """
    company = _get_default_company()

    for dept_def in DEPARTMENTS:
        try:
            _create_department(dept_def, company)
        except Exception:
            logger.exception("Failed to create department: %s", dept_def["name"])


def _create_department(dept_def: dict, company: str | None) -> None:
    """Create a single department if it does not already exist."""
    dept_name = dept_def["name"]

    # Frappe stores Department names as "Dept - ABBR" when a company exists.
    # We check both with and without the company abbreviation.
    if company:
        abbr = frappe.db.get_value("Company", company, "abbr") or ""
        full_name = f"{dept_name} - {abbr}" if abbr else dept_name
    else:
        full_name = dept_name

    if frappe.db.exists("Department", full_name) or frappe.db.exists("Department", dept_name):
        logger.debug("Department already exists, skipping: %s", dept_name)
        return

    doc = frappe.new_doc("Department")
    doc.department_name = dept_name
    if company:
        doc.company = company
    doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True)
    logger.info("Created department: %s", doc.name)


def _get_default_company() -> str | None:
    """Return the first company in the system, or None."""
    companies = frappe.db.get_all("Company", limit_page_length=1, pluck="name")
    return companies[0] if companies else None
