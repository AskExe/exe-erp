"""
Exe Monitor — Enhanced health endpoint for exe-os stack integration.

Goes beyond Frappe's basic /api/method/ping to report component-level health
for exe-os stack-update health checks and exe-monitor dashboard.

Endpoint: GET /api/method/erpnext.exe_monitor.health.check
"""

import logging
import os
from datetime import datetime, timezone

import frappe

logger = logging.getLogger("exe_monitor")


@frappe.whitelist(allow_guest=True)
def check():
	"""Return detailed health status for exe-os stack health checks.

	Returns JSON with component-level health:
	  - database: PostgreSQL connectivity
	  - redis_cache: Redis cache connectivity
	  - redis_queue: Redis queue connectivity
	  - scheduler: Frappe scheduler status
	  - site: Site configuration status
	"""
	status = "healthy"
	components = {}

	# ── Database ──────────────────────────────────────────────
	try:
		frappe.db.sql("SELECT 1", as_list=True)
		components["database"] = {
			"status": "healthy",
			"type": "postgres",
			# db_name is omitted from the unauthenticated response to avoid
			# leaking internal database names to unauthenticated callers.
		}
	except Exception as e:
		logger.error(f"Health check — database error: {e}")
		components["database"] = {"status": "unhealthy", "error": "Database connectivity error"}
		status = "unhealthy"

	# ── Redis Cache ───────────────────────────────────────────
	try:
		import redis

		redis_url = frappe.conf.get("redis_cache", "redis://localhost:6379/3")
		r = redis.from_url(redis_url, socket_timeout=2)
		r.ping()
		info = r.info("memory")
		components["redis_cache"] = {
			"status": "healthy",
			"used_memory_human": info.get("used_memory_human", "unknown"),
		}
	except Exception as e:
		logger.error(f"Health check — redis cache error: {e}")
		components["redis_cache"] = {"status": "unhealthy", "error": "Cache connectivity error"}
		status = "degraded" if status == "healthy" else status

	# ── Redis Queue ───────────────────────────────────────────
	try:
		import redis

		redis_url = frappe.conf.get("redis_queue", "redis://localhost:6379/4")
		r = redis.from_url(redis_url, socket_timeout=2)
		r.ping()

		# Check queue depths
		queues = {}
		for q in ["default", "short", "long"]:
			queues[q] = r.llen(f"rq:queue:{q}") or 0

		components["redis_queue"] = {
			"status": "healthy",
			"queue_depths": queues,
		}
	except Exception as e:
		logger.error(f"Health check — redis queue error: {e}")
		components["redis_queue"] = {"status": "unhealthy", "error": "Queue connectivity error"}
		status = "degraded" if status == "healthy" else status

	# ── Scheduler ─────────────────────────────────────────────
	try:
		from frappe.utils.scheduler import is_scheduler_inactive

		scheduler_disabled = is_scheduler_inactive()
		components["scheduler"] = {
			"status": "disabled" if scheduler_disabled else "healthy",
		}
		if scheduler_disabled:
			status = "degraded" if status == "healthy" else status
	except Exception as e:
		logger.error(f"Health check — scheduler error: {e}")
		components["scheduler"] = {"status": "unknown", "error": "Scheduler check failed"}

	# ── Bridge (exedb connectivity) ───────────────────────────
	try:
		from erpnext.exe_bridge.connection import get_connection

		conn = get_connection()
		if conn:
			cursor = conn.cursor()
			cursor.execute("SELECT 1")
			cursor.close()
			components["bridge"] = {"status": "healthy", "target": "exedb"}
		else:
			components["bridge"] = {"status": "unavailable", "note": "exedb not reachable"}
	except Exception as e:
		logger.error(f"Health check — bridge error: {e}")
		components["bridge"] = {"status": "unhealthy", "error": "Bridge connectivity error"}

	# ── Site info ─────────────────────────────────────────────
	site_name = frappe.local.site or "unknown"

	return {
		"status": status,
		"service": "exe-erp",
		"version": "0.1.0",
		"site": site_name,
		"timestamp": datetime.now(timezone.utc).isoformat(),
		"components": components,
	}
