"""Fixed private owned-container entry. NEVER a public/caller allocation API.

Parent must inspect the exact CREATED returned CID/image/entry/UID/limits and
protected mounts BEFORE original successful first-start dispatch. Only that
branch starts/attaches stdin. A serialized frame alone authenticates nothing;
parent process/container/pipe ownership is a separate required composition.
No action file or read-after-uncertain-start fallback exists.
"""

import json
import logging
import math
import os
import selectors
import stat
import sys
import time

CORE_SECRET = PACKAGE = bind_frame = bounded_owner_read = closed_json = None
emit_main = frame_from_pipe = Refused = exact = Failures = CoreReader = None
allocate = stable_secret = None


def load_bindings():
    # Called only after the PID1 supervisor has bound and released this worker.
    if __package__:
        from . import (
            native_cleanup,
            native_contract,
            native_core,
            native_site,
            native_transport,
        )
    else:
        from erpnext.exe_auth import (
            native_cleanup,
            native_contract,
            native_core,
            native_site,
            native_transport,
        )
    globals().update({name: getattr(native_transport, name) for name in (
        "CORE_SECRET", "PACKAGE", "bind_frame", "bounded_owner_read", "closed_json", "emit_main", "frame_from_pipe")})
    globals().update(Refused=native_contract.Refused, exact=native_contract.exact,
                     Failures=native_cleanup.Failures, CoreReader=native_core.CoreReader,
                     allocate=native_site.allocate, stable_secret=native_site.stable_secret)


OPERATOR_SECRET = "/run/exe-native/native-operator.json"


class DiscardNativeOutput:
    """Do not expose installer diagnostics/password/query text on the pipe."""
    def __init__(self):
        self.bytes = 0
    def write(self, text):
        self.bytes += len(text.encode("utf-8"))
        if self.bytes > 65536:
            raise Refused("native_output_limit")
        return len(text)
    def flush(self):
        pass


def run(before=None, original_end=None):
    before = time.monotonic() if before is None else before
    package = closed_json(stable_secret(PACKAGE, 8192, before + 5))
    # Operator-held package is default off. Do not read credentials or stdin
    # into allocation until the fixed private package is explicitly enabled.
    if type(package) is not dict or package.get("enabled") is not True:
        raise Refused("private_package_disabled")
    frame = frame_from_pipe(0, before + 5)
    value, config, end = bind_frame(frame, package, before, time.monotonic())
    end = min(end, original_end) if original_end is not None else end
    publish_budget(end, initial=True)
    failures = Failures(end)
    core, result = None, None
    try:
        core = CoreReader(CORE_SECRET, end)
        original_read = bounded_owner_read(core, frame, before, end)
        def read(value):
            result = original_read(value)
            publish_budget(original_read.budget_end())
            return result
        read.budget_end = original_read.budget_end
        operator = closed_json(stable_secret(OPERATOR_SECRET, 8192, end))
        exact(operator, ("host", "port", "user", "password"))
        if type(operator["host"]) is not str or not 1 <= len(operator["host"]) <= 253 or type(operator["port"]) is not int or not 1 <= operator["port"] <= 65535 or type(operator["user"]) is not str or not 1 <= len(operator["user"]) <= 63 or type(operator["password"]) is not str or not 1 <= len(operator["password"]) <= 4096 or operator["user"] == config["database"]:
            raise Refused("invalid_private_operator")
        consumed = False
        def consume_once():
            nonlocal consumed
            if consumed:
                raise Refused("private_dispatch_consumed")
            consumed = True
            return value, end
        result = allocate(consume_once, read, config, operator)
        result = {**result, "state": "pending_observation", "native_readiness": "unverified"}
    except BaseException as error:
        failures.first(error)
    finally:
        if core is not None:
            failures.cleanup("receiver_core_close", core.close)
    failures.finish()
    if time.monotonic() >= min(end, read.budget_end()):
        raise Refused("late_private_observation")
    return result


RESULT_FD = None
_budget_bytes = 0

def publish_budget(absolute_end, *, initial=False):
    global _budget_bytes
    raw = (json.dumps({"admitted_end" if initial else "budget_end": absolute_end}, separators=(",", ":")) + "\n").encode()
    _budget_bytes += len(raw)
    if _budget_bytes > 1024 or os.write(RESULT_FD, raw) != len(raw):
        raise Refused("private_budget_channel")


def admit_gate(raw, admitted, now):
    # Absolute monotonic values exist only on this same-kernel internal pipe,
    # never on the public/Core frame or a cross-host/cross-runtime protocol.
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_private_gate")
            value[key] = item
        return value
    if not raw.startswith(b"RELEASE ") or not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise ValueError("invalid_private_gate")
    gate = json.loads(raw[8:-1], object_pairs_hook=pairs)
    if type(gate) is not dict or set(gate) != {"start", "startup_end", "work_end"}:
        raise ValueError("invalid_private_gate")
    if not all(type(v) in (int, float) and math.isfinite(v) for v in gate.values()):
        raise ValueError("invalid_private_gate_clock")
    before, startup, end = gate["start"], gate["startup_end"], gate["work_end"]
    if not 0 <= before <= admitted <= now < startup or startup != before + 5 or not startup < end <= before + 300:
        raise ValueError("expired_private_gate")
    return before, end


def main():
    # Standby performs stdlib-only admission before any package/native import.
    if sys.platform != "linux" or os.getpid() <= 1 or os.getppid() != 1 or os.getuid() != 1000:
        os._exit(1)
    global RESULT_FD
    # This locator comes only from the supervisor's internally allocated pipe.
    # It grants no authority; parent verifies its actual inode before release.
    if len(sys.argv) != 3 or sys.argv[1] != "--standby-result" or not sys.argv[2].isdigit():
        os._exit(1)
    RESULT_FD = int(sys.argv[2])
    if not 3 <= RESULT_FD <= 1024 or not all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (0, 1, 2, RESULT_FD)):
        os._exit(1)
    admitted = time.monotonic()
    os.set_blocking(0, False)
    selector = selectors.DefaultSelector()
    header = bytearray()
    try:
        selector.register(0, selectors.EVENT_READ)
        while not header.endswith(b"\n"):
            left = admitted + 5 - time.monotonic()
            if left <= 0 or not selector.select(left):
                os._exit(1)
            part = os.read(0, 1)
            if not part or len(header) >= 256:
                os._exit(1)
            header.extend(part)
    finally:
        selector.close()
    before, original_end = admit_gate(bytes(header), admitted, time.monotonic())
    # DDL warnings contain SQL and can exhaust the supervisor's fixed cap.
    # This fixed private process setting precedes all Frappe imports; native
    # stdout/stderr still have the unchanged supervisor bounds and discard.
    logging.disable(logging.CRITICAL)
    load_bindings()
    # Imports cannot renew either startup or work allowance.
    if time.monotonic() >= min(before + 5, original_end):
        os._exit(1)
    emit_main(lambda: run(before, original_end), output_fd=RESULT_FD)



if __name__ == "__main__":
    main()
