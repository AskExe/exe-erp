"""Separate protected read-only phase. No allocator or native-write login.

Only the original-success parent may dispatch this child after exact operator
setup. Its mount excludes native-operator.json. No returned JSON establishes
readiness or a Core receipt; the parent retains quarantine on lost evidence.
"""

import time

from .native_cleanup import Failures
from .native_contract import CurrentOwner, Refused
from .native_core import CoreReader
from .native_observer import observe_owned
from .native_shared import stable_secret
from .native_transport import (
    CORE_SECRET,
    OBSERVER_SECRET,
    PACKAGE,
    bind_phase_frame,
    bounded_phase_owner_read,
    closed_json,
    emit_main,
    frame_from_pipe,
)


def run():
    before = time.monotonic()
    package = closed_json(stable_secret(PACKAGE, 8192, before + 5))
    frame = frame_from_pipe(0, before + 5)
    value, config, end = bind_phase_frame(frame, package, before, time.monotonic(), "observe", required_seconds=60)
    failures = Failures(end)
    core, result, read = None, None, None
    try:
        core = CoreReader(CORE_SECRET, end)
        read = bounded_phase_owner_read(core, frame, before, end)
        current = CurrentOwner(value, read, end, deadline_bound=read.budget_end)
        current.observe(30)
        result = observe_owned(value, read, end, config, OBSERVER_SECRET)
        current.observe(0)
    except BaseException as error:
        failures.first(error)
    finally:
        if core is not None:
            failures.cleanup("observer_entry_core_close", core.close)
    failures.finish()
    if read is None or time.monotonic() + 30 >= min(end, read.budget_end()):
        raise Refused("late_private_observation")
    return result


if __name__ == "__main__":
    emit_main(run)
