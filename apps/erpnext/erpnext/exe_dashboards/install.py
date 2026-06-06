"""
Exe ERP — Pre-built Dashboard Configurations

Creates Dashboard + Dashboard Chart records for 5 role-based dashboards:
  - CFO Dashboard
  - Sales Dashboard
  - Procurement Dashboard
  - Inventory Dashboard
  - Executive Overview

Usage:
  bench execute erpnext.exe_dashboards.install.create_all_dashboards

Idempotent: safe to run multiple times. Existing charts/dashboards are
updated in place (matched by name).
"""

import json

import frappe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upsert_chart(chart_def):
    """Create or update a Dashboard Chart by name."""
    name = chart_def["chart_name"]
    if frappe.db.exists("Dashboard Chart", name):
        doc = frappe.get_doc("Dashboard Chart", name)
        doc.update(chart_def)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "Dashboard Chart", **chart_def})
        doc.insert(ignore_permissions=True)
    return doc.name


def _upsert_dashboard(dashboard_def, chart_links):
    """Create or update a Dashboard, then wire its chart links."""
    name = dashboard_def["name"]
    if frappe.db.exists("Dashboard", name):
        doc = frappe.get_doc("Dashboard", name)
        doc.update(dashboard_def)
    else:
        doc = frappe.get_doc({"doctype": "Dashboard", **dashboard_def})

    # Replace chart links each run (idempotent)
    doc.set("charts", [])
    for idx, link in enumerate(chart_links, start=1):
        doc.append("charts", {
            "chart": link["chart"],
            "width": link.get("width", "Half"),
        })

    if doc.is_new():
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    return doc.name


def _filters_json(filters):
    """Serialize a filter dict to the JSON string Frappe expects."""
    return json.dumps(filters, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Chart definitions
# ---------------------------------------------------------------------------

def _cfo_charts():
    """CFO Dashboard charts."""
    return [
        {
            "chart_name": "Exe CFO — Revenue Trend",
            "chart_type": "Sum",
            "document_type": "Sales Invoice",
            "based_on": "posting_date",
            "value_based_on": "grand_total",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Line",
            "color": "#F5D76E",
        },
        {
            "chart_name": "Exe CFO — Expenses Trend",
            "chart_type": "Sum",
            "document_type": "Purchase Invoice",
            "based_on": "posting_date",
            "value_based_on": "grand_total",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Line",
            "color": "#FF6B6B",
        },
        {
            "chart_name": "Exe CFO — Accounts Receivable Aging",
            "chart_type": "Group By",
            "document_type": "Sales Invoice",
            "group_by_type": "Count",
            "group_by_based_on": "status",
            "filters_json": _filters_json({"docstatus": 1, "outstanding_amount": [">", 0]}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#5E64FF",
        },
        {
            "chart_name": "Exe CFO — Accounts Payable Aging",
            "chart_type": "Group By",
            "document_type": "Purchase Invoice",
            "group_by_type": "Count",
            "group_by_based_on": "status",
            "filters_json": _filters_json({"docstatus": 1, "outstanding_amount": [">", 0]}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#FF9F43",
        },
        {
            "chart_name": "Exe CFO — Cash Flow Summary",
            "chart_type": "Sum",
            "document_type": "Payment Entry",
            "based_on": "posting_date",
            "value_based_on": "paid_amount",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#2ED8A3",
        },
        {
            "chart_name": "Exe CFO — P&L Month-over-Month",
            "chart_type": "Sum",
            "document_type": "GL Entry",
            "based_on": "posting_date",
            "value_based_on": "debit_in_account_currency",
            "filters_json": _filters_json({
                "is_cancelled": 0,
                "root_type": ["in", ["Income", "Expense"]],
            }),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#7C4DFF",
        },
    ]


def _sales_charts():
    """Sales Dashboard charts."""
    return [
        {
            "chart_name": "Exe Sales — Pipeline by Stage",
            "chart_type": "Group By",
            "document_type": "Quotation",
            "group_by_type": "Count",
            "group_by_based_on": "status",
            "filters_json": _filters_json({"docstatus": ["in", [0, 1]]}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#F5D76E",
        },
        {
            "chart_name": "Exe Sales — Top Customers by Revenue",
            "chart_type": "Group By",
            "document_type": "Sales Invoice",
            "group_by_type": "Sum",
            "group_by_based_on": "customer",
            "aggregate_function_based_on": "grand_total",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#2ED8A3",
            "number_of_groups": 10,
        },
        {
            "chart_name": "Exe Sales — Monthly Sales Trend",
            "chart_type": "Sum",
            "document_type": "Sales Invoice",
            "based_on": "posting_date",
            "value_based_on": "grand_total",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Line",
            "color": "#5E64FF",
        },
        {
            "chart_name": "Exe Sales — Quotation Conversion Rate",
            "chart_type": "Group By",
            "document_type": "Quotation",
            "group_by_type": "Count",
            "group_by_based_on": "status",
            "filters_json": _filters_json({}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#FF9F43",
        },
        {
            "chart_name": "Exe Sales — Open Sales Orders",
            "chart_type": "Count",
            "document_type": "Sales Order",
            "based_on": "transaction_date",
            "filters_json": _filters_json({
                "docstatus": 1,
                "status": ["not in", ["Completed", "Cancelled", "Closed"]],
            }),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#FF6B6B",
        },
    ]


def _procurement_charts():
    """Procurement Dashboard charts."""
    return [
        {
            "chart_name": "Exe Procurement — PO by Status",
            "chart_type": "Group By",
            "document_type": "Purchase Order",
            "group_by_type": "Count",
            "group_by_based_on": "status",
            "filters_json": _filters_json({"docstatus": ["in", [0, 1]]}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#F5D76E",
        },
        {
            "chart_name": "Exe Procurement — Top Suppliers by Spend",
            "chart_type": "Group By",
            "document_type": "Purchase Invoice",
            "group_by_type": "Sum",
            "group_by_based_on": "supplier",
            "aggregate_function_based_on": "grand_total",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#2ED8A3",
            "number_of_groups": 10,
        },
        {
            "chart_name": "Exe Procurement — Pending Deliveries",
            "chart_type": "Count",
            "document_type": "Purchase Order",
            "based_on": "transaction_date",
            "filters_json": _filters_json({
                "docstatus": 1,
                "status": ["in", ["To Receive and Bill", "To Receive"]],
            }),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#FF9F43",
        },
        {
            "chart_name": "Exe Procurement — Material Requests Pending",
            "chart_type": "Count",
            "document_type": "Material Request",
            "based_on": "transaction_date",
            "filters_json": _filters_json({
                "docstatus": 1,
                "status": ["not in", ["Transferred", "Received", "Cancelled"]],
            }),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#FF6B6B",
        },
    ]


def _inventory_charts():
    """Inventory Dashboard charts."""
    return [
        {
            "chart_name": "Exe Inventory — Stock by Warehouse",
            "chart_type": "Group By",
            "document_type": "Bin",
            "group_by_type": "Sum",
            "group_by_based_on": "warehouse",
            "aggregate_function_based_on": "actual_qty",
            "filters_json": _filters_json({}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#F5D76E",
        },
        {
            "chart_name": "Exe Inventory — Items Below Reorder",
            "chart_type": "Count",
            "document_type": "Bin",
            "based_on": "modified",
            "filters_json": _filters_json({}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#FF6B6B",
            "custom_options": json.dumps({
                "fieldname": "actual_qty",
                "condition": "<=",
                "compare_field": "ordered_qty",
            }),
        },
        {
            "chart_name": "Exe Inventory — Stock Value by Item Group",
            "chart_type": "Group By",
            "document_type": "Bin",
            "group_by_type": "Sum",
            "group_by_based_on": "item_group",
            "aggregate_function_based_on": "stock_value",
            "filters_json": _filters_json({}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#5E64FF",
        },
        {
            "chart_name": "Exe Inventory — Recent Stock Movements",
            "chart_type": "Count",
            "document_type": "Stock Entry",
            "based_on": "posting_date",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Quarter",
            "time_interval": "Weekly",
            "type": "Bar",
            "color": "#2ED8A3",
        },
    ]


def _executive_charts():
    """Executive Overview charts."""
    return [
        {
            "chart_name": "Exe Executive — Revenue vs Budget",
            "chart_type": "Sum",
            "document_type": "Sales Invoice",
            "based_on": "posting_date",
            "value_based_on": "grand_total",
            "filters_json": _filters_json({"docstatus": 1}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#F5D76E",
        },
        {
            "chart_name": "Exe Executive — Key Financial Ratios",
            "chart_type": "Group By",
            "document_type": "GL Entry",
            "group_by_type": "Sum",
            "group_by_based_on": "root_type",
            "aggregate_function_based_on": "debit_in_account_currency",
            "filters_json": _filters_json({"is_cancelled": 0}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Pie",
            "color": "#7C4DFF",
        },
        {
            "chart_name": "Exe Executive — Department Headcount",
            "chart_type": "Group By",
            "document_type": "Employee",
            "group_by_type": "Count",
            "group_by_based_on": "department",
            "filters_json": _filters_json({"status": "Active"}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#2ED8A3",
        },
        {
            "chart_name": "Exe Executive — Open Tasks",
            "chart_type": "Count",
            "document_type": "ToDo",
            "based_on": "date",
            "filters_json": _filters_json({"status": "Open"}),
            "timespan": "Last Year",
            "time_interval": "Monthly",
            "type": "Bar",
            "color": "#FF9F43",
        },
    ]


# ---------------------------------------------------------------------------
# Dashboard assembly
# ---------------------------------------------------------------------------

_DASHBOARDS = {
    "Exe CFO Dashboard": {
        "module": "Accounts",
        "is_default": 0,
        "charts_fn": _cfo_charts,
    },
    "Exe Sales Dashboard": {
        "module": "Selling",
        "is_default": 0,
        "charts_fn": _sales_charts,
    },
    "Exe Procurement Dashboard": {
        "module": "Buying",
        "is_default": 0,
        "charts_fn": _procurement_charts,
    },
    "Exe Inventory Dashboard": {
        "module": "Stock",
        "is_default": 0,
        "charts_fn": _inventory_charts,
    },
    "Exe Executive Overview": {
        "module": "Setup",
        "is_default": 0,
        "charts_fn": _executive_charts,
    },
}


def create_all_dashboards():
    """
    Idempotent entry point. Creates or updates all Exe dashboards and their charts.

    Usage:
        bench execute erpnext.exe_dashboards.install.create_all_dashboards
    """
    created_charts = 0
    created_dashboards = 0

    for dashboard_name, cfg in _DASHBOARDS.items():
        charts = cfg["charts_fn"]()
        chart_links = []

        for chart_def in charts:
            _upsert_chart(chart_def)
            created_charts += 1
            # Half-width for pie/donut, Full for line/bar with >6 charts
            width = "Half"
            if chart_def.get("type") in ("Line",) and len(charts) <= 4:
                width = "Full"
            chart_links.append({"chart": chart_def["chart_name"], "width": width})

        _upsert_dashboard(
            {
                "name": dashboard_name,
                "dashboard_name": dashboard_name,
                "module": cfg["module"],
                "is_default": cfg["is_default"],
                "is_standard": 0,
            },
            chart_links,
        )
        created_dashboards += 1

    frappe.db.commit()
    frappe.msgprint(
        f"Exe dashboards installed: {created_dashboards} dashboards, {created_charts} charts.",
        alert=True,
    )
