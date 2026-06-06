"""
Exe ERP -- Approval Workflows

Three opinionated workflows for a 500-person company:

1. Purchase Order Approval
   Draft -> Pending Approval (auto when grand_total > 5000)
   Pending Approval -> Approved (Purchase Manager if < 20000)
   Pending Approval -> CFO Review (auto if >= 20000)
   CFO Review -> Approved (Accounts Manager)
   Any state -> Rejected

2. Sales Discount Approval
   Applied -> Pending Approval (auto when discount > 15%)
   Pending Approval -> Approved (Sales Manager if discount <= 30%)
   Pending Approval -> Executive Review (auto if discount > 30%)
   Executive Review -> Approved (by any Executive-profile user)
   Any state -> Rejected

3. Expense Claim Approval
   Draft -> Pending Manager -> Pending Finance -> Approved
   Linear three-step chain with Rejected exits at each stage.

All workflows are idempotent: re-running replaces the active workflow
with the canonical definition.
"""

import frappe

logger = frappe.logger("exe_setup.workflows")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_workflows() -> None:
    """Create or replace all Exe ERP approval workflows."""
    _create_purchase_order_workflow()
    _create_sales_discount_workflow()
    _create_expense_claim_workflow()


# ---------------------------------------------------------------------------
# 1. Purchase Order Approval
# ---------------------------------------------------------------------------

def _create_purchase_order_workflow() -> None:
    """Purchase Order approval: amount-tiered with CFO escalation."""
    name = "Exe PO Approval"

    try:
        _delete_existing_workflow(name)

        doc = frappe.get_doc(
            {
                "doctype": "Workflow",
                "workflow_name": name,
                "document_type": "Purchase Order",
                "workflow_state_field": "workflow_state",
                "is_active": 1,
                "send_email_alert": 1,
            }
        )

        # -- States ----------------------------------------------------------
        doc.append("states", dict(
            state="Draft",
            allow_edit="All",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Pending Approval",
            allow_edit="Purchase Manager",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="CFO Review",
            allow_edit="Accounts Manager",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Approved",
            allow_edit="Purchase Manager",
            doc_status=1,
        ))
        doc.append("states", dict(
            state="Rejected",
            allow_edit="All",
            doc_status=1,
        ))

        # -- Transitions ------------------------------------------------------
        # Draft -> Pending Approval (submitter sends for approval when > 5000)
        doc.append("transitions", dict(
            state="Draft",
            action="Request Approval",
            next_state="Pending Approval",
            allowed="Purchase User",
            allow_self_approval=0,
            condition="doc.grand_total > 5000",
        ))
        # Draft -> Approved (small POs skip approval)
        doc.append("transitions", dict(
            state="Draft",
            action="Submit",
            next_state="Approved",
            allowed="Purchase Manager",
            allow_self_approval=1,
            condition="doc.grand_total <= 5000",
        ))
        # Pending Approval -> Approved (Purchase Manager, under 20k)
        doc.append("transitions", dict(
            state="Pending Approval",
            action="Approve",
            next_state="Approved",
            allowed="Purchase Manager",
            allow_self_approval=0,
            condition="doc.grand_total < 20000",
        ))
        # Pending Approval -> CFO Review (auto-escalate >= 20k)
        doc.append("transitions", dict(
            state="Pending Approval",
            action="Escalate to CFO",
            next_state="CFO Review",
            allowed="Purchase Manager",
            allow_self_approval=1,
            condition="doc.grand_total >= 20000",
        ))
        # CFO Review -> Approved
        doc.append("transitions", dict(
            state="CFO Review",
            action="Approve",
            next_state="Approved",
            allowed="Accounts Manager",
            allow_self_approval=0,
        ))
        # Reject from any pending state
        for src in ("Pending Approval", "CFO Review"):
            doc.append("transitions", dict(
                state=src,
                action="Reject",
                next_state="Rejected",
                allowed="Purchase Manager",
                allow_self_approval=1,
            ))
        doc.append("transitions", dict(
            state="CFO Review",
            action="Reject",
            next_state="Rejected",
            allowed="Accounts Manager",
            allow_self_approval=1,
        ))

        doc.insert(ignore_permissions=True)
        logger.info("Created workflow: %s", name)

    except Exception:
        logger.exception("Failed to create workflow: %s", name)


# ---------------------------------------------------------------------------
# 2. Sales Discount Approval
# ---------------------------------------------------------------------------

def _create_sales_discount_workflow() -> None:
    """Quotation discount approval: tiered by discount percentage."""
    name = "Exe Sales Discount Approval"

    try:
        _delete_existing_workflow(name)

        doc = frappe.get_doc(
            {
                "doctype": "Workflow",
                "workflow_name": name,
                "document_type": "Quotation",
                "workflow_state_field": "workflow_state",
                "is_active": 1,
                "send_email_alert": 1,
            }
        )

        # -- States ----------------------------------------------------------
        doc.append("states", dict(
            state="Draft",
            allow_edit="All",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Pending Approval",
            allow_edit="Sales Manager",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Executive Review",
            allow_edit="Accounts User",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Approved",
            allow_edit="Sales Manager",
            doc_status=1,
        ))
        doc.append("states", dict(
            state="Rejected",
            allow_edit="All",
            doc_status=1,
        ))

        # -- Transitions ------------------------------------------------------
        # No significant discount -- direct submit
        doc.append("transitions", dict(
            state="Draft",
            action="Submit",
            next_state="Approved",
            allowed="Sales User",
            allow_self_approval=1,
            condition="doc.additional_discount_percentage <= 15",
        ))
        # Discount > 15% needs Sales Manager
        doc.append("transitions", dict(
            state="Draft",
            action="Request Approval",
            next_state="Pending Approval",
            allowed="Sales User",
            allow_self_approval=0,
            condition="doc.additional_discount_percentage > 15",
        ))
        # Sales Manager approves <= 30%
        doc.append("transitions", dict(
            state="Pending Approval",
            action="Approve",
            next_state="Approved",
            allowed="Sales Manager",
            allow_self_approval=0,
            condition="doc.additional_discount_percentage <= 30",
        ))
        # Sales Manager escalates > 30%
        doc.append("transitions", dict(
            state="Pending Approval",
            action="Escalate to Executive",
            next_state="Executive Review",
            allowed="Sales Manager",
            allow_self_approval=1,
            condition="doc.additional_discount_percentage > 30",
        ))
        # Executive approves
        doc.append("transitions", dict(
            state="Executive Review",
            action="Approve",
            next_state="Approved",
            allowed="Accounts User",  # Executive profile has Accounts User
            allow_self_approval=0,
        ))
        # Reject from any pending state
        for src in ("Pending Approval", "Executive Review"):
            doc.append("transitions", dict(
                state=src,
                action="Reject",
                next_state="Rejected",
                allowed="Sales Manager",
                allow_self_approval=1,
            ))
        doc.append("transitions", dict(
            state="Executive Review",
            action="Reject",
            next_state="Rejected",
            allowed="Accounts User",
            allow_self_approval=1,
        ))

        doc.insert(ignore_permissions=True)
        logger.info("Created workflow: %s", name)

    except Exception:
        logger.exception("Failed to create workflow: %s", name)


# ---------------------------------------------------------------------------
# 3. Expense Claim Approval
# ---------------------------------------------------------------------------

def _create_expense_claim_workflow() -> None:
    """Expense Claim: Draft -> Manager -> Finance -> Approved."""
    name = "Exe Expense Claim Approval"

    try:
        _delete_existing_workflow(name)

        doc = frappe.get_doc(
            {
                "doctype": "Workflow",
                "workflow_name": name,
                "document_type": "Expense Claim",
                "workflow_state_field": "workflow_state",
                "is_active": 1,
                "send_email_alert": 1,
            }
        )

        # -- States ----------------------------------------------------------
        doc.append("states", dict(
            state="Draft",
            allow_edit="All",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Pending Manager",
            allow_edit="HR User",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Pending Finance",
            allow_edit="Accounts User",
            doc_status=0,
        ))
        doc.append("states", dict(
            state="Approved",
            allow_edit="Accounts Manager",
            doc_status=1,
        ))
        doc.append("states", dict(
            state="Rejected",
            allow_edit="All",
            doc_status=1,
        ))

        # -- Transitions ------------------------------------------------------
        # Employee submits claim
        doc.append("transitions", dict(
            state="Draft",
            action="Submit for Approval",
            next_state="Pending Manager",
            allowed="HR User",
            allow_self_approval=0,
        ))
        # Manager approves -> finance
        doc.append("transitions", dict(
            state="Pending Manager",
            action="Approve",
            next_state="Pending Finance",
            allowed="HR Manager",
            allow_self_approval=0,
        ))
        # Manager rejects
        doc.append("transitions", dict(
            state="Pending Manager",
            action="Reject",
            next_state="Rejected",
            allowed="HR Manager",
            allow_self_approval=1,
        ))
        # Finance approves -> done
        doc.append("transitions", dict(
            state="Pending Finance",
            action="Approve",
            next_state="Approved",
            allowed="Accounts Manager",
            allow_self_approval=0,
        ))
        # Finance rejects
        doc.append("transitions", dict(
            state="Pending Finance",
            action="Reject",
            next_state="Rejected",
            allowed="Accounts Manager",
            allow_self_approval=1,
        ))

        doc.insert(ignore_permissions=True)
        logger.info("Created workflow: %s", name)

    except Exception:
        logger.exception("Failed to create workflow: %s", name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _delete_existing_workflow(name: str) -> None:
    """Delete a workflow by name if it exists, so we can recreate it cleanly."""
    if frappe.db.exists("Workflow", name):
        frappe.delete_doc("Workflow", name, ignore_permissions=True, force=True)
        logger.info("Deleted existing workflow for replacement: %s", name)
