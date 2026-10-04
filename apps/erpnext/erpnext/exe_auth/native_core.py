"""Actual private function-only Core reader; no HTTP/claim/start/retry path."""

import time

from .native_cleanup import Failures
from .native_contract import validate_tuple
from .native_site import stable_secret

QUERY = """SELECT core.read_native_action_owner(
 %s::uuid,%s::uuid,%s::integer,%s::text,%s::uuid,%s::uuid,
 %s::text,%s::text,%s::text,%s::text,%s::uuid,%s::uuid,%s::uuid)"""


class CoreReader:
    """Credential is loaded from an operator-owned file, not argv/env/logs.

    Existing Core SQL function/fence enforces the effective provisioner guard.
    Its credential has ALL existing provisioner EXEC capabilities, not an
    invented read-only role; keep it only in the private command/observer.
    Each query is a distinct actual observation, never a cached callback result.
    """
    def __init__(self, credential_file, absolute_end):
        import psycopg2
        dsn = stable_secret(credential_file, 4096, absolute_end)
        self.end, self.connection = absolute_end, None
        failures = Failures(absolute_end)
        try:
            if time.monotonic() >= absolute_end:
                from .native_contract import Refused
                raise Refused("expired_private_setup")
            self.connection = psycopg2.connect(dsn, connect_timeout=3, options="-c statement_timeout=1000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=1000", application_name="exe_erp_private_native_owner")
            self.connection.autocommit = True
        except BaseException as error:
            failures.first(error)
            if self.connection is not None:
                failures.cleanup("core_init_close", self.connection.close)
        failures.finish()

    def __call__(self, value):
        value = validate_tuple(value)
        failures = Failures(self.end)
        cursor, result = None, None
        try:
            cursor = self.connection.cursor()
            cursor.execute(QUERY, tuple(value[k] for k in ("job_id", "lease_token", "attempt", "worker_id", "company_id", "deployment_id", "product", "profile_sha256", "config_sha256", "initializer_sha256", "request_key", "intent_id", "action_id")))
            rows = cursor.fetchmany(2)
            if len(rows) != 1 or len(rows[0]) != 1:
                from .native_contract import Refused
                raise Refused("invalid_core_read")
            result = rows[0][0]
        except BaseException as error:
            failures.first(error)
        finally:
            if cursor is not None:
                failures.cleanup("core_cursor_close", cursor.close)
        failures.finish()
        return result

    def close(self):
        failures = Failures(self.end)
        if self.connection is not None:
            failures.cleanup("core_close", self.connection.close)
        failures.finish()
