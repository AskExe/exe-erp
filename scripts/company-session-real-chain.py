"""Disposable operator/WSGI boundary for real Auth/Core ERP sessions.

No handler, central reply, native ORM or permission method is replaced. Input
secrets stay in the owned command pipe; output is privately retained by caller.
"""
import base64
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from werkzeug.test import Client
from werkzeug.wrappers import Response

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("native_fixture", ROOT / "scripts/company-session-native.integration.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
frappe, adapter = fixture.frappe, fixture.adapter
raw = sys.stdin.buffer.read(65537)
if len(raw) > 65536:
    raise ValueError("Bounded private input required")
c = json.loads(raw)
if c["operation"] not in ("prepare", "request", "native-revoke") or c["plane"] not in ("a", "b"):
    raise ValueError("Fixed operation/plane required")
fixture.ARGS = SimpleNamespace(sites_path=Path("/home/frappe/frappe-bench/sites"),
    site_a=c["sites"]["a"], site_b=c["sites"]["b"], fixture_id=c["fixture_id"])
if not re.fullmatch(r"[a-f0-9]{32}", c["fixture_id"]):
    raise ValueError("Random fixture marker required")
site = c["sites"][c["plane"]]
if not re.fullmatch(r"erp\.acl-[ab]-[a-f0-9]{12}\.example\.test", site):
    raise ValueError("Owned site required")
site_config = json.loads((fixture.ARGS.sites_path / site / "site_config.json").read_text())
if site_config.get("company_acl_fixture") != c["fixture_id"] or site_config.get("allow_tests") is not True:
    raise ValueError("Exact disposable marker required")
address = ipaddress.IPv4Address(c["native_address"])
if not any(address in ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
    raise ValueError("Owned internal Core address required")
p = c["profile"]
if p["native_id"] != site or p["callback"] != "https://" + site + "/company-session/callback":
    raise ValueError("Exact site registration required")
private = fixture.ARGS.sites_path / (".issuer-chain-" + c["plane"])
if c["operation"] == "prepare":
    # Validate every genuine opaque bootstrap against restricted Core BEFORE
    # creating native users or the fixed UUID→native User operator bindings.
    fixture.verify_sites() if c["plane"] == "a" else None
    users = {}
    for role, value in c["bootstrap"].items():
        expected_user = ("bowner" if c["plane"] == "b" else "a" + role) + "@native-acl.example.test"
        if not adapter.UUID.fullmatch(value["subject"]) or not adapter.SESSION.fullmatch(value["token"]):
            raise ValueError("Genuine subject/session syntax required")
        connection = http.client.HTTPConnection(str(address), 8097, timeout=2)
        try:
            connection.request("POST", "/internal/session-broker/introspect",
                json.dumps({"session_token": value["token"]}),
                {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode((p["client_id"] + ":" + c["client_secret"]).encode()).decode()})
            response = connection.getresponse()
            body = response.read(8193)
            if response.status != 200 or len(body) > 8192:
                raise ValueError("Current genuine bootstrap refused")
            envelope = json.loads(body)
            expected = {"subject_id": value["subject"], "company_id": p["company_id"], "product": "erp",
                "resource_kind": "erp-site", "binding_id": p["binding_id"], "native_id": site,
                "generation_id": p["generation_id"], "audience": p["audience"], "scopes": ["erp:read"],
                "current_role": "owner" if role == "owner" else "member", "technical_status": "accepted", "subscription_entitled": True, "version": 1}
            if set(envelope) != adapter.FIELDS or not isinstance(envelope.get("authz_epoch"), str) or not re.fullmatch(r"[1-9][0-9]*", envelope["authz_epoch"]) or any(envelope.get(k) != v for k, v in expected.items()):
                raise ValueError("Exact current bootstrap authority required")
            users[value["subject"]] = expected_user
        finally:
            connection.close()
    if set(c["bootstrap"]) != ({"owner", "member"} if c["plane"] == "a" else {"owner"}):
        raise ValueError("Exact operator subjects required")
    fixture.setup(site, c["plane"])
    fixture.connect(site)
    try:
        identity = frappe.db.sql("SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user", as_dict=True)[0]
        if identity["rolsuper"] or identity["rolbypassrls"]:
            raise ValueError("Non-superuser native database required")
        for user in users.values():
            if not frappe.db.exists("User", user) or "System Manager" in frappe.get_roles(user) or fixture.ROLE not in frappe.get_roles(user):
                raise ValueError("Exact existing native User required")
    finally:
        fixture.close()
    private.mkdir(mode=0o700)
    binding = {"version": 1, "company_id": p["company_id"], "site": site, "binding_id": p["binding_id"],
        "generation_id": p["generation_id"], "audience": p["audience"],
        "subjects": [{"subject_id": subject, "native_user": user} for subject, user in users.items()]}
    for name, data in (("bindings", json.dumps(binding)), ("client", c["client_secret"]),
            ("flow", base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")), ("native-address", str(address))):
        with (private / name).open("x") as stream:
            stream.write(data)
        (private / name).chmod(0o600)
    result = {"prepared": True, "native_role": dict(identity), "subjects": len(users)}
elif c["operation"] == "native-revoke":
    # Committed owned fixture mutation before a fresh request, never a read mock
    # or a claim about a race occurring during an application transaction.
    # Separate operator process still uses the production background refusal.
    os.environ["ERP_COMPANY_MODE"] = "true"
    def change():
        user = "amember@native-acl.example.test"
        if c["change"] == "role":
            filters = {"parent": user, "parenttype": "User", "role": fixture.ROLE}
            name = frappe.db.get_value("Has Role", filters, "name")
            if not name or frappe.db.count("Has Role", filters) != 1:
                raise ValueError("Exact single owned role row required")
            row = frappe.get_doc("Has Role", name).as_dict()
            if row["parentfield"] != "roles":
                raise ValueError("Exact owned User child row required")
            retained = json.dumps(row, default=str)
            if len(retained.encode()) > 8192:
                raise ValueError("Retained role row bound")
            with (private / "retained-role.json").open("x") as stream:
                stream.write(retained)
            (private / "retained-role.json").chmod(0o600)
            frappe.db.delete("Has Role", {"name": name})
        elif c["change"] == "restore-role":
            row = json.loads(adapter.private_file(str(private / "retained-role.json"), 8192))
            if (row.get("doctype"), row.get("parent"), row.get("parenttype"), row.get("parentfield"), row.get("role")) != ("Has Role", user, "User", "roles", fixture.ROLE):
                raise ValueError("Exact retained owned role row required")
            if not isinstance(row.get("name"), str) or frappe.db.exists("Has Role", row["name"]) or frappe.db.count("Has Role", {"parent": user, "parenttype": "User", "role": fixture.ROLE}):
                raise ValueError("Owned role restoration collision")
            frappe.get_doc(row).db_insert()
        elif c["change"] in ("row", "restore-row"):
            doc = frappe.get_doc("User Permission", frappe.db.get_value("User Permission", {"user": user, "allow": "Customer"}))
            doc.for_value = "ACL-a-restricted" if c["change"] == "row" else "ACL-a-visible"
            doc.save()
        else:
            raise ValueError("Fixed native mutation required")
    if c["plane"] != "a":
        raise ValueError("Owned A member only")
    fixture.mutate(site, change)
    result = {"committed": True}
else:
    if (private / "native-address").read_text() != str(address):
        raise ValueError("Immutable owned network address changed")
    os.environ.update(ERP_COMPANY_MODE="true", SITE_NAME=site, SITES_PATH=str(fixture.ARGS.sites_path),
        ERP_COMPANY_ID=p["company_id"], ERP_COMPANY_SITE=site, ERP_COMPANY_BINDING_ID=p["binding_id"],
        ERP_COMPANY_GENERATION_ID=p["generation_id"], ERP_COMPANY_AUDIENCE=p["audience"],
        ERP_COMPANY_CLIENT_ID=p["client_id"], ERP_COMPANY_ORIGIN="https://" + site,
        ERP_COMPANY_AUTHORITY_URL="http://" + str(address) + ":8097",
        ERP_COMPANY_CLIENT_SECRET_FILE=str(private / "client"), ERP_COMPANY_BINDINGS_FILE=str(private / "bindings"),
        ERP_COMPANY_BINDINGS_SHA256=hashlib.sha256((private / "bindings").read_bytes()).hexdigest(),
        ERP_COMPANY_BROWSER_ENABLED="true", ERP_COMPANY_AUTH_ORIGIN="https://auth.issuer.example.test",
        ERP_COMPANY_FLOW_SECRET_FILE=str(private / "flow"))
    import frappe.app as app
    if not app._company_config.browser_enabled:
        raise ValueError("Explicit producer configuration required")
    path = c["path"]
    if not isinstance(path, str) or len(path) > 8192 or not path.startswith(("/company-session/", "/api/resource/Customer")):
        raise ValueError("Finite native test route required")
    client = Client(app.application, Response, use_cookies=False)
    response = client.get(path, base_url="https://" + site,
        headers={"Host": site, "Origin": "https://" + site, "Cookie": c.get("cookie", "")},
        environ_overrides={"RAW_URI": path})
    try:
        body = response.get_data()
        if len(body) > 65536:
            raise ValueError("Native response bound")
        result = {"status": response.status_code, "body": body.decode(),
            "headers": {"location": response.headers.get("Location"), "set-cookie": response.headers.getlist("Set-Cookie")}}
    finally:
        response.close()
print("CHAIN_RESULT=" + json.dumps(result, separators=(",", ":")))
