"""Private Linux PID1 supervisor; no native imports or writes.

Only the parent-owned CREATED container/original SQL-success pipe authorizes
release. The frame is data, not authority. Worker termination is pidfd-bound;
PID1 exit closes the private namespace. PostgreSQL effects remain uncertain.
"""

import hashlib
import json
import math
import os
import selectors
import signal
import stat
import subprocess
import sys
import time

WORKER = "/home/frappe/frappe-bench/apps/erpnext/erpnext/exe_auth/native_allocate_worker.py"
LOG_CAP = 65536
RESULT_CAP = 2048


def closed_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_private_key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def receive_frame(fd, end):
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        raise ValueError("private_input_not_pipe")
    os.set_blocking(fd, False)
    data = bytearray()
    selector = selectors.DefaultSelector()
    try:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            left = end - time.monotonic()
            if left <= 0 or not selector.select(left):
                raise TimeoutError("private_startup_expired")
            chunk = os.read(fd, min(8192, 16385 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 16384:
                raise ValueError("private_frame_limit")
        if time.monotonic() >= end or not data.endswith(b"\n") or data.count(b"\n") != 1:
            raise ValueError("private_frame_eof")
        return bytes(data)
    finally:
        selector.close()


def pipe_identity(value):
    observed = os.fstat(value) if isinstance(value, int) else os.stat(value)
    if not stat.S_ISFIFO(observed.st_mode):
        raise ValueError("private_worker_not_fifo")
    return observed.st_dev, observed.st_ino, observed.st_uid, stat.S_IFMT(observed.st_mode)


def shrink_deadline(original_start, current_end, proposed_end, now):
    if type(proposed_end) not in (int, float) or not math.isfinite(proposed_end):
        raise ValueError("private_budget_invalid")
    if not original_start < proposed_end <= original_start + 300 or proposed_end <= now:
        raise ValueError("private_budget_expired")
    return min(current_end, proposed_end)


class AdmissionBudget:
    """One original startup end until one timely post-bind admission event."""
    def __init__(self, start, work_end):
        self.start = start
        self.startup_end = start + 5
        self.work_end = work_end
        self.admitted = False

    def deadline(self):
        return self.work_end if self.admitted else min(self.startup_end, self.work_end)

    def event(self, value, now):
        if type(value) is not dict:
            raise ValueError("private_budget_invalid")
        if set(value) == {"admitted_end"}:
            if self.admitted or now >= self.startup_end:
                raise ValueError("private_admission_duplicate_or_late")
            self.work_end = shrink_deadline(self.start, self.work_end, value["admitted_end"], now)
            self.admitted = True
        elif set(value) == {"budget_end"}:
            if not self.admitted:
                raise ValueError("private_budget_before_admission")
            self.work_end = shrink_deadline(self.start, self.work_end, value["budget_end"], now)
        else:
            raise ValueError("private_budget_invalid")


def worker_binding(q, result_fd):
    # Known live child only; no global proc enumeration or caller PID.
    if q.pid <= 1 or q.poll() is not None:
        raise ValueError("private_worker_not_live")
    namespace = os.stat("/proc/self/ns/pid")
    child = os.stat(f"/proc/{q.pid}/ns/pid")
    if (namespace.st_dev, namespace.st_ino) != (child.st_dev, child.st_ino):
        raise ValueError("private_worker_namespace")
    with open(f"/proc/{q.pid}/status", encoding="ascii") as handle:
        rows = dict(line.split(":", 1) for line in handle if ":" in line)
    if rows.get("PPid", "").strip() != "1" or [int(x) for x in rows.get("Uid", "").split()] != [1000] * 4:
        raise ValueError("private_worker_identity")
    for child_fd, parent_fd in ((0, q.stdin.fileno()), (1, q.stdout.fileno()),
                               (2, q.stderr.fileno()), (result_fd, result_fd)):
        if pipe_identity(parent_fd) != pipe_identity(f"/proc/{q.pid}/fd/{child_fd}"):
            raise ValueError("private_worker_pipe_mismatch")
    if q.poll() is not None:
        raise ValueError("private_worker_not_live")


def supervise():
    before = time.monotonic()
    end = before + 5
    if sys.platform != "linux" or os.getpid() != 1 or os.getuid() != 1000:
        raise ValueError("private_supervisor_identity")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise ValueError("private_pidfd_unavailable")
    q, pidfd, selector = None, None, None
    result_r, result_w = os.pipe()
    primary, secondary = None, []
    overflow = 0
    payload = None
    log_bytes = 0
    digest = hashlib.sha256()
    eof = {"stdout": False, "stderr": False, "result": False}
    result = bytearray()
    result_bytes = 0

    def later(error):
        nonlocal overflow
        if len(secondary) < 16:
            secondary.append(type(error).__name__)
        else:
            overflow += 1

    try:
        # Preserve the exact internally allocated descriptor through exec.
        # Its number is a locator, not dispatch authority: all four actual
        # child FIFOs are matched to retained parent pipes before release.
        q = subprocess.Popen(
            [sys.executable, "-B", "-I", WORKER, "--standby-result", str(result_w)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, pass_fds=(result_w,),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        pidfd = os.pidfd_open(q.pid, 0)
        worker_binding(q, result_w)
        selector = selectors.DefaultSelector()
        for name, pipe in (("stdout", q.stdout), ("stderr", q.stderr), ("result", result_r)):
            fd = pipe if isinstance(pipe, int) else pipe.fileno()
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ, name)
        raw = receive_frame(0, before + 5)
        frame = closed_json(raw)
        remaining = frame.get("remaining_work_milliseconds") if type(frame) is dict else None
        if type(remaining) is not int or not 1 <= remaining <= 270000:
            raise ValueError("private_frame_work")
        end = before + remaining / 1000 + 30
        admission = AdmissionBudget(before, end)
        if time.monotonic() >= before + 5:
            raise TimeoutError("private_startup_expired")
        worker_binding(q, result_w)
        gate = {"start": before, "startup_end": before + 5, "work_end": end}
        pending = memoryview(b"RELEASE " + json.dumps(gate, separators=(",", ":")).encode() + b"\n" + raw)
        os.close(result_w)
        result_w = None
        os.set_blocking(q.stdin.fileno(), False)
        selector.register(q.stdin.fileno(), selectors.EVENT_WRITE, "input")
        while selector.get_map():
            left = admission.deadline() - time.monotonic()
            if left <= 0:
                raise TimeoutError("private_lifetime_expired")
            for key, _ in selector.select(min(left, .1)):
                name = key.data
                if name == "input":
                    count = os.write(key.fd, pending)
                    if count <= 0:
                        raise ValueError("private_frame_write")
                    pending = pending[count:]
                    if not pending:
                        selector.unregister(key.fd)
                        q.stdin.close()
                    continue
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    eof[name] = True
                    selector.unregister(key.fd)
                    continue
                if name != "result":
                    log_bytes += len(chunk)
                    digest.update(chunk)
                    if log_bytes > LOG_CAP:
                        raise ValueError("native_output_limit")
                    continue
                result_bytes += len(chunk)
                result.extend(chunk)
                if result_bytes > RESULT_CAP:
                    raise ValueError("private_result_limit")
                while b"\n" in result:
                    line, _, rest = result.partition(b"\n")
                    result = bytearray(rest)
                    event = closed_json(line)
                    if type(event) is dict and set(event) in ({"admitted_end"}, {"budget_end"}):
                        admission.event(event, time.monotonic())
                        end = admission.work_end
                    elif payload is None and type(event) is dict and type(event.get("ok")) is bool:
                        payload = event
                    else:
                        raise ValueError("private_result_duplicate")
        left = admission.deadline() - time.monotonic()
        if left <= 0:
            raise TimeoutError("private_reap_expired")
        status = q.wait(timeout=left)
        if status not in (0, 1) or result or payload is None or not all(eof.values()) or (status == 0) != (payload.get("ok") is True) or (status == 0 and not admission.admitted):
            raise ValueError("private_worker_failed")
    except BaseException as error:
        primary = type(error).__name__
    finally:
        # Never signal same-namespace PID1. A retained pidfd is non-recycled
        # ownership; exiting this supervisor closes all namespace descendants.
        cleanup_end = min(end, time.monotonic() + 3)
        if q is not None and q.poll() is None:
            if pidfd is None:
                later(ValueError("private_worker_unbound"))
            else:
                try:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                except BaseException as error:
                    later(error)
            left = cleanup_end - time.monotonic()
            if left > 0:
                try:
                    q.wait(timeout=left)
                except BaseException as error:
                    later(error)
            else:
                later(TimeoutError("private_cleanup_expired"))
        for resource in (selector, q.stdin if q else None, q.stdout if q else None,
                         q.stderr if q else None):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    later(error)
        for fd in (result_r, result_w, pidfd):
            if fd is not None:
                try:
                    os.close(fd)
                except BaseException as error:
                    later(error)
    return payload, primary, secondary, overflow, eof, log_bytes, digest.hexdigest()


def main():
    try:
        payload, primary, secondary, overflow, eof, _count, _digest = supervise()
        closed = primary is None and not secondary and not overflow and all(eof.values())
        passed = closed and payload is not None and payload.get("ok") is True
        # Native logs are never emitted. A result is not readiness/receipt.
        output = payload if closed else {"ok": False, "code": "private_native_failed", "cleanup": {
            "primary_class": primary, "secondary_count": len(secondary), "overflow_count": overflow}}
        raw = (json.dumps(output, sort_keys=True) + "\n").encode()
        if len(raw) > RESULT_CAP or not stat.S_ISFIFO(os.fstat(1).st_mode):
            os._exit(1)
        os.set_blocking(1, False)
        if os.write(1, raw) != len(raw):
            os._exit(1)
        os._exit(0 if passed else 1)
    except BaseException:
        os._exit(1)


if __name__ == "__main__":
    main()
