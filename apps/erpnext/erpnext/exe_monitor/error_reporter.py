"""
Exe Monitor — Error forwarding to exe-monitor-hub.

Forwards critical errors (5xx, unhandled exceptions, queue failures)
to the central monitoring hub for alerting and dashboard visibility.

Configuration (environment variables):
  MONITOR_ERROR_URL — Monitor hub endpoint (default: http://exe-monitor-hub:8090/api/exe-monitor/errors)
  MONITOR_API_KEY — Authentication token for monitor hub. Sent as the X-Monitor-Key
    header (matching exe-monitor's contract). EXE_MONITOR_KEY is accepted as a fallback.
  ERROR_REPORTING_ENABLED — Set to "false" to disable (default: true)

Usage:
  Hook into Frappe's error handling via hooks.py:
    after_request_error = "erpnext.exe_monitor.error_reporter.on_request_error"
"""

import os
import re
import logging
import traceback
from datetime import datetime, timezone

logger = logging.getLogger("exe_monitor")

MONITOR_URL = os.environ.get(
	"MONITOR_ERROR_URL",
	"http://exe-monitor-hub:8090/api/exe-monitor/errors",
)
MONITOR_KEY = os.environ.get("MONITOR_API_KEY") or os.environ.get("EXE_MONITOR_KEY", "")
ENABLED = os.environ.get("ERROR_REPORTING_ENABLED", "true").lower() != "false"

# Rate limiting: max N reports per minute to avoid flooding
_report_count = 0
_report_window_start = None
MAX_REPORTS_PER_MINUTE = 30


def _rate_limited():
	"""Simple rate limiter — max 30 reports per minute."""
	global _report_count, _report_window_start

	now = datetime.now(timezone.utc)
	if _report_window_start is None or (now - _report_window_start).total_seconds() > 60:
		_report_window_start = now
		_report_count = 0

	_report_count += 1
	return _report_count > MAX_REPORTS_PER_MINUTE


def report_error(error, context=None, severity="error"):
	"""Forward an error to exe-monitor-hub.

	Args:
		error: Exception object or error string
		context: Optional dict with additional context (endpoint, user, etc.)
		severity: "error", "warning", or "critical"

	Never raises — monitoring should never break the application.
	"""
	if not ENABLED:
		return

	if _rate_limited():
		return

	try:
		import requests

		tb = ""
		if isinstance(error, Exception):
			tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
			error_msg = str(error)
		else:
			error_msg = str(error)
			tb = traceback.format_stack()
			tb = "".join(tb) if isinstance(tb, list) else str(tb)

		# Sanitize sensitive data before sending externally
		tb = _sanitize_traceback(tb)
		error_msg = _sanitize_traceback(error_msg)

		payload = {
			"service": "exe-erp",
			"severity": severity,
			"message": error_msg,
			"traceback": tb,
			"context": context or {},
			"timestamp": datetime.now(timezone.utc).isoformat(),
		}

		headers = {"Content-Type": "application/json"}
		if MONITOR_KEY:
			headers["X-Monitor-Key"] = MONITOR_KEY

		requests.post(
			MONITOR_URL,
			json=payload,
			headers=headers,
			timeout=5,
		)

	except Exception as e:
		# Monitoring must NEVER break the app
		logger.debug(f"exe_monitor: error report failed — {e}")


def on_request_error(doc=None, method=None):
	"""Frappe hook: called on unhandled request errors.

	Wired via hooks.py:
		after_request_error = ["erpnext.exe_monitor.error_reporter.on_request_error"]
	"""
	import frappe

	try:
		error = frappe.local.error_message or "Unknown error"
		context = {
			"endpoint": frappe.local.request.path if hasattr(frappe.local, "request") and frappe.local.request else "unknown",
			"method": frappe.local.request.method if hasattr(frappe.local, "request") and frappe.local.request else "unknown",
			"user": frappe.session.user if frappe.session else "unknown",
		}
		report_error(error, context, severity="error")
	except Exception:
		pass  # Never break the error handler


def _sanitize_traceback(tb):
	"""Remove sensitive data from tracebacks before sending externally."""
	# Redact connection strings
	tb = re.sub(r'postgresql?://[^\s"\']+', '[REDACTED_DSN]', tb)
	# Redact tokens/keys/passwords
	tb = re.sub(r'(password|token|secret|key)\s*[=:]\s*\S+', r'\1=[REDACTED]', tb, flags=re.IGNORECASE)
	# Cap length
	return tb[:2048]


def report_queue_failure(job_name, error, queue="default"):
	"""Report a background job failure.

	Call from custom RQ error handlers or scheduler hooks.
	"""
	report_error(
		error,
		context={
			"job_name": job_name,
			"queue": queue,
			"type": "queue_failure",
		},
		severity="warning",
	)
