"""Independent private read-only observer; no allocation/replay implementation."""
import hashlib
import json
from pathlib import Path
import stat

from .native_contract import CurrentOwner, Refused
from .native_site import ROLE, DOCTYPES, AUTOMATIC, WRITE, CONTROL, stable_secret
from .native_cleanup import Failures

READ_SURFACE = {
    "__exe_native_binding": ("action_id", "intent_id", "company_id", "creator_subject", "native_user", "marker_sha256", "binding"),
    "tabUser": ("name", "enabled", "user_type"),
    "tabHas Role": ("parent", "parenttype", "role"),
    "tabRole": ("name", "disabled", "desk_access"),
    "tabCustom DocPerm": ("parent", "role", "permlevel", "if_owner", "read", "select", "report", *WRITE),
    "tabDocPerm": ("parent", "role", "read", "select", "report", *WRITE),
    "tabDocShare": ("share_doctype", "share_name"),
    "__Auth": ("name", "doctype"),  # NEVER password/hash/encrypted credential fields.
}


def assert_native_reader(cursor):
    """Fresh actual effective privilege catalog, no cached/caller assertion.

    Exact column SELECTs on these eight tables require operator-side grants on this
    newly owned DB. Default PUBLIC TEMP/CREATE must be closed by that operator;
    no runtime/observer function repairs or grants anything.
    """
    surface = json.dumps([{"name": name, "cols": list(columns)} for name, columns in READ_SURFACE.items()], sort_keys=True)
    cursor.execute("""WITH approved AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) AS a(name text,cols jsonb))
      SELECT session_user=current_user AND r.rolcanlogin
      AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
      AND NOT EXISTS (SELECT 1 FROM pg_roles p WHERE p.oid<>r.oid AND pg_has_role(session_user,p.oid,'MEMBER'))
      AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid OR m.roleid=r.oid)
      AND NOT EXISTS (SELECT 1 FROM pg_database d WHERE d.datdba=r.oid OR (d.datname=current_database() AND has_database_privilege(session_user,d.oid,'CREATE,TEMP')))
      AND NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp_)' AND (n.nspowner=r.oid OR has_schema_privilege(session_user,n.oid,'CREATE')))
      AND NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT IN ('pg_catalog','information_schema','public') AND n.nspname !~ '^pg_(toast|temp_)' AND has_schema_privilege(session_user,n.oid,'USAGE'))
      AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp_)' AND
        (c.relowner=r.oid OR CASE WHEN c.relkind IN ('r','p','v','m','f') THEN
          has_table_privilege(session_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(session_user,c.oid,'INSERT,UPDATE,REFERENCES') OR
          ((has_table_privilege(session_user,c.oid,'SELECT') OR has_any_column_privilege(session_user,c.oid,'SELECT')) AND NOT (n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM approved a WHERE a.name=c.relname))) OR
          EXISTS(SELECT 1 FROM pg_attribute att WHERE att.attrelid=c.oid AND att.attnum>0 AND NOT att.attisdropped AND has_column_privilege(session_user,c.oid,att.attnum,'SELECT') AND NOT EXISTS(SELECT 1 FROM approved a WHERE a.name=c.relname AND a.cols ? att.attname))
        WHEN c.relkind='S' THEN has_sequence_privilege(session_user,c.oid,'SELECT,UPDATE,USAGE') ELSE false END))
      AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp_)' AND (p.proowner=r.oid OR (p.prosecdef AND has_function_privilege(session_user,p.oid,'EXECUTE'))))
      AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp_)' AND t.typowner=r.oid)
      AND (SELECT count(*) FROM approved a CROSS JOIN LATERAL jsonb_array_elements_text(a.cols) wanted(name) JOIN pg_class c ON c.relname=a.name AND c.relkind='r' JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public' JOIN pg_attribute att ON att.attrelid=c.oid AND att.attname=wanted.name AND att.attnum>0 AND NOT att.attisdropped WHERE has_column_privilege(session_user,c.oid,att.attnum,'SELECT'))=%s
      FROM pg_roles r WHERE r.rolname=session_user""", (surface, sum(len(columns) for columns in READ_SURFACE.values())))
    if cursor.fetchall() != [(True,)]:
        raise Refused("invalid_native_reader_privileges")


def observe_owned(value, read_owner, original_end, config, credential_file):
    """Separate real native reader socket, bounded queries, always closed."""
    import psycopg2
    failures = Failures(original_end)
    connection, result = None, None
    try:
        connection = psycopg2.connect(stable_secret(credential_file, absolute_end=original_end), connect_timeout=3, options="-c statement_timeout=1000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=1000", application_name="exe_erp_private_native_observer")
        result = observe(value, read_owner, original_end, config, connection)
    except BaseException as error:
        failures.first(error)
    finally:
        if connection is not None:
            failures.cleanup("observer_close", connection.close)
    failures.finish()
    return result


def observe(value, read_owner, original_end, config, connection):
    """connection must be a DISTINCT operator-issued least-privileged reader.

    No command response supplies identity. Query actual binding, PostgreSQL OID,
    Frappe User/Has Role/Auth and permission rows. Reader credential provisioning
    is a separately held operator-composition gate, never a runtime grant here.
    """
    current = CurrentOwner(value, read_owner, original_end, deadline_bound=getattr(read_owner, "budget_end", None))
    owner = current.observe()
    failures = Failures(original_end)
    result, cursor = None, None
    try:
        connection.set_session(readonly=True, autocommit=False, isolation_level="READ COMMITTED")
        cursor = connection.cursor()
        assert_native_reader(cursor)
        cursor.execute("SELECT current_user, current_database(), d.oid, r.rolcanlogin, r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls FROM pg_database d JOIN pg_roles r ON r.rolname=current_user WHERE d.datname=current_database()")
        role = cursor.fetchone()
        if not role or role[1] != config["database"] or not role[3] or any(role[4:]):
            raise Refused("invalid_native_reader")
        cursor.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_(toast|temp_)' AND c.relkind IN ('r','p','v','m','f') AND (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')) LIMIT 1")
        if cursor.fetchone():
            raise Refused("native_reader_can_write")
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema') AND nspname !~ '^pg_(toast|temp_)' AND has_schema_privilege(current_user,oid,'CREATE') LIMIT 1")
        if cursor.fetchone():
            raise Refused("native_reader_can_create")
        cursor.execute("SELECT binding, marker_sha256, native_user, creator_subject::text FROM __exe_native_binding WHERE action_id=%s::uuid AND intent_id=%s::uuid AND company_id=%s::uuid", (value["action_id"], value["intent_id"], value["company_id"]))
        rows = cursor.fetchmany(2)
        if len(rows) != 1:
            raise Refused("missing_or_ambiguous_native_binding")
        binding, digest, user, creator = rows[0]
        expected = {k: v for k, v in value.items() if k != "lease_token"}
        expected.update(owner_subject=owner["owner_subject"], site=config["site"], database=config["database"], native_user=user, state="issued_or_quarantined")
        if creator != owner["owner_subject"] or binding != expected:
            raise Refused("changed_native_binding")
        path = Path(config["sites_dir"]) / ".exe-native-first-writers" / (value["action_id"] + ".json")
        s = path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or stat.S_IMODE(s.st_mode) != 0o600 or s.st_size > 8192:
            raise Refused("invalid_native_marker")
        raw = stable_secret(path, 8192, original_end).encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != digest or json.loads(raw) != binding:
            raise Refused("changed_native_marker")
        cursor.execute('SELECT enabled,user_type FROM "tabUser" WHERE name=%s', (user,))
        if cursor.fetchall() != [(1, "System User")]:
            raise Refused("invalid_native_user")
        cursor.execute('SELECT role FROM "tabHas Role" WHERE parent=%s AND parenttype=%s ORDER BY role', (user, "User"))
        if cursor.fetchall() != [(ROLE,)]:
            raise Refused("unexpected_native_roles")
        cursor.execute('SELECT 1 FROM "__Auth" WHERE name=%s AND doctype=%s LIMIT 1', (user, "User"))
        if cursor.fetchone():
            raise Refused("native_password_present")
        cursor.execute('SELECT 1 FROM "tabDocShare" WHERE share_doctype=%s AND share_name=%s LIMIT 1', ("User", user))
        if cursor.fetchone():
            raise Refused("native_self_share_present")
        cursor.execute('SELECT disabled,desk_access FROM "tabRole" WHERE name=%s', (ROLE,))
        if cursor.fetchall() != [(0, 1)]:
            raise Refused("invalid_native_role")
        cursor.execute('SELECT parent,permlevel,if_owner,"read","select",report FROM "tabCustom DocPerm" WHERE role=%s ORDER BY parent', (ROLE,))
        if cursor.fetchall() != [(d, 0, 0, 1, 1, 1) for d in sorted(DOCTYPES)]:
            raise Refused("changed_viewer_permissions")
        write_predicate = " OR ".join('COALESCE("' + key + '",0)<>0' for key in WRITE)
        cursor.execute('SELECT 1 FROM "tabCustom DocPerm" WHERE role=ANY(%s) AND (' + write_predicate + ') LIMIT 1', ([ROLE, *AUTOMATIC],))
        if cursor.fetchone():
            raise Refused("automatic_or_viewer_write_grant")
        cursor.execute('SELECT 1 FROM "tabCustom DocPerm" WHERE role=ANY(%s) AND parent=ANY(%s) AND (COALESCE("read",0)<>0 OR COALESCE("select",0)<>0 OR COALESCE(report,0)<>0) LIMIT 1', (list(AUTOMATIC), list(CONTROL)))
        if cursor.fetchone():
            raise Refused("automatic_control_grant")
        cursor.execute("SELECT 1 FROM pg_trigger WHERE tgrelid='__exe_native_binding'::regclass AND NOT tgisinternal AND tgenabled='O' AND tgname IN ('__exe_native_binding_no_change','__exe_native_binding_no_truncate') ORDER BY tgname")
        if len(cursor.fetchall()) != 2:
            raise Refused("binding_trigger_missing")
        # A second actual catalog observation after all native reads.
        assert_native_reader(cursor)
        current.observe()
        result = {"action_id": value["action_id"], "native_database_oid": str(role[2]), "site": config["site"], "native_user": user, "native_readiness": "unverified"}
    except BaseException as error:
        failures.first(error)
    finally:
        if cursor is not None:
            failures.cleanup("observer_cursor_close", cursor.close)
        failures.cleanup("observer_rollback", connection.rollback)
    failures.finish()
    return result
