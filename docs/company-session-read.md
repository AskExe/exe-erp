# Staged native ERP company-session reads

Default off, source-only: this is transport/native ACL preparation, not browser
SSO, ERP UI, a tested tenant deployment, technical admission or public beta.
Actual native A/B PostgreSQL/Redis fixtures have not run. Core issuance and
rollout remain separate gates. Exe intelligence belongs to the company operations
core; optional isolated exe-os memory is not required or used by this adapter.

## Immutable operator configuration

`ERP_COMPANY_MODE` is exactly `true` or `false`; absent means false. False leaves
legacy native Frappe/GoTrue/session/API-key behavior unchanged. Enabled startup
loads the following immutable config before serving. Unknown company keys fail.

| Variable | Registered value |
| --- | --- |
| `ERP_COMPANY_ID` | Canonical lowercase core company UUID |
| `ERP_COMPANY_SITE` | `erp-site` native_id: exact canonical `erp.<base>` hostname |
| `SITE_NAME` | Exactly that existing native site |
| `ERP_COMPANY_ORIGIN` | Exactly `https://` plus site; no port/path/credentials |
| `ERP_COMPANY_BINDING_ID` | Canonical registered binding UUID |
| `ERP_COMPANY_GENERATION_ID` | Canonical accepted generation UUID |
| `ERP_COMPANY_AUDIENCE` | Registered `[a-z][a-z0-9_-]{2,63}` audience |
| `ERP_COMPANY_CLIENT_ID` | Registered native client, same grammar |
| `ERP_COMPANY_AUTHORITY_URL` | Operator-fixed resolved private IPv4 bare origin |
| `ERP_COMPANY_CLIENT_SECRET_FILE` | Native-client credential only, 43..128 base64url characters, no newline |
| `ERP_COMPANY_BINDINGS_FILE` | Immutable operator identity mapping JSON |
| `ERP_COMPANY_BINDINGS_SHA256` | SHA256 of exact approved mapping bytes |

Files require canonical absolute paths, regular/no-symlink/one-link, process UID
ownership and mode0600. Credential cap128 bytes; mapping64KiB and100 unique
subjects/users. No reload. Example mapping (synthetic, not a permission grant):

```json
{"version":1,"company_id":"00000000-0000-4000-8000-000000000001","site":"erp.alpha.example.test","binding_id":"00000000-0000-4000-8000-000000000002","generation_id":"00000000-0000-4000-8000-000000000003","audience":"erp-alpha","subjects":[{"subject_id":"00000000-0000-4000-8000-000000000004","native_user":"reader@example.test"}]}
```

The mapping binds exact existing native User primary keys, never email matching.
Users must be currently enabled System Users, hold an explicit current enabled
native Role and not hold Administrator/System Manager/Script Manager. No
creation, role mutation or owner-to-admin promotion exists. Operator mapping
backup/rotation/restore is a separate gate.

Tenant receives only native DB/Redis and its registered native-client credential.
No parent access/refresh, GoTrue/admin/JWT signer, issuer/parent cipher, Stripe,
memory or central bridge secrets. Enabled startup refuses known central/legacy
secret configuration; requests refuse legacy GoTrue/admin/bridge site config.
No legacy vendor license/auth/cloud hooks run in this branch. Fresh central
technical acceptance and independent subscription are mandatory, but admit only
this finite read policy, not other native or commercial features.

## Finite routes and authority

* GET `/api/resource/Customer` and `/api/resource/Item`: exact mandatory
  `limit_page_length=1..100&limit_start=0..10000`, either parameter order.
* GET `/api/resource/{Customer|Item}/{name}`: no query; name grammar
  `[A-Za-z0-9][A-Za-z0-9_-]{0,139}`. Encoded/Unicode/space natural names stay closed.
* GET `/company-session/status`: no query, current authority/native checks.
* POST `/company-session/logout`: exact ERP Origin, no body/query; success only
  after private result exactly `{"revoked":true}`.

Customer fields are name/customer_name/customer_type/customer_group/territory;
Item fields name/item_name/item_group/stock_uom/disabled. Native ACL may remove
or mask them. Duplicates, percent encoding, dot/separator ambiguities, filters,
user/site/doctype/fields/expand/run_method/cmd selectors, Desk/UI/login/signup,
APIv2/RPC/files/import/export/print/write/AI/MCP and unknown routes are denied.
Status makes no readiness claim. No start/callback/renewal or cookie issuance is
implemented; future renewal requires a separately reviewed central code flow.

Only `__Host-exe_erp_session=exs_<43 base64url characters>` is accepted.
Authorization (including parent JWT/native OAuth/API keys/business exk keys),
other cookies and duplicate own cookies deny. A future business-key adapter is
separate. Trusted edge must first strip all apex/central browser credentials:
legacy cookies reaching that edge do not invalidate a legitimate own session.
Backend is private per company with no public ports. Site/user/company/role/
forwarded-auth selectors deny. Fixed `X-Forwarded-Proto:https` matches reviewed
edge transport context and supplies no identity. WSGI RAW_URI/REQUEST_URI is
required; normalized ambiguous URLs cannot become an admitted route.

Each business/status use privately POSTs exact `{"session_token":...}` to fixed
`/internal/session-broker/introspect`, server-fixed Basic native client, no
browser cookies/parent token/selectors. Exact14-field CompanyIntrospectionV1:
version1, subject_id, company_id, product`erp`, resource_kind`erp-site`, binding_id,
native_id/site, generation_id, positive bounded decimal authz_epoch, audience,
scopes exactly`["erp:read"]`, current_role owner/member, technical_status accepted,
subscription_entitled true. Fixed registration fields must match and subject
must have operator binding. Role is not native authority. Private HTTP is only
on reviewed isolated networks; HTTPS verifies system certificates. Cap12KiB,
socket idle timeout 2s and an owned per-call teardown timer enforcing an absolute
3s network deadline across numeric connect, TLS handshake, status/headers and body.
The socket is never reused; every timer is cancelled/joined before return and late
results fail closed. No redirects/credential logs/token URLs. DNS is not cancellable
by standard-library HTTP: authority endpoints therefore must be operator-fixed
canonical IPv4 addresses in RFC1918, loopback or the RFC 6598 shared-address range.
No hostname resolution/discovery/fallback/public endpoint is allowed. HTTPS keeps
normal certificate verification with an IP SAN; operator endpoint/certificate and
network admission remain separate review gates. TLS trust-store initialization
precedes the network deadline. Enabled startup refuses UID0. Invalid/revoked/
expired/foreign/current-account/member/subscription/outage responses deny.

## Native ACL and startup boundary

`frappe.app` branches before HTTPRequest/LoginManager/native sid restoration,
site selectors/OAuth/APIkey/hooks/CookieManager. After introspection, fresh
native identity/role checks precede actual permission-aware `frappe.get_list`.
Direct reads also invoke Document read/field checks and intersect current
query row/field/mask ACL. No business get_all/raw SQL/ignore_permissions/system
principal bypass. Read-only native transaction always rolls back; no sid or
native session persists. Company-only branches bypass warmed shared role/User
Permission/document/meta/mask/System Settings caches. Legacy cache paths remain
unchanged. This is per-request ACL evaluation, not atomic mid-statement revocation.

Logout is audience-local only. False/malformed/private failure returns
honest failure without clearing cookies. Success clears host-only Path=/ Secure
HttpOnly SameSite=Lax cookie; never claims other app sessions revoked. All
responses no-store/no-referrer with frame-ancestors self.

Operator bootstrap/schema/users/roles/assets happen off mode using reviewed
native tooling. Enabled entrypoint accepts only existing-site gunicorn without
admin password, bypassing provisioning/migrations/site/Redis mutations. Native
dev/static serving, RQ enqueue/worker/task execution/scheduler and direct Node
realtime starts refuse mode. Legacy Compose/public image/ping healthcheck/shared
network is **not** a company deployment: reviewed exact-source image/private
edge/network/health overlay must disable jobs/realtime/bridge/memory egress.

## Verification and remaining gates

Controlled tests: `python3 scripts/company-session-read.test.py`; `ruff check
frappe/company_session.py scripts/company-session-read.test.py`. They execute
actual parser/transport/cache/dispatch functions with controlled native repository
stubs and local HTTP. They do not prove native ACL behavior against PostgreSQL.
Existing legacy entrypoint and exe_auth regressions remain required.

Still required before admission: separate actual A/B native sites/nonadmin Users;
known Customer/Item positives/foreign IDs; object/field/mask/User Permission row
proofs with warmed Redis/meta and native downgrades while introspection is blocked;
actual private Core fixed registration/redemption/revocation; exact-source image
and complete legacy checks; trusted edge ingress/cookies; GUI/native SSO,
jobs/realtime,100-user capacity and backup/recovery/operator-binding restore.
No production/readiness proof is claimed.

Business/status requests obtain and validate fresh central authority before native
initialization/connect/read-only transaction, then read current native identity and
ACLs. No native transaction spans a provider wait. Logout deliberately requires
only the exact request/cookie/origin policy and private audience-local revoke; it
performs no introspection or native initialization/identity/transaction. Expired
parent/subscription or disabled native users can therefore clean up their local
session. Only exact `{"revoked":true}` clears the cookie; private failure or malformed
success preserves it. This does not assert other sessions were revoked.

The derivative source checks execute shipped application control flow with controlled
native repositories: no native calls before blocked introspection resolves and a
role downgrade during that wait denies access. Actual loopback slow status/header
and body drips prove the network deadline, forbidden DNS and repeated healthy recovery
without surviving timer threads. These are transport/control-flow checks, not an
actual native PostgreSQL concurrent-downgrade admission proof.


## Staged company browser producer

`ERP_COMPANY_BROWSER_ENABLED` defaults to false independently of company mode.
When true it additionally requires `ERP_COMPANY_AUTH_ORIGIN`, a fixed canonical
central `https://auth.<platform-domain>` origin, and `ERP_COMPANY_FLOW_SECRET_FILE`,
a distinct bounded native-owned 0600 flow HMAC secret. The Auth host is explicitly
configured, not inferred from the company site. Partial or unknown configuration
refuses startup. No central issuer, GoTrue signing/admin/refresh credential is added.

The two fixed GET browser routes allow cross-host top-level navigation. They
retain exact Host/Origin checks; foreign Origin still denies. Fetch mode/destination,
when available, must describe navigation/document. The reviewed edge forwards
Sec-Fetch-Site but strips mode/destination, so their absence is accepted only for
these two state-bound browser routes; business read policy is unchanged.

GET `/company-session/start` accepts no query or body. It creates 32-byte random
state and PKCE verifier, signs a finite 600-second `__Host-exe_erp_flow` cookie,
and redirects to the same central `/company-session/authorize` used by Wiki.
The flow signature is domain-separated and binds the company, exact site, native
binding, generation, audience, client, fixed callback and Auth origin. Both cookies
are host-only Path=/ Secure HttpOnly SameSite=Lax; the edge already permits these
exact cookie names and the fixed registered-client S256 redirect. In browser-enabled
mode, business/status/logout requests tolerate exactly one bounded own flow cookie
alongside exactly one canonical session cookie. The flow supplies no identity.
Flow-only, duplicate, unknown/legacy cookies and all off-mode flow cookies deny.

GET `/company-session/callback` accepts only canonical `exc_` code and state.
It checks the signed registration-bound flow and expiry before the fixed native
client exchanges that code at `/internal/session-broker/token` with exact
`grant_type`, `code`, `redirect_uri`, `code_verifier` and `state_hash`. One-use code
consumption and replay denial remain central SQL authority, not local cookie state.
The exact opaque token result must have a positive lifetime no greater than 900
seconds. Fresh central introspection must return the exact ERP envelope. Then the
existing operator-bound native User and explicit roles are checked in a short
read-only native identity transaction. Callback membership uses a fresh `Has Role`
query with `cache=False` and limit 101, rejecting more than 100 memberships,
malformed/duplicate roles and privileged roles; each explicit Role must currently
be enabled. No inferred automatic role grants access. The callback operates without LoginManager, native sid persistence,
email matching, provisioning, new roles or Core-owner-to-admin promotion. Native
rollback completes before cookies are emitted; no transaction spans provider calls.

Redirect-chain GET `/company-session/status` with no query is also permitted in
browser-enabled mode when `Sec-Fetch-Site` is `same-site` or `cross-site`, but only
with explicit `Sec-Fetch-Mode: navigate` and `Sec-Fetch-Dest: document`. Fetch
Metadata considers the entire redirect URL list ([W3C redirect semantics](https://www.w3.org/TR/fetch-metadata/#redirects)),
so the callback's 303 does not necessarily restore same-origin metadata. This
completion exception retains exact Host/Origin, one canonical own session cookie,
fresh central introspection and current mapped native User/role checks. A flow
cookie alone supplies no authority. CORS, iframe, missing mode/destination and
foreign Origin deny this cross-host status request. Native resource reads, logout,
query-bearing status and browser-off behavior retain their existing policy.
Controlled complete-module regressions exercise callback-to-status navigation and
current-authority denials; actual GoTrue/native database/browser integration remains
unproved by those source doubles.

Success sets the opaque ERP session cookie, clears only its flow cookie and uses
`/company-session/status` as a staged completion destination. This is deliberately
not a replacement ERP UI or a claim that Desk works. Failure emits no new session
or clearing cookie and preserves honest 400/401/403/503 denial. A centrally redeemed
child that cannot complete native validation receives no browser credential; it
remains centrally expiring/revocable. This producer does not claim compensating
revocation on an uncertain provider outcome. Audience-local logout retains its
existing no-native-transaction behavior and clears the session only after exact
`{revoked:true}`. It also clears the flow cookie when the browser producer is enabled.

Private calls retain the actual three-second owned-socket teardown. The producer
checks both monotonic and absolute nine-second budgets after awaited stages and
before success; it does not cancel a hung native database call or prove native
transaction/driver deadlines. Full native Desk needs a separately reviewed current
company request-context/CSRF/boot adapter preserving native User/DocPerm/field/User
Permission ACLs, plus explicit central browser write admission and commit semantics.
API/MCP read scopes do not confer browser write rights. Normal off-mode native ERP,
Desk, REST, workflows and business source remain present and unchanged. Jobs,
realtime, broad native dispatch and company write scopes are not activated here.

The new controlled source fixture loads the complete shipped Python module with
finite private transport/native/response boundaries. It has no network, SDK, live
GoTrue, native Desk or database proof. Actual Auth→Dashboard→ERP code redemption,
current native ACLs and cross-company browser behavior remain acceptance gates.

The callback-specific bounded lookup leaves the existing read path unchanged. At
this frozen native base, `frappe.permissions.get_roles` already queries current
Has Role membership instead of Redis when `frappe.flags.company_session` is set.
The new source fixture tracks transaction-active state on begin/rollback; provider
assertions do not infer an open transaction from historical trace entries. Source
checks still do not establish concurrent native role-revocation linearizability.

Browser dispatch installs the lightweight request/CallbackManager context before
start or early callback denial. Native callback initialization restores that same
manager after resetting site context, including initialization failure, preserving
the existing WSGI wrapper’s callable `request.after_response.run` requirement.
The complete-module fixture checks this interface using a controlled SDK boundary;
it does not execute the native WSGI wrapper or establish native cleanup behavior.

## Post-read publication guard (source-only successor)

Company business reads now share one nine-second wall/monotonic request budget.
After the first read rolls back and its connection closes, the same opaque session
is introspected again. The complete validated envelope must equal the first,
including subject, registration, membership role, authorization epoch and entitlement.
A fresh force-init resets request-local authorization/meta state before the same
native permission-aware read is repeated. Only that second permitted result may
be published, after rollback/close and a third matching introspection. Status
uses one native identity pass and two introspections; logout still performs only
its audience-local revoke. Off-mode dispatch is unchanged.

The complete-application controlled post-read suite is not yet run. It blocks
the first read, second read and final introspection; checks central drift and
current native disable/role/row/field restrictions, cleanup refusal, and bounded
status/logout behavior. Its native repository and authority are controlled; it
is not actual GoTrue, PostgreSQL/Redis or native browser acceptance. The deadline
is checked around actual operations and publication, but does not cancel a hung
native DB driver. Rechecking reduces stale publication and does not establish
linearizable central/native revocation during a final in-flight native query.
