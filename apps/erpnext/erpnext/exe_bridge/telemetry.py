"""
Exe Bridge — Structured telemetry event emission.

Beyond doc events (which capture document lifecycle) and request traces
(which capture HTTP request lifecycle), this module provides structured
telemetry for system health, business metrics, and operational events.

These are emitted on a schedule (via Frappe scheduler_events) and on
critical system events.

Telemetry events go to raw.raw_events with source="telemetry" for
unified cross-service visibility in exe-monitor and Company Brain.

Scheduler integration (hooks.py):
    scheduler_events = {
        "cron": {
            "*/5 * * * *": ["erpnext.exe_bridge.telemetry.emit_health_snapshot"],
        },
        "daily": ["erpnext.exe_bridge.telemetry.emit_daily_summary"],
    }
"""

import json
import logging
import uuid
from datetime import datetime, timezone

import frappe

from erpnext.exe_bridge.connection import get_connection

logger = logging.getLogger("exe_bridge.telemetry")


def _emit_telemetry(event_type, payload, metadata=None):
    """Emit a structured telemetry event to raw.raw_events.

    Args:
        event_type: e.g. "erp.health_snapshot", "erp.daily_summary"
        payload: dict with event data
        metadata: optional dict with context
    """
    conn = get_connection()
    if conn is None:
        return

    try:
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        meta = {
            "service": "exe-erp",
            "site": frappe.local.site if hasattr(frappe, "local") else "unknown",
            **(metadata or {}),
        }

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO raw.raw_events (id, source, source_id, event_type, payload, metadata, timestamp)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            """,
            (
                event_id,
                "telemetry",
                f"erp-{event_type}-{now.strftime('%Y%m%d%H%M')}",
                event_type,
                json.dumps(payload, default=str),
                json.dumps(meta, default=str),
                now,
            ),
        )
        cursor.close()

    except Exception as e:
        logger.debug(f"exe_bridge.telemetry: emit failed — {e}")


def emit_health_snapshot():
    """Emit a health snapshot every 5 minutes (scheduled task).

    Captures system-level metrics that exe-monitor can aggregate
    and alert on. Complements the Prometheus /metrics endpoint
    with time-series data stored in the landing pad.
    """
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Active users
        try:
            active = frappe.db.sql(
                """SELECT COUNT(DISTINCT "user") FROM "tabSessions"
                   WHERE "lastupdate" > NOW() - INTERVAL '30 minutes'
                   AND "user" != 'Guest'""",
                as_list=True,
            )[0][0] or 0
            payload["active_users"] = active
        except Exception:
            payload["active_users"] = -1

        # Error count (last 5 min)
        try:
            errors = frappe.db.sql(
                """SELECT COUNT(*) FROM "tabError Log"
                   WHERE creation > NOW() - INTERVAL '5 minutes'""",
                as_list=True,
            )[0][0] or 0
            payload["errors_5min"] = errors
        except Exception:
            payload["errors_5min"] = -1

        # Queue depths
        try:
            import redis

            redis_url = frappe.conf.get("redis_queue", "redis://localhost:6379/4")
            r = redis.from_url(redis_url, socket_timeout=2)
            queues = {}
            for q in ["default", "short", "long"]:
                queues[q] = r.llen(f"rq:queue:{q}") or 0
            payload["queue_depths"] = queues
        except Exception:
            payload["queue_depths"] = {}

        # Database size
        try:
            db_size = frappe.db.sql(
                "SELECT pg_database_size(current_database())",
                as_list=True,
            )[0][0] or 0
            payload["db_size_bytes"] = db_size
        except Exception:
            payload["db_size_bytes"] = -1

        # Database connections
        try:
            conns = frappe.db.sql(
                """SELECT count(*) FROM pg_stat_activity
                   WHERE datname = current_database()""",
                as_list=True,
            )[0][0] or 0
            payload["db_connections"] = conns
        except Exception:
            payload["db_connections"] = -1

        _emit_telemetry("erp.health_snapshot", payload)

    except Exception as e:
        logger.warning(f"emit_health_snapshot failed: {e}")


def emit_daily_summary():
    """Emit a daily business summary (scheduled daily task).

    Captures key business KPIs for trend analysis and executive dashboards.
    This data feeds into erp.financial_snapshot via projection workers.
    """
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")

        payload = {
            "date": today,
        }

        # Revenue this month
        try:
            revenue = frappe.db.sql(
                """SELECT COALESCE(SUM(grand_total), 0)
                   FROM "tabSales Invoice"
                   WHERE docstatus = 1 AND posting_date >= %s""",
                (month_start,),
                as_list=True,
            )[0][0] or 0
            payload["revenue_mtd"] = float(revenue)
        except Exception:
            payload["revenue_mtd"] = -1

        # New customers this month
        try:
            new_customers = frappe.db.count(
                "Customer",
                {"creation": (">=", month_start)},
            )
            payload["new_customers_mtd"] = new_customers
        except Exception:
            payload["new_customers_mtd"] = -1

        # Outstanding receivables
        try:
            ar = frappe.db.sql(
                """SELECT COALESCE(SUM(outstanding_amount), 0)
                   FROM "tabSales Invoice"
                   WHERE docstatus = 1 AND outstanding_amount > 0""",
                as_list=True,
            )[0][0] or 0
            payload["accounts_receivable"] = float(ar)
        except Exception:
            payload["accounts_receivable"] = -1

        # Outstanding payables
        try:
            ap = frappe.db.sql(
                """SELECT COALESCE(SUM(outstanding_amount), 0)
                   FROM "tabPurchase Invoice"
                   WHERE docstatus = 1 AND outstanding_amount > 0""",
                as_list=True,
            )[0][0] or 0
            payload["accounts_payable"] = float(ap)
        except Exception:
            payload["accounts_payable"] = -1

        # Open orders
        try:
            open_so = frappe.db.count(
                "Sales Order",
                {"docstatus": 1, "status": ("not in", ["Completed", "Cancelled", "Closed"])},
            )
            payload["open_sales_orders"] = open_so
        except Exception:
            payload["open_sales_orders"] = -1

        try:
            open_po = frappe.db.count(
                "Purchase Order",
                {"docstatus": 1, "status": ("not in", ["Completed", "Cancelled", "Closed"])},
            )
            payload["open_purchase_orders"] = open_po
        except Exception:
            payload["open_purchase_orders"] = -1

        # Today's sales orders
        try:
            today_orders = frappe.db.count(
                "Sales Order",
                {"docstatus": 1, "transaction_date": today},
            )
            payload["sales_orders_today"] = today_orders
        except Exception:
            payload["sales_orders_today"] = -1

        # Error count today
        try:
            today_errors = frappe.db.sql(
                """SELECT COUNT(*) FROM "tabError Log"
                   WHERE creation >= %s""",
                (today,),
                as_list=True,
            )[0][0] or 0
            payload["errors_today"] = today_errors
        except Exception:
            payload["errors_today"] = -1

        _emit_telemetry("erp.daily_summary", payload)

    except Exception as e:
        logger.warning(f"emit_daily_summary failed: {e}")


def emit_custom_event(event_name, data, severity="info"):
    """Emit a custom telemetry event (for use by other modules).

    Args:
        event_name: e.g. "payment.gateway_error", "stock.reorder_triggered"
        data: dict with event-specific data
        severity: "info", "warning", "error", "critical"
    """
    _emit_telemetry(
        f"erp.custom.{event_name}",
        {"severity": severity, **data},
        metadata={"severity": severity},
    )
