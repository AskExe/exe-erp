"""
Exe Bridge — OpenTelemetry-style request tracing for exe-erp.

Provides hierarchical span tracking that exceeds Claude Code's telemetry:
- request spans (like CC's "interaction" spans)
- database query spans (like CC's "llm_request" spans)
- background job spans (like CC's "tool.execution" spans)
- cross-service trace propagation via X-Trace-Id header

Architecture:
  HTTP Request → RequestTracer middleware → span tree
    ├── request span (method, path, status, duration)
    │   ├── db_query spans (query, duration, rows)
    │   └── bridge_event spans (doctype, event_type)
    └── trace_id propagated in response header

All trace data is emitted to raw.raw_events (source: "telemetry") for
cross-service correlation. exe-monitor scrapes Prometheus metrics.
Traces are queryable via Company Brain.

Unlike Claude Code (which uses OTLP/gRPC exporters to BigQuery),
we write directly to our landing pad — keeping data sovereign and
queryable without external dependencies.
"""

import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps

logger = logging.getLogger("exe_bridge.tracing")

# Thread-local storage for trace context propagation
_trace_context = threading.local()

# ── Configuration ─────────────────────────────────────────────
# Maximum spans per trace before we start dropping (prevent memory leaks)
MAX_SPANS_PER_TRACE = 100

# Minimum request duration (ms) to emit a trace (skip trivial requests)
MIN_TRACE_DURATION_MS = 50

# Slow query threshold (ms) — always emit these
SLOW_QUERY_THRESHOLD_MS = 1000

# Sample rate for normal traces (1.0 = all, 0.1 = 10%)
TRACE_SAMPLE_RATE = 0.1

# Always trace these paths regardless of sample rate
ALWAYS_TRACE_PATHS = {
    "/api/method/erpnext.exe_bridge.metrics.get_metrics",
    "/api/method/erpnext.exe_monitor.health.check",
    "/api/method/erpnext.exe_auth.api.gotrue_login",
    "/api/method/erpnext.exe_auth.api.admin_token",
}


class Span:
    """A single trace span — represents one unit of work."""

    __slots__ = (
        "attributes",
        "children",
        "end_time",
        "name",
        "parent_id",
        "span_id",
        "span_type",
        "start_time",
        "status",
        "trace_id",
    )

    def __init__(self, name, span_type, trace_id=None, parent_id=None):
        self.span_id = uuid.uuid4().hex[:16]
        self.trace_id = trace_id or uuid.uuid4().hex[:32]
        self.parent_id = parent_id
        self.name = name
        self.span_type = span_type  # request, db_query, bridge_event, background_job, hook
        self.start_time = time.monotonic()
        self.end_time = None
        self.attributes = {}
        self.status = "ok"
        self.children = []

    def set_attribute(self, key, value):
        """Set a span attribute (like OTEL attributes)."""
        self.attributes[key] = value
        return self

    def set_status(self, status, message=None):
        """Set span status: ok, error, timeout."""
        self.status = status
        if message:
            self.attributes["error.message"] = str(message)[:512]
        return self

    def end(self):
        """End the span and record duration."""
        self.end_time = time.monotonic()
        return self

    @property
    def duration_ms(self):
        """Duration in milliseconds."""
        if self.end_time is None:
            return (time.monotonic() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    def to_dict(self):
        """Serialize span for storage in raw.raw_events."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "type": self.span_type,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "attributes": self.attributes,
            "children": [c.to_dict() for c in self.children],
        }


class RequestTracer:
    """Manages trace context for a single request lifecycle.

    Usage in WSGI middleware:
        tracer = RequestTracer.from_request(environ)
        with tracer.span("process_request", "request") as span:
            span.set_attribute("http.method", "GET")
            # ... do work ...
            with tracer.span("db_query", "db_query") as db_span:
                db_span.set_attribute("db.statement", "SELECT ...")
        tracer.flush()
    """

    def __init__(self, trace_id=None):
        self.trace_id = trace_id or uuid.uuid4().hex[:32]
        self.root_span = None
        self._span_stack = []
        self._span_count = 0
        self._should_sample = True
        self._start_wall = datetime.now(timezone.utc)

    @classmethod
    def from_request(cls, environ):
        """Create tracer from WSGI environ, propagating X-Trace-Id if present."""
        # Check for incoming trace ID (cross-service propagation)
        trace_id = (
            environ.get("HTTP_X_TRACE_ID")
            or environ.get("HTTP_X_REQUEST_ID")
            or None
        )
        tracer = cls(trace_id=trace_id)

        # Determine sampling
        path = environ.get("PATH_INFO", "")
        if path in ALWAYS_TRACE_PATHS:
            tracer._should_sample = True
        else:
            import random
            tracer._should_sample = random.random() < TRACE_SAMPLE_RATE

        return tracer

    @contextmanager
    def span(self, name, span_type, **attributes):
        """Create a child span within the current trace context.

        Usage:
            with tracer.span("my_operation", "db_query", table="users") as s:
                # ... do work ...
                s.set_attribute("rows_affected", 42)
        """
        if self._span_count >= MAX_SPANS_PER_TRACE:
            # Yield a no-op span to prevent crashes
            yield Span(name, span_type)
            return

        parent = self._span_stack[-1] if self._span_stack else None
        s = Span(
            name=name,
            span_type=span_type,
            trace_id=self.trace_id,
            parent_id=parent.span_id if parent else None,
        )

        for k, v in attributes.items():
            s.set_attribute(k, v)

        if parent:
            parent.children.append(s)
        elif self.root_span is None:
            self.root_span = s

        self._span_stack.append(s)
        self._span_count += 1

        try:
            yield s
        except Exception as e:
            s.set_status("error", str(e))
            raise
        finally:
            s.end()
            self._span_stack.pop()

    def flush(self):
        """Emit the completed trace to raw.raw_events (source: 'telemetry').

        Called at end of request. Fire-and-forget.
        """
        if not self._should_sample:
            return

        if self.root_span is None:
            return

        # Skip very fast requests (noise)
        if self.root_span.duration_ms < MIN_TRACE_DURATION_MS:
            # Unless it had errors
            if self.root_span.status == "ok":
                return

        try:
            from erpnext.exe_bridge.connection import get_connection

            conn = get_connection()
            if conn is None:
                return

            payload = {
                "trace_id": self.trace_id,
                "service": "exe-erp",
                "started_at": self._start_wall.isoformat(),
                "duration_ms": round(self.root_span.duration_ms, 2),
                "span_count": self._span_count,
                "status": self.root_span.status,
                "root_span": self.root_span.to_dict(),
            }

            event_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)

            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO raw.raw_events (id, source, source_id, event_type, payload, metadata, timestamp)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    event_id,
                    "telemetry",
                    self.trace_id,
                    "request.trace",
                    json.dumps(payload, default=str),
                    json.dumps({
                        "service": "exe-erp",
                        "trace_id": self.trace_id,
                        "span_count": self._span_count,
                    }),
                    now,
                ),
            )
            cursor.close()

        except Exception as e:
            logger.debug(f"exe_bridge.tracing: flush failed — {e}")


# ── Thread-local trace context ────────────────────────────────

def get_current_tracer():
    """Get the tracer for the current request (thread-local)."""
    return getattr(_trace_context, "tracer", None)


def set_current_tracer(tracer):
    """Set the tracer for the current request."""
    _trace_context.tracer = tracer


def clear_current_tracer():
    """Clear the current request's tracer."""
    _trace_context.tracer = None


def get_current_trace_id():
    """Get the trace ID for the current request, or generate one."""
    tracer = get_current_tracer()
    return tracer.trace_id if tracer else None


# ── Convenience decorators ────────────────────────────────────

def traced(span_type="function", **span_attrs):
    """Decorator to automatically trace a function call.

    Usage:
        @traced(span_type="db_query", table="users")
        def get_user(user_id):
            ...
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            tracer = get_current_tracer()
            if tracer is None:
                return fn(*args, **kwargs)

            with tracer.span(fn.__qualname__, span_type, **span_attrs):
                return fn(*args, **kwargs)

        return wrapper
    return decorator
