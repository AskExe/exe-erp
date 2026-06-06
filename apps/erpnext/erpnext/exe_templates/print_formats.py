"""
Exe ERP -- Professional Print Formats (Exe Foundry Bold design system).

Creates Frappe Print Format records for:
  - Sales Invoice
  - Purchase Order
  - Quotation
  - Delivery Note
  - Sales Order

All templates use Jinja2, are idempotent, and produce clean PDF output
suitable for client-facing documents.
"""

import frappe

# ---------------------------------------------------------------------------
# Shared CSS -- injected into every print format
# ---------------------------------------------------------------------------

EXE_PRINT_CSS = """\
<style>
    :root {
        --exe-gold: #F5D76E;
        --exe-dark: #0F0E1A;
        --exe-white: #FFFFFF;
        --exe-gray-100: #F7F7F8;
        --exe-gray-200: #EDEDEF;
        --exe-gray-400: #9B9BA5;
        --exe-gray-600: #6B6B76;
        --exe-gray-800: #2D2D35;
    }

    @import url('https://fonts.googleapis.com/css2?family=Epilogue:wght@500;600;700&family=Manrope:wght@400;500;600&family=Space+Grotesk:wght@400;500;600&display=swap');

    .exe-print {
        font-family: 'Manrope', 'Helvetica Neue', Arial, sans-serif;
        color: var(--exe-dark);
        font-size: 13px;
        line-height: 1.5;
        padding: 0;
        margin: 0;
    }

    .exe-print * {
        box-sizing: border-box;
    }

    /* -- Header bar -- */
    .exe-header-bar {
        background: var(--exe-dark);
        padding: 20px 32px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 0;
    }
    .exe-header-bar .exe-logo {
        max-height: 48px;
    }
    .exe-header-bar .exe-doc-title {
        font-family: 'Epilogue', sans-serif;
        font-size: 22px;
        font-weight: 700;
        color: var(--exe-gold);
        text-transform: uppercase;
        letter-spacing: 1.5px;
    }

    /* -- Company & recipient info -- */
    .exe-info-row {
        display: flex;
        justify-content: space-between;
        padding: 24px 32px 16px;
        gap: 40px;
    }
    .exe-info-block {
        flex: 1;
    }
    .exe-info-block h4 {
        font-family: 'Epilogue', sans-serif;
        font-size: 11px;
        font-weight: 600;
        color: var(--exe-gray-400);
        text-transform: uppercase;
        letter-spacing: 1px;
        margin: 0 0 6px 0;
    }
    .exe-info-block p {
        margin: 0 0 2px 0;
        font-size: 13px;
        color: var(--exe-gray-800);
    }

    /* -- Metadata grid (invoice #, date, etc.) -- */
    .exe-meta-grid {
        padding: 0 32px 20px;
    }
    .exe-meta-grid table {
        border-collapse: collapse;
    }
    .exe-meta-grid td {
        padding: 4px 0;
        font-size: 13px;
    }
    .exe-meta-grid .exe-meta-label {
        color: var(--exe-gray-400);
        font-weight: 500;
        padding-right: 16px;
        white-space: nowrap;
    }
    .exe-meta-grid .exe-meta-value {
        color: var(--exe-dark);
        font-family: 'Space Grotesk', monospace;
        font-weight: 500;
    }

    /* -- Items table -- */
    .exe-items-section {
        padding: 0 32px;
    }
    .exe-items-table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 0;
    }
    .exe-items-table thead tr {
        background: var(--exe-dark);
    }
    .exe-items-table thead td,
    .exe-items-table thead th {
        color: var(--exe-gold) !important;
        font-family: 'Epilogue', sans-serif;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        padding: 10px 12px;
        border: none;
    }
    .exe-items-table tbody tr {
        border-bottom: 1px solid var(--exe-gray-200);
    }
    .exe-items-table tbody tr:nth-child(even) {
        background: var(--exe-gray-100);
    }
    .exe-items-table tbody td {
        padding: 10px 12px;
        font-size: 13px;
        vertical-align: top;
    }
    .exe-items-table .exe-num {
        font-family: 'Space Grotesk', monospace;
        font-weight: 500;
    }
    .exe-items-table .text-right {
        text-align: right;
    }
    .exe-items-table .text-center {
        text-align: center;
    }

    /* -- Totals section -- */
    .exe-totals-section {
        padding: 16px 32px 0;
        display: flex;
        justify-content: flex-end;
    }
    .exe-totals-table {
        border-collapse: collapse;
        min-width: 280px;
    }
    .exe-totals-table td {
        padding: 6px 0;
        font-size: 13px;
    }
    .exe-totals-table .exe-totals-label {
        color: var(--exe-gray-600);
        padding-right: 24px;
        text-align: right;
    }
    .exe-totals-table .exe-totals-value {
        font-family: 'Space Grotesk', monospace;
        font-weight: 500;
        text-align: right;
        color: var(--exe-dark);
    }
    .exe-totals-table .exe-grand-total td {
        border-top: 2px solid var(--exe-dark);
        padding-top: 10px;
        font-size: 15px;
        font-weight: 700;
    }
    .exe-totals-table .exe-grand-total .exe-totals-value {
        font-size: 16px;
        color: var(--exe-dark);
    }

    /* -- In words -- */
    .exe-in-words {
        padding: 12px 32px 0;
        font-size: 12px;
        color: var(--exe-gray-600);
        font-style: italic;
    }

    /* -- Additional sections (terms, bank, notes) -- */
    .exe-section {
        padding: 20px 32px 0;
    }
    .exe-section h4 {
        font-family: 'Epilogue', sans-serif;
        font-size: 12px;
        font-weight: 600;
        color: var(--exe-dark);
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin: 0 0 8px 0;
        padding-bottom: 4px;
        border-bottom: 2px solid var(--exe-gold);
        display: inline-block;
    }
    .exe-section p, .exe-section div {
        font-size: 13px;
        color: var(--exe-gray-800);
        line-height: 1.6;
    }

    /* -- Footer -- */
    .exe-footer {
        margin-top: 40px;
        padding: 16px 32px;
        border-top: 1px solid var(--exe-gray-200);
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 11px;
        color: var(--exe-gray-400);
    }
    .exe-footer .exe-footer-gold {
        color: var(--exe-gold);
        font-weight: 600;
    }

    /* -- Status overlays -- */
    .exe-status-draft {
        text-align: center;
        padding: 8px;
        font-family: 'Epilogue', sans-serif;
        font-size: 14px;
        font-weight: 700;
        color: var(--exe-gray-400);
        letter-spacing: 3px;
        text-transform: uppercase;
    }
    .exe-status-cancelled {
        text-align: center;
        padding: 8px;
        font-family: 'Epilogue', sans-serif;
        font-size: 14px;
        font-weight: 700;
        color: #E74C3C;
        letter-spacing: 3px;
        text-transform: uppercase;
    }

    /* -- Print tweaks -- */
    @media print {
        .exe-print {
            -webkit-print-color-adjust: exact !important;
            print-color-adjust: exact !important;
        }
        .exe-header-bar {
            background: var(--exe-dark) !important;
        }
        .exe-items-table thead tr {
            background: var(--exe-dark) !important;
        }
        .exe-items-table thead td,
        .exe-items-table thead th {
            color: var(--exe-gold) !important;
        }
    }

    .print-format {
        padding: 0 !important;
        margin: 0 !important;
    }
</style>
"""

# ---------------------------------------------------------------------------
# Macro: header with letterhead + status
# ---------------------------------------------------------------------------

EXE_HEADER_MACRO = """\
{%- macro exe_header(doc, letter_head, no_letterhead, print_settings, doc_title) -%}
{% if letter_head and not no_letterhead %}
<div class="letter-head">{{ letter_head }}</div>
{% endif %}
{%- if doc.meta.is_submittable and doc.docstatus == 2 -%}
<div class="exe-status-cancelled">CANCELLED</div>
{%- endif -%}
{%- if doc.meta.is_submittable and doc.docstatus == 0 and (print_settings == None or print_settings.add_draft_heading) -%}
<div class="exe-status-draft">DRAFT</div>
{%- endif -%}
{%- endmacro -%}
"""

# ---------------------------------------------------------------------------
# Macro: company info block
# ---------------------------------------------------------------------------

EXE_COMPANY_BLOCK = """\
{%- macro company_info(doc) -%}
{% set company = frappe.get_doc("Company", doc.company) %}
<div class="exe-info-block">
    <h4>From</h4>
    <p style="font-weight: 600;">{{ doc.company }}</p>
    {% if company.company_address %}
        {% set addr = frappe.get_doc("Address", company.company_address) %}
        <p>{{ addr.address_line1 or "" }}</p>
        {% if addr.address_line2 %}<p>{{ addr.address_line2 }}</p>{% endif %}
        <p>{{ addr.city or "" }}{% if addr.state %}, {{ addr.state }}{% endif %} {{ addr.pincode or "" }}</p>
        <p>{{ addr.country or "" }}</p>
    {% endif %}
    {% if company.phone_no %}<p>{{ company.phone_no }}</p>{% endif %}
    {% if company.email %}<p>{{ company.email }}</p>{% endif %}
    {% if company.tax_id %}<p>Tax ID: {{ company.tax_id }}</p>{% endif %}
</div>
{%- endmacro -%}
"""

# ---------------------------------------------------------------------------
# Macro: totals block (reused across invoice, quotation, SO)
# ---------------------------------------------------------------------------

EXE_TOTALS_BLOCK = """\
{%- macro render_totals(doc, print_settings) -%}
<div class="exe-totals-section">
    <table class="exe-totals-table">
        <tr>
            <td class="exe-totals-label">Subtotal</td>
            <td class="exe-totals-value">{{ doc.get_formatted("total", doc) }}</td>
        </tr>
        {%- if doc.discount_amount -%}
        <tr>
            <td class="exe-totals-label">Discount{% if doc.additional_discount_percentage %} ({{ doc.additional_discount_percentage }}%){% endif %}</td>
            <td class="exe-totals-value">-{{ doc.get_formatted("discount_amount", doc) }}</td>
        </tr>
        {%- endif -%}
        {%- for tax in doc.taxes -%}
            {%- if (tax.tax_amount or (print_settings and print_settings.print_taxes_with_zero_amount)) and (not tax.included_in_print_rate or doc.flags.show_inclusive_tax_in_print) -%}
        <tr>
            <td class="exe-totals-label">{{ tax.description }}{% if tax.rate %} ({{ tax.rate }}%){% endif %}</td>
            <td class="exe-totals-value">{{ tax.get_formatted("tax_amount") }}</td>
        </tr>
            {%- endif -%}
        {%- endfor -%}
        <tr class="exe-grand-total">
            <td class="exe-totals-label">Grand Total</td>
            <td class="exe-totals-value">{{ doc.get_formatted("grand_total", doc) }}</td>
        </tr>
        {%- if doc.rounded_total -%}
        <tr>
            <td class="exe-totals-label">Rounded Total</td>
            <td class="exe-totals-value">{{ doc.get_formatted("rounded_total", doc) }}</td>
        </tr>
        {%- endif -%}
    </table>
</div>
{%- if doc.in_words -%}
<div class="exe-in-words">
    In words: {{ doc.in_words }}
</div>
{%- endif -%}
{%- endmacro -%}
"""

# ---------------------------------------------------------------------------
# Macro: footer
# ---------------------------------------------------------------------------

EXE_FOOTER_BLOCK = """\
{%- macro render_footer(doc, page_num, max_pages) -%}
<div class="exe-footer">
    <span>{{ doc.company }}</span>
    <span>{{ doc.name }} &mdash; Page {{ page_num }} of {{ max_pages }}</span>
</div>
{%- endmacro -%}
"""

# ---------------------------------------------------------------------------
# 1. Sales Invoice
# ---------------------------------------------------------------------------

SALES_INVOICE_HTML = (
    EXE_PRINT_CSS
    + EXE_HEADER_MACRO
    + EXE_COMPANY_BLOCK
    + EXE_TOTALS_BLOCK
    + EXE_FOOTER_BLOCK
    + """
{% for page in layout %}
<div class="page-break exe-print">
    {{ exe_header(doc, letter_head, no_letterhead, print_settings, "Invoice") }}

    <!-- Header Bar -->
    <div class="exe-header-bar">
        {% if doc.company and frappe.db.get_value("Company", doc.company, "company_logo") %}
        <img class="exe-logo" src="{{ frappe.db.get_value('Company', doc.company, 'company_logo') }}" alt="{{ doc.company }}">
        {% else %}
        <span style="color: var(--exe-white); font-family: 'Epilogue', sans-serif; font-weight: 700; font-size: 18px;">{{ doc.company }}</span>
        {% endif %}
        <span class="exe-doc-title">Invoice</span>
    </div>

    <!-- Company & Customer Info -->
    <div class="exe-info-row">
        {{ company_info(doc) }}
        <div class="exe-info-block">
            <h4>Bill To</h4>
            <p style="font-weight: 600;">{{ doc.customer_name }}</p>
            {% if doc.customer_address %}
                {% set caddr = frappe.db.get_value("Address", doc.customer_address, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
                <p>{{ caddr.address_line1 or "" }}</p>
                {% if caddr.address_line2 %}<p>{{ caddr.address_line2 }}</p>{% endif %}
                <p>{{ caddr.city or "" }}{% if caddr.state %}, {{ caddr.state }}{% endif %} {{ caddr.pincode or "" }}</p>
                <p>{{ caddr.country or "" }}</p>
            {% endif %}
            {% if doc.contact_email %}<p>{{ doc.contact_email }}</p>{% endif %}
        </div>
        <div class="exe-info-block">
            {% if doc.shipping_address_name %}
            <h4>Ship To</h4>
            {% set saddr = frappe.db.get_value("Address", doc.shipping_address_name, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
            <p>{{ saddr.address_line1 or "" }}</p>
            {% if saddr.address_line2 %}<p>{{ saddr.address_line2 }}</p>{% endif %}
            <p>{{ saddr.city or "" }}{% if saddr.state %}, {{ saddr.state }}{% endif %} {{ saddr.pincode or "" }}</p>
            {% endif %}
        </div>
    </div>

    <!-- Document Metadata -->
    <div class="exe-meta-grid">
        <table>
            <tr>
                <td class="exe-meta-label">Invoice No.</td>
                <td class="exe-meta-value">{{ doc.name }}</td>
                <td style="width: 40px;"></td>
                <td class="exe-meta-label">Invoice Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.posting_date) }}</td>
            </tr>
            <tr>
                <td class="exe-meta-label">Due Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.due_date) }}</td>
                <td></td>
                {% if doc.po_no %}
                <td class="exe-meta-label">PO Reference</td>
                <td class="exe-meta-value">{{ doc.po_no }}</td>
                {% else %}
                <td></td><td></td>
                {% endif %}
            </tr>
        </table>
    </div>

    <!-- Items Table -->
    <div class="exe-items-section">
        <table class="exe-items-table">
            <thead>
                <tr>
                    <td class="text-center" style="width: 40px;">#</td>
                    <td>Item</td>
                    <td>Description</td>
                    <td class="text-center">Qty</td>
                    <td class="text-right">Rate</td>
                    <td class="text-right" style="width: 110px;">Amount</td>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr>
                    <td class="text-center exe-num">{{ loop.index }}</td>
                    <td>{{ item.item_name }}</td>
                    <td style="font-size: 12px; color: var(--exe-gray-600);">{{ item.description or "" | truncate(80) }}</td>
                    <td class="text-center exe-num">{{ item.get_formatted("qty", 0) }} {{ item.uom or "" }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("rate", doc) }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("amount", doc) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    <!-- Totals -->
    {{ render_totals(doc, print_settings) }}

    <!-- Payment Terms -->
    {% if doc.payment_schedule %}
    <div class="exe-section">
        <h4>Payment Terms</h4>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
            {% for ps in doc.payment_schedule %}
            <tr style="border-bottom: 1px solid var(--exe-gray-200);">
                <td style="padding: 6px 0;">{{ ps.payment_term or "" }}</td>
                <td class="exe-num" style="padding: 6px 12px;">Due: {{ frappe.utils.format_date(ps.due_date) }}</td>
                <td class="exe-num" style="padding: 6px 0; text-align: right;">{{ ps.get_formatted("payment_amount", doc) }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <!-- Bank Details -->
    {% if doc.company %}
    {% set bank_account = frappe.db.get_value("Bank Account", {"company": doc.company, "is_company_account": 1, "is_default": 1}, ["bank", "bank_account_no", "branch_code"], as_dict=True) %}
    {% if bank_account %}
    <div class="exe-section">
        <h4>Bank Details</h4>
        <p><strong>Bank:</strong> {{ bank_account.bank or "" }}</p>
        <p><strong>Account No:</strong> {{ bank_account.bank_account_no or "" }}</p>
        {% if bank_account.branch_code %}<p><strong>Branch/SWIFT:</strong> {{ bank_account.branch_code }}</p>{% endif %}
    </div>
    {% endif %}
    {% endif %}

    <!-- Terms -->
    {% if doc.terms %}
    <div class="exe-section">
        <h4>Terms & Conditions</h4>
        <div>{{ doc.terms }}</div>
    </div>
    {% endif %}

    <!-- Footer -->
    {{ render_footer(doc, loop.index, layout|length) }}
</div>
{% endfor %}
"""
)

# ---------------------------------------------------------------------------
# 2. Purchase Order
# ---------------------------------------------------------------------------

PURCHASE_ORDER_HTML = (
    EXE_PRINT_CSS
    + EXE_HEADER_MACRO
    + EXE_COMPANY_BLOCK
    + EXE_TOTALS_BLOCK
    + EXE_FOOTER_BLOCK
    + """
{% for page in layout %}
<div class="page-break exe-print">
    {{ exe_header(doc, letter_head, no_letterhead, print_settings, "Purchase Order") }}

    <div class="exe-header-bar">
        {% if doc.company and frappe.db.get_value("Company", doc.company, "company_logo") %}
        <img class="exe-logo" src="{{ frappe.db.get_value('Company', doc.company, 'company_logo') }}" alt="{{ doc.company }}">
        {% else %}
        <span style="color: var(--exe-white); font-family: 'Epilogue', sans-serif; font-weight: 700; font-size: 18px;">{{ doc.company }}</span>
        {% endif %}
        <span class="exe-doc-title">Purchase Order</span>
    </div>

    <div class="exe-info-row">
        {{ company_info(doc) }}
        <div class="exe-info-block">
            <h4>Supplier</h4>
            <p style="font-weight: 600;">{{ doc.supplier_name }}</p>
            {% if doc.supplier_address %}
                {% set saddr = frappe.db.get_value("Address", doc.supplier_address, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
                <p>{{ saddr.address_line1 or "" }}</p>
                {% if saddr.address_line2 %}<p>{{ saddr.address_line2 }}</p>{% endif %}
                <p>{{ saddr.city or "" }}{% if saddr.state %}, {{ saddr.state }}{% endif %} {{ saddr.pincode or "" }}</p>
                <p>{{ saddr.country or "" }}</p>
            {% endif %}
            {% if doc.contact_email %}<p>{{ doc.contact_email }}</p>{% endif %}
        </div>
    </div>

    <div class="exe-meta-grid">
        <table>
            <tr>
                <td class="exe-meta-label">PO No.</td>
                <td class="exe-meta-value">{{ doc.name }}</td>
                <td style="width: 40px;"></td>
                <td class="exe-meta-label">Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.transaction_date) }}</td>
            </tr>
            {% if doc.schedule_date %}
            <tr>
                <td class="exe-meta-label">Required By</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.schedule_date) }}</td>
                <td></td>
                <td></td><td></td>
            </tr>
            {% endif %}
        </table>
    </div>

    <div class="exe-items-section">
        <table class="exe-items-table">
            <thead>
                <tr>
                    <td class="text-center" style="width: 40px;">#</td>
                    <td>Item</td>
                    <td>Description</td>
                    <td class="text-center">Qty</td>
                    <td class="text-right">Rate</td>
                    <td class="text-right" style="width: 110px;">Amount</td>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr>
                    <td class="text-center exe-num">{{ loop.index }}</td>
                    <td>{{ item.item_name }}</td>
                    <td style="font-size: 12px; color: var(--exe-gray-600);">{{ item.description or "" | truncate(80) }}</td>
                    <td class="text-center exe-num">{{ item.get_formatted("qty", 0) }} {{ item.uom or "" }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("rate", doc) }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("amount", doc) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {{ render_totals(doc, print_settings) }}

    {% if doc.terms %}
    <div class="exe-section">
        <h4>Terms & Conditions</h4>
        <div>{{ doc.terms }}</div>
    </div>
    {% endif %}

    {% if doc.notes %}
    <div class="exe-section">
        <h4>Notes</h4>
        <div>{{ doc.notes }}</div>
    </div>
    {% endif %}

    {{ render_footer(doc, loop.index, layout|length) }}
</div>
{% endfor %}
"""
)

# ---------------------------------------------------------------------------
# 3. Quotation
# ---------------------------------------------------------------------------

QUOTATION_HTML = (
    EXE_PRINT_CSS
    + EXE_HEADER_MACRO
    + EXE_COMPANY_BLOCK
    + EXE_TOTALS_BLOCK
    + EXE_FOOTER_BLOCK
    + """
{% for page in layout %}
<div class="page-break exe-print">
    {{ exe_header(doc, letter_head, no_letterhead, print_settings, "Quotation") }}

    <div class="exe-header-bar">
        {% if doc.company and frappe.db.get_value("Company", doc.company, "company_logo") %}
        <img class="exe-logo" src="{{ frappe.db.get_value('Company', doc.company, 'company_logo') }}" alt="{{ doc.company }}">
        {% else %}
        <span style="color: var(--exe-white); font-family: 'Epilogue', sans-serif; font-weight: 700; font-size: 18px;">{{ doc.company }}</span>
        {% endif %}
        <span class="exe-doc-title">Quotation</span>
    </div>

    <div class="exe-info-row">
        {{ company_info(doc) }}
        <div class="exe-info-block">
            <h4>Prepared For</h4>
            <p style="font-weight: 600;">{{ doc.party_name }}</p>
            {% if doc.customer_address %}
                {% set caddr = frappe.db.get_value("Address", doc.customer_address, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
                <p>{{ caddr.address_line1 or "" }}</p>
                {% if caddr.address_line2 %}<p>{{ caddr.address_line2 }}</p>{% endif %}
                <p>{{ caddr.city or "" }}{% if caddr.state %}, {{ caddr.state }}{% endif %} {{ caddr.pincode or "" }}</p>
                <p>{{ caddr.country or "" }}</p>
            {% endif %}
            {% if doc.contact_email %}<p>{{ doc.contact_email }}</p>{% endif %}
        </div>
    </div>

    <div class="exe-meta-grid">
        <table>
            <tr>
                <td class="exe-meta-label">Quotation No.</td>
                <td class="exe-meta-value">{{ doc.name }}</td>
                <td style="width: 40px;"></td>
                <td class="exe-meta-label">Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.transaction_date) }}</td>
            </tr>
            <tr>
                <td class="exe-meta-label">Valid Till</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.valid_till) if doc.valid_till else "N/A" }}</td>
                <td></td>
                <td></td><td></td>
            </tr>
        </table>
    </div>

    <div class="exe-items-section">
        <table class="exe-items-table">
            <thead>
                <tr>
                    <td class="text-center" style="width: 40px;">#</td>
                    <td>Item</td>
                    <td>Description</td>
                    <td class="text-center">Qty</td>
                    <td class="text-right">Rate</td>
                    <td class="text-right" style="width: 110px;">Amount</td>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr>
                    <td class="text-center exe-num">{{ loop.index }}</td>
                    <td>{{ item.item_name }}</td>
                    <td style="font-size: 12px; color: var(--exe-gray-600);">{{ item.description or "" | truncate(80) }}</td>
                    <td class="text-center exe-num">{{ item.get_formatted("qty", 0) }} {{ item.uom or "" }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("rate", doc) }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("amount", doc) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {{ render_totals(doc, print_settings) }}

    {% if doc.valid_till %}
    <div class="exe-section">
        <h4>Validity</h4>
        <p>This quotation is valid until <strong>{{ frappe.utils.format_date(doc.valid_till) }}</strong>. Prices and availability are subject to change after this date.</p>
    </div>
    {% endif %}

    {% if doc.terms %}
    <div class="exe-section">
        <h4>Terms & Conditions</h4>
        <div>{{ doc.terms }}</div>
    </div>
    {% endif %}

    {{ render_footer(doc, loop.index, layout|length) }}
</div>
{% endfor %}
"""
)

# ---------------------------------------------------------------------------
# 4. Delivery Note
# ---------------------------------------------------------------------------

DELIVERY_NOTE_HTML = (
    EXE_PRINT_CSS
    + EXE_HEADER_MACRO
    + EXE_COMPANY_BLOCK
    + EXE_FOOTER_BLOCK
    + """
{% for page in layout %}
<div class="page-break exe-print">
    {{ exe_header(doc, letter_head, no_letterhead, print_settings, "Delivery Note") }}

    <div class="exe-header-bar">
        {% if doc.company and frappe.db.get_value("Company", doc.company, "company_logo") %}
        <img class="exe-logo" src="{{ frappe.db.get_value('Company', doc.company, 'company_logo') }}" alt="{{ doc.company }}">
        {% else %}
        <span style="color: var(--exe-white); font-family: 'Epilogue', sans-serif; font-weight: 700; font-size: 18px;">{{ doc.company }}</span>
        {% endif %}
        <span class="exe-doc-title">Delivery Note</span>
    </div>

    <div class="exe-info-row">
        {{ company_info(doc) }}
        <div class="exe-info-block">
            <h4>Deliver To</h4>
            <p style="font-weight: 600;">{{ doc.customer_name }}</p>
            {% if doc.shipping_address_name %}
                {% set saddr = frappe.db.get_value("Address", doc.shipping_address_name, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
                <p>{{ saddr.address_line1 or "" }}</p>
                {% if saddr.address_line2 %}<p>{{ saddr.address_line2 }}</p>{% endif %}
                <p>{{ saddr.city or "" }}{% if saddr.state %}, {{ saddr.state }}{% endif %} {{ saddr.pincode or "" }}</p>
                <p>{{ saddr.country or "" }}</p>
            {% elif doc.customer_address %}
                {% set caddr = frappe.db.get_value("Address", doc.customer_address, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
                <p>{{ caddr.address_line1 or "" }}</p>
                {% if caddr.address_line2 %}<p>{{ caddr.address_line2 }}</p>{% endif %}
                <p>{{ caddr.city or "" }}{% if caddr.state %}, {{ caddr.state }}{% endif %} {{ caddr.pincode or "" }}</p>
                <p>{{ caddr.country or "" }}</p>
            {% endif %}
        </div>
    </div>

    <div class="exe-meta-grid">
        <table>
            <tr>
                <td class="exe-meta-label">Delivery Note No.</td>
                <td class="exe-meta-value">{{ doc.name }}</td>
                <td style="width: 40px;"></td>
                <td class="exe-meta-label">Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.posting_date) }}</td>
            </tr>
            {% if doc.po_no %}
            <tr>
                <td class="exe-meta-label">Customer PO</td>
                <td class="exe-meta-value">{{ doc.po_no }}</td>
                <td></td>
                {% if doc.transporter_name %}
                <td class="exe-meta-label">Transporter</td>
                <td class="exe-meta-value">{{ doc.transporter_name }}</td>
                {% else %}
                <td></td><td></td>
                {% endif %}
            </tr>
            {% endif %}
            {% if doc.lr_no %}
            <tr>
                <td class="exe-meta-label">Tracking / LR No.</td>
                <td class="exe-meta-value">{{ doc.lr_no }}</td>
                <td></td>
                <td></td><td></td>
            </tr>
            {% endif %}
        </table>
    </div>

    <div class="exe-items-section">
        <table class="exe-items-table">
            <thead>
                <tr>
                    <td class="text-center" style="width: 40px;">#</td>
                    <td>Item Code</td>
                    <td>Item Name</td>
                    <td class="text-center">Qty</td>
                    <td class="text-center">UOM</td>
                    <td class="text-center" style="width: 80px;">Received</td>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr>
                    <td class="text-center exe-num">{{ loop.index }}</td>
                    <td class="exe-num" style="font-size: 12px;">{{ item.item_code }}</td>
                    <td>{{ item.item_name }}</td>
                    <td class="text-center exe-num">{{ item.get_formatted("qty", 0) }}</td>
                    <td class="text-center">{{ item.uom or "" }}</td>
                    <td class="text-center" style="width: 80px;">
                        <span style="display: inline-block; width: 18px; height: 18px; border: 2px solid var(--exe-gray-400); border-radius: 3px;"></span>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {% if doc.instructions %}
    <div class="exe-section">
        <h4>Delivery Instructions</h4>
        <div>{{ doc.instructions }}</div>
    </div>
    {% endif %}

    <!-- Signature block -->
    <div class="exe-section" style="margin-top: 40px;">
        <div style="display: flex; justify-content: space-between; gap: 60px;">
            <div style="flex: 1;">
                <p style="border-bottom: 1px solid var(--exe-gray-400); padding-bottom: 30px; margin-bottom: 4px;">&nbsp;</p>
                <p style="font-size: 11px; color: var(--exe-gray-400);">Received by (Name & Signature)</p>
            </div>
            <div style="flex: 0 0 180px;">
                <p style="border-bottom: 1px solid var(--exe-gray-400); padding-bottom: 30px; margin-bottom: 4px;">&nbsp;</p>
                <p style="font-size: 11px; color: var(--exe-gray-400);">Date</p>
            </div>
        </div>
    </div>

    {{ render_footer(doc, loop.index, layout|length) }}
</div>
{% endfor %}
"""
)

# ---------------------------------------------------------------------------
# 5. Sales Order
# ---------------------------------------------------------------------------

SALES_ORDER_HTML = (
    EXE_PRINT_CSS
    + EXE_HEADER_MACRO
    + EXE_COMPANY_BLOCK
    + EXE_TOTALS_BLOCK
    + EXE_FOOTER_BLOCK
    + """
{% for page in layout %}
<div class="page-break exe-print">
    {{ exe_header(doc, letter_head, no_letterhead, print_settings, "Sales Order") }}

    <div class="exe-header-bar">
        {% if doc.company and frappe.db.get_value("Company", doc.company, "company_logo") %}
        <img class="exe-logo" src="{{ frappe.db.get_value('Company', doc.company, 'company_logo') }}" alt="{{ doc.company }}">
        {% else %}
        <span style="color: var(--exe-white); font-family: 'Epilogue', sans-serif; font-weight: 700; font-size: 18px;">{{ doc.company }}</span>
        {% endif %}
        <span class="exe-doc-title">Order Confirmation</span>
    </div>

    <div class="exe-info-row">
        {{ company_info(doc) }}
        <div class="exe-info-block">
            <h4>Customer</h4>
            <p style="font-weight: 600;">{{ doc.customer_name }}</p>
            {% if doc.customer_address %}
                {% set caddr = frappe.db.get_value("Address", doc.customer_address, ["address_line1", "address_line2", "city", "state", "pincode", "country"], as_dict=True) %}
                <p>{{ caddr.address_line1 or "" }}</p>
                {% if caddr.address_line2 %}<p>{{ caddr.address_line2 }}</p>{% endif %}
                <p>{{ caddr.city or "" }}{% if caddr.state %}, {{ caddr.state }}{% endif %} {{ caddr.pincode or "" }}</p>
                <p>{{ caddr.country or "" }}</p>
            {% endif %}
            {% if doc.contact_email %}<p>{{ doc.contact_email }}</p>{% endif %}
        </div>
    </div>

    <div class="exe-meta-grid">
        <table>
            <tr>
                <td class="exe-meta-label">Order No.</td>
                <td class="exe-meta-value">{{ doc.name }}</td>
                <td style="width: 40px;"></td>
                <td class="exe-meta-label">Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.transaction_date) }}</td>
            </tr>
            {% if doc.delivery_date %}
            <tr>
                <td class="exe-meta-label">Delivery Date</td>
                <td class="exe-meta-value">{{ frappe.utils.format_date(doc.delivery_date) }}</td>
                <td></td>
                {% if doc.po_no %}
                <td class="exe-meta-label">Customer PO</td>
                <td class="exe-meta-value">{{ doc.po_no }}</td>
                {% else %}
                <td></td><td></td>
                {% endif %}
            </tr>
            {% endif %}
        </table>
    </div>

    <div class="exe-items-section">
        <table class="exe-items-table">
            <thead>
                <tr>
                    <td class="text-center" style="width: 40px;">#</td>
                    <td>Item</td>
                    <td>Description</td>
                    <td class="text-center">Qty</td>
                    <td class="text-right">Rate</td>
                    <td class="text-right" style="width: 110px;">Amount</td>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr>
                    <td class="text-center exe-num">{{ loop.index }}</td>
                    <td>{{ item.item_name }}</td>
                    <td style="font-size: 12px; color: var(--exe-gray-600);">{{ item.description or "" | truncate(80) }}</td>
                    <td class="text-center exe-num">{{ item.get_formatted("qty", 0) }} {{ item.uom or "" }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("rate", doc) }}</td>
                    <td class="text-right exe-num">{{ item.get_formatted("amount", doc) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {{ render_totals(doc, print_settings) }}

    {% if doc.terms %}
    <div class="exe-section">
        <h4>Terms & Conditions</h4>
        <div>{{ doc.terms }}</div>
    </div>
    {% endif %}

    {{ render_footer(doc, loop.index, layout|length) }}
</div>
{% endfor %}
"""
)

# ---------------------------------------------------------------------------
# Registry: name -> (doc_type, html)
# ---------------------------------------------------------------------------

PRINT_FORMATS = {
    "Exe Sales Invoice": ("Sales Invoice", SALES_INVOICE_HTML),
    "Exe Purchase Order": ("Purchase Order", PURCHASE_ORDER_HTML),
    "Exe Quotation": ("Quotation", QUOTATION_HTML),
    "Exe Delivery Note": ("Delivery Note", DELIVERY_NOTE_HTML),
    "Exe Sales Order": ("Sales Order", SALES_ORDER_HTML),
}


def create_print_formats():
    """Create all Exe print formats. Idempotent -- skips if name exists."""
    for name, (doc_type, html) in PRINT_FORMATS.items():
        if frappe.db.exists("Print Format", name):
            frappe.logger().info(f"Print Format '{name}' already exists, skipping")
            continue

        doc = frappe.get_doc(
            {
                "doctype": "Print Format",
                "name": name,
                "doc_type": doc_type,
                "html": html,
                "print_format_type": "Jinja",
                "print_format_for": "DocType",
                "standard": "No",
                "custom_format": 1,
                "default_print_language": "en",
                "font_size": 13,
                "margin_top": 10.0,
                "margin_bottom": 10.0,
                "margin_left": 10.0,
                "margin_right": 10.0,
            }
        )
        doc.insert(ignore_permissions=True)
        frappe.logger().info(f"Created Print Format: {name}")
