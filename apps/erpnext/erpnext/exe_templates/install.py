"""
Exe ERP -- Template Installer.

Idempotent setup function that installs all Exe print formats,
email templates, and naming series in a single call.

Usage:
    import frappe
    from erpnext.exe_templates.install import setup_templates
    setup_templates()
    frappe.db.commit()

Or from bench console:
    bench --site <site> execute erpnext.exe_templates.install.setup_templates
"""

import frappe


def setup_templates():
    """
    Install all Exe templates. Safe to call multiple times -- each
    sub-installer checks for existence before creating records.
    """
    frappe.logger().info("Exe Templates: starting installation...")

    # 1. Print Formats
    from erpnext.exe_templates.print_formats import create_print_formats

    create_print_formats()
    frappe.logger().info("Exe Templates: print formats done")

    # 2. Email Templates
    from erpnext.exe_templates.email_templates import create_email_templates

    create_email_templates()
    frappe.logger().info("Exe Templates: email templates done")

    # 3. Naming Series
    from erpnext.exe_templates.naming_series import configure_naming_series

    configure_naming_series()
    frappe.logger().info("Exe Templates: naming series done")

    frappe.db.commit()
    frappe.logger().info("Exe Templates: installation complete")
