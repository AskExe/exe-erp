"""Held finite phase controls; no Frappe/psycopg/native service imports.

These controls prove local refusal/order only. They do not authenticate a parent
pipe, native image, mounted UID, installed database or actual PostgreSQL grants.
"""

import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location('erp_private_phase_fixture', Path(__file__).with_name('test_native_site.py'))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
load, value, row = _fixture.load, _fixture.value, _fixture.row

contract = load('native_contract')
setup_module = load('native_observer_setup')
transport = load('native_transport')


class SQL(str):
    def format(self, *args):
        return SQL(str(self).format(*args))
    def join(self, args):
        return SQL(str(self).join(args))


class Cursor:
    def __init__(self, events, existing=False, preload=''):
        self.events, self.existing, self.preload = events, existing, preload
        self.rows = []
    def execute(self, statement, args=None):
        statement = str(statement)
        # Intentionally retain statement kind only, never the password bind.
        self.events.append(statement.split()[0] + ':' + statement.split()[1])
        self.rows = []
        if "shared_preload_libraries" in statement:
            self.rows = [(self.preload, '', '')]
        elif "current_setting('pgaudit.log'" in statement:
            self.rows = [(None,)]
        elif 'SELECT current_database()' in statement:
            self.rows = [('exe_fixture', 123, True, True, True)]
        elif 'SELECT 1 FROM pg_roles' in statement and self.existing:
            self.rows = [(1,)]
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def close(self):
        self.events.append('cursor-close')


class Connection:
    def __init__(self, cursor, on_commit=lambda: None, rollback_error=None):
        self.c = cursor
        self.on_commit, self.rollback_error = on_commit, rollback_error
    def set_session(self, **kwargs):
        pass
    def cursor(self):
        return self.c
    def commit(self):
        self.c.events.append('commit')
        self.on_commit()
    def rollback(self):
        self.c.events.append('rollback')
        if self.rollback_error is not None:
            raise self.rollback_error


class TestPrivatePhases(unittest.TestCase):
    def testObserverCredentialCanBePreparedBeforeUnpredictableStartAction(self):
        # Core creates action_id only at start, after the protected DSN was
        # admitted. The already reserved immutable intent is known beforehand.
        before = value()
        fields = {'host': 'owned-db', 'port': '5432', 'dbname': 'exe_fixture',
                  'user': 'exe_erp_observer_' + before['intent_id'].replace('-', ''),
                  'password': 'x' * 32}
        after = {**before, 'action_id': '99999999-9999-4999-8999-999999999999'}
        for current in (before, after):
            self.assertEqual(setup_module.observer_credentials(
                'controlled-not-a-dsn', {'host': 'owned-db', 'port': 5432},
                {'database': 'exe_fixture'}, current, lambda _: fields),
                (fields['user'], fields['password']))
        foreign = {**after, 'intent_id': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'}
        with self.assertRaises(contract.Refused):
            setup_module.observer_credentials(
                'controlled-not-a-dsn', {'host': 'owned-db', 'port': 5432},
                {'database': 'exe_fixture'}, foreign, lambda _: fields)

    def credentials(self, **changes):
        fields = {'host': 'owned-db', 'port': '5432', 'dbname': 'exe_fixture', 'user': 'exe_erp_observer_' + value()['intent_id'].replace('-', ''), 'password': 'x' * 32}
        fields.update(changes)
        return setup_module.observer_credentials('controlled-not-a-dsn', {'host': 'owned-db', 'port': 5432}, {'database': 'exe_fixture'}, value(), lambda _: fields)

    def testObserverCredentialIsExactIntentDatabaseAndEndpoint(self):
        role, password = self.credentials()
        self.assertEqual(role, 'exe_erp_observer_' + value()['intent_id'].replace('-', ''))
        self.assertEqual(len(password), 32)
        for change in ({'user': 'existing_owner'}, {'dbname': 'foreign'}, {'host': 'foreign'}, {'port': '5433'}, {'options': '-c role=admin'}, {'password': 'bad password'}):
            with self.assertRaises(contract.Refused):
                self.credentials(**change)

    def testUnknownPreloadRefusesBeforePasswordOrGrant(self):
        events = []
        with self.assertRaises(contract.Refused):
            setup_module.suppress_collection(Cursor(events, preload='unknown_module'))
        self.assertFalse(any(x.startswith(('CREATE:', 'GRANT:')) for x in events))

    def run_setup(self, events, existing=False, on_commit=lambda: None, read=None, rollback_error=None):
        cursor = Cursor(events, existing)
        connection = Connection(cursor, on_commit, rollback_error)
        fake = types.ModuleType('psycopg2')
        fake.sql = types.SimpleNamespace(SQL=SQL, Identifier=SQL)
        with patch.dict(sys.modules, {'psycopg2': fake}), patch.object(setup_module, 'read_native_binding', return_value='opaque-user'):
            return setup_module.setup(value(), read or (lambda _: row(value())), contract.time.monotonic() + 250, {'database': 'exe_fixture'}, connection, 'exe_erp_observer_' + value()['intent_id'].replace('-', ''), 'x' * 32)

    def testExistingRoleCannotBeAdoptedOrAltered(self):
        events = []
        with self.assertRaises(contract.Refused):
            self.run_setup(events, existing=True)
        self.assertFalse(any(x.startswith(('CREATE:', 'ALTER:', 'GRANT:')) for x in events))
        self.assertIn('rollback', events)

    def testCollectionSuppressedBeforeCreateAndExactFiniteGrants(self):
        events = []
        result = self.run_setup(events)
        create = events.index('CREATE:ROLE')
        self.assertGreater(create, events.index('SET:LOCAL'))
        self.assertEqual(sum(x == 'GRANT:SELECT' for x in events), len(setup_module.READ_SURFACE))
        self.assertEqual(sum(x == 'CREATE:ROLE' for x in events), 1)
        self.assertEqual(result['state'], 'pending_observation')
        self.assertEqual(result['native_readiness'], 'unverified')
        self.assertEqual(events.count('commit'), 1)

    def testOwnerLossDuringHeldCommitDeniesWithoutAnotherFinancialPhase(self):
        events, changed = [], [False]
        def read(_):
            if changed[0]:
                return {**row(value()), 'owner_subject': '00000000-0000-4000-8000-000000000009'}
            return row(value())
        with self.assertRaises(contract.Refused):
            self.run_setup(events, on_commit=lambda: changed.__setitem__(0, True), read=read)
        self.assertEqual(events.count('commit'), 1)
        self.assertEqual(sum(x == 'CREATE:ROLE' for x in events), 1)
        self.assertIn('rollback', events)

    def testPostCommitLeaseLossRefusesRatherThanObserverSuccess(self):
        events, committed = [], [False]
        def read(_):
            if committed[0]:
                return {**row(value()), 'lease_expires_at': row(value())['sql_time']}
            return row(value())
        with self.assertRaises(contract.Refused):
            self.run_setup(events, on_commit=lambda: committed.__setitem__(0, True), read=read)
        self.assertEqual(events.count('commit'), 1)

    def testOriginatingOwnerFailureAndRollbackFailureBothRetained(self):
        events = []
        def read(_):
            return {**row(value()), 'company_id': '00000000-0000-4000-8000-000000000009'}
        # Admission fails before cursor/transaction, so use commit-loss to
        # measure actual issued setup plus its separate rollback failure.
        changed = [False]
        def after_read(_):
            return read(_) if changed[0] else row(value())
        cleanup = load('native_cleanup')
        with self.assertRaises(cleanup.CleanupFailure) as caught:
            self.run_setup(events, on_commit=lambda: changed.__setitem__(0, True), read=after_read, rollback_error=OSError('controlled'))
        self.assertIsInstance(caught.exception.primary, contract.Refused)
        self.assertEqual(len(caught.exception.secondary), 1)
        self.assertEqual(events.count('commit'), 1)

    def testObserverStaticClosureHasNoAllocatorOrNativeOperatorCredential(self):
        root = Path(__file__).parent
        pending, visited = ['native_observer_entry'], set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            source = (root / (name + '.py')).read_text()
            self.assertNotIn('/run/exe-native/native-operator.json', source)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    self.assertNotEqual(node.module, 'native_site')
                    self.assertNotEqual(node.module, 'native_receiver')
                    if node.module and (root / (node.module + '.py')).is_file():
                        pending.append(node.module)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, ('allocate', 'startNativeIntent'))
        self.assertIn('native_observer', visited)
        self.assertIn('native_core', visited)

    def testLaterPhaseCannotResetShortOriginalBudget(self):
        frame = {'version': 'core-first-writer-v1', 'tuple': value(), 'initial_sql_time': row(value())['sql_time'], 'original_lease_expires_at': row(value())['lease_expires_at'], 'remaining_work_milliseconds': 25000}
        package = {'version': 'erp-native-package-v1', 'enabled': True, 'uid': 1000, 'image_id': 'sha256:' + 'f' * 64, 'site': 'erp.owned.invalid', 'database': 'exe_fixture', **{k: value()[k] for k in ('job_id', 'intent_id', 'company_id', 'deployment_id', 'profile_sha256', 'config_sha256', 'initializer_sha256')}}
        with patch.object(transport.os, 'getuid', return_value=1000):
            with self.assertRaises(contract.Refused):
                transport.bind_frame(frame, package, 0, 1, required_seconds=60)


    def phase_data(self):
        frame = {'version': 'core-first-writer-phase-v2', 'phase': 'observe', 'tuple': value(), 'initial_sql_time': '2026-10-05T00:00:00Z', 'handoff_sql_time': '2026-10-05T00:01:40Z', 'original_lease_expires_at': '2026-10-05T00:04:00Z', 'original_remaining_work_milliseconds': 200000, 'remaining_work_milliseconds': 95000}
        package = {'version': 'erp-native-package-v1', 'enabled': True, 'uid': 1000, 'image_id': 'sha256:' + 'f' * 64, 'site': 'erp.owned.invalid', 'database': 'exe_fixture', **{k: value()[k] for k in ('job_id', 'intent_id', 'company_id', 'deployment_id', 'profile_sha256', 'config_sha256', 'initializer_sha256')}}
        return frame, package

    def testLaterPhaseChargesTransitNotPreviousAllocation(self):
        frame, package = self.phase_data()
        with patch.object(transport.os, 'getuid', return_value=1000):
            _, _, end = transport.bind_phase_frame(frame, package, 0, 1, 'observe', 60)
        ticks = iter((1, 2))
        read = transport.bounded_phase_owner_read(lambda _: {**row(value()), 'sql_time': '2026-10-05T00:01:41Z'}, frame, 0, end, lambda: next(ticks))
        read(value())
        # 100 seconds of completed earlier work is NOT charged again.
        self.assertGreater(read.budget_end(), 120)
        self.assertLess(read.budget_end(), 125)

    def testLaterPhaseRequiresClosedVersionAndHandoff(self):
        frame, package = self.phase_data()
        bads = [{k: v for k, v in frame.items() if k != 'handoff_sql_time'}, {**frame, 'version': 'core-first-writer-v1'}, {**frame, 'phase': 'setup'}, {**frame, 'forged': True}, {**frame, 'handoff_sql_time': '2026-10-04T23:59:59Z'}]
        with patch.object(transport.os, 'getuid', return_value=1000):
            for bad in bads:
                with self.subTest(fields=sorted(bad)), self.assertRaises(contract.Refused):
                    transport.bind_phase_frame(bad, package, 0, 1, 'observe', 60)

    def testLaterPhaseFutureHandoffRefusesActualRead(self):
        frame, _ = self.phase_data()
        read = transport.bounded_phase_owner_read(lambda _: {**row(value()), 'sql_time': '2026-10-05T00:01:39Z'}, frame, 0, 125, lambda: 1)
        with self.assertRaises(contract.Refused):
            read(value())

    def testLaterPhaseExtendedBudgetAndExpiryRefuse(self):
        frame, package = self.phase_data()
        with patch.object(transport.os, 'getuid', return_value=1000):
            for bad in ({**frame, 'remaining_work_milliseconds': 100001}, {**frame, 'original_remaining_work_milliseconds': 270001}, {**frame, 'original_lease_expires_at': '2026-10-05T00:05:01Z'}):
                with self.assertRaises(contract.Refused):
                    transport.bind_phase_frame(bad, package, 0, 1, 'observe', 60)

    def testLaterPhaseCurrentRenewalNeverExtendsFrozenEnd(self):
        frame, _ = self.phase_data()
        rows = iter(({**row(value()), 'sql_time': '2026-10-05T00:01:41Z'}, {**row(value()), 'sql_time': '2026-10-05T00:01:42Z', 'lease_expires_at': '2026-10-05T00:10:00Z'}, {**row(value()), 'sql_time': '2026-10-05T00:04:01Z'}))
        ticks = iter((1, 2, 3, 4, 5, 6))
        read = transport.bounded_phase_owner_read(lambda _: next(rows), frame, 0, 125, lambda: next(ticks))
        read(value())
        frozen = read.budget_end()
        read(value())
        self.assertLessEqual(read.budget_end(), frozen)
        with self.assertRaises(contract.Refused):
            read(value())


if __name__ == '__main__':
    unittest.main()
