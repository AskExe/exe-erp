"""
Exe Bridge — Prometheus-compatible /metrics endpoint for exe-monitor.

Exposes key ERP health and business metrics in Prometheus exposition format.
Scraped by exe-monitor (Beszel hub) for dashboards and alerting.

Endpoint: GET /api/method/erpnext.exe_bridge.metrics.get_metrics
"""

import frappe
from datetime import datetime, timedelta


@frappe.whitelist(allow_guest=False)
def get_metrics():
	"""Return Prometheus-compatible metrics text.

	Requires authentication (admin_token or session).
	Returns Content-Type: text/plain with Prometheus exposition format.
	"""
	lines = []

	def gauge(name, value, help_text, labels=None):
		lines.append(f"# HELP {name} {help_text}")
		lines.append(f"# TYPE {name} gauge")
		label_str = ""
		if labels:
			label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
		lines.append(f"{name}{label_str} {value}")

	def counter(name, value, help_text, labels=None):
		lines.append(f"# HELP {name} {help_text}")
		lines.append(f"# TYPE {name} counter")
		label_str = ""
		if labels:
			label_str = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
		lines.append(f"{name}{label_str} {value}")

	try:
		# ── System metrics ────────────────────────────────────
		# Active users (sessions in last 30 min)
		active_users = frappe.db.sql(
			"""SELECT COUNT(DISTINCT "user") FROM "tabSessions"
			   WHERE "lastupdate" > NOW() - INTERVAL '30 minutes'
			   AND "user" != 'Guest'""",
			as_list=True,
		)[0][0] or 0
		gauge("erp_active_users", active_users, "Active user sessions in last 30 minutes")

		# Total users
		total_users = frappe.db.count("User", {"enabled": 1, "user_type": "System User"})
		gauge("erp_total_users", total_users, "Total enabled system users")

		# ── Business metrics ──────────────────────────────────
		today = datetime.now().strftime("%Y-%m-%d")
		month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")

		# Sales orders (this month)
		try:
			monthly_orders = frappe.db.count(
				"Sales Order",
				{"docstatus": 1, "transaction_date": (">=", month_start)},
			)
			gauge("erp_sales_orders_month", monthly_orders, "Submitted sales orders this month")
		except Exception:
			pass

		# Sales invoices (this month)
		try:
			monthly_invoices = frappe.db.count(
				"Sales Invoice",
				{"docstatus": 1, "posting_date": (">=", month_start)},
			)
			gauge("erp_sales_invoices_month", monthly_invoices, "Posted sales invoices this month")
		except Exception:
			pass

		# Revenue this month
		try:
			revenue = frappe.db.sql(
				"""SELECT COALESCE(SUM(grand_total), 0)
				   FROM "tabSales Invoice"
				   WHERE docstatus = 1 AND posting_date >= %s""",
				(month_start,),
				as_list=True,
			)[0][0] or 0
			gauge("erp_revenue_month", float(revenue), "Total revenue this month (base currency)")
		except Exception:
			pass

		# Open sales orders
		try:
			open_so = frappe.db.count(
				"Sales Order",
				{"docstatus": 1, "status": ("not in", ["Completed", "Cancelled", "Closed"])},
			)
			gauge("erp_open_sales_orders", open_so, "Open sales orders")
		except Exception:
			pass

		# Open purchase orders
		try:
			open_po = frappe.db.count(
				"Purchase Order",
				{"docstatus": 1, "status": ("not in", ["Completed", "Cancelled", "Closed"])},
			)
			gauge("erp_open_purchase_orders", open_po, "Open purchase orders")
		except Exception:
			pass

		# Outstanding receivables
		try:
			receivables = frappe.db.sql(
				"""SELECT COALESCE(SUM(outstanding_amount), 0)
				   FROM "tabSales Invoice"
				   WHERE docstatus = 1 AND outstanding_amount > 0""",
				as_list=True,
			)[0][0] or 0
			gauge("erp_accounts_receivable", float(receivables), "Total outstanding receivables")
		except Exception:
			pass

		# Outstanding payables
		try:
			payables = frappe.db.sql(
				"""SELECT COALESCE(SUM(outstanding_amount), 0)
				   FROM "tabPurchase Invoice"
				   WHERE docstatus = 1 AND outstanding_amount > 0""",
				as_list=True,
			)[0][0] or 0
			gauge("erp_accounts_payable", float(payables), "Total outstanding payables")
		except Exception:
			pass

		# Pending material requests
		try:
			pending_mr = frappe.db.count(
				"Material Request",
				{"docstatus": 1, "status": ("in", ["Pending", "Partially Ordered"])},
			)
			gauge("erp_pending_material_requests", pending_mr, "Pending material requests")
		except Exception:
			pass

		# ── Error metrics ─────────────────────────────────────
		try:
			recent_errors = frappe.db.sql(
				"""SELECT COUNT(*) FROM "tabError Log"
				   WHERE creation > NOW() - INTERVAL '1 hour'""",
				as_list=True,
			)[0][0] or 0
			gauge("erp_errors_last_hour", recent_errors, "Error log entries in last hour")
		except Exception:
			pass

		# ── Queue metrics ─────────────────────────────────────
		try:
			import redis

			redis_url = frappe.conf.get("redis_queue", "redis://localhost:6379/4")
			r = redis.from_url(redis_url, socket_timeout=2)
			for queue_name in ["default", "short", "long"]:
				queue_key = f"rq:queue:{queue_name}"
				depth = r.llen(queue_key) or 0
				gauge(
					"erp_queue_depth",
					depth,
					"RQ queue depth",
					{"queue": queue_name},
				)
		except Exception:
			pass

		# ── Database metrics ──────────────────────────────────
		try:
			db_size = frappe.db.sql(
				"""SELECT pg_database_size(current_database())""",
				as_list=True,
			)[0][0] or 0
			gauge("erp_database_size_bytes", db_size, "Database size in bytes")
		except Exception:
			pass

	except Exception as e:
		lines.append(f"# ERROR: {e}")

	frappe.response["type"] = "text"
	frappe.response["content_type"] = "text/plain; version=0.0.4; charset=utf-8"
	return "\n".join(lines) + "\n"
