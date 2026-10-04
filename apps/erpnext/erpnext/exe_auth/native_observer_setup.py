"""Operator-only exact fresh-database observer setup; not allocation or retry.

Only the live original-success parent may start this distinct fixed child. An
existing role, incomplete allocation, changed creator, lost commit response or
failed post-commit fence remains quarantined. Nothing here starts a Core action,
adopts a site, clears its marker, changes defaults or returns readiness.
"""

import re
import time

from .native_cleanup import Failures
from .native_contract import CurrentOwner, Refused, exact
from .native_core import CoreReader
from .native_observer import READ_SURFACE, read_native_binding
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

OPERATOR_SECRET = "/run/exe-native/native-operator.json"


def suppress_collection(cursor):
    # BEFORE any password-bearing statement or bind; refusal is not repaired.
    for statement in (
        "SET LOCAL log_statement='none'", "SET LOCAL log_duration=off",
        "SET LOCAL log_min_duration_statement=-1", "SET LOCAL log_min_error_statement='panic'",
        "SET LOCAL log_parameter_max_length=0", "SET LOCAL log_parameter_max_length_on_error=0",
    ):
        cursor.execute(statement)
    cursor.execute("SELECT current_setting('shared_preload_libraries'), current_setting('session_preload_libraries'), current_setting('local_preload_libraries')")
    rows = cursor.fetchall()
    if len(rows) != 1 or len(rows[0]) != 3 or any(type(x) is not str for x in rows[0]):
        raise Refused("unproved_collection_settings")
    modules = [x.strip() for field in rows[0] for x in field.split(',') if x.strip()]
    if any(x not in ('pg_stat_statements', 'pgaudit') for x in modules):
        raise Refused("unsupported_collection_module")
    cursor.execute("SET LOCAL pg_stat_statements.track='none'")
    cursor.execute("SELECT current_setting('pgaudit.log',true)")
    rows = cursor.fetchall()
    if len(rows) != 1 or len(rows[0]) != 1:
        raise Refused("unproved_audit_setting")
    if rows[0][0] is not None:
        cursor.execute("SET LOCAL pgaudit.log='none'")


def observer_credentials(raw, operator, config, value, parse_dsn):
    """No caller-selected role/database/host or ambient DSN option fallback."""
    fields = parse_dsn(raw)
    expected_user = 'exe_erp_observer_' + value['action_id'].replace('-', '')
    if set(fields) != {'host', 'port', 'dbname', 'user', 'password'}:
        raise Refused("invalid_observer_credentials")
    if fields['host'] != operator['host'] or fields['port'] != str(operator['port']) or fields['dbname'] != config['database'] or fields['user'] != expected_user:
        raise Refused("changed_observer_credentials")
    password = fields['password']
    if type(password) is not str or not 24 <= len(password) <= 256 or re.search(r'[\x00-\x20\x7f]', password):
        raise Refused("invalid_observer_credentials")
    return expected_user, password


def setup(value, read_owner, original_end, config, connection, role, password):
    """Fresh admin socket owns one transaction on ONLY the exact bound DB.

    PostgreSQL roles are cluster-wide: use a unique action-derived create-only
    name under an advisory transaction lock, never alter/adopt an existing role.
    Only this new DB's PUBLIC TEMP and public-schema CREATE are closed. No
    default privileges, global role membership or wildcard table grants exist.
    """
    from psycopg2 import sql
    current = CurrentOwner(value, read_owner, original_end, deadline_bound=getattr(read_owner, 'budget_end', None))
    owner = current.observe(60)
    failures = Failures(original_end)
    cursor, result = None, None
    try:
        connection.set_session(autocommit=False, isolation_level='READ COMMITTED')
        cursor = connection.cursor()
        suppress_collection(cursor)
        cursor.execute("SELECT current_database(), d.oid, (r.rolsuper OR d.datdba=r.oid), r.rolcreaterole OR r.rolsuper, session_user=current_user FROM pg_database d JOIN pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()")
        rows = cursor.fetchall()
        if len(rows) != 1 or rows[0][0] != config['database'] or rows[0][2:] != (True, True, True):
            raise Refused('invalid_fresh_database_operator')
        database_oid = str(rows[0][1])
        cursor.execute('LOCK TABLE public.__exe_native_binding IN SHARE MODE')
        read_native_binding(cursor, value, owner, config, original_end)
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 748320136))", (role,))
        cursor.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,))
        if cursor.fetchone():
            raise Refused('existing_observer_role')
        # Refuse unsafe PUBLIC executable definers rather than broad revocation
        # or mutation of arbitrary native function grants.
        cursor.execute("SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp_)' AND EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') LIMIT 1")
        if cursor.fetchone():
            raise Refused('public_native_definer')
        current.observe(60)
        cursor.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 1 PASSWORD %s').format(sql.Identifier(role)), (password,))
        for setting in ("statement_timeout='1s'", "lock_timeout='1s'", "idle_in_transaction_session_timeout='1s'", "default_transaction_read_only=on", "log_parameter_max_length=0", "log_parameter_max_length_on_error=0"):
            current.observe(60)
            cursor.execute(sql.SQL('ALTER ROLE {} SET ' + setting).format(sql.Identifier(role)))
        current.observe(60)
        cursor.execute(sql.SQL('REVOKE CREATE,TEMP ON DATABASE {} FROM PUBLIC').format(sql.Identifier(config['database'])))
        current.observe(60)
        cursor.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        current.observe(60)
        cursor.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(config['database']), sql.Identifier(role)))
        current.observe(60)
        cursor.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        for table, columns in READ_SURFACE.items():
            current.observe(60)
            cursor.execute(sql.SQL('GRANT SELECT ({}) ON TABLE public.{} TO {}').format(sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table), sql.Identifier(role)))
        current.observe(60)
        connection.commit()
        # Completion/owner loss after an issued commit is quarantined, not a
        # reason to recreate roles, repeat grants or permit observer dispatch.
        current.observe(30)
        result = {'action_id': value['action_id'], 'native_database_oid': database_oid, 'state': 'pending_observation', 'native_readiness': 'unverified'}
    except BaseException as error:
        failures.first(error)
    finally:
        if cursor is not None:
            failures.cleanup('observer_setup_cursor_close', cursor.close)
        failures.cleanup('observer_setup_rollback', connection.rollback)
    failures.finish()
    current.observe(30)
    return result


def run():
    import psycopg2
    from psycopg2.extensions import parse_dsn
    before = time.monotonic()
    package = closed_json(stable_secret(PACKAGE, 8192, before + 5))
    frame = frame_from_pipe(0, before + 5)
    value, config, end = bind_phase_frame(frame, package, before, time.monotonic(), "setup", required_seconds=90)
    failures = Failures(end)
    core, connection, result, read = None, None, None, None
    try:
        core = CoreReader(CORE_SECRET, end)
        read = bounded_phase_owner_read(core, frame, before, end)
        # This first actual Core observation precedes native credentials/socket.
        current = CurrentOwner(value, read, end, deadline_bound=read.budget_end)
        current.observe(60)
        operator = closed_json(stable_secret(OPERATOR_SECRET, 8192, current.end))
        exact(operator, ('host', 'port', 'user', 'password'))
        if type(operator['host']) is not str or not 1 <= len(operator['host']) <= 253 or type(operator['port']) is not int or not 1 <= operator['port'] <= 65535 or type(operator['user']) is not str or not 1 <= len(operator['user']) <= 63 or type(operator['password']) is not str or not 1 <= len(operator['password']) <= 4096 or operator['user'] == config['database']:
            raise Refused('invalid_private_operator')
        role, password = observer_credentials(stable_secret(OBSERVER_SECRET, 8192, current.end), operator, config, value, parse_dsn)
        current.observe(60)
        connection = psycopg2.connect(**operator, dbname=config['database'], connect_timeout=3, options='-c statement_timeout=1000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=1000', application_name='exe_erp_private_observer_setup')
        result = setup(value, read, end, config, connection, role, password)
    except BaseException as error:
        failures.first(error)
    finally:
        if connection is not None:
            failures.cleanup('observer_setup_close', connection.close)
        if core is not None:
            failures.cleanup('observer_setup_core_close', core.close)
    failures.finish()
    if read is None or time.monotonic() + 30 >= min(end, read.budget_end()):
        raise Refused('late_private_setup')
    return result


if __name__ == '__main__':
    emit_main(run)
