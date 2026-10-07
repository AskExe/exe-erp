"""Finite controlled stdlib carrier tests; not Linux PID1/native proof."""
import importlib.util
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("supervisor_subject", Path(__file__).with_name("native_receiver.py"))
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class SupervisorCarrierTests(unittest.TestCase):
    def pipe(self, raw):
        r, w = os.pipe()
        try:
            os.write(w, raw)
        finally:
            os.close(w)
        self.addCleanup(os.close, r)
        return r

    def test_exact_pipe_bytes_and_eof(self):
        raw = b'{"version":"original"}\n'
        self.assertEqual(subject.receive_frame(self.pipe(raw), time.monotonic() + 1), raw)

    def test_duplicate_top_key_refused(self):
        with self.assertRaises(ValueError):
            subject.closed_json(b'{"tuple":{},"tuple":{}}')

    def test_duplicate_nested_key_refused(self):
        with self.assertRaises(ValueError):
            subject.closed_json(b'{"tuple":{"job":1,"job":2}}')

    def test_multiple_frames_refused(self):
        with self.assertRaises(ValueError):
            subject.receive_frame(self.pipe(b'{}\n{}\n'), time.monotonic() + 1)

    def test_missing_newline_refused(self):
        with self.assertRaises(ValueError):
            subject.receive_frame(self.pipe(b'{}'), time.monotonic() + 1)

    def test_expired_original_end_refused(self):
        with self.assertRaises(TimeoutError):
            subject.receive_frame(self.pipe(b'{}\n'), time.monotonic() - 1)

    def test_non_pid1_refused_before_launch(self):
        with patch.object(subject.os, "getpid", return_value=2), patch.object(subject.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                subject.supervise()
            launch.assert_not_called()

    def test_non_linux_refused_before_launch(self):
        with patch.object(subject.sys, "platform", "darwin"), patch.object(subject.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                subject.supervise()
            launch.assert_not_called()

    def test_private_worker_environment_is_fixed_before_launch(self):
        with patch.object(subject.sys, "platform", "linux"), \
             patch.object(subject.os, "getpid", return_value=1), \
             patch.object(subject.os, "getuid", return_value=1000), \
             patch.object(subject.os, "pidfd_open", create=True), \
             patch.object(subject.signal, "pidfd_send_signal", create=True), \
             patch.dict(os.environ, {"FRAPPE_STREAM_LOGGING": "caller-value", "PRIVATE_TEST_SECRET": "not-forwarded"}), \
             patch.object(subject.subprocess, "Popen", side_effect=RuntimeError) as launch:
            result = subject.supervise()
        self.assertEqual(result[1], "RuntimeError")
        self.assertEqual(launch.call_args.kwargs["env"], {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "FRAPPE_STREAM_LOGGING": "1"})


class InternalDeadlineTests(unittest.TestCase):
    def worker(self):
        spec = importlib.util.spec_from_file_location("worker_gate_subject", Path(__file__).with_name("native_allocate_worker.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def gate(self, start=100, startup=105, end=280):
        import json
        return b"RELEASE " + json.dumps({"start": start, "startup_end": startup, "work_end": end}).encode() + b"\n"

    def run_worker_header(self, raw, load):
        import logging
        import stat
        import types
        worker = self.worker()
        old_disable = logging.root.manager.disable
        selector = types.SimpleNamespace(register=lambda *args: None, select=lambda *args: True, close=lambda: None)
        try:
            with patch.object(worker.sys, "platform", "linux"), \
                 patch.object(worker.sys, "argv", ["worker", "--standby-result", "3"]), \
                 patch.object(worker.os, "getpid", return_value=2), \
                 patch.object(worker.os, "getppid", return_value=1), \
                 patch.object(worker.os, "getuid", return_value=1000), \
                 patch.object(worker.os, "fstat", return_value=types.SimpleNamespace(st_mode=stat.S_IFIFO)), \
                 patch.object(worker.os, "set_blocking"), \
                 patch.object(worker.os, "read", side_effect=[bytes([b]) for b in raw]), \
                 patch.object(worker.time, "monotonic", return_value=101), \
                 patch.object(worker.selectors, "DefaultSelector", return_value=selector), \
                 patch.object(worker, "load_bindings", side_effect=load) as bindings, \
                 patch.object(worker, "emit_main"):
                worker.main()
                return bindings.call_count
        finally:
            logging.disable(old_disable)

    def test_logging_disabled_after_release_before_native_imports(self):
        import logging
        seen = []
        self.assertEqual(self.run_worker_header(self.gate(), lambda: seen.append(logging.root.manager.disable)), 1)
        self.assertEqual(seen, [logging.CRITICAL])

    def test_invalid_release_does_not_load_native_bindings(self):
        with self.assertRaises(ValueError):
            self.run_worker_header(b"RELEASE {}\n", lambda: self.fail("native bindings loaded"))

    def test_delayed_worker_retains_original_start(self):
        self.assertEqual(self.worker().admit_gate(self.gate(), 101, 104.9), (100, 280))

    def test_delayed_worker_cannot_renew_receive_window(self):
        with self.assertRaises(ValueError):
            self.worker().admit_gate(self.gate(), 101, 105)

    def test_nonfinite_internal_gate_refused(self):
        with self.assertRaises(ValueError):
            self.worker().admit_gate(self.gate(end=float("inf")), 101, 102)

    def test_earlier_lease_absolute_end_retained_after_renewal(self):
        end = subject.shrink_deadline(100, 280, 200, 120)
        self.assertEqual(end, 200)
        self.assertEqual(subject.shrink_deadline(100, end, 260, 150), 200)

    def test_expired_earlier_lease_refused(self):
        with self.assertRaises(ValueError):
            subject.shrink_deadline(100, 280, 160, 160)

    def test_same_fifo_identity_matches_inherited_endpoint(self):
        r, w = os.pipe()
        duplicate = None
        try:
            # Same actual endpoint, not macOS opposite-end inode equality.
            duplicate = os.dup(r)
            self.assertEqual(subject.pipe_identity(r), subject.pipe_identity(duplicate))
        finally:
            if duplicate is not None:
                os.close(duplicate)
            os.close(r)
            os.close(w)

    def test_replacement_fifo_identity_does_not_match(self):
        first = os.pipe()
        replacement = os.pipe()
        try:
            self.assertNotEqual(subject.pipe_identity(first[0]), subject.pipe_identity(replacement[0]))
        finally:
            for fd in (*first, *replacement):
                os.close(fd)


class ExactPipeBindingTests(unittest.TestCase):
    def check(self, replace_fd=None):
        import io
        import types
        pipes = [os.pipe() for _ in range(4)]
        try:
            result_fd = pipes[3][1]
            q = types.SimpleNamespace(pid=42, poll=lambda: None,
                stdin=types.SimpleNamespace(fileno=lambda: pipes[0][1]),
                stdout=types.SimpleNamespace(fileno=lambda: pipes[1][0]),
                stderr=types.SimpleNamespace(fileno=lambda: pipes[2][0]))
            namespace = types.SimpleNamespace(st_dev=1, st_ino=2)
            # Explicit controlled Linux proc model: each child FIFO reports the
            # identity of its corresponding retained parent endpoint. This is
            # not a measurement of Linux opposite-end or namespace semantics.
            child = {0: pipes[0][1], 1: pipes[1][0], 2: pipes[2][0], result_fd: result_fd}
            def selected_stat(path):
                if path.endswith("/ns/pid"):
                    return namespace
                fd = int(path.rsplit("/", 1)[1])
                return os.fstat(pipes[0][0] if fd == replace_fd else child[fd])
            # Controlled proc carrier only: no physical UID/PID/namespace claim.
            with patch.object(subject.os, "stat", side_effect=selected_stat), patch("builtins.open", return_value=io.StringIO("PPid:\t1\nUid:\t1000 1000 1000 1000\n")):
                subject.worker_binding(q, result_fd)
        finally:
            for pair in pipes:
                for fd in pair:
                    os.close(fd)

    def test_exact_four_fifo_bindings_admitted(self):
        self.check()

    def test_swapped_stdout_fifo_refused_before_release(self):
        with self.assertRaises(ValueError):
            self.check(replace_fd=1)

    def test_swapped_result_fifo_refused_before_release(self):
        # Pass the descriptor selected by the actual owned pipe allocation.
        original = subject.pipe_identity
        count = 0
        def mismatch(value):
            nonlocal count
            answer = original(value)
            count += 1
            return (answer[0], answer[1] + 1, *answer[2:]) if count == 8 else answer
        with patch.object(subject, "pipe_identity", side_effect=mismatch):
            with self.assertRaises(ValueError):
                self.check()


class StartupAdmissionTests(unittest.TestCase):
    def test_blocked_import_keeps_original_five_second_deadline(self):
        state = subject.AdmissionBudget(100, 280)
        self.assertEqual(state.deadline(), 105)
        self.assertFalse(state.admitted)
        # No worker message has been observed: work end is not active.
        self.assertLessEqual(state.deadline() - 104.9, .100001)

    def test_timely_initial_event_selects_only_fixed_work_end(self):
        state = subject.AdmissionBudget(100, 280)
        state.event({"admitted_end": 270}, 104.9)
        self.assertEqual(state.deadline(), 270)
        self.assertTrue(state.admitted)

    def test_initial_event_at_original_startup_end_refused(self):
        state = subject.AdmissionBudget(100, 280)
        with self.assertRaises(ValueError):
            state.event({"admitted_end": 270}, 105)
        self.assertFalse(state.admitted)
        self.assertEqual(state.deadline(), 105)

    def test_duplicate_initial_event_cannot_renew(self):
        state = subject.AdmissionBudget(100, 280)
        state.event({"admitted_end": 200}, 104)
        with self.assertRaises(ValueError):
            state.event({"admitted_end": 270}, 104.5)
        self.assertEqual(state.deadline(), 200)

    def test_later_lease_event_cannot_expand_admitted_end(self):
        state = subject.AdmissionBudget(100, 280)
        state.event({"admitted_end": 270}, 104)
        state.event({"budget_end": 200}, 120)
        state.event({"budget_end": 260}, 150)
        self.assertEqual(state.deadline(), 200)

    def test_regular_budget_cannot_bypass_initial_admission(self):
        state = subject.AdmissionBudget(100, 280)
        with self.assertRaises(ValueError):
            state.event({"budget_end": 270}, 104)
        self.assertEqual(state.deadline(), 105)


class TerminationReserveTests(unittest.TestCase):
    def test_expired_shrunk_work_still_has_original_reap_reserve(self):
        original_end = 140
        state = subject.AdmissionBudget(100, original_end)
        state.event({"admitted_end": 130}, 104)
        state.event({"budget_end": 108}, 107)
        self.assertEqual(state.deadline(), 108)
        cleanup_end = subject.termination_deadline(original_end, 108.1)
        self.assertAlmostEqual(cleanup_end - 108.1, 3)
        # Pure termination time cannot admit a write under the expired lease.
        with self.assertRaises(ValueError):
            state.event({"budget_end": 108}, 108.1)
        self.assertEqual(state.deadline(), 108)

    def test_startup_refusal_reserve_does_not_reset_receive_window(self):
        state = subject.AdmissionBudget(100, 140)
        self.assertEqual(subject.termination_deadline(140, 105), 108)
        with self.assertRaises(ValueError):
            state.event({"admitted_end": 130}, 105)
        self.assertFalse(state.admitted)
        self.assertEqual(state.deadline(), 105)

    def test_original_overall_bound_caps_reap_and_never_renews(self):
        self.assertEqual(subject.termination_deadline(140, 139), 140)
        self.assertEqual(subject.termination_deadline(140, 141), 140)
        # Renewal of a work event cannot rewrite the independently held bound.
        original_end = 140
        state = subject.AdmissionBudget(100, original_end)
        state.event({"admitted_end": 130}, 104)
        state.event({"budget_end": 110}, 105)
        state.event({"budget_end": 130}, 106)
        self.assertEqual(state.deadline(), 110)
        self.assertEqual(subject.termination_deadline(original_end, 111), 114)


if __name__ == "__main__":
    unittest.main()
