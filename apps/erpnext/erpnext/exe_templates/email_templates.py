"""
Exe ERP -- Professional Email Templates (Exe Foundry Bold design system).

Creates Frappe Email Template records for:
  - Order Confirmation (Sales Order submit)
  - Invoice Notification (Sales Invoice submit)
  - Payment Receipt (Payment Entry)
  - Purchase Order to Supplier (PO submit)
  - Welcome Email (new user onboarding)

All templates use inline CSS for maximum email client compatibility.
"""

import frappe

# ---------------------------------------------------------------------------
# Shared email wrapper -- inline CSS for broad client support
# ---------------------------------------------------------------------------

EMAIL_WRAPPER_START = """\
<div style="max-width: 640px; margin: 0 auto; font-family: -apple-system, 'Helvetica Neue', Arial, sans-serif; color: #2D2D35; line-height: 1.6; font-size: 14px;">
    <!-- Header -->
    <div style="background-color: #0F0E1A; padding: 24px 32px; text-align: center;">
        <span style="font-size: 20px; font-weight: 700; color: #F5D76E; letter-spacing: 1.5px; text-transform: uppercase;">
            {{ doc.company }}
        </span>
    </div>
    <!-- Body -->
    <div style="padding: 32px; background-color: #FFFFFF; border-left: 1px solid #EDEDEF; border-right: 1px solid #EDEDEF;">
"""

EMAIL_WRAPPER_END = """\
    </div>
    <!-- Footer -->
    <div style="background-color: #F7F7F8; padding: 20px 32px; text-align: center; font-size: 12px; color: #9B9BA5; border: 1px solid #EDEDEF; border-top: none;">
        <p style="margin: 0;">{{ doc.company }}</p>
        <p style="margin: 4px 0 0;">This is an automated message. Please do not reply directly to this email.</p>
    </div>
</div>
"""

# ---------------------------------------------------------------------------
# Shared: items summary table (reused in order confirmation, invoice)
# ---------------------------------------------------------------------------

ITEMS_TABLE_SNIPPET = """\
        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <thead>
                <tr style="background-color: #0F0E1A;">
                    <th style="padding: 10px 12px; text-align: left; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Item</th>
                    <th style="padding: 10px 12px; text-align: center; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Qty</th>
                    <th style="padding: 10px 12px; text-align: right; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Amount</th>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr style="border-bottom: 1px solid #EDEDEF;">
                    <td style="padding: 10px 12px;">{{ item.item_name }}</td>
                    <td style="padding: 10px 12px; text-align: center;">{{ item.qty }} {{ item.uom or '' }}</td>
                    <td style="padding: 10px 12px; text-align: right; font-weight: 500;">{{ item.get_formatted("amount", doc) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 8px 12px; text-align: right; color: #6B6B76;">Subtotal</td>
                <td style="padding: 8px 12px; text-align: right; width: 120px; font-weight: 500;">{{ doc.get_formatted("total", doc) }}</td>
            </tr>
            {% for tax in doc.taxes %}
            {% if tax.tax_amount %}
            <tr>
                <td style="padding: 4px 12px; text-align: right; color: #6B6B76;">{{ tax.description }}</td>
                <td style="padding: 4px 12px; text-align: right; width: 120px;">{{ tax.get_formatted("tax_amount") }}</td>
            </tr>
            {% endif %}
            {% endfor %}
            <tr style="border-top: 2px solid #0F0E1A;">
                <td style="padding: 12px 12px 8px; text-align: right; font-weight: 700; font-size: 15px;">Grand Total</td>
                <td style="padding: 12px 12px 8px; text-align: right; width: 120px; font-weight: 700; font-size: 15px;">{{ doc.get_formatted("grand_total", doc) }}</td>
            </tr>
        </table>
"""

# ---------------------------------------------------------------------------
# 1. Order Confirmation
# ---------------------------------------------------------------------------

ORDER_CONFIRMATION_SUBJECT = "Order Confirmation - {{ doc.name }}"

ORDER_CONFIRMATION_BODY = (
    EMAIL_WRAPPER_START
    + """\
        <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 700; color: #0F0E1A;">
            Order Confirmed
        </h2>
        <p style="margin: 0 0 20px; color: #6B6B76;">
            Thank you for your order. Here are the details of your purchase.
        </p>

        <!-- Order meta -->
        <table style="width: 100%; margin-bottom: 20px;">
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5; width: 140px;">Order No.</td>
                <td style="padding: 4px 0; font-weight: 600;">{{ doc.name }}</td>
            </tr>
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">Order Date</td>
                <td style="padding: 4px 0;">{{ frappe.utils.format_date(doc.transaction_date) }}</td>
            </tr>
            {% if doc.delivery_date %}
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">Expected Delivery</td>
                <td style="padding: 4px 0;">{{ frappe.utils.format_date(doc.delivery_date) }}</td>
            </tr>
            {% endif %}
        </table>

        <!-- Items -->
"""
    + ITEMS_TABLE_SNIPPET
    + """\

        {% if doc.terms %}
        <div style="margin-top: 24px; padding: 16px; background: #F7F7F8; border-radius: 6px; font-size: 13px; color: #6B6B76;">
            <strong style="color: #2D2D35;">Terms & Conditions</strong><br>
            {{ doc.terms }}
        </div>
        {% endif %}

        <p style="margin-top: 24px;">
            If you have questions about this order, please reply to this email or contact us directly.
        </p>
"""
    + EMAIL_WRAPPER_END
)

# ---------------------------------------------------------------------------
# 2. Invoice Notification
# ---------------------------------------------------------------------------

INVOICE_NOTIFICATION_SUBJECT = "Invoice {{ doc.name }} from {{ doc.company }}"

INVOICE_NOTIFICATION_BODY = (
    EMAIL_WRAPPER_START
    + """\
        <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 700; color: #0F0E1A;">
            Invoice
        </h2>
        <p style="margin: 0 0 20px; color: #6B6B76;">
            Please find the details of your invoice below.
        </p>

        <table style="width: 100%; margin-bottom: 20px;">
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5; width: 140px;">Invoice No.</td>
                <td style="padding: 4px 0; font-weight: 600;">{{ doc.name }}</td>
            </tr>
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">Invoice Date</td>
                <td style="padding: 4px 0;">{{ frappe.utils.format_date(doc.posting_date) }}</td>
            </tr>
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">Due Date</td>
                <td style="padding: 4px 0; font-weight: 600; color: #0F0E1A;">{{ frappe.utils.format_date(doc.due_date) }}</td>
            </tr>
            {% if doc.po_no %}
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">PO Reference</td>
                <td style="padding: 4px 0;">{{ doc.po_no }}</td>
            </tr>
            {% endif %}
        </table>

"""
    + ITEMS_TABLE_SNIPPET
    + """\

        <!-- Payment info -->
        <div style="margin-top: 24px; padding: 20px; background: #0F0E1A; border-radius: 6px; color: #FFFFFF;">
            <p style="margin: 0 0 4px; font-size: 13px; color: #F5D76E; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">
                Amount Due
            </p>
            <p style="margin: 0; font-size: 24px; font-weight: 700;">
                {{ doc.get_formatted("outstanding_amount", doc) }}
            </p>
            <p style="margin: 8px 0 0; font-size: 12px; color: #9B9BA5;">
                Due by {{ frappe.utils.format_date(doc.due_date) }}
            </p>
        </div>

        <p style="margin-top: 24px;">
            A PDF copy of this invoice is attached. For payment inquiries, please contact us.
        </p>
"""
    + EMAIL_WRAPPER_END
)

# ---------------------------------------------------------------------------
# 3. Payment Receipt
# ---------------------------------------------------------------------------

PAYMENT_RECEIPT_SUBJECT = "Payment Receipt - {{ doc.name }}"

PAYMENT_RECEIPT_BODY = (
    EMAIL_WRAPPER_START
    + """\
        <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 700; color: #0F0E1A;">
            Payment Received
        </h2>
        <p style="margin: 0 0 20px; color: #6B6B76;">
            We have received your payment. Thank you.
        </p>

        <div style="padding: 24px; background: #F7F7F8; border-radius: 6px; margin-bottom: 20px;">
            <table style="width: 100%;">
                <tr>
                    <td style="padding: 6px 0; color: #9B9BA5; width: 160px;">Receipt No.</td>
                    <td style="padding: 6px 0; font-weight: 600;">{{ doc.name }}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9B9BA5;">Payment Date</td>
                    <td style="padding: 6px 0;">{{ frappe.utils.format_date(doc.posting_date) }}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9B9BA5;">Payment Mode</td>
                    <td style="padding: 6px 0;">{{ doc.mode_of_payment or "N/A" }}</td>
                </tr>
                {% if doc.reference_no %}
                <tr>
                    <td style="padding: 6px 0; color: #9B9BA5;">Reference No.</td>
                    <td style="padding: 6px 0;">{{ doc.reference_no }}</td>
                </tr>
                {% endif %}
            </table>
        </div>

        <div style="text-align: center; padding: 20px; background: #0F0E1A; border-radius: 6px; color: #FFFFFF;">
            <p style="margin: 0 0 4px; font-size: 13px; color: #F5D76E; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">
                Amount Paid
            </p>
            <p style="margin: 0; font-size: 28px; font-weight: 700;">
                {{ doc.get_formatted("paid_amount", doc) }}
            </p>
        </div>

        {% if doc.references %}
        <div style="margin-top: 20px;">
            <p style="font-weight: 600; margin-bottom: 8px;">Applied to:</p>
            <table style="width: 100%; border-collapse: collapse;">
                {% for ref in doc.references %}
                <tr style="border-bottom: 1px solid #EDEDEF;">
                    <td style="padding: 8px 0;">{{ ref.reference_doctype }} {{ ref.reference_name }}</td>
                    <td style="padding: 8px 0; text-align: right; font-weight: 500;">{{ ref.get_formatted("allocated_amount", doc) }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        {% endif %}

        <p style="margin-top: 24px;">
            This receipt confirms your payment has been recorded. Please retain for your records.
        </p>
"""
    + EMAIL_WRAPPER_END
)

# ---------------------------------------------------------------------------
# 4. Purchase Order to Supplier
# ---------------------------------------------------------------------------

PO_TO_SUPPLIER_SUBJECT = "Purchase Order {{ doc.name }} from {{ doc.company }}"

PO_TO_SUPPLIER_BODY = (
    EMAIL_WRAPPER_START
    + """\
        <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 700; color: #0F0E1A;">
            Purchase Order
        </h2>
        <p style="margin: 0 0 20px; color: #6B6B76;">
            Please find our purchase order details below. A PDF copy is attached.
        </p>

        <table style="width: 100%; margin-bottom: 20px;">
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5; width: 140px;">PO No.</td>
                <td style="padding: 4px 0; font-weight: 600;">{{ doc.name }}</td>
            </tr>
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">Date</td>
                <td style="padding: 4px 0;">{{ frappe.utils.format_date(doc.transaction_date) }}</td>
            </tr>
            {% if doc.schedule_date %}
            <tr>
                <td style="padding: 4px 0; color: #9B9BA5;">Required By</td>
                <td style="padding: 4px 0; font-weight: 600;">{{ frappe.utils.format_date(doc.schedule_date) }}</td>
            </tr>
            {% endif %}
        </table>

        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <thead>
                <tr style="background-color: #0F0E1A;">
                    <th style="padding: 10px 12px; text-align: left; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Item</th>
                    <th style="padding: 10px 12px; text-align: center; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Qty</th>
                    <th style="padding: 10px 12px; text-align: right; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Rate</th>
                    <th style="padding: 10px 12px; text-align: right; color: #F5D76E; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Amount</th>
                </tr>
            </thead>
            <tbody>
                {% for item in doc.items %}
                <tr style="border-bottom: 1px solid #EDEDEF;">
                    <td style="padding: 10px 12px;">{{ item.item_name }}</td>
                    <td style="padding: 10px 12px; text-align: center;">{{ item.qty }} {{ item.uom or '' }}</td>
                    <td style="padding: 10px 12px; text-align: right;">{{ item.get_formatted("rate", doc) }}</td>
                    <td style="padding: 10px 12px; text-align: right; font-weight: 500;">{{ item.get_formatted("amount", doc) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>

        <table style="width: 100%; border-collapse: collapse;">
            <tr style="border-top: 2px solid #0F0E1A;">
                <td style="padding: 12px 12px 8px; text-align: right; font-weight: 700; font-size: 15px;">Total</td>
                <td style="padding: 12px 12px 8px; text-align: right; width: 120px; font-weight: 700; font-size: 15px;">{{ doc.get_formatted("grand_total", doc) }}</td>
            </tr>
        </table>

        {% if doc.terms %}
        <div style="margin-top: 24px; padding: 16px; background: #F7F7F8; border-radius: 6px; font-size: 13px; color: #6B6B76;">
            <strong style="color: #2D2D35;">Terms & Conditions</strong><br>
            {{ doc.terms }}
        </div>
        {% endif %}

        <p style="margin-top: 24px;">
            Please confirm receipt of this order and advise on delivery schedule.
        </p>
"""
    + EMAIL_WRAPPER_END
)

# ---------------------------------------------------------------------------
# 5. Welcome Email
# ---------------------------------------------------------------------------

WELCOME_EMAIL_SUBJECT = "Welcome to {{ doc.company }}"

WELCOME_EMAIL_BODY = (
    EMAIL_WRAPPER_START
    + """\
        <h2 style="margin: 0 0 8px; font-size: 22px; font-weight: 700; color: #0F0E1A;">
            Welcome Aboard
        </h2>
        <p style="margin: 0 0 20px; color: #6B6B76;">
            Your account has been created. Here is everything you need to get started.
        </p>

        <div style="padding: 24px; background: #F7F7F8; border-radius: 6px; margin-bottom: 20px;">
            <table style="width: 100%;">
                <tr>
                    <td style="padding: 6px 0; color: #9B9BA5; width: 120px;">Full Name</td>
                    <td style="padding: 6px 0; font-weight: 600;">{{ doc.full_name }}</td>
                </tr>
                <tr>
                    <td style="padding: 6px 0; color: #9B9BA5;">Email</td>
                    <td style="padding: 6px 0;">{{ doc.name }}</td>
                </tr>
            </table>
        </div>

        <div style="text-align: center; margin: 28px 0;">
            <a href="{{ frappe.utils.get_url() }}"
               style="display: inline-block; background: #0F0E1A; color: #F5D76E; padding: 14px 40px; text-decoration: none; border-radius: 6px; font-weight: 700; font-size: 14px; letter-spacing: 0.5px; text-transform: uppercase;">
                Log In to Your Account
            </a>
        </div>

        <p style="color: #6B6B76; font-size: 13px;">
            If you did not request this account, please disregard this email.
        </p>
"""
    + EMAIL_WRAPPER_END
)

# ---------------------------------------------------------------------------
# Registry: (name, subject, response, use_html, doctype reference)
# ---------------------------------------------------------------------------

EMAIL_TEMPLATES = [
    {
        "name": "Exe Order Confirmation",
        "subject": ORDER_CONFIRMATION_SUBJECT,
        "response": ORDER_CONFIRMATION_BODY,
    },
    {
        "name": "Exe Invoice Notification",
        "subject": INVOICE_NOTIFICATION_SUBJECT,
        "response": INVOICE_NOTIFICATION_BODY,
    },
    {
        "name": "Exe Payment Receipt",
        "subject": PAYMENT_RECEIPT_SUBJECT,
        "response": PAYMENT_RECEIPT_BODY,
    },
    {
        "name": "Exe Purchase Order to Supplier",
        "subject": PO_TO_SUPPLIER_SUBJECT,
        "response": PO_TO_SUPPLIER_BODY,
    },
    {
        "name": "Exe Welcome Email",
        "subject": WELCOME_EMAIL_SUBJECT,
        "response": WELCOME_EMAIL_BODY,
    },
]


def create_email_templates():
    """Create all Exe email templates. Idempotent -- skips if name exists."""
    for tmpl in EMAIL_TEMPLATES:
        if frappe.db.exists("Email Template", tmpl["name"]):
            frappe.logger().info(f"Email Template '{tmpl['name']}' already exists, skipping")
            continue

        doc = frappe.get_doc(
            {
                "doctype": "Email Template",
                "name": tmpl["name"],
                "subject": tmpl["subject"],
                "response": tmpl["response"],
                "use_html": 1,
            }
        )
        doc.insert(ignore_permissions=True)
        frappe.logger().info(f"Created Email Template: {tmpl['name']}")
