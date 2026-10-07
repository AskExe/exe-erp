"""Controlled contracts/real temporary marker files, no native allocation.

Held until Source review. These tests do not authenticate a Core dispatch pipe.
"""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

package = types.ModuleType("erp_private_source_subject")
package.__path__ = [str(Path(__file__).parent)]
sys.modules[package.__name__] = package


def load(name):
    qualified = package.__name__ + "." + name
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


contract = load("native_contract")
subject = load("native_site")
core_reader = load("native_core")
cleanup = load("native_cleanup")
observer = load("native_observer")
receiver = load("native_allocate_worker")
receiver.load_bindings()


def value():
    out = {k: "00000000-0000-4000-8000-000000000001" for k in ("job_id", "lease_token", "company_id", "deployment_id", "request_key", "intent_id", "action_id")}
    out.update(attempt=1, worker_id="private-worker", product="erp-site", profile_sha256="a" * 64, config_sha256="b" * 64, initializer_sha256="c" * 64)
    return out


def row(v):
    out = {k: x for k, x in v.items() if k != "lease_token"}
    out.update(owner_subject="00000000-0000-4000-8000-000000000002", sql_time="2026-10-05T00:00:00+00:00", lease_expires_at="2026-10-05T00:04:00+00:00")
    return out


class TestPrivateNativeContract(unittest.TestCase):
    def testPrivateProgressSuppressionRestoresOnSuccessAndInstallerError(self):
        from unittest.mock import patch
        calls = []
        def original(*args, **kwargs):
            calls.append((args, kwargs))
        sync = types.SimpleNamespace(update_progress_bar=original)
        model = types.ModuleType("frappe.model")
        model.sync = sync
        error = RuntimeError("controlled installer failure")
        with patch.dict(sys.modules, {"frappe.model": model}):
            with subject.private_install_progress():
                sync.update_progress_bar("Updating DocTypes for erpnext", 0, 895)
            self.assertIs(sync.update_progress_bar, original)
            with self.assertRaises(RuntimeError) as caught:
                with subject.private_install_progress():
                    sync.update_progress_bar("Updating DocTypes for frappe", 0, 351)
                    raise error
            self.assertIs(caught.exception, error)
            self.assertIs(sync.update_progress_bar, original)
            sync.update_progress_bar("ordinary progress", 0, 1)
        self.assertEqual(calls, [(("ordinary progress", 0, 1), {})])

    def testInheritedWritableBridgeRefusesBeforeNativeAdmission(self):
        from unittest.mock import patch
        subject.require_private_environment({})
        called = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(subject.os.environ, {"EXE_BRIDGE_DATABASE_URL": "controlled-not-a-real-dsn"}):
                with self.assertRaises(contract.Refused):
                    subject.allocate(lambda: called.append("dispatch"), lambda _: called.append("core"), {"sites_dir": directory}, {})
            self.assertEqual(called, [])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def testExactTupleRejectsForeignKindsExtraFieldsAndBoolAttempt(self):
        for change in ({"product": "erp"}, {"product": "crm-workspace"}, {"attempt": True}, {"email": "someone@example.test"}, {"company_id": "bad"}):
            with self.assertRaises(contract.Refused):
                contract.validate_tuple({**value(), **change})

    def testOriginalStartStructuralBindingDoesNotCreateDispatch(self):
        v = value()
        start = dict(intent_id=v["intent_id"], action_id=v["action_id"], started_attempt=1, started_by="private-worker", started_at="2026-10-05T00:00:00Z")
        contract.successful_start(start, v)
        for change in ({"started_attempt": 2}, {"started_by": "other"}, {"action_id": "00000000-0000-4000-8000-000000000009"}):
            with self.assertRaises(contract.Refused):
                contract.successful_start({**start, **change}, v)
        self.assertFalse(hasattr(contract, "dispatch_from_file"))

    def testCurrentReadCannotExtendOriginalOrEarlierLease(self):
        v = value()
        readings = iter([row(v), {**row(v), "lease_expires_at": "2026-10-05T00:02:00Z"}, row(v)])
        current = contract.CurrentOwner(v, lambda _: next(readings), 200, clock=lambda: 0)
        current.observe()
        self.assertEqual(current.end, 200)
        current.observe()
        shorter = current.end
        current.observe()
        self.assertEqual(current.end, shorter)

    def testReadIntervalAndCleanupReserveAreCharged(self):
        now = [0]
        def read(v):
            now[0] = 180
            return row(v)
        current = contract.CurrentOwner(value(), read, 200, clock=lambda: now[0])
        with self.assertRaises(contract.Refused):
            current.observe()

    def testCurrentCreatorChangeOrForeignTupleRefuses(self):
        for key, changed in (("owner_subject", "00000000-0000-4000-8000-000000000009"), ("company_id", "00000000-0000-4000-8000-000000000009")):
            rows = iter([row(value()), {**row(value()), key: changed}])
            current = contract.CurrentOwner(value(), lambda _: next(rows), 250, clock=lambda: 0)
            current.observe()
            with self.assertRaises(contract.Refused):
                current.observe()

    def testNativeCurrentFailureIsNotReplacedByCachedProjection(self):
        error = RuntimeError("controlled failure")
        def read(_):
            raise error
        current = contract.CurrentOwner(value(), read, 250, clock=lambda: 0)
        with self.assertRaises(RuntimeError) as caught:
            current.observe()
        self.assertIs(caught.exception, error)

    def testInitialRoleIsFiniteAndNotControlOrGlobalAdmin(self):
        self.assertEqual(subject.DOCTYPES, ("Company", "Customer", "Supplier", "Item"))
        self.assertFalse(set(subject.DOCTYPES) & set(subject.CONTROL))
        self.assertNotIn(subject.ROLE, ("Administrator", "System Manager", "Script Manager"))
        self.assertIn("share", subject.WRITE)


class TestOwnedFirstWriter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = dict(site="erp.example.test", database="fresh_erp", sites_dir=str(self.root), profile_sha256="a" * 64, config_sha256="b" * 64, initializer_sha256="c" * 64)

    def tearDown(self):
        self.temp.cleanup()

    def testFreshSiteAndAnyExistingDatabaseLoginRefuseReuse(self):
        subject.validate_config(self.config)
        base = self.root / "existing.example.test"
        base.mkdir()
        (base / "site_config.json").write_text(json.dumps({"db_user": "fresh_erp"}))
        with self.assertRaises(contract.Refused):
            subject.validate_config(self.config)

    def testExistingTargetAndSymlinkRefuse(self):
        target = self.root / self.config["site"]
        target.mkdir()
        with self.assertRaises(contract.Refused):
            subject.validate_config(self.config)
        target.rmdir()
        target.symlink_to(self.root / "absent")
        with self.assertRaises(contract.Refused):
            subject.validate_config(self.config)

    def testMarkerNoReplacePersistsQuarantineAndDoesNotPersistLeaseToken(self):
        v = value()
        body, digest = subject.marker(self.root, v, row(v)["owner_subject"], self.config, "native-user@users.invalid")
        saved = self.root / ".exe-native-first-writers" / (v["action_id"] + ".json")
        before = saved.read_bytes()
        self.assertNotIn("lease_token", body)
        self.assertEqual(body["state"], "issued_or_quarantined")
        self.assertEqual(len(digest), 64)
        with self.assertRaises(contract.Refused):
            subject.marker(self.root, v, row(v)["owner_subject"], self.config, "other@users.invalid")
        self.assertEqual(saved.read_bytes(), before)

    def testMarkerRootSymlinkDoesNotWriteOutsideOwnedRoot(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / ".exe-native-first-writers").symlink_to(other, target_is_directory=True)
        with self.assertRaises(contract.Refused):
            subject.marker(self.root, value(), row(value())["owner_subject"], self.config, "native-user@users.invalid")
        self.assertEqual(list(other.iterdir()), [])

    def testOwnedSecretHasExactModesAndRejectsSymlink(self):
        self.root.chmod(0o700)
        path = self.root / "secret"
        path.write_text("controlled-not-a-real-secret")
        path.chmod(0o400)
        self.assertEqual(subject.stable_secret(path), "controlled-not-a-real-secret")
        path.chmod(0o644)
        with self.assertRaises(contract.Refused):
            subject.stable_secret(path)


class TestActualCoreQueryBoundary(unittest.TestCase):
    def setup_reader(self, result=None, error=None):
        from unittest.mock import patch
        calls = []
        class Cursor:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def execute(self, query, args):
                calls.append((query, args))
                if error is not None:
                    raise error
            def fetchmany(self, limit):
                calls.append(("fetchmany", limit))
                return [(row(value()) if result is None else result,)]
            def close(self):
                calls.append(("cursor_close", None))
        class Connection:
            autocommit = False
            closed = False
            def cursor(self):
                return Cursor()
            def close(self):
                self.closed = True
        connection = Connection()
        fake = types.ModuleType("psycopg2")
        def connect(dsn, **kwargs):
            calls.append(("connect", kwargs))
            return connection
        fake.connect = connect
        with patch.dict(sys.modules, {"psycopg2": fake}), patch.object(core_reader, "stable_secret", return_value="controlled-dsn"):
            reader = core_reader.CoreReader("/held-secret", contract.time.monotonic() + 30)
        return reader, connection, calls

    def testActualParameterizedReaderUsesExactThirteenTupleOnceAndCloses(self):
        reader, connection, calls = self.setup_reader()
        self.assertEqual(reader(value()), row(value()))
        query, args = calls[1]
        self.assertIn("SELECT core.read_native_action_owner(", query)
        self.assertEqual(len(args), 13)
        self.assertEqual(args[0], value()["job_id"])
        self.assertEqual(args[-1], value()["action_id"])
        self.assertNotIn("start_native_initialization", query)
        reader.close()
        self.assertTrue(connection.closed)

    def testReaderPreservesActualSqlFailureWithoutFallback(self):
        error = RuntimeError("controlled refusal")
        reader, _connection, calls = self.setup_reader(error=error)
        with self.assertRaises(RuntimeError) as caught:
            reader(value())
        self.assertIs(caught.exception, error)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1], ("cursor_close", None))
        reader.close()

    def testForeignInvalidTupleDoesNotReachDatabase(self):
        reader, _connection, calls = self.setup_reader()
        with self.assertRaises(contract.Refused):
            reader({**value(), "product": "crm-workspace"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["connect_timeout"], 3)
        self.assertIn("statement_timeout=1000", calls[0][1]["options"])
        reader.close()


class TestCommitAndCleanup(unittest.TestCase):
    def testActualMarkerWriteAndFsyncFailuresKeepDescriptorCloseFailures(self):
        import os
        import stat
        from unittest.mock import patch
        for phase in ("write", "marker_fsync", "directory_fsync"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                primary, close_error = OSError("not logged"), RuntimeError("not logged")
                real_write, real_fsync, real_close = os.write, os.fsync, os.close
                def write(fd, raw):
                    if phase == "write":
                        raise primary
                    return real_write(fd, raw)
                def fsync(fd):
                    directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                    if (phase == "directory_fsync") == directory and phase != "write":
                        raise primary
                    return real_fsync(fd)
                def close(fd):
                    directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                    real_close(fd)  # Actually close the owned test descriptor.
                    if (phase == "directory_fsync") == directory:
                        raise close_error
                with patch.object(subject.os, "write", write), patch.object(subject.os, "fsync", fsync), patch.object(subject.os, "close", close):
                    with self.assertRaises(cleanup.CleanupFailure) as caught:
                        subject.marker(root, value(), row(value())["owner_subject"], {"site": "erp.example.test", "database": "fresh_erp"}, "opaque@users.invalid")
                self.assertIs(caught.exception.primary, primary)
                self.assertEqual(len(caught.exception.secondary), 1)
                self.assertIs(caught.exception.secondary[0][1], close_error)
                self.assertTrue((root / ".exe-native-first-writers" / (value()["action_id"] + ".json")).exists())

    def testAllocationFailureAndActualOwnedLockReleaseFailureStaySeparate(self):
        primary, close_error = ValueError("not logged"), OSError("not logged")
        calls = []
        class Lock:
            def acquire(self, timeout):
                calls.append("acquired")
            def release(self):
                calls.append("released")
                raise close_error
        with self.assertRaises(cleanup.CleanupFailure) as caught:
            with subject.OwnedInstallLock(Lock(), contract.time.monotonic() + 3):
                calls.append("native_operation")
                raise primary
        self.assertIs(caught.exception.primary, primary)
        self.assertIs(caught.exception.secondary[0][1], close_error)
        self.assertEqual(calls, ["acquired", "native_operation", "released"])

    def testOneCombinedFailureCapAccountsNestedAndLaterOverflowWithoutReplacingPrimary(self):
        primary = ValueError("not logged")
        errors = [RuntimeError("not logged") for _ in range(40)]
        failures = cleanup.Failures(0, clock=lambda: 1)
        nested = cleanup.CleanupFailure(primary, [("nested_close", e) for e in errors[:12]], [("nested_pending", "not_attempted_deadline")] * 12)
        failures.first(nested)
        failures.first(errors[12])
        failures.local_state("local_release", lambda: (_ for _ in ()).throw(errors[13]))
        failures.cleanup("later_close", lambda: None)
        with self.assertRaises(cleanup.CleanupFailure) as caught:
            failures.finish()
        self.assertIs(caught.exception.primary, primary)
        self.assertEqual(len(caught.exception.secondary) + len(caught.exception.pending), 16)
        self.assertEqual(caught.exception.overflow, {"secondary": 2, "pending": 9})
        self.assertEqual(failures.sanitized()["overflow"], {"secondary": 2, "pending": 9})
        # Full accounting does not suppress a required later cleanup attempt.
        calls = []
        live = cleanup.Failures(contract.time.monotonic() + 3)
        live.first(caught.exception)
        live.cleanup("needed_close", lambda: calls.append("closed"))
        self.assertEqual(calls, ["closed"])

    def testExpiredParentSecretBudgetDoesNotOpenOrRenewLocalWindow(self):
        from unittest.mock import patch
        with patch.object(subject.os, "open") as opened:
            with self.assertRaises(contract.Refused):
                subject.stable_secret("/no-secret", absolute_end=contract.time.monotonic() - 1)
        opened.assert_not_called()

    def testCommitSucceededButOwnerLostWhileCommitHeldCannotReturnMetadata(self):
        now, committed, calls = [0], [], []
        live = [True]
        def read(v):
            calls.append("read")
            if not live[0]:
                raise contract.Refused("actual_current_owner_denied")
            return row(v)
        current = contract.CurrentOwner(value(), read, 250, clock=lambda: now[0])
        current.observe()
        class Database:
            def commit(self):
                committed.append("committed")
                live[0] = False
        with self.assertRaises(contract.Refused):
            subject.commit_and_observe(types.SimpleNamespace(db=Database()), current)
        self.assertEqual(committed, ["committed"])
        self.assertEqual(calls, ["read", "read"])

    def testActualCommitContinuationPastOriginalLeaseRefuses(self):
        now = [0]
        current = contract.CurrentOwner(value(), lambda v: row(v), 200, clock=lambda: now[0])
        current.observe()
        class Database:
            def commit(self):
                now[0] = 201
        with self.assertRaises(contract.Refused):
            subject.commit_and_observe(types.SimpleNamespace(db=Database()), current)

    def testOriginatingErrorAndAllCleanupErrorsRetainedSeparately(self):
        primary = RuntimeError("must not be logged")
        second, third = OSError("secret path"), ValueError("secret argument")
        failures = cleanup.Failures(contract.time.monotonic() + 5)
        failures.first(primary)
        def fail(error):
            raise error
        failures.cleanup("native_rollback", lambda: fail(second))
        failures.cleanup("frappe_destroy", lambda: fail(third))
        with self.assertRaises(cleanup.CleanupFailure) as caught:
            failures.finish()
        self.assertIs(caught.exception.primary, primary)
        self.assertEqual(caught.exception.secondary, (("native_rollback", second), ("frappe_destroy", third)))
        rendered = json.dumps(failures.sanitized())
        self.assertNotIn("secret", rendered)
        self.assertNotIn("must not", rendered)

    def testDeadlineDoesNotStartAnotherCleanupOrRenewClock(self):
        failures = cleanup.Failures(4, clock=lambda: 5)
        called = []
        failures.cleanup("native_rollback", lambda: called.append(True))
        self.assertEqual(called, [])
        with self.assertRaises(cleanup.CleanupFailure):
            failures.finish()
        self.assertEqual(failures.sanitized()["pending"], ["native_rollback"])

    def testCleanupUsesActualCallerContextAndNestedFailuresRemainSeparate(self):
        import contextvars
        context = contextvars.ContextVar("controlled_native_context")
        context.set("actual-caller")
        observed = []
        failures = cleanup.Failures(contract.time.monotonic() + 5)
        failures.cleanup("context_close", lambda: observed.append(context.get()))
        self.assertEqual(observed, ["actual-caller"])
        primary, second = RuntimeError("not logged"), OSError("not logged")
        failures.first(cleanup.CleanupFailure(primary, [("inner_close", second)], []))
        with self.assertRaises(cleanup.CleanupFailure) as caught:
            failures.finish()
        self.assertIs(caught.exception.primary, primary)
        self.assertEqual(caught.exception.secondary, (("inner_close", second),))


class TestProtectedReceiver(unittest.TestCase):
    def frame(self):
        return dict(version="core-first-writer-v1", tuple=value(), initial_sql_time="2026-10-05T00:00:00Z", original_lease_expires_at="2026-10-05T00:04:00Z", remaining_work_milliseconds=200000)

    def package(self):
        p = {k: value()[k] for k in ("job_id", "intent_id", "company_id", "deployment_id", "profile_sha256", "config_sha256", "initializer_sha256")}
        p.update(version="erp-native-package-v1", enabled=True, uid=1000, image_id="sha256:" + "a" * 64, site="erp.example.test", database="fresh_erp")
        return p

    def testPipeFrameRequiresExactlyFiveKeysAndChargesReceivingInterval(self):
        from unittest.mock import patch
        with patch.object(receiver.os, "getuid", return_value=1000):
            v, _config, end = receiver.bind_frame(self.frame(), self.package(), 0, 5)
            self.assertEqual(v, value())
            self.assertEqual(end, 230)
            with self.assertRaises(contract.Refused):
                receiver.bind_frame({**self.frame(), "action_file": "forbidden"}, self.package(), 0, 5)
            with self.assertRaises(contract.Refused):
                receiver.bind_frame(self.frame(), self.package(), 0, 50)

    def testDisabledOrForeignPackageCannotAuthorizeFrame(self):
        from unittest.mock import patch
        with patch.object(receiver.os, "getuid", return_value=1000):
            for change in ({"enabled": False}, {"uid": 0}, {"company_id": "00000000-0000-4000-8000-000000000009"}):
                with self.assertRaises(contract.Refused):
                    receiver.bind_frame(self.frame(), {**self.package(), **change}, 0, 1)

    def testCurrentSqlCanShortenButNeverExtendOriginalLease(self):
        f = self.frame()
        v = value()
        read = receiver.bounded_owner_read(lambda _: {**row(v), "lease_expires_at": "2026-10-05T00:05:00Z"}, f)
        self.assertEqual(read(v)["lease_expires_at"], f["original_lease_expires_at"])
        read = receiver.bounded_owner_read(lambda _: {**row(v), "lease_expires_at": "2026-10-05T00:01:00Z"}, f)
        self.assertEqual(read(v)["lease_expires_at"], "2026-10-05T00:01:00Z")
        with self.assertRaises(contract.Refused):
            receiver.bounded_owner_read(lambda _: {**row(v), "sql_time": "2026-10-04T23:59:59Z"}, f)(v)

    def testDuplicateKeysAndInstallerOutputBudgetRefuse(self):
        with self.assertRaises(contract.Refused):
            receiver.closed_json('{"version":"one","version":"two"}')
        sink = receiver.DiscardNativeOutput()
        sink.write("not exposed")
        with self.assertRaises(contract.Refused):
            sink.write("x" * 65536)

    def testInitialSqlTransitDebitFreezesOriginalWorkAndLaterReadsCannotRenew(self):
        now = [10]
        f = self.frame()
        rows = iter([{**row(value()), "sql_time": "2026-10-05T00:00:20Z"}, {**row(value()), "sql_time": "2026-10-05T00:00:21Z"}])
        read = receiver.bounded_owner_read(lambda _: next(rows), f, receive_before=0, original_end=230, clock=lambda: now[0])
        read(value())
        end = read.budget_end()
        self.assertAlmostEqual(end, 209.999)
        now[0] = 11
        read(value())
        self.assertEqual(read.budget_end(), end)

    def testActualOwnedPipeRequiresEofAndOneClosedFrame(self):
        read_fd, write_fd = receiver.os.pipe()
        failures = cleanup.Failures(contract.time.monotonic() + 2)
        try:
            receiver.os.write(write_fd, b'{"fixture":true}\n')
            receiver.os.close(write_fd)
            write_fd = None
            self.assertEqual(receiver.frame_from_pipe(read_fd, contract.time.monotonic() + 1), {"fixture": True})
        except BaseException as error:
            failures.first(error)
        finally:
            failures.cleanup("pipe_read_close", lambda: receiver.os.close(read_fd))
            if write_fd is not None:
                failures.cleanup("pipe_write_close", lambda: receiver.os.close(write_fd))
        failures.finish()

    def testActualOwnedPipeRejectsSecondFrameAndClosedOversize(self):
        for raw in (b'{}\n{}\n', b'x' * 16385):
            read_fd, write_fd = receiver.os.pipe()
            failures = cleanup.Failures(contract.time.monotonic() + 2)
            try:
                # Small test-specific cap ensures one pipe write never blocks.
                receiver.os.write(write_fd, raw[:256])
                receiver.os.close(write_fd)
                write_fd = None
                with self.assertRaises(contract.Refused):
                    receiver.frame_from_pipe(read_fd, contract.time.monotonic() + 1, maximum=128)
            except BaseException as error:
                failures.first(error)
            finally:
                failures.cleanup("pipe_read_close", lambda: receiver.os.close(read_fd))
                if write_fd is not None:
                    failures.cleanup("pipe_write_close", lambda: receiver.os.close(write_fd))
            failures.finish()

    def testRegularActionFileCannotActAsProtectedStdin(self):
        with tempfile.TemporaryFile() as file:
            file.write(b'{}\n')
            file.seek(0)
            with self.assertRaises(contract.Refused):
                receiver.frame_from_pipe(file.fileno(), contract.time.monotonic() + 1)


class TestActualNativeReaderGuard(unittest.TestCase):
    def testActualGuardRefusesFalseMissingAndAmbiguousCatalogRows(self):
        class Cursor:
            def execute(self, query, args):
                self.query, self.args = query, args
            def fetchall(self):
                return self.rows
        cursor = Cursor()
        cursor.rows = [(True,)]
        observer.assert_native_reader(cursor)
        self.assertEqual(len(observer.READ_SURFACE), 8)
        self.assertEqual(observer.READ_SURFACE["__Auth"], ("name", "doctype"))
        self.assertEqual(cursor.args[-1], sum(len(cols) for cols in observer.READ_SURFACE.values()))
        for rows in ([], [(False,)], [(True,), (True,)]):
            cursor.rows = rows
            with self.assertRaises(contract.Refused):
                observer.assert_native_reader(cursor)

    def testFreshFinalCatalogDriftDeniesObservationAndStillRollsBack(self):
        v = value()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = dict(site="erp.example.test", database="fresh_erp", sites_dir=temporary)
            user = "native-user@users.invalid"
            binding, digest = subject.marker(root, v, row(v)["owner_subject"], config, user)
            seen, guards, closes = [], [0], []
            class Cursor:
                def execute(self, query, args=None):
                    seen.append(query)
                    self.rows = []
                    if query.startswith("WITH approved"):
                        guards[0] += 1
                        self.rows = [(guards[0] == 1,)]
                    elif query.startswith("SELECT current_user"):
                        self.rows = [("reader", "fresh_erp", 4242, True, False, False, False, False, False)]
                    elif query.startswith("SELECT binding"):
                        self.rows = [(binding, digest, user, row(v)["owner_subject"])]
                    elif query.startswith('SELECT enabled,user_type'):
                        self.rows = [(1, "System User")]
                    elif query.startswith('SELECT role FROM'):
                        self.rows = [(subject.ROLE,)]
                    elif query.startswith('SELECT disabled,desk_access'):
                        self.rows = [(0, 1)]
                    elif query.startswith('SELECT parent,permlevel'):
                        self.rows = [(d, 0, 0, 1, 1, 1) for d in sorted(subject.DOCTYPES)]
                    elif query.startswith("SELECT 1 FROM pg_trigger"):
                        self.rows = [(1,), (1,)]
                def fetchone(self):
                    return self.rows[0] if self.rows else None
                def fetchall(self):
                    return self.rows
                def fetchmany(self, _):
                    return self.rows
                def close(self):
                    closes.append("cursor")
            class Connection:
                def set_session(self, **kwargs):
                    self.readonly = kwargs["readonly"]
                def cursor(self):
                    return Cursor()
                def rollback(self):
                    closes.append("rollback")
            connection = Connection()
            with self.assertRaises(contract.Refused):
                observer.observe(v, lambda _: row(v), contract.time.monotonic() + 200, config, connection)
            self.assertTrue(connection.readonly)
            self.assertEqual(guards[0], 2)
            self.assertEqual(closes, ["cursor", "rollback"])
            self.assertTrue(any(q.startswith("SELECT binding") for q in seen))
if __name__ == "__main__":
    unittest.main()
