"""Private fresh-only ERP allocation library; never mounted or whitelisted.

The only entry takes the trusted Core parent's single-use memory/owned-pipe
consumer. There is intentionally no --action-file, JSON stdin allocation CLI,
or owner-read-to-dispatch fallback. Parent/pipe process binding is a separate
composition gate and is not qualified by this library's controlled tests.
"""

import hashlib
import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from .native_cleanup import Failures
from .native_contract import CurrentOwner, Refused
from .native_shared import (
    AUTOMATIC,
    CONTROL,
    DOCTYPES,
    PERMISSIONS,
    ROLE,
    WRITE,
    stable_secret,
)


def validate_config(config):
    if type(config) is not dict or set(config) != {"site", "database", "sites_dir", "profile_sha256", "config_sha256", "initializer_sha256"}:
        raise Refused("invalid_config")
    site = config["site"]
    if type(site) is not str or len(site) > 253 or len(site.split(".")) < 3 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in site.split(".")) or not site.startswith("erp."):
        raise Refused("invalid_site")
    if type(config["database"]) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{3,62}", config["database"]):
        raise Refused("invalid_database")
    root = Path(config["sites_dir"])
    s = root.stat()
    if not root.is_absolute() or root.resolve() != root or not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid():
        raise Refused("invalid_sites_root")
    if (root / site).exists() or (root / site).is_symlink():
        raise Refused("existing_site")
    for child in root.iterdir():
        p = child / "site_config.json"
        if p.is_file():
            if child.is_symlink() or p.is_symlink() or p.stat().st_size > 65536:
                raise Refused("unsafe_existing_config")
            existing = json.loads(p.read_text())
            if config["database"] in (existing.get("db_name"), existing.get("db_user")):
                raise Refused("existing_database")
    return root


def marker(root, value, creator, config, user, absolute_end=None):
    """Atomic no-replace first writer. NEVER removed by success/failure cleanup."""
    end = time.monotonic() + 3 if absolute_end is None else absolute_end
    if time.monotonic() >= end:
        raise Refused("expired_marker")
    directory = root / ".exe-native-first-writers"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    s = directory.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
        raise Refused("invalid_marker_root")
    path = directory / (value["action_id"] + ".json")
    body = {k: v for k, v in value.items() if k != "lease_token"}
    body.update(owner_subject=creator, site=config["site"], database=config["database"], native_user=user, state="issued_or_quarantined")
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Refused("already_issued_or_quarantined") from None
    errors = Failures(end)
    try:
        if os.write(fd, raw) != len(raw):
            raise Refused("incomplete_first_writer")
        os.fsync(fd)
    except BaseException as error:
        errors.first(error)
    finally:
        errors.cleanup("marker_fd_close", lambda: os.close(fd))
    errors.finish()
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    errors = Failures(end)
    try:
        os.fsync(parent)
    except BaseException as error:
        errors.first(error)
    finally:
        errors.cleanup("marker_dir_close", lambda: os.close(parent))
    errors.finish()
    if time.monotonic() >= end:
        raise Refused("late_marker")
    return body, hashlib.sha256(raw).hexdigest()


class OwnedInstallLock:
    """Explicit shared-state file lock; bounded release retains operation error.

    The actual FileLock is constructed with thread_local=False so a bounded
    cleanup thread releases the acquired descriptor, not an empty local state.
    """
    def __init__(self, lock, absolute_end):
        self.lock, self.end = lock, absolute_end

    def __enter__(self):
        failures = Failures(self.end)
        try:
            left = min(1, self.end - time.monotonic())
            if left <= 0:
                raise Refused("expired_install_lock")
            self.lock.acquire(timeout=left)
            if time.monotonic() >= self.end:
                raise Refused("late_install_lock")
        except BaseException as error:
            failures.first(error)
            failures.cleanup("install_lock_release", self.lock.release)
        failures.finish()
        return self

    def __exit__(self, kind, error, traceback):
        failures = Failures(self.end)
        if error is not None:
            failures.first(error)
        failures.cleanup("install_lock_release", self.lock.release)
        failures.finish()
        return False


def install_permissions(frappe, current):
    from frappe.permissions import setup_custom_perms
    current.observe()
    if frappe.session.user != "Administrator" or frappe.db.exists("Role", ROLE):
        raise Refused("role_exists_or_nonoperator")
    # Change only this genuinely fresh site's automatic grants, never a base site.
    parents = set()
    for table in ("DocPerm", "Custom DocPerm"):
        parents.update(frappe.get_all(table, filters={"role": ["in", AUTOMATIC]}, pluck="parent"))
    for doctype in sorted(parents):
        current.observe()
        setup_custom_perms(doctype)
        for name in frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": ["in", AUTOMATIC]}, pluck="name"):
            current.observe()
            row = frappe.get_doc("Custom DocPerm", name)
            for flag in WRITE + (("read", "select", "report") if doctype in CONTROL else ()):
                row.set(flag, 0)
            row.save()
    current.observe()
    frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1, "disabled": 0}).insert()
    for doctype in DOCTYPES:
        current.observe()
        meta = frappe.get_meta(doctype)
        if meta.istable or meta.issingle:
            raise Refused("invalid_viewer_doctype")
        setup_custom_perms(doctype)
        row = {"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType", "parentfield": "permissions", "role": ROLE, "permlevel": 0, "if_owner": 0}
        row.update({key: int(key in ("read", "select", "report")) for key in PERMISSIONS})
        frappe.get_doc(row).insert()


def commit_and_observe(frappe, current):
    """Actual commit may succeed despite later denial: never retry allocation.

    This fresh observation is required even for private unverified metadata.
    Its failure leaves the immutable binding and first-writer quarantined.
    """
    current.live(30)
    frappe.db.commit()
    return current.observe(30)


def require_private_environment(environment):
    # ERP's wildcard Contact/business hooks use this separate writable DSN.
    # Refuse inheritance before the first marker or native write, without
    # modifying existing hooks or any shared site's configuration.
    if environment.get("EXE_BRIDGE_DATABASE_URL"):
        raise Refused("outbound_bridge_configured")


@contextmanager
def private_install_progress():
    """Suppress only schema-sync display in this private installer process."""
    from frappe.model import sync
    original = sync.update_progress_bar
    sync.update_progress_bar = lambda *args, **kwargs: None
    try:
        yield
    finally:
        sync.update_progress_bar = original


def allocate(consume_original_dispatch, read_owner, config, operator):
    """Private parent-only operation. operator is an already private DB recipe.

    Installer performs multiple committed database/filesystem effects. A local
    deadline or SQL revocation cannot undo issued effects: marker is quarantined,
    no automatic retry/delete/repair, and no accepted/native-ready claim is made.
    """
    require_private_environment(os.environ)
    import frappe
    from filelock import FileLock
    from frappe.installer import _new_site
    # Parent owns authenticity and single consumption; cannot recover from SQL.
    value, original_end = consume_original_dispatch()
    current = CurrentOwner(value, read_owner, original_end, deadline_bound=getattr(read_owner, "budget_end", None))
    if any(config.get(k) != value[k] for k in ("profile_sha256", "config_sha256", "initializer_sha256")):
        raise Refused("changed_recipe")
    root = validate_config(config)
    user = "native-" + secrets.token_hex(16) + "@users.invalid"
    with OwnedInstallLock(FileLock(str(root / ".exe-private-native-install.lock"), timeout=1, thread_local=False), original_end):
        validate_config(config)
        row = current.observe(151)  # 120 install + 30 observer + 1 margin; cleanup separate.
        body, digest = marker(root, current.value, row["owner_subject"], config, user, original_end)
        # A second actual read is AFTER durable marker and immediately BEFORE
        # issuance. Any failure leaves the marker and forbids replay allocation.
        current.observe(151)
        previous_cwd = os.getcwd()
        previous_sites = os.environ.get("SITES_PATH")
        failures = Failures(original_end)
        result = None
        try:
            os.environ["SITES_PATH"] = str(root)
            os.chdir(root)
            frappe.destroy()
            frappe.init(site=config["site"], sites_path=str(root), new_site=True)
            with private_install_progress():
                _new_site(db_name=config["database"], db_user=config["database"], site=config["site"], db_type="postgres", db_host=operator["host"], db_port=operator["port"], db_root_username=operator["user"], db_root_password=operator["password"], db_password=secrets.token_hex(32), admin_password=secrets.token_urlsafe(48), install_apps=["erpnext"], force=False, setup_db=True, require_new_database=True)
            current.observe(30)
            frappe.destroy()
            frappe.init(site=config["site"], sites_path=str(root))
            frappe.connect()
            frappe.set_user("Administrator")
            install_permissions(frappe, current)
            current.observe(30)
            if frappe.db.exists("User", user):
                raise Refused("native_user_exists")
            doc = frappe.get_doc({"doctype": "User", "email": user, "username": "native_" + secrets.token_hex(12), "first_name": "Company viewer", "enabled": 1, "user_type": "System User", "send_welcome_email": 0, "user_image": "/assets/frappe/images/ui/avatar.png", "roles": [{"role": ROLE}]})
            doc.flags.no_welcome_mail = True
            doc.insert()
            # User.on_update creates a writable self-share. No owner/control
            # record bypass is part of the initial business viewer authority.
            current.observe(30)
            frappe.db.delete("DocShare", {"share_doctype": "User", "share_name": user})
            roles = frappe.db.get_values("Has Role", {"parent": user, "parenttype": "User"}, "role", pluck=True, cache=False)
            passwords = frappe.db.sql('SELECT 1 FROM "__Auth" WHERE doctype=%s AND name=%s LIMIT 1', ("User", user))
            if roles != [ROLE] or passwords:
                raise Refused("unexpected_native_authority")
            current.observe(30)
            frappe.db.sql('CREATE TABLE __exe_native_binding (action_id uuid PRIMARY KEY, intent_id uuid UNIQUE NOT NULL, company_id uuid NOT NULL, creator_subject uuid NOT NULL, native_user text UNIQUE NOT NULL, marker_sha256 text NOT NULL, binding jsonb NOT NULL)')
            frappe.db.sql('CREATE FUNCTION __exe_native_binding_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE=\'23514\', MESSAGE=\'immutable native binding\'; END $$')
            frappe.db.sql('CREATE TRIGGER __exe_native_binding_no_change BEFORE UPDATE OR DELETE ON __exe_native_binding FOR EACH ROW EXECUTE FUNCTION __exe_native_binding_immutable()')
            frappe.db.sql('CREATE TRIGGER __exe_native_binding_no_truncate BEFORE TRUNCATE ON __exe_native_binding FOR EACH STATEMENT EXECUTE FUNCTION __exe_native_binding_immutable()')
            frappe.db.sql('INSERT INTO __exe_native_binding VALUES (%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::jsonb)', (value["action_id"], value["intent_id"], value["company_id"], row["owner_subject"], user, digest, json.dumps(body, sort_keys=True)))
            current.observe(30)
            commit_and_observe(frappe, current)
            result = {"action_id": value["action_id"], "site": config["site"], "database": config["database"], "native_user": user, "native_readiness": "unverified"}
        except BaseException as error:
            failures.first(error)
            if getattr(frappe.local, "db", None):
                failures.cleanup("native_rollback", frappe.db.rollback)
        finally:
            # Shipped destroy == db.close + release_local. Close the ACTUAL
            # captured database under its copied ContextVar, then release the
            # original caller ContextVar (not an empty new-thread context).
            from frappe.utils.local import release_local
            database = getattr(frappe.local, "db", None)
            if database is not None:
                failures.cleanup("frappe_db_close", database.close)
            failures.local_state("frappe_local_release", lambda: release_local(frappe.local))
            failures.cleanup("restore_cwd", lambda: os.chdir(previous_cwd))
            def restore_environment():
                if previous_sites is None:
                    os.environ.pop("SITES_PATH", None)
                else:
                    os.environ["SITES_PATH"] = previous_sites
            failures.cleanup("restore_environment", restore_environment)
        failures.finish()
        current.observe()
        return result
