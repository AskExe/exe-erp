"""
Exe ERP — WSGI entry point with tracing middleware.

This wraps Frappe's standard WSGI application with the exe_bridge
TracingMiddleware for request tracing, X-Trace-Id propagation, and
Prometheus metrics.

Usage in docker-compose.yml:
  gunicorn --bind=0.0.0.0:8000 wsgi:application
"""

from frappe.app import application as _frappe_app, application_with_statics

# Ensure static file serving middleware is applied (SharedDataMiddleware for /assets)
# Without this, /assets/* returns 404 and the frontend has no CSS/JS/images.
_frappe_app = application_with_statics()

try:
    from erpnext.exe_bridge.middleware import TracingMiddleware
    application = TracingMiddleware(_frappe_app)
except ImportError:
    # Graceful fallback if exe_bridge module not available
    application = _frappe_app
except Exception:
    # Never block boot — tracing is optional
    application = _frappe_app
