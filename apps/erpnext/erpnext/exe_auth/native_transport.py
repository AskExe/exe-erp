"""Protected pipe/budget helpers; no allocator or native-write credential path.

Serialized fields never establish original-start authority. Only the reviewed
parent's live in-memory first-success branch may start these fixed children.
"""

import json
import os
import re
import selectors
import stat
import time

from .native_cleanup import CleanupFailure, Failures
from .native_contract import Refused, exact, stamp, validate_tuple

PACKAGE = "/run/exe-native/erp-package.json"
CORE_SECRET = "/run/exe-native/core-worker.dsn"
OBSERVER_SECRET = "/run/exe-native/native-observer.dsn"
SITES = "/home/frappe/frappe-bench/sites"
FRAME_KEYS = ("version", "tuple", "initial_sql_time", "original_lease_expires_at", "remaining_work_milliseconds")
PACKAGE_KEYS = ("version", "enabled", "uid", "image_id", "job_id", "intent_id", "company_id", "deployment_id", "profile_sha256", "config_sha256", "initializer_sha256", "site", "database")


def closed_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise Refused("duplicate_private_field")
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs)


def frame_from_pipe(fd, absolute_end, maximum=16384):
    """One <=16 KiB newline frame plus actual EOF, absolute <=5s admission.

    EOF matters: no second frame/late input is accepted. This owns a selector,
    not stdin itself; owning parent retains the actual attach/process group.
    """
    chunks = bytearray()
    errors = Failures(absolute_end)
    selector = None
    result = None
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise Refused("not_private_os_pipe")
        os.set_blocking(fd, False)
        selector = selectors.DefaultSelector()
        selector.register(fd, selectors.EVENT_READ)
        while True:
            left = absolute_end - time.monotonic()
            if left <= 0:
                raise Refused("private_pipe_deadline")
            if not selector.select(min(left, .1)):
                continue
            try:
                part = os.read(fd, maximum + 1 - len(chunks))
            except BlockingIOError:
                continue
            if time.monotonic() >= absolute_end:
                raise Refused("private_pipe_deadline")
            if not part:
                break
            chunks.extend(part)
            if len(chunks) > maximum:
                raise Refused("private_pipe_limit")
        if not chunks.endswith(b"\n") or chunks.count(b"\n") != 1:
            raise Refused("invalid_private_frame")
        result = closed_json(chunks[:-1].decode("utf-8"))
    except BaseException as error:
        errors.first(error)
    finally:
        if selector is not None:
            errors.cleanup("pipe_selector_close", selector.close)
    errors.finish()
    return result


def bind_frame(frame, package, before, after, required_seconds=181):
    exact(frame, FRAME_KEYS)
    exact(package, PACKAGE_KEYS)
    value = validate_tuple(frame["tuple"])
    if frame["version"] != "core-first-writer-v1" or package["version"] != "erp-native-package-v1" or package["enabled"] is not True or type(package["uid"]) is not int or package["uid"] != 1000 or os.getuid() != package["uid"]:
        raise Refused("private_package_disabled")
    if type(package["image_id"]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", package["image_id"]):
        raise Refused("invalid_image_binding")
    if any(value[key] != package[key] for key in ("job_id", "intent_id", "company_id", "deployment_id", "profile_sha256", "config_sha256", "initializer_sha256")):
        raise Refused("changed_private_package")
    initial, original = stamp(frame["initial_sql_time"]), stamp(frame["original_lease_expires_at"])
    milliseconds = frame["remaining_work_milliseconds"]
    if type(milliseconds) is not int or not 0 < milliseconds <= 270000 or not 0 < original - initial <= 300 or after < before:
        raise Refused("invalid_original_budget")
    # Full receiving interval is charged; never transport a monotonic epoch.
    end = before + milliseconds / 1000 + 30
    if type(required_seconds) not in (int, float) or not 0 < required_seconds <= 181 or after + required_seconds >= end:
        raise Refused("insufficient_original_budget")
    config = {k: package[k] for k in ("site", "database", "profile_sha256", "config_sha256", "initializer_sha256")}
    config["sites_dir"] = SITES
    return value, config, end


def bounded_owner_read(reader, frame, receive_before=None, original_end=None, clock=time.monotonic):
    initial = stamp(frame["initial_sql_time"])
    original = stamp(frame["original_lease_expires_at"])
    receive_before = clock() if receive_before is None else receive_before
    original_end = receive_before + frame["remaining_work_milliseconds"] / 1000 + 30 if original_end is None else original_end
    class Read:
        end, frozen = original_end, False
        def budget_end(self):
            return self.end
        def __call__(self, value):
            before = clock()
            row = reader(value)
            after = clock()
            # Actual same-Core SQL clock debits pre-start/CLI transit. Charge
            # the full child receive+query bracket additionally/conservatively.
            elapsed_sql = stamp(row["sql_time"]) - initial
            if elapsed_sql < 0 or after < before or after < receive_before:
                raise Refused("backwards_sql_clock")
            if not self.frozen:
                remaining = frame["remaining_work_milliseconds"] / 1000 - elapsed_sql - (after - receive_before) - .001
                if remaining <= 0:
                    raise Refused("expired_original_work")
                self.end = min(self.end, after + remaining + 30)
                self.frozen = True
            if stamp(row["lease_expires_at"]) > original:
                row = {**row, "lease_expires_at": frame["original_lease_expires_at"]}
            lease_left = stamp(row["lease_expires_at"]) - stamp(row["sql_time"]) - (after - before) - .001
            if lease_left <= 0:
                raise Refused("expired_current_lease")
            self.end = min(self.end, after + lease_left)
            return row
    return Read()



PHASE_FRAME_KEYS = (*FRAME_KEYS, "phase", "handoff_sql_time", "original_remaining_work_milliseconds")


def bind_phase_frame(frame, package, before, after, phase, required_seconds):
    """Later-phase data; original-success/CREATED authentication stays parent-owned."""
    exact(frame, PHASE_FRAME_KEYS)
    if frame["version"] != "core-first-writer-phase-v2" or phase not in ("setup", "observe") or frame["phase"] != phase:
        raise Refused("invalid_private_phase")
    initial, handoff = stamp(frame["initial_sql_time"]), stamp(frame["handoff_sql_time"])
    expiry = stamp(frame["original_lease_expires_at"])
    original_ms, remaining_ms = frame["original_remaining_work_milliseconds"], frame["remaining_work_milliseconds"]
    if type(original_ms) is not int or not 0 < original_ms <= 270000 or type(remaining_ms) is not int or not initial <= handoff < expiry or remaining_ms > original_ms - (handoff - initial) * 1000:
        raise Refused("extended_phase_budget")
    # Reuse unchanged allocation package/tuple/expiry checks, never its transit
    # origin. This local translation is not a recovered start acknowledgement.
    allocation = {key: frame[key] for key in FRAME_KEYS}
    allocation["version"] = "core-first-writer-v1"
    return bind_frame(allocation, package, before, after, required_seconds)


def bounded_phase_owner_read(reader, frame, receive_before, original_end, clock=time.monotonic):
    exact(frame, PHASE_FRAME_KEYS)
    if frame["version"] != "core-first-writer-phase-v2" or frame["phase"] not in ("setup", "observe"):
        raise Refused("invalid_private_phase")
    initial, handoff = stamp(frame["initial_sql_time"]), stamp(frame["handoff_sql_time"])
    def current(value):
        row = reader(value)
        if not initial <= handoff <= stamp(row["sql_time"]):
            raise Refused("backwards_handoff_clock")
        return row
    transit = {key: frame[key] for key in FRAME_KEYS}
    transit["initial_sql_time"] = frame["handoff_sql_time"]
    # Only handoff-to-first-current-read transit is debited from the ALREADY
    # shrunk work. Later reads cannot renew either the frozen end or expiry.
    return bounded_owner_read(current, transit, receive_before, original_end, clock)



def emit_main(runner, *, output_fd=1):
    output_attempted = False
    def emit(payload):
        nonlocal output_attempted
        raw = (json.dumps(payload, sort_keys=True) + "\n").encode()
        if len(raw) > 2048 or not stat.S_ISFIFO(os.fstat(output_fd).st_mode):
            raise Refused("invalid_private_output")
        os.set_blocking(output_fd, False)
        output_attempted = True
        # <=PIPE_BUF atomic write on the required pipe. No buffering/flush,
        # second payload or guessed success if the response channel failed.
        if os.write(output_fd, raw) != len(raw):
            raise Refused("incomplete_private_output")
    try:
        result = runner()
        emit({"ok": True, "observation": result})
    except BaseException as error:
        # No exception messages, stack, query, credentials or caller IDs.
        def safe_class(error):
            name = type(error).__name__
            return name if re.fullmatch(r"[A-Za-z0-9_]{1,48}", name) else "Error"
        detail = {"primary_class": safe_class(error), "secondary_count": 0, "pending_count": 0, "overflow_count": 0}
        if isinstance(error, CleanupFailure):
            detail.update(primary_class=safe_class(error.primary) if error.primary else None, secondary_count=len(error.secondary), pending_count=len(error.pending), overflow_count=sum(error.overflow.values()))
        if not output_attempted:
            try:
                emit({"ok": False, "code": "private_native_failed", "cleanup": detail})
            except BaseException:
                # Failed/ambiguous channel is itself quarantined; no repeat or
                # fabricated durable diagnostic is claimed by this receiver.
                pass
        # Pending cleanup is not canceled/proved closed. Never stay alive or
        # accept another dispatch; owning parent must retain quarantine/closure.
        os._exit(1)

