"""
Exe ERP -- Naming Series Configuration.

Adds Exe-branded naming series to transaction doctypes.
Does NOT remove existing series -- only appends if missing.

Series format: EXE-{PREFIX}-.YYYY.-
"""

import frappe

# ---------------------------------------------------------------------------
# Mapping: doctype -> list of naming series to add
# ---------------------------------------------------------------------------

EXE_NAMING_SERIES = {
    "Sales Invoice": ["EXE-INV-.YYYY.-"],
    "Purchase Order": ["EXE-PO-.YYYY.-"],
    "Sales Order": ["EXE-SO-.YYYY.-"],
    "Quotation": ["EXE-QTN-.YYYY.-"],
    "Delivery Note": ["EXE-DN-.YYYY.-"],
    "Material Request": ["EXE-MR-.YYYY.-"],
    "Payment Entry": ["EXE-PAY-.YYYY.-"],
}


def configure_naming_series():
    """
    Append Exe naming series to each doctype's naming_series options.

    Idempotent -- only adds series that are not already present.
    Preserves all existing series; does not change the default.
    """
    for doctype, series_list in EXE_NAMING_SERIES.items():
        meta = frappe.get_meta(doctype)
        naming_field = meta.get_field("naming_series")
        if not naming_field:
            frappe.logger().warning(
                f"Doctype '{doctype}' has no naming_series field, skipping"
            )
            continue

        existing_options = (naming_field.options or "").strip()
        existing_series = [s.strip() for s in existing_options.split("\n") if s.strip()]

        added = []
        for series in series_list:
            if series not in existing_series:
                existing_series.append(series)
                added.append(series)

        if not added:
            frappe.logger().info(
                f"Naming series for '{doctype}' already up to date, skipping"
            )
            continue

        new_options = "\n".join(existing_series)

        # Use Property Setter to add options without modifying core JSON
        property_name = "naming_series"
        existing_ps = frappe.db.exists(
            "Property Setter",
            {
                "doc_type": doctype,
                "field_name": property_name,
                "property": "options",
            },
        )

        if existing_ps:
            frappe.db.set_value("Property Setter", existing_ps, "value", new_options)
        else:
            frappe.make_property_setter(
                {
                    "doctype": doctype,
                    "fieldname": property_name,
                    "property": "options",
                    "value": new_options,
                    "property_type": "Text",
                },
                is_system_generated=False,
            )

        frappe.logger().info(
            f"Added naming series to '{doctype}': {', '.join(added)}"
        )
