"""Fixed private owned-container entry. NEVER a public/caller allocation API.

Parent must inspect the exact CREATED returned CID/image/entry/UID/limits and
protected mounts BEFORE original successful first-start dispatch. Only that
branch starts/attaches stdin. A serialized frame alone authenticates nothing;
parent process/container/pipe ownership is a separate required composition.
No action file or read-after-uncertain-start fallback exists.
"""

import contextlib
import json
import os
import re
import selectors
import stat
import sys
import time

from .native_cleanup import CleanupFailure, Failures
from .native_contract import Refused, exact, stamp, validate_tuple
from .native_core import CoreReader
from .native_site import allocate, stable_secret
from .native_transport import (
    CORE_SECRET,
    PACKAGE,
    bind_frame,
    bounded_owner_read,
    closed_json,
    emit_main,
    frame_from_pipe,
)

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


def run():
    before = time.monotonic()
    package = closed_json(stable_secret(PACKAGE, 8192, before + 5))
    # Operator-held package is default off. Do not read credentials or stdin
    # into allocation until the fixed private package is explicitly enabled.
    if type(package) is not dict or package.get("enabled") is not True:
        raise Refused("private_package_disabled")
    frame = frame_from_pipe(0, before + 5)
    value, config, end = bind_frame(frame, package, before, time.monotonic())
    failures = Failures(end)
    core, result = None, None
    try:
        core = CoreReader(CORE_SECRET, end)
        read = bounded_owner_read(core, frame, before, end)
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
        sink = DiscardNativeOutput()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
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


def main():
    emit_main(run)


if __name__ == "__main__":
    main()
