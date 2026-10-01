"""Operator-only creation of a separate synthetic ERP site on an existing bench.

Never called from HTTP. Defaults to a read-only plan; --apply requires the plan
hash. Database/root credentials stay in process memory, not CLI arguments. The
native installer receives force=False, no backup/source SQL, and a new database
and login. No existing site, user password, membership or business record is
changed. Failed partial creations require operator diagnosis, never deletion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

VIEWER_ROLE = "Exe ERP Viewer"
# Deliberately finite. No User, API credential, settings, File, integration,
# communication or permission-management doctypes belong in a public demo.
VIEWER_DOCTYPES = (
    "Company", "Customer", "Supplier", "Item", "Item Group", "UOM",
    "Account", "Cost Center", "Warehouse", "Sales Order", "Sales Invoice",
    "Purchase Order", "Purchase Invoice", "Quotation", "Opportunity", "Lead",
    "Delivery Note", "Purchase Receipt", "Stock Entry", "Payment Entry",
    "Journal Entry", "GL Entry", "Stock Ledger Entry", "Project", "Task",
)
WRITE_PERMISSIONS = (
    "write", "create", "delete", "submit", "cancel", "amend", "import",
    "export", "share", "email", "print",
)
AUTOMATIC_ROLES = ("All", "Guest", "Desk User")
CONTROL_DOCTYPES = ("User", "Role", "DocPerm", "Custom DocPerm", "System Settings", "File", "Communication")


def _require_synthetic_admin():
    import frappe

    if frappe.conf.get("exe_hosted_site_mode") != "synthetic_demo" or not frappe.conf.get("exe_org_id") or not frappe.conf.get("gotrue_url"):
        raise ValueError("Viewer setup is restricted to an explicitly managed synthetic site")
    if frappe.session.user != "Administrator":
        raise ValueError("Viewer setup requires the native bench administrator")


def restrict_automatic_permissions():
    """Close native automatic-role grants in this NEW synthetic site only.

    A Desk user also inherits All/Guest/Desk User. Removing write flags from
    Viewer alone would still permit uploads, ToDos and Desk customization.
    Keep native read support; owners' explicit roles remain untouched.
    """
    import frappe
    from frappe.permissions import setup_custom_perms

    _require_synthetic_admin()
    parents = set()
    for permission_type in ("DocPerm", "Custom DocPerm"):
        parents.update(frappe.get_all(permission_type, filters={"role": ["in", AUTOMATIC_ROLES]}, pluck="parent"))
    for doctype in sorted(parents):
        setup_custom_perms(doctype)
        names = frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": ["in", AUTOMATIC_ROLES]}, pluck="name")
        for name in names:
            permission = frappe.get_doc("Custom DocPerm", name)
            flags = WRITE_PERMISSIONS + (("read", "select", "report") if doctype in CONTROL_DOCTYPES else ())
            changed = False
            for flag in flags:
                if permission.get(flag):
                    permission.set(flag, 0)
                    changed = True
            if changed:
                permission.save()


def validate_spec(spec):
    expected = {"site", "base_site", "database", "org_id", "auth_url"}
    if not isinstance(spec, dict) or set(spec) != expected:
        raise ValueError("Site spec must contain exactly site, base_site, database, org_id, auth_url")
    for key in ("site", "base_site"):
        if not isinstance(spec[key], str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?){2,}", spec[key]
        ):
            raise ValueError("Site names must be canonical public DNS hostnames")
        if len(spec[key]) > 253 or any(len(label) > 63 for label in spec[key].split(".")):
            raise ValueError("Site name exceeds DNS limits")
    if spec["site"] == spec["base_site"]:
        raise ValueError("New and protected base site must differ")
    if not isinstance(spec["database"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{3,62}", spec["database"]):
        raise ValueError("Database must be a canonical PostgreSQL identifier")
    if not isinstance(spec["org_id"], str) or not re.fullmatch(r"(?!\.{1,2}$)[a-z0-9._:-]{1,128}", spec["org_id"]):
        raise ValueError("Organization id must be canonical")
    if not isinstance(spec["auth_url"], str):
        raise ValueError("Auth URL must be an HTTPS origin")
    auth = urlsplit(spec["auth_url"])
    if auth.scheme != "https" or not auth.hostname or auth.netloc != auth.hostname or auth.path not in ("", "/") or auth.query or auth.fragment:
        raise ValueError("Auth URL must be an HTTPS origin without credentials or port")
    apex = spec["base_site"].split(".", 1)[1]
    if auth.hostname != "auth." + apex or not spec["site"].endswith("." + apex):
        raise ValueError("Site and central Auth must share the protected site's apex")
    if spec["site"] == auth.hostname:
        raise ValueError("ERP site cannot replace the central Auth host")
    return dict(spec)


def plan(spec, sites_dir):
    spec = validate_spec(spec)
    sites_dir = Path(sites_dir).resolve()
    base = sites_dir / spec["base_site"] / "site_config.json"
    if not base.is_file() or base.is_symlink() or base.parent.is_symlink():
        raise ValueError("Protected base site must already exist as a real directory")
    if (sites_dir / spec["site"]).exists() or (sites_dir / spec["site"]).is_symlink():
        raise ValueError("Target site exists; automatic replacement or repair is forbidden")
    # Refuse reuse of ANY existing site's DB or login, not just the base site's.
    for site in sites_dir.iterdir():
        config_path = site / "site_config.json"
        if config_path.is_file():
            config = json.loads(config_path.read_text())
            if spec["database"] in (config.get("db_name"), config.get("db_user")):
                raise ValueError("Target database/login is already assigned to a site")
    base_config = json.loads(base.read_text())
    if not base_config.get("exe_org_id"):
        raise ValueError("Protected base site must have an explicit organization binding")
    if base_config.get("exe_org_id") == spec["org_id"]:
        raise ValueError("New site must belong to a different organization")
    result = {**spec, "mode": "synthetic_demo", "viewer_role": VIEWER_ROLE,
              "viewer_doctypes": list(VIEWER_DOCTYPES),
              "base_config_sha256": hashlib.sha256(base.read_bytes()).hexdigest()}
    digest = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result, digest


def install_viewer_permissions():
    """Install a finite native read-only role in THIS newly created demo DB."""
    import frappe
    from frappe.permissions import setup_custom_perms

    _require_synthetic_admin()
    # Never repurpose a role which may already carry unknown custom grants.
    if frappe.db.exists("Role", VIEWER_ROLE):
        raise ValueError("Viewer role already exists; inspect it instead of overwriting permissions")
    for doctype in VIEWER_DOCTYPES:
        meta = frappe.get_meta(doctype)
        if meta.istable or meta.issingle:
            raise ValueError("Viewer allowlist must contain normal document types")
    restrict_automatic_permissions()
    frappe.get_doc({"doctype": "Role", "role_name": VIEWER_ROLE, "desk_access": 1}).insert()
    for doctype in VIEWER_DOCTYPES:
        setup_custom_perms(doctype)
        permission = {"doctype": "Custom DocPerm", "parent": doctype,
                      "parenttype": "DocType", "parentfield": "permissions",
                      "role": VIEWER_ROLE, "permlevel": 0, "if_owner": 0,
                      "read": 1, "select": 1, "report": 1}
        permission.update({key: 0 for key in WRITE_PERMISSIONS})
        frappe.get_doc(permission).insert()
    frappe.db.commit()
    frappe.clear_cache()


def apply(spec, sites_dir, reviewed_digest):
    # Same native lock on this operator command prevents concurrent creation.
    # The native installer retains its independent bench_new_site lock as well.
    import frappe
    import psycopg2
    from filelock import FileLock
    from frappe.installer import _new_site, update_site_config

    sites_dir = Path(sites_dir).resolve()
    with FileLock(str(sites_dir / ".exe-hosted-site-provision.lock"), timeout=1):
        result, digest = plan(spec, sites_dir)
        if not secrets.compare_digest(digest, reviewed_digest):
            raise ValueError("Reviewed plan does not match current protected site/configuration")
        root_password = os.environ.get("DB_PASSWORD")
        root_user = os.environ.get("POSTGRES_USER", "exe")
        db_host = os.environ.get("DB_HOST")
        if not root_password or not db_host or spec["database"] == root_user:
            raise ValueError("Existing database operator connection is unavailable or reused")
        # Require absence of BOTH database and login during preflight. The
        # native require_new_database mode repeats this and uses CREATE only,
        # so a collision after this check never drops a DB or rotates a login.
        with psycopg2.connect(host=db_host, port=os.environ.get("DB_PORT", "5432"), user=root_user,
                              password=root_password, dbname="postgres", connect_timeout=10) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s UNION ALL SELECT 1 FROM pg_roles WHERE rolname = %s", (spec["database"], spec["database"]))
                if cursor.fetchone():
                    raise ValueError("Database/login already exists; replacement is forbidden")
        # Site installs resolve directories relative to sites/. No global
        # common_site_config or currentsite.txt is changed.
        os.environ["SITES_PATH"] = str(sites_dir)
        os.chdir(sites_dir)
        _new_site(db_name=spec["database"], db_user=spec["database"], site=spec["site"],
                  db_type="postgres", db_host=db_host, db_port=int(os.environ.get("DB_PORT", "5432")),
                  db_root_username=root_user, db_root_password=root_password,
                  db_password=secrets.token_hex(32), admin_password=secrets.token_urlsafe(48),
                  install_apps=["erpnext"], force=False, setup_db=True, require_new_database=True)
        config = {"exe_org_id": spec["org_id"], "exe_hosted_site_mode": "synthetic_demo",
                            "gotrue_url": os.environ.get("GOTRUE_URL", "http://gotrue:9999"),
                            "gotrue_external_url": spec["auth_url"].rstrip("/"),
                            "gotrue_require_callback_state": True, "host_name": "https://" + spec["site"],
                            "allowed_email_domains": [], "gotrue_allow_all_domains": False}
        for key, value in config.items():
            update_site_config(key, value, site_config_path=frappe.get_site_path("site_config.json"))
        frappe.destroy()
        frappe.init(site=spec["site"], sites_path=str(sites_dir))
        frappe.connect()
        frappe.set_user("Administrator")
        try:
            install_viewer_permissions()
            update_site_config("exe_erp_readonly_desk", True, site_config_path=frappe.get_site_path("site_config.json"))
        finally:
            frappe.destroy()
        if hashlib.sha256((sites_dir / spec["base_site"] / "site_config.json").read_bytes()).hexdigest() != result["base_config_sha256"]:
            raise ValueError("Protected base configuration changed; stop and investigate")
        return {"site": spec["site"], "org_id": spec["org_id"], "database": spec["database"],
                "created": True, "public_readiness_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="Nonsecret JSON site specification")
    parser.add_argument("--sites-dir", type=Path, default=Path(os.environ.get("SITES_PATH", "sites")))
    parser.add_argument("--apply", metavar="PLAN_SHA256", help="Create only the reviewed, still-current plan")
    args = parser.parse_args()
    spec = validate_spec(json.loads(args.spec.read_text()))
    if args.apply:
        result = apply(spec, args.sites_dir, args.apply)
    else:
        result, digest = plan(spec, args.sites_dir)
        result = {"plan": result, "plan_sha256": digest, "mutations": False}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
