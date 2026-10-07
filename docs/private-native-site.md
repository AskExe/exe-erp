# Private fresh ERP allocation (Source only)

Parent is exact ERP135 `f5e9b33c9823109ba72c1e90db58bf5cc96c06da`. Core authority is published Core66 `346a4da14d333cdd37b8c9501817d4c9f1ac29da`, function-only `read_native_action_owner` with 13 inputs and a closed 15-key result. Its effective runtime privilege guard remains authoritative; this ERP code neither adds a Core role nor duplicates a weaker guard. No import, parser check, unit test, build or native operation has run for this Source.

The new private library/receiver are not whitelisted, mounted or startup-enabled. Registry owns the concrete Core parent: exactly one actual successful original `startNativeIntent` acknowledgement can create its single-use memory capability and protected OS-pipe dispatch. Missing/uncertain acknowledgement cannot be recovered with an action UUID or owner read. `successful_start` remains a structural control helper only; the allocator/receiver never synthesizes or consumes a reconstructed start result. The fixed receiver is `/home/frappe/frappe-bench/env/bin/python -B -I -m erpnext.exe_auth.native_receiver`, overriding ordinary boot entrypoint in a separately CREATED owned initializer container. Registry has aligned its prospective entry/UID1000/mount metadata with this command. Its exact CREATED CID/image/entry/UID/limits/protected readonly mounts and sole original-success stdin branch remain required parent-side authentication; a serialized frame or mounted descriptor alone is not authority. Physical package/process/pipe qualification remains unrun, and no standalone action-file/caller-JSON allocation mode exists.

The receiver reads one <=16 KiB newline frame plus actual EOF under an absolute five-second admission. The five closed fields are `version`, exact 13-field `tuple`, actual `initial_sql_time`, `original_lease_expires_at`, and `remaining_work_milliseconds`. `/run/exe-native/erp-package.json` is an owner-only default-off operator package. It binds job/company/intent/deployment/profile/config/initializer to the frame and a fixed site/database, UID1000 and image digest; it does not claim independently measured image identity. Fixed sibling credential files are `core-worker.dsn`, `native-operator.json`, and `native-observer.dsn`; sites root is `/home/frappe/frappe-bench/sites`. No credential, tuple or selector comes from argv/env/HTTP. The actual Core parameterized reader reuses the guarded published function and exposes no claim/start/recovery path. Core credential exposes existing provisioner EXEC capabilities and must remain confined to this private package, not a falsely described read-only Core login.

The trusted recipe must come from the stored immutable profile/config/initializer, with an independently owned sites root and reviewed fixed database operator connection. Core returns no hostname or email. Matching the three digest fields alone is not proof that an arbitrary site recipe is authentic; the parent must resolve and bind the actual recipe. Private credential files are canonical, owner-only 0400/0600 under a 0700 owned parent, bounded and checked against FD/name identity; credentials stay private memory, never argv, web env, logs or the marker. Native installation needs create-only database authority, not a public app credential.

Each current read binds the exact job/token/attempt/worker/company/deployment/intent/action/profile/config/initializer/request tuple and original creator. SQL-relative remaining time is reduced by the complete monotonic read interval and a precision margin; neither renewals nor later reads extend the original admitted end. Child clocks never receive a parent's monotonic epoch. On the first actual Core read, current SQL time minus the initial SQL observation conservatively debits pre-start/CLI transit from relative remaining work, with the complete child receive/read bracket and a precision margin additionally charged. The resulting local work end freezes; every actual current SQL expiry can shorten it, and original SQL expiry always caps it. Parent retains its original absolute container lifetime independently. Admission requires 120 seconds installation, 30 seconds observer, 30 seconds cleanup and one-second margin. This is finite checkpoint authorization, not distributed atomicity or remote cancellation. The existing Frappe installer has many internal commits/filesystem writes and is not continuously cancelable; if the lease becomes insufficient after issuance, the durable marker remains quarantined and no retry, repair, replacement, deletion or accepted readiness is permitted. The actual installation duration may exceed this recipe: genuine measurement is required, and a short lease must refuse rather than receive a waiver.

The atomic O_EXCL/fsync first-writer record precedes installer issuance and native user creation. It omits the lease token/passwords and retains `issued_or_quarantined` even on success. Native `_new_site` uses `force=False`, `require_new_database=True`, no source SQL/backup, fresh DB/login, freshly generated database secret and unreturned random built-in Administrator secret. Ordinary `bench new-site` does not forward this fresh-only guard and is not used. Existing `hosted_site.py` remains unchanged and synthetic-demo-only. No base site or common config is mutated; target collision refuses.

## Initial native authority

`frappe.permissions.get_roles` adds All, Guest and Desk User. User JSON gives Desk User a User-select grant. `User.on_update` calls `share_with_self`, granting write/share on its own User document, queues Contact creation, and would queue gravatar without a user image. The new user supplies a fixed local avatar, no welcome email, no password or role profile, and only `Exe Company ERP Viewer`. Its opaque technical email/username is freshly generated; no creator email, existing user or global administrator is adopted. Contact's own update hook performs local DB work. ERP wildcard hooks also allowlist Contact and can write a separate bridge database via `EXE_BRIDGE_DATABASE_URL`; private allocation refuses any nonempty inherited value before importing the installer, consuming dispatch, writing a marker or performing native writes. Existing hooks and shared site configuration remain unchanged. This refusal is Source/control coverage, not genuine installation evidence or an exhaustive absence-of-egress claim.

On the new site only, custom permissions close all automatic-role writes and automatic read/select/report on control/credential doctypes; the writable self DocShare is removed before the user/binding transaction commits. The dedicated role grants read/select/report only on Company, Customer, Supplier and Item, no writes/export/share/control roles. Existing company-session `native_user` requires an enabled System User with an enabled explicit role and denies Administrator/System Manager/Script Manager, so this finite role is compatible with its identity gate. The actual business API currently exposes only Customer and Item. This does not claim complete business-owner capabilities.

The User controller `has_permission` hook denies standard users to non-Administrator callers but otherwise returns True; its query-condition hook excludes standard users, rather than providing a self-only rule. General role/DocShare checks still matter. Native self-service endpoints such as theme/password/profile require separate direct-HTTP QA; removal of DocPerm/DocShare is not a blanket proof that every whitelisted User method is unavailable. The public company-mode ingress remains separately constrained. No public company-session mapping or paid entitlement is installed by this private library.

## Binding and observer

The new passwordless User, finite role policy and `__exe_native_binding` record commit together after installation. Its exact action/intent/company/creator/user/marker digest and nonsecret tuple are persisted, with update/delete/truncate refusal triggers. An actual current/original-budget/creator read is required after commit and before returning unverified metadata, plus another after native cleanup. Loss during commit denies the response while retaining the committed binding and marker; it never retries allocation. This transaction does not make CREATE DATABASE, installer commits or filesystem effects atomic. Native DBA authority can bypass native triggers; it is not a runtime capability and must stay private.

The independent observer takes a distinct native read-only credential, repeats the actual current Core owner read, queries actual native database OID, binding, user, roles, password absence, self-share absence, finite permissions and immutable trigger presence, then rechecks Core. It does not trust the allocation response as evidence and returns `native_readiness:unverified`. A fresh catalog guard runs before native reads and at the final native observation: session_user must equal current_user and be a safe LOGIN; all memberships/SET ROLE paths and parent-role grants refuse. It denies DB ownership anywhere and CREATE/TEMP on the fixed current DB, non-system schema/object/type/routine ownership or CREATE, foreign-schema usage, any table/column write, sequence privilege and non-system SECURITY DEFINER EXECUTE. SELECT is limited to exact columns on eight fixed tables; __Auth exposes only name/doctype, never password/hash/encrypted credential columns. Required columns must actually exist and be readable. This is Source-authored and unrun, not measured privilege proof. Operator setup must explicitly close PUBLIC TEMP on this newly owned DB and supply precisely scoped native reader grants; no existing site/role is mutated by the observer. Physical image/storage/site-file provenance and real native observer transport remain genuine qualification gates.

Primary exceptions and every actual cleanup error remain separately retained, with <=16 finite secondary records, fixed stage/class summaries and no message/SQL/argument logging. FD, cursor, connection and native rollback/close waits share supplied absolute cleanup ends and each wait is at most three seconds; no new work interval is created. Pending cleanup is not canceled or claimed closed, and the fixed receiver exits unsuccessfully while the owning parent retains quarantine and actual resource closure responsibility. Frappe uses ContextVars: bounded cleanup copies the actual caller context, closes the captured native DB and releases the original caller local state, rather than calling destroy in an empty new-thread context. Thread wait bounds are not continuous OS limits. Installer stdout/stderr is discarded under a 64 KiB ceiling rather than exposing potential native diagnostic/credential text; actual underlying native logging behavior still needs review during genuine qualification.

## Held validation proposal

Marker write/file-fsync and directory-fsync errors preserve their original exception alongside descriptor-close failures. The install lock uses filelock's explicit shared (non-thread-local) context so bounded release closes the acquired lock; operation and release errors remain separate. Cleanup retains one combined maximum of 16 secondary/pending entries, with explicit per-kind overflow counts rather than silently dropping or replacing the primary error. Required cleanup is still attempted after the ledger reaches its cap. Secret reads and marker cleanup use the originating absolute end when one exists; standalone file controls use an explicit local three-second end. No renewed lifecycle budget is introduced. Filelock 3.20.1's documented `thread_local=False` supports cross-thread state; actual native package and lock shutdown still need qualification.

First review all new files and exact unchanged ERP135 parent bytes. Then propose one fixed existing Python parser pass (each <=5 seconds), followed by the exact named controlled test vector once (<=30 seconds, finite output), using only existing runtime and no imports before grant. The sealed plan enumerates the current controls. They cover exact tuple/start consistency, current shortening/revocation/foreign refusal, charged interval, no-replace/quarantine marker, collision/symlink, private secret modes, parameterized Core query, commit/owner and commit/lease loss, primary/secondary/context cleanup, receiver closed fields/original work transit debit, actual bounded pipe framing and final native catalog drift. They do not prove actual Frappe installation, parent-pipe authenticity or real native observer isolation.

Before genuine services, independently review and exercise the actual native catalog predicate/readonly role grants and immutable binding, fixed query/socket and parent lifetime bounds, receiver-created metadata/pipe provenance and physical observers. A separately reviewed existing owned fixture may then create two genuinely fresh sites using stock cached images under exact quotas, first-start provenance, actual owner revocation/lease expiry/foreign tuple negatives, both independent observers and complete owned-resource cleanup. No existing site adoption, destructive repair, global-admin promotion, live Jkt, provider calls, public listener, price/payment/native readiness or paid activation claim is authorized by this Source stage.

## Local controlled observation

The authorized ordinary run used the fixed existing Python 3.14.7 interpreter: version, seven AST-only parser commands and all 35 named local controls passed. The producer completed in 1.503 seconds with no primary/secondary failure, complete recorded EOF/reap/child-group/pipe/selector flags and exact owned-TMP removal. These controls use fake database/lock interfaces and real temporary files/pipes; they do not import actual Frappe, psycopg2 or FileLock, provision a native site, or authenticate a parent dispatch. Selected runtime and Source pre/post checks are local supplier scope, not exhaustive runtime-image qualification.

The separate read-only post-packaging helper failed its whole-stat stability assertion. Its actual before/after values were not retained, so the changed field/cause is unknown. Original target output and partial original Tool prefix remain preserved; the post closure/current-resource outputs were not produced or reconstructed. This does not change the target parser/control result and is not a native product failure. Root's independent checks, if accepted, are separate evidence. No target or post helper retry occurred.

Actual Core SQL/ERP PostgreSQL privilege enforcement, real Frappe installer and User/Contact hook behavior, native viewer ACLs, physical package/image/storage provenance, protected original-success parent/container/pipe handoff and independent real observer remain unqualified. No deployment, public route, live provider/bridge, customer-site mutation or readiness/activation claim follows from these local controls.


## Separate observer composition (Source only)

This descendant is rooted at the accepted ERP136 lint head `841b527ee681d8c2ffe9a7563b2bc6064eb19369` and changes the private receiver to allocation-only completion. It
returns `pending_observation` and `native_readiness: unverified`. It never starts
an observer, records a Core receipt, retries installation, adopts an existing
site or clears the durable first-writer marker. The existing 35 local controls
and ERP136's ordinary observations remain predecessor evidence; none of this
new composition has been parsed, imported, tested or run yet.

The trusted Core parent must retain its ORIGINAL successful first-start in
memory across three separately bound owned children. A tuple, file, owner read
or returned JSON cannot recreate that authority. Each child must have an actual
inspected CREATED container, fixed entry/image/UID/quotas/protected mounts and
exact company/job/intent/profile/config/initializer binding before dispatch.

1. Allocation uses the fixed `native_receiver` entry and native operator file.
   Actual Frappe installation and the immutable native User/action binding
   commit may complete; response loss or post-commit owner/lease failure leaves
   pending/quarantined state. No phase retries it or manufactures a new start.
2. Operator setup uses `native_observer_setup`, never an allocator. It verifies
   the actual current Core creator and persisted native binding/owned marker on
   the EXACT newly created database. Under one transaction and an intent-derived
   role advisory lock, it refuses any existing observer role, suppresses
   collection BEFORE password-bearing SQL and creates one unique LOGIN with no
   elevated attributes or memberships, connection limit 1 and read-only finite
   settings. It closes PUBLIC CREATE/TEMP only on this owned new database and
   public-schema CREATE, grants CONNECT/USAGE and exactly the observer's eight
   tables' approved columns. There are no defaults, table-wide SELECTs,
   password-column grants, global role promotion or arbitrary definer repairs.
   An executable PUBLIC non-system SECURITY DEFINER is a refusal. Current Core
   checks precede each native grant/write and follow successful commit and cleanup.
   Failure plus rollback exits quarantined without a guaranteed later Core read. A lost
   setup commit acknowledgement, owner loss or teardown failure stays
   quarantined; existing-role refusal prevents re-adoption on a later attempt.
3. Read-only observation uses `native_observer_entry` with a protected mount
   containing package, Core worker and native observer files ONLY. The operator
   credential must be absent, not merely unused. Its complete local import path
   has no `native_site`, allocator or native-write credential loader. It uses
   the actual read-only native login/catalog ceiling, exact binding/marker/User/
   permissions and fresh Core observations; returned metadata remains
   unverified and cannot itself open an app or create a receipt.

All three frames keep the original `initial_sql_time` and
`original_lease_expires_at`. The parent's relative remaining work only shrinks.
It retains one original hard monotonic container lifetime across all children,
including the gaps and operator phase. Allocation v1 retains its original five fields and transit calculation. Setup
and observation require the closed `core-first-writer-phase-v2` eight-field
frame: the original five fields plus fixed `phase` (`setup` or `observe`),
`handoff_sql_time` and `original_remaining_work_milliseconds`. The fresh
handoff is from the parent's actual same-Core owner read. Original allowance,
initial SQL time and expiry must remain bound to its retained first-success
state; JSON cannot prove those anchors. Remaining work cannot exceed the
original allowance less elapsed lineage time. Later children debit only
handoff-to-first-current-read SQL transit, their full receive/query bracket
and precision margin from the already-shrunk remaining work; every shortened current lease wins. The shared
absolute 5-second pipe admission does not renew. Allocation admission retains
181 seconds; setup retains 90 seconds and observation 60 seconds, including the
same 30-second cleanup reserve. Neither a later child clock nor role commit
renews work/SQL expiry. If setup/observer no longer fits, leave pending/quarantine.
Finite SQL authorization observations do not establish distributed atomicity
with native commits or OS teardown, and local abort is not cancellation proof.

The phase package and physical staging are still unqualified. Host UID 501,
container UID labels and an old stock image do not prove actual UID1000/0400
protected file readability under a UID1000/0700 parent. The real image must
contain the new modules and fixed entries. Owned-only staging, mounted stat/read
checks, installer/package/hooks, actual role creation and column grants,
independent catalog observation, post-commit failure/quarantine and exact
returned-resource cleanup require the separately reviewed genuine fixture.
No existing site's policy or shared PostgreSQL roles may be modified.

Held ordinary validation proposal: retain the seven ERP136 parsers and all 35
controls, add AST-only parsing of the five new Python files and run the nine
phase controls. Use selected pinned Python/stdlib only, no Frappe, FileLock,
psycopg or native service imports. Keep the original 70-second work/90-second
absolute clock, per-parser 5 seconds, controls 30 seconds, sampled group RSS
512 MiB, 64 KiB per stream and 1 MiB/80-file/8-directory evidence. Admission and
cleanup must refuse or fail honestly when shared remaining time is insufficient;
these are future recipes, not executed observations. Actual FileLock/native
packages, parent dispatch, SQL grants, image and observer remain unqualified.

## Private phase controlled validation

Import05 passed the two changed-file AST parsers, all 49 controlled cases (the
unchanged 35 allocation/contract controls plus 14 phase controls), and Ruff
0.14.10 across all 12 Python paths. The five-command run finished in
2.151308917 seconds; all child pipes reached EOF, leaders were reaped, owned
process groups were checked absent and the owned temporary directory was
removed. Source and selected runtime identities matched before and after.
Clock04 remains a failed attempt: its 12 AST parsers and 49 controls passed,
then Ruff refused two import-format blocks. Only those blocks were formatted
for Import05; the ten other prior parser outcomes are separate reused evidence.
No Frappe, psycopg, FileLock native runtime, database, installer, image, protected
parent three-child transport or fresh-site observer qualification occurred.
The physical package/UID/mount/socket and genuine three-phase gates above
remain open. No readiness or paid/native activation follows these results.

The observer login is derived from the existing immutable Core intent UUID, obtained by a fresh restricted function read before protected phase files are sealed. The start action UUID remains SQL-generated later. This change permits a presealed observer DSN without predicting the action; current action/owner/job/lease and exact native marker checks are unchanged. SQL permits only one start per intent; observer setup still refuses an existing role and issues only its finite read surface. Added regression checks are controlled Python tests, not actual native phase qualification.
