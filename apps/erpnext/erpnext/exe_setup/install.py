"""
Exe ERP -- Post-Install Hook

Wire this in hooks.py:
    after_install = "erpnext.exe_setup.install.after_install"

Or append to the existing after_install list if one already exists.
"""

import frappe

logger = frappe.logger("exe_setup.install")


def after_install():
    """Run after Exe ERP is installed on a new site.

    Calls setup_defaults() which creates departments, role profiles, and
    approval workflows.  Safe to run multiple times (fully idempotent).
    """
    try:
        from erpnext.exe_setup import setup_defaults

        logger.info("Running Exe ERP post-install setup...")
        setup_defaults()
        logger.info("Exe ERP post-install setup complete.")
    except Exception:
        logger.exception("Exe ERP post-install setup failed -- site may need manual configuration")
        # Don't re-raise: a setup failure should not block site creation.
        # The admin can run setup_defaults() manually via bench console.

    # Templates: print formats, email templates, naming series
    try:
        from erpnext.exe_templates.install import setup_templates

        setup_templates()
    except Exception:
        logger.exception("Exe ERP template setup failed -- can be run manually later")

    # Dashboards: role-based dashboard charts
    try:
        from erpnext.exe_dashboards.install import setup_dashboards

        setup_dashboards()
    except Exception:
        logger.exception("Exe ERP dashboard setup failed -- can be run manually later")
