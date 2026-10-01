# Isolated synthetic ERP site on an existing bench

The hosted site operator creates a new Frappe/ERPNext site, PostgreSQL database
and login, and site-specific public/private file directories. It installs a
finite read-only Desk role. Existing company sites keep their current role
mapping, database, files, configuration, passwords, and default site. There is
no copy or restore of company data, email-domain admission, membership grant, or
HTTP provisioning endpoint.

Native Desk users also inherit `All`, `Guest`, and `Desk User`. In the new
synthetic site only, the operator removes mutating permissions from those
automatic roles and removes their control-record/file/communication access.
Explicit owner/admin roles are preserved. This closes the inherited ToDo,
upload and Desk-customization write grants; whitelisted RPCs, actual Desk
loading, native APIs and file access still require live acceptance.
Frappe's separate self-share still allows a user to access their own native
profile; this is not a grant to read another user's profile or change roles.
Native self-profile role elevation is a required negative acceptance check.

Run the command only from the released, digest-pinned ERP image in a serialized
operator window, after deploying the reviewed host-bound ERP nginx site header
configuration. A second site must not exist behind a proxy which still accepts
caller-controlled site selectors. It uses existing container environment DB operator credentials
in memory and native Frappe installation APIs. It defaults to a read-only plan.
The apply argument is the plan SHA-256, which is not a credential. Applying
rechecks the protected site's config digest, rejects any existing target site,
database or database login, and never uses force or automatic cleanup. Diagnose
a failed partial install; do not retry by dropping a database or removing files.
The new site's native encryption key is initialized in the serialized creation
step, before admission, because the shared entrypoint initializes only its
primary configured site.

Example nonsecret specification (replace with the actual approved org/site):

```json
{
  "site": "erp-demo.example.com",
  "base_site": "erp.example.com",
  "database": "demo_erp",
  "org_id": "demo",
  "auth_url": "https://auth.example.com"
}
```

Inside the bench, with the JSON file mounted read-only:

```sh
env/bin/python -m erpnext.exe_auth.hosted_site --spec /run/erp-demo-site.json --sites-dir sites
# After reviewing the plan, pass its exact nonsecret hash:
env/bin/python -m erpnext.exe_auth.hosted_site --spec /run/erp-demo-site.json --sites-dir sites --apply PLAN_SHA256
```

The command deliberately does not publish DNS, seed synthetic business records,
change GoTrue org grants, or claim public readiness. Do not enable the public host
until all of the following are complete:

1. The DEMO owner has approved this distinct site. Add its hostname through the
   normal DNS/tunnel configuration; route it through the SSO edge, then ERP
   nginx. Keep the incoming Host unchanged and set `X-Frappe-Site-Name` from that
   trusted Host at ERP nginx on every proxied location. Never forward a caller's
   site-selector header. Keep Frappe native `sid` and callback-state cookies
   host-only; only the central Auth cookies span the apex.
2. Add only the actual DEMO hostname to central Auth's allowed redirect/app-host
   configuration and the signed SSO edge vhost. Central login/recovery stay at
   the existing auth host. Bind this site's `exe_org_id` to the actual DEMO org;
   a private-company grant is insufficient to enter it.
3. Admit DEMO viewers through supported membership/grant controls. A verified
   GoTrue subject must carry a positive `erp:read` grant for THIS org. Owners
   retain their authoritative admin grants. The public role can read only the
   listed business doctypes; it receives no write/create/delete/submit/cancel,
   export/share/email or control-plane permissions. Never grant private-company
   ERP access as a substitute for a DEMO site.
4. The DEMO data owner seeds only synthetic records through native ERP APIs.
   Dashboard/product navigation must resolve the selected real org to its
   approved ERP host. A label or HTTP 200 is not sufficient.
5. Verify with fresh central logins: viewer Desk and records, native API writes
   refused, owner access, cross-site record/file/session isolation, central
   logout, and mobile navigation. Keep exact JEV results and native API evidence.

After EVERY ERP image upgrade, run native `bench --site <demo-host> migrate`
under the same normal release/backup gates, before admitting DEMO traffic. The
existing entrypoint migrates only its configured primary `SITE_NAME`; this
operator does not silently expand that set or change the global default site.
Shared static assets, Redis workers and process resources are shared within the
bench; business records and native session/cache namespaces are site/database
specific. Live file, cache, queue and websocket isolation must be checked before
public acceptance. This utility is not a general multi-customer deployment
engine or a claim that a public DEMO currently exists.
