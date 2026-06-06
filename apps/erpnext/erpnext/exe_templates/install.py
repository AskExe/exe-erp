"""
Exe ERP — Print format, email template, and naming series installer.

Creates professional Exe-branded print formats and email templates
on first install. Idempotent — skips if already exists.
"""

import frappe
import logging

logger = logging.getLogger("exe_templates")

# ── Exe Foundry Bold Design System ────────────────────────────
EXE_CSS = """
<style>
  :root {
    --exe-gold: #F5D76E;
    --exe-dark: #0F0E1A;
    --exe-white: #FFFFFF;
    --exe-gray: #6B7280;
    --exe-light: #F9FAFB;
    --exe-border: #E5E7EB;
  }
  .exe-print { font-family: 'Manrope', 'Helvetica Neue', sans-serif; color: #1F2937; font-size: 10pt; }
  .exe-print h1, .exe-print h2, .exe-print h3 { font-family: 'Epilogue', 'Helvetica Neue', sans-serif; }
  .exe-header { border-bottom: 3px solid var(--exe-gold); padding-bottom: 12px; margin-bottom: 20px; }
  .exe-header-row { display: flex; justify-content: space-between; align-items: flex-start; }
  .exe-company { font-size: 18pt; font-weight: 700; color: var(--exe-dark); }
  .exe-doc-title { font-size: 14pt; color: var(--exe-dark); margin-top: 4px; }
  .exe-doc-number { font-family: 'Space Grotesk', monospace; font-size: 11pt; color: var(--exe-gray); }
  .exe-section { margin-bottom: 16px; }
  .exe-section-title { font-size: 9pt; text-transform: uppercase; letter-spacing: 0.05em; color: var(--exe-gray); margin-bottom: 6px; font-weight: 600; }
  .exe-addr { font-size: 9pt; line-height: 1.5; }
  .exe-table { width: 100%; border-collapse: collapse; margin: 12px 0; }
  .exe-table th { background: var(--exe-dark); color: var(--exe-white); padding: 8px 10px; text-align: left; font-size: 8pt; text-transform: uppercase; letter-spacing: 0.05em; }
  .exe-table td { padding: 8px 10px; border-bottom: 1px solid var(--exe-border); font-size: 9pt; }
  .exe-table tr:nth-child(even) { background: var(--exe-light); }
  .exe-table .text-right { text-align: right; }
  .exe-totals { margin-top: 12px; width: 300px; float: right; }
  .exe-totals td { padding: 4px 10px; font-size: 9pt; }
  .exe-totals .grand-total { font-weight: 700; font-size: 12pt; border-top: 2px solid var(--exe-dark); }
  .exe-footer { margin-top: 40px; padding-top: 12px; border-top: 1px solid var(--exe-border); font-size: 8pt; color: var(--exe-gray); text-align: center; }
  .exe-terms { margin-top: 20px; font-size: 8pt; color: var(--exe-gray); }
  .exe-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 8pt; font-weight: 600; }
  .exe-badge-paid { background: #D1FAE5; color: #065F46; }
  .exe-badge-unpaid { background: #FEE2E2; color: #991B1B; }
  .exe-badge-draft { background: #E5E7EB; color: #374151; }
  @media print { .exe-print { -webkit-print-color-adjust: exact; print-color-adjust: exact; } }
</style>
"""

# ── Invoice Print Format ──────────────────────────────────────
INVOICE_HTML = EXE_CSS + """
<div class="exe-print">
  <div class="exe-header">
    <div class="exe-header-row">
      <div>
        {% if doc.company_logo %}<img src="{{ doc.company_logo }}" style="max-height: 48px;">{% endif %}
        <div class="exe-company">{{ doc.company }}</div>
      </div>
      <div style="text-align: right;">
        <div class="exe-doc-title">Sales Invoice</div>
        <div class="exe-doc-number">{{ doc.name }}</div>
        <div style="font-size: 9pt; color: #6B7280; margin-top: 4px;">
          Date: {{ doc.posting_date }} | Due: {{ doc.due_date or "On Receipt" }}
        </div>
        {% if doc.status == "Paid" %}<span class="exe-badge exe-badge-paid">PAID</span>
        {% elif doc.docstatus == 0 %}<span class="exe-badge exe-badge-draft">DRAFT</span>
        {% else %}<span class="exe-badge exe-badge-unpaid">UNPAID</span>{% endif %}
      </div>
    </div>
  </div>

  <div style="display: flex; gap: 40px;">
    <div class="exe-section" style="flex: 1;">
      <div class="exe-section-title">Bill To</div>
      <div class="exe-addr">
        <strong>{{ doc.customer_name }}</strong><br>
        {{ doc.address_display or "" }}
      </div>
    </div>
    <div class="exe-section" style="flex: 1;">
      <div class="exe-section-title">From</div>
      <div class="exe-addr">
        <strong>{{ doc.company }}</strong><br>
        {{ doc.company_address_display or "" }}
      </div>
    </div>
  </div>

  <table class="exe-table">
    <thead>
      <tr><th style="width: 5%">#</th><th>Item</th><th>Qty</th><th>Rate</th><th class="text-right">Amount</th></tr>
    </thead>
    <tbody>
      {% for item in doc.items %}
      <tr>
        <td>{{ loop.index }}</td>
        <td>{{ item.item_name }}{% if item.description and item.description != item.item_name %}<br><small style="color: #6B7280;">{{ item.description[:100] }}</small>{% endif %}</td>
        <td>{{ item.qty }} {{ item.uom or "" }}</td>
        <td>{{ frappe.format_value(item.rate, {"fieldtype": "Currency", "options": doc.currency}) }}</td>
        <td class="text-right">{{ frappe.format_value(item.amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <table class="exe-totals">
    <tr><td>Subtotal</td><td class="text-right">{{ frappe.format_value(doc.net_total, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
    {% for tax in doc.taxes %}
    <tr><td>{{ tax.description }}</td><td class="text-right">{{ frappe.format_value(tax.tax_amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
    {% endfor %}
    {% if doc.discount_amount %}<tr><td>Discount</td><td class="text-right">-{{ frappe.format_value(doc.discount_amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>{% endif %}
    <tr class="grand-total"><td>Total</td><td class="text-right">{{ frappe.format_value(doc.grand_total, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
    {% if doc.outstanding_amount and doc.outstanding_amount > 0 %}
    <tr><td><strong>Balance Due</strong></td><td class="text-right"><strong>{{ frappe.format_value(doc.outstanding_amount, {"fieldtype": "Currency", "options": doc.currency}) }}</strong></td></tr>
    {% endif %}
  </table>

  <div style="clear: both;"></div>

  {% if doc.terms %}<div class="exe-terms"><strong>Terms & Conditions:</strong><br>{{ doc.terms }}</div>{% endif %}

  <div class="exe-footer">
    Thank you for your business | {{ doc.company }}
    {% if doc.company_email %} | {{ doc.company_email }}{% endif %}
    {% if doc.company_phone %} | {{ doc.company_phone }}{% endif %}
  </div>
</div>
"""

# ── PO Print Format ───────────────────────────────────────────
PO_HTML = EXE_CSS + """
<div class="exe-print">
  <div class="exe-header">
    <div class="exe-header-row">
      <div>
        <div class="exe-company">{{ doc.company }}</div>
      </div>
      <div style="text-align: right;">
        <div class="exe-doc-title">Purchase Order</div>
        <div class="exe-doc-number">{{ doc.name }}</div>
        <div style="font-size: 9pt; color: #6B7280;">Date: {{ doc.transaction_date }}</div>
      </div>
    </div>
  </div>

  <div style="display: flex; gap: 40px;">
    <div class="exe-section" style="flex: 1;">
      <div class="exe-section-title">Supplier</div>
      <div class="exe-addr"><strong>{{ doc.supplier_name }}</strong><br>{{ doc.address_display or "" }}</div>
    </div>
    <div class="exe-section" style="flex: 1;">
      <div class="exe-section-title">Deliver To</div>
      <div class="exe-addr"><strong>{{ doc.company }}</strong><br>{{ doc.shipping_address_display or doc.company_address_display or "" }}</div>
    </div>
  </div>

  <table class="exe-table">
    <thead><tr><th>#</th><th>Item</th><th>Qty</th><th>Rate</th><th class="text-right">Amount</th></tr></thead>
    <tbody>
      {% for item in doc.items %}
      <tr><td>{{ loop.index }}</td><td>{{ item.item_name }}</td><td>{{ item.qty }} {{ item.uom or "" }}</td><td>{{ frappe.format_value(item.rate, {"fieldtype": "Currency", "options": doc.currency}) }}</td><td class="text-right">{{ frappe.format_value(item.amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
      {% endfor %}
    </tbody>
  </table>

  <table class="exe-totals">
    <tr><td>Subtotal</td><td class="text-right">{{ frappe.format_value(doc.net_total, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
    {% for tax in doc.taxes %}<tr><td>{{ tax.description }}</td><td class="text-right">{{ frappe.format_value(tax.tax_amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>{% endfor %}
    <tr class="grand-total"><td>Total</td><td class="text-right">{{ frappe.format_value(doc.grand_total, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
  </table>
  <div style="clear: both;"></div>

  {% if doc.terms %}<div class="exe-terms"><strong>Terms & Conditions:</strong><br>{{ doc.terms }}</div>{% endif %}
  <div class="exe-footer">{{ doc.company }} — Purchase Order {{ doc.name }}</div>
</div>
"""

# ── Quotation Print Format ────────────────────────────────────
QUOTE_HTML = EXE_CSS + """
<div class="exe-print">
  <div class="exe-header">
    <div class="exe-header-row">
      <div><div class="exe-company">{{ doc.company }}</div></div>
      <div style="text-align: right;">
        <div class="exe-doc-title">Quotation</div>
        <div class="exe-doc-number">{{ doc.name }}</div>
        <div style="font-size: 9pt; color: #6B7280;">Date: {{ doc.transaction_date }} | Valid Until: {{ doc.valid_till or "30 days" }}</div>
      </div>
    </div>
  </div>

  <div class="exe-section">
    <div class="exe-section-title">Prepared For</div>
    <div class="exe-addr"><strong>{{ doc.party_name }}</strong><br>{{ doc.address_display or "" }}</div>
  </div>

  <table class="exe-table">
    <thead><tr><th>#</th><th>Item</th><th>Qty</th><th>Rate</th><th class="text-right">Amount</th></tr></thead>
    <tbody>
      {% for item in doc.items %}
      <tr><td>{{ loop.index }}</td><td>{{ item.item_name }}{% if item.description and item.description != item.item_name %}<br><small style="color: #6B7280;">{{ item.description[:100] }}</small>{% endif %}</td><td>{{ item.qty }}</td><td>{{ frappe.format_value(item.rate, {"fieldtype": "Currency", "options": doc.currency}) }}</td><td class="text-right">{{ frappe.format_value(item.amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
      {% endfor %}
    </tbody>
  </table>

  <table class="exe-totals">
    <tr><td>Subtotal</td><td class="text-right">{{ frappe.format_value(doc.net_total, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
    {% for tax in doc.taxes %}<tr><td>{{ tax.description }}</td><td class="text-right">{{ frappe.format_value(tax.tax_amount, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>{% endfor %}
    <tr class="grand-total"><td>Total</td><td class="text-right">{{ frappe.format_value(doc.grand_total, {"fieldtype": "Currency", "options": doc.currency}) }}</td></tr>
  </table>
  <div style="clear: both;"></div>

  {% if doc.terms %}<div class="exe-terms"><strong>Terms & Conditions:</strong><br>{{ doc.terms }}</div>{% endif %}
  <div class="exe-footer">We look forward to working with you | {{ doc.company }}</div>
</div>
"""


def setup_print_formats():
    """Create Exe-branded print formats. Idempotent."""
    formats = [
        ("Exe Invoice", "Sales Invoice", INVOICE_HTML),
        ("Exe Purchase Order", "Purchase Order", PO_HTML),
        ("Exe Quotation", "Quotation", QUOTE_HTML),
    ]

    for name, doc_type, html in formats:
        if frappe.db.exists("Print Format", name):
            logger.info(f"Print format '{name}' already exists — skipping")
            continue
        try:
            frappe.get_doc({
                "doctype": "Print Format",
                "name": name,
                "doc_type": doc_type,
                "html": html,
                "print_format_type": "Jinja",
                "standard": "No",
                "custom_format": 1,
                "disabled": 0,
            }).insert(ignore_permissions=True)
            logger.info(f"Created print format: {name}")
        except Exception as e:
            logger.warning(f"Failed to create print format '{name}': {e}")


def setup_email_templates():
    """Create standard email templates. Idempotent."""
    templates = [
        {
            "name": "Exe Order Confirmation",
            "subject": "Order Confirmed — {{ doc.name }}",
            "response": (
                "<p>Dear {{ doc.customer_name }},</p>"
                "<p>Thank you for your order <strong>{{ doc.name }}</strong>.</p>"
                "<p>We've received your order for <strong>{{ doc.currency }} {{ doc.grand_total }}</strong> "
                "and will begin processing it shortly.</p>"
                "<p>If you have any questions, please don't hesitate to reach out.</p>"
                "<p>Best regards,<br>{{ doc.company }}</p>"
            ),
        },
        {
            "name": "Exe Invoice Notification",
            "subject": "Invoice {{ doc.name }} — {{ doc.currency }} {{ doc.grand_total }}",
            "response": (
                "<p>Dear {{ doc.customer_name }},</p>"
                "<p>Please find attached your invoice <strong>{{ doc.name }}</strong> "
                "for <strong>{{ doc.currency }} {{ doc.grand_total }}</strong>.</p>"
                "<p>Payment is due by <strong>{{ doc.due_date or 'upon receipt' }}</strong>.</p>"
                "<p>Thank you for your business.</p>"
                "<p>Best regards,<br>{{ doc.company }}</p>"
            ),
        },
        {
            "name": "Exe Payment Receipt",
            "subject": "Payment Received — {{ doc.name }}",
            "response": (
                "<p>Dear Customer,</p>"
                "<p>We've received your payment of <strong>{{ doc.paid_amount }} {{ doc.paid_from_account_currency }}</strong> "
                "(Reference: {{ doc.name }}).</p>"
                "<p>Thank you!</p>"
                "<p>Best regards,<br>{{ doc.company }}</p>"
            ),
        },
        {
            "name": "Exe PO to Supplier",
            "subject": "Purchase Order {{ doc.name }} from {{ doc.company }}",
            "response": (
                "<p>Dear {{ doc.supplier_name }},</p>"
                "<p>Please find attached Purchase Order <strong>{{ doc.name }}</strong> "
                "for <strong>{{ doc.currency }} {{ doc.grand_total }}</strong>.</p>"
                "<p>Please confirm receipt and expected delivery date.</p>"
                "<p>Best regards,<br>{{ doc.company }}</p>"
            ),
        },
        {
            "name": "Exe Welcome Email",
            "subject": "Welcome to {{ doc.company }} — Your Account is Ready",
            "response": (
                "<p>Hello {{ doc.first_name }},</p>"
                "<p>Your account has been created. You can log in at:</p>"
                "<p><a href='{{ frappe.utils.get_url() }}'>{{ frappe.utils.get_url() }}</a></p>"
                "<p>If you have any questions, please contact your administrator.</p>"
                "<p>Welcome aboard!</p>"
            ),
        },
    ]

    for tmpl in templates:
        if frappe.db.exists("Email Template", tmpl["name"]):
            logger.info(f"Email template '{tmpl['name']}' already exists — skipping")
            continue
        try:
            frappe.get_doc({
                "doctype": "Email Template",
                "name": tmpl["name"],
                "subject": tmpl["subject"],
                "response": tmpl["response"],
                "enabled": 1,
            }).insert(ignore_permissions=True)
            logger.info(f"Created email template: {tmpl['name']}")
        except Exception as e:
            logger.warning(f"Failed to create email template '{tmpl['name']}': {e}")


def setup_naming_series():
    """Configure professional naming series for key doctypes."""
    series_map = {
        "Sales Invoice": "EXE-INV-.YYYY.-",
        "Purchase Order": "EXE-PO-.YYYY.-",
        "Sales Order": "EXE-SO-.YYYY.-",
        "Quotation": "EXE-QTN-.YYYY.-",
        "Delivery Note": "EXE-DN-.YYYY.-",
        "Material Request": "EXE-MR-.YYYY.-",
        "Payment Entry": "EXE-PAY-.YYYY.-",
    }

    for doctype, series in series_map.items():
        try:
            meta = frappe.get_meta(doctype)
            if not meta:
                continue

            # Add series to the doctype's naming options
            existing = frappe.db.get_value("DocType", doctype, "autoname") or ""
            if series not in existing:
                # Append our series as an option
                options = existing.split("\n") if existing else []
                if series not in options:
                    options.append(series)
                    # Set as default if no series exists
                    if not existing or existing == "naming_series:":
                        frappe.db.set_value("Property Setter", {
                            "doctype_or_field": "DocType",
                            "doc_type": doctype,
                            "property": "options",
                            "field_name": "naming_series",
                        }, "value", "\n".join(options))
                logger.info(f"Added naming series {series} for {doctype}")
        except Exception as e:
            logger.debug(f"Naming series setup for {doctype}: {e}")


def setup_templates():
    """Install all templates. Called from exe_setup.install.after_install."""
    logger.info("Setting up Exe ERP templates...")
    setup_print_formats()
    setup_email_templates()
    setup_naming_series()
    logger.info("Exe ERP templates setup complete.")
