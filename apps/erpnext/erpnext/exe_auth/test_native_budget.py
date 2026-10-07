"""Controlled actual pipe/read-wrapper tests, no SQL or native allocation."""
import importlib.util
import json
import math
import os
import sys
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent


def modules():
    package = types.ModuleType("private_budget_test_subject")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package
    for name in list(sys.modules):
        if name.startswith(package.__name__ + "."):
            del sys.modules[name]
    spec = importlib.util.spec_from_file_location(package.__name__ + ".native_allocate_worker", ROOT / "native_allocate_worker.py")
    worker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = worker
    spec.loader.exec_module(worker)
    worker.load_bindings()
    return worker, sys.modules[package.__name__ + ".native_transport"]


class BudgetPublisherTests(unittest.TestCase):
    def setUp(self):
        self.worker, self.transport = modules()
        self.r, self.w = os.pipe()
        self.worker.RESULT_FD = self.w
        self.addCleanup(self.close)

    def close(self):
        for fd in (self.r, self.w):
            if fd is not None:
                os.close(fd)
        self.r = self.w = None

    def frames(self):
        os.close(self.w)
        self.w = None
        raw = os.read(self.r, 4096)
        return raw, [json.loads(line) for line in raw.splitlines()]

    def terminal(self, operation, error_type=None):
        expected = error_type or self.worker.Refused
        with self.assertRaises(expected):
            operation()
        self.assertTrue(self.worker._budget_failed)
        with patch.object(self.worker.os, "write") as write:
            previous = self.worker._published_end or 280
            for value in (previous, previous - 1):
                with self.assertRaises(self.worker.Refused):
                    self.worker.publish_budget(value)
            write.assert_not_called()

    def test_many_equal_deadlines_keep_one_initial_and_all_strict_shrinks(self):
        self.worker.publish_budget(280, initial=True)
        for _ in range(1000):
            self.worker.publish_budget(280)
        smaller = math.nextafter(280.0, 0)
        self.worker.publish_budget(smaller)
        self.worker.publish_budget(270)
        raw, frames = self.frames()
        self.assertEqual(frames, [{"admitted_end": 280}, {"budget_end": smaller}, {"budget_end": 270}])
        self.assertEqual(self.worker._budget_bytes, len(raw))
        self.assertLess(len(raw), 1024)
        spec = importlib.util.spec_from_file_location("budget_receiver_subject", ROOT / "native_receiver.py")
        receiver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(receiver)
        admission = receiver.AdmissionBudget(100, 280)
        for frame in frames:
            admission.event(frame, 101)
        self.assertTrue(admission.admitted)
        self.assertEqual(admission.deadline(), 270)

    def test_actual_run_wrapper_keeps_all_1000_fresh_owner_reads(self):
        before = time.monotonic()
        initial = datetime.now(timezone.utc)
        frame = {"initial_sql_time": initial.isoformat(), "original_lease_expires_at": (initial + timedelta(seconds=300)).isoformat(), "remaining_work_milliseconds": 210000}
        value = {"controlled": True}
        calls = []
        class Reader:
            def __call__(self, observed):
                calls.append(dict(observed))
                return {"sql_time": datetime.now(timezone.utc).isoformat(), "lease_expires_at": frame["original_lease_expires_at"]}
            def close(self):
                pass
        def secret(path, *args):
            return json.dumps({"enabled": True} if path == self.worker.PACKAGE else {"host": "127.0.0.1", "port": 1, "user": "controlled_operator", "password": "controlled_not_a_credential"})
        def allocate(consume, read, config, operator):
            self.assertEqual(consume()[0], value)
            for _ in range(1000):
                read(value)
            return {"controlled": True}
        with patch.object(self.worker, "stable_secret", side_effect=secret), patch.object(self.worker, "frame_from_pipe", return_value=frame), patch.object(self.worker, "bind_frame", return_value=(value, {"database": "controlled_target"}, before + 240)), patch.object(self.worker, "CoreReader", return_value=Reader()), patch.object(self.worker, "allocate", side_effect=allocate):
            self.assertEqual(self.worker.run(before, before + 240)["state"], "pending_observation")
        self.assertEqual(calls, [value] * 1000)
        raw, frames = self.frames()
        self.assertEqual(len(frames), 2)
        self.assertEqual(set(frames[0]), {"admitted_end"})
        self.assertEqual(set(frames[1]), {"budget_end"})
        self.assertLess(frames[1]["budget_end"], frames[0]["admitted_end"])
        self.assertLess(len(raw), 1024)

    def test_distinct_shrinks_still_exhaust_original_cap_terminally(self):
        self.worker.publish_budget(280, initial=True)
        def exhaust():
            for end in range(279, 100, -1):
                self.worker.publish_budget(end)
        self.terminal(exhaust)
        self.assertGreater(self.worker._budget_bytes, 1024)
        raw, _ = self.frames()
        self.assertLessEqual(len(raw), 1024)

    def test_partial_write_preserves_success_cache_and_is_terminal(self):
        self.worker.publish_budget(280, initial=True)
        with patch.object(self.worker.os, "write", return_value=1):
            self.terminal(lambda: self.worker.publish_budget(270))
        self.assertEqual(self.worker._published_end, 280)

    def test_raised_write_preserves_original_exception_and_is_terminal(self):
        self.worker.publish_budget(280, initial=True)
        error = OSError("controlled")
        with patch.object(self.worker.os, "write", side_effect=error):
            with self.assertRaises(OSError) as caught:
                self.worker.publish_budget(270)
            self.assertIs(caught.exception, error)
        self.terminal(lambda: self.worker.publish_budget(280))
        self.assertEqual(self.worker._published_end, 280)

    def test_invalid_publication_is_terminal_before_any_write(self):
        for value in (True, float("nan"), float("inf"), 0, -1, "280"):
            worker, _ = modules()
            worker.RESULT_FD = self.w
            with patch.object(worker.os, "write") as write:
                with self.assertRaises(worker.Refused):
                    worker.publish_budget(value, initial=True)
                self.assertTrue(worker._budget_failed)
                with self.assertRaises(worker.Refused):
                    worker.publish_budget(280, initial=True)
                write.assert_not_called()

    def test_before_initial_duplicate_initial_and_extension_are_terminal(self):
        self.terminal(lambda: self.worker.publish_budget(280))
        for initial, end in ((True, 280), (False, 281)):
            self.worker, _ = modules()
            self.worker.RESULT_FD = self.w
            self.worker.publish_budget(280, initial=True)
            self.terminal(lambda: self.worker.publish_budget(end, initial=initial))


class ClosedRefusalTests(unittest.TestCase):
    def setUp(self):
        self.worker, self.transport = modules()

    def emit(self, error):
        r, w = os.pipe()
        try:
            def fail():
                raise error
            with patch.object(self.transport.os, "_exit", side_effect=SystemExit):
                with self.assertRaises(SystemExit):
                    self.transport.emit_main(fail, output_fd=w)
            os.close(w)
            w = None
            return json.loads(os.read(r, 4096))
        finally:
            os.close(r)
            if w is not None:
                os.close(w)

    def test_every_allowlisted_literal_is_emitted_with_existing_counters(self):
        self.assertEqual(len(self.transport.REFUSAL_CODES), 14)
        for code in self.transport.REFUSAL_CODES:
            row = self.emit(self.worker.Refused(code))
            self.assertEqual(row["cleanup"], {"primary_class": "Refused", "secondary_count": 0, "pending_count": 0, "overflow_count": 0, "refusal_code": code})

    def test_unknown_subclass_arbitrary_args_and_secret_text_are_omitted(self):
        class Subclass(self.worker.Refused):
            def __str__(self):
                raise AssertionError("must not stringify")
        class StringSubclass(str):
            pass
        for error in (self.worker.Refused("unknown"), self.worker.Refused("private_budget_channel:controlled_secret"), self.worker.Refused(123), self.worker.Refused("private_budget_channel", "extra"), self.worker.Refused(StringSubclass("private_budget_channel")), Subclass("private_budget_channel"), ValueError("private_budget_channel")):
            row = self.emit(error)
            self.assertNotIn("refusal_code", row["cleanup"])
            self.assertNotIn("controlled_secret", json.dumps(row))

    def test_exact_cleanup_primary_keeps_counters_and_code(self):
        cleanup = self.transport.CleanupFailure(self.worker.Refused("private_budget_channel"), [("close", OSError())], [("pending", object())])
        row = self.emit(cleanup)
        self.assertEqual(row["cleanup"], {"primary_class": "Refused", "secondary_count": 1, "pending_count": 1, "overflow_count": 0, "refusal_code": "private_budget_channel"})
        class Subclass(self.transport.CleanupFailure):
            pass
        self.assertNotIn("refusal_code", self.emit(Subclass(self.worker.Refused("private_budget_channel"), [], []))["cleanup"])


if __name__ == "__main__":
    unittest.main()
