"""
Exe Bridge — WSGI middleware for request tracing and trace propagation.

Wraps Frappe's WSGI application to:
1. Create a RequestTracer for each incoming request
2. Propagate X-Trace-Id from incoming requests (cross-service)
3. Set X-Trace-Id in response headers (for downstream services)
4. Emit completed traces to raw.raw_events on request completion
5. Track request metrics for Prometheus

This middleware is registered via Frappe's app_include mechanism.
It wraps the gunicorn WSGI callable.

Installation (in hooks.py or wsgi.py):
    from erpnext.exe_bridge.middleware import TracingMiddleware
    application = TracingMiddleware(application)
"""

import logging
import os
import time

logger = logging.getLogger("exe_bridge.middleware")

# Disable middleware via env var if needed
TRACING_ENABLED = os.environ.get("EXE_TRACING_ENABLED", "true").lower() != "false"

# Paths to skip tracing entirely (static assets, favicon, etc.)
SKIP_PATHS = {
    "/favicon.ico",
    "/robots.txt",
    "/assets/",
    "/files/",
}


class TracingMiddleware:
    """WSGI middleware that wraps each request in a trace span.

    Usage:
        # In wsgi.py or wherever gunicorn loads the app:
        from erpnext.exe_bridge.middleware import TracingMiddleware
        application = TracingMiddleware(application)
    """

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if not TRACING_ENABLED:
            return self.app(environ, start_response)

        path = environ.get("PATH_INFO", "")

        # Skip static assets
        for skip in SKIP_PATHS:
            if path.startswith(skip):
                return self.app(environ, start_response)

        # Import here to avoid circular imports during module load
        from erpnext.exe_bridge.tracing import (
            RequestTracer,
            clear_current_tracer,
            set_current_tracer,
        )

        # Create tracer (propagates X-Trace-Id if present)
        tracer = RequestTracer.from_request(environ)
        set_current_tracer(tracer)

        method = environ.get("REQUEST_METHOD", "GET")
        user_agent = environ.get("HTTP_USER_AGENT", "")[:100]
        content_length = environ.get("CONTENT_LENGTH", "0")

        # Track response status
        response_status = [None]

        def traced_start_response(status, headers, exc_info=None):
            """Wrap start_response to capture status and inject trace header."""
            response_status[0] = status

            # Inject X-Trace-Id in response for cross-service correlation
            headers.append(("X-Trace-Id", tracer.trace_id))
            headers.append(("X-Request-Id", tracer.trace_id))

            return start_response(status, headers, exc_info)

        try:
            with tracer.span("http.request", "request") as span:
                span.set_attribute("http.method", method)
                span.set_attribute("http.path", path)
                span.set_attribute("http.user_agent", user_agent)
                span.set_attribute("http.request_content_length", content_length)

                # Extract Frappe-specific context
                try:
                    import frappe
                    if hasattr(frappe, "session") and frappe.session:
                        span.set_attribute("user", frappe.session.user or "Guest")
                    if hasattr(frappe, "local") and hasattr(frappe.local, "site"):
                        span.set_attribute("site", frappe.local.site or "unknown")
                except Exception:
                    pass

                result = self.app(environ, traced_start_response)

                # Set response attributes
                if response_status[0]:
                    status_code = response_status[0].split(" ")[0]
                    span.set_attribute("http.status_code", status_code)

                    if status_code.startswith("5"):
                        span.set_status("error", f"HTTP {status_code}")
                    elif status_code.startswith("4"):
                        span.set_attribute("http.client_error", True)

                return result

        except Exception as e:
            logger.debug(f"TracingMiddleware error: {e}")
            # Don't let tracing break the app
            return self.app(environ, start_response)

        finally:
            # Flush trace to landing pad (fire-and-forget)
            try:
                tracer.flush()
            except Exception:
                pass
            clear_current_tracer()
