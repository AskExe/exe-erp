"""
Exe ERP -- Opinionated Setup for 7-Department Company

Modules:
  - role_profiles: 8 role profiles covering every department + executive layer
  - workflows:     PO approval, sales discount approval, expense claim approval
  - departments:   7 default departments with cost center mapping
  - user_provisioning: Bulk user creation API (Frappe + GoTrue)

Usage:
  from erpnext.exe_setup import setup_defaults
  setup_defaults()

Or after install via hooks.py:
  after_install = "erpnext.exe_setup.install.after_install"
"""

import frappe

from erpnext.exe_setup.departments import create_departments
from erpnext.exe_setup.role_profiles import create_role_profiles
from erpnext.exe_setup.workflows import create_workflows


def setup_defaults():
    """Run all Exe ERP opinionated setup steps.

    Safe to call multiple times -- every sub-function is idempotent.
    """
    frappe.logger("exe_setup").info("Starting Exe ERP opinionated setup...")

    create_departments()
    create_role_profiles()
    create_workflows()

    frappe.db.commit()
    frappe.logger("exe_setup").info("Exe ERP opinionated setup complete.")
