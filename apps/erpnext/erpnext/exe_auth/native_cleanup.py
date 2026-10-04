"""Finite private cleanup composition, never logs exception messages/arguments.

One supplied absolute end, <=16 actual failures. Bounded waits are not proof of
SQL cancellation: pending daemon cleanup is retained and the fixed private
receiver must exit unsuccessfully; the owning parent still enforces lifetime.
"""

import contextvars
import re
import threading
import time


class CleanupFailure(Exception):
    def __init__(self, primary, secondary, pending, overflow=None):
        super().__init__("private_operation_failed")
        self.primary = primary
        self.secondary = tuple(secondary[:16])
        self.pending = tuple(pending[:16 - len(self.secondary)])
        self.overflow = dict(overflow or {"secondary": 0, "pending": 0})
        self.overflow["secondary"] += len(secondary) - len(self.secondary)
        self.overflow["pending"] += len(pending) - len(self.pending)


class Failures:
    def __init__(self, absolute_end, clock=time.monotonic):
        self.end, self.clock = absolute_end, clock
        self.primary = None
        self.secondary = []
        self.pending = []
        self.overflow = {"secondary": 0, "pending": 0}

    def entry(self, kind, value):
        if len(self.secondary) + len(self.pending) < 16:
            getattr(self, kind).append(value)
        else:
            self.overflow[kind] += 1

    def first(self, error):
        if isinstance(error, CleanupFailure):
            if error.primary is not None:
                self.first(error.primary)
            for value in error.secondary:
                self.entry("secondary", value)
            for value in error.pending:
                self.entry("pending", value)
            for kind in self.overflow:
                self.overflow[kind] += error.overflow[kind]
            return
        if self.primary is None:
            self.primary = error
        else:
            self.entry("secondary", ("operation", error))

    def cleanup(self, stage, operation):
        if not re.fullmatch(r"[a-z_]{1,32}", stage):
            raise ValueError("invalid_cleanup_stage")
        left = min(3, self.end - self.clock())
        if left <= 0:
            self.entry("pending", (stage, "not_attempted_deadline"))
            return
        done = threading.Event()
        errors = []
        context = contextvars.copy_context()
        def finish():
            try:
                context.run(operation)
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
        thread = threading.Thread(target=finish, daemon=True, name="private_native_cleanup")
        thread.start()
        if not done.wait(left):
            self.entry("pending", (stage, thread))
        elif errors:
            self.entry("secondary", (stage, errors[0]))

    def local_state(self, stage, operation):
        """Only fixed, non-I/O context release on the ORIGINAL caller context."""
        if not re.fullmatch(r"[a-z_]{1,32}", stage):
            raise ValueError("invalid_cleanup_stage")
        try:
            operation()
        except BaseException as error:
            self.entry("secondary", (stage, error))

    def finish(self):
        if self.primary is not None and not self.secondary and not self.pending and not any(self.overflow.values()):
            raise self.primary
        if self.primary is not None or self.secondary or self.pending or any(self.overflow.values()):
            raise CleanupFailure(self.primary, self.secondary, self.pending, self.overflow)

    def sanitized(self):
        def code(error):
            name = type(error).__name__
            return name if re.fullmatch(r"[A-Za-z0-9_]{1,48}", name) else "Error"
        return {"primary_class": code(self.primary) if self.primary is not None else None,
                "secondary": [{"stage": stage, "class": code(error)} for stage, error in self.secondary],
                "pending": [stage for stage, _ in self.pending], "overflow": dict(self.overflow)}
