# Held private native output supervisor

Native runtime remains unrun. The fixed private receiver is now a stdlib Linux PID1
supervisor. It must be launched as the fixed package-owned script, not with
`-m erpnext...`: ERP137 erpnext/__init__.py imports Frappe before the module runs.
The proposed fixed entry is Python -B -I followed by
`/home/frappe/frappe-bench/apps/erpnext/erpnext/exe_auth/native_receiver.py`.
Core73's existing -m descriptor does not yet admit this entry and remains a
separate explicit integration prerequisite. Its only worker is `native_allocate_worker`, using the same Python
executable and one exact internally allocated/preserved result descriptor. It binds the live child to UID1000,
PPID1, the exact PID namespace all four actual FIFO device/inode identities, and a retained pidfd before releasing the original
bounded Core frame once. No native package/DSN/Frappe import occurs in standby.
The worker keeps the prior package, frame, Core read, operator, first-writer and
allocation ordering; normal stdout/stderr are genuine pipe-backed TextIO.

The original five-second admission includes supervisor/standby startup. The same-kernel internal gate alone carries the supervisor's original absolute
monotonic start/startup/work ends; worker delay/imports cannot recompute or renew
them. These times never enter the Core/public frame and are not authority.
Core initial SQL transit and the original read bracket remain charged. Internal
budget messages carry an absolute end on the same kernel and only reduce the
supervisor end, never renew it. All native output
is hashed/discarded with a combined 65,536-byte ceiling. Internal budget and final
JSON share a 2,048-byte result ceiling. Ordinary setup/observer emit_main callers
still use FD1. The historical DiscardNativeOutput class remains only for existing
controlled assertions, and is never used for allocation output.

Fatal output, channel or lifetime failure kills only the retained PID>1 worker
through pidfd. PID1 then exits; Linux kernel namespace teardown kills remaining
worker descendants, including new sessions. The supervisor never sends SIGKILL
to same-namespace PID1. This is Source reasoning, not observed namespace closure.
External PostgreSQL backends, commit/file side effects and cancellation/rollback
remain unproved and quarantined; no automatic retry, second start or adoption.

Pending physical prerequisites: Linux kernel pidfd support, Python signal API,
actual PID1/private namespace, UID1000, Python/module packaging, preserved descriptor/actual child FIFO inheritance,
Core73 parent ownership/lifetime, protected mounts and ERP137 receiver image.
The existing cache overlay does not contain this successor. No native creation,
setup, independent observation, receipt or readiness is qualified by these tests.

Ordinary validation retained all prior native-site/phase assertions with an
explicit worker carrier binding. All 73 controlled tests and 14 syntax checks
passed in the second ordinary attempt. That attempt remained FAILED on four
Ruff findings; import layout and two unused binding names were then corrected
with scoped AST proof, and a separate Ruff-only fourteen-path check passed.
The first attempt remains FAILED at 71/73 due two macOS pipe-carrier assumptions.
No Docker/SQL/network or native package replay occurred in these ordinary checks.
The owner used shared 70s work/90s absolute ends, 30s controls/Ruff, 64KiB streams,
1MiB proof and sampled 512MiB owned-group RSS; these are finite ordinary bounds,
not hard physical native quotas. Selected Python/runtime pins are provenance,
not exhaustive dynamic dependency proof. No test is a PID1 proof. Subsequent
separately reviewed physical controls must measure log cap, blocking worker,
setsid grandchildren, pidfd kill/reap and namespace closure with genuine Linux
PID1, and retain first versus cleanup errors before native allocation is allowed.

The supervisor keeps the original five-second startup end active after release,
including blocking worker imports/package/frame admission. Exactly one initial
admitted_end event must be observed before that end; only then is fixed work end
active. Late/duplicate initial or pre-admission ordinary budget events refuse.
Subsequent SQL events only shrink the admitted end. No startup window resets.

### Held correction after controlled Linux observations

The cached-image three-case fixture observed original startup refusal, 65,537-byte combined log refusal and a detached setsid grandchild under UID1000. In the detached case the shrinking work budget expired before Source wait, so the supervisor retained a TimeoutError secondary and returned no worker status; the controlled harness subsequently reaped the killed worker. This is not complete supervisor cleanup, native allocation proof or individual descendant absence after PID1 exit. All forced-close EOFfalse observations and earlier failed metadata admissions remain retained.

This Source-only successor keeps a separate original start-based termination bound: before a valid frame it is startup5s plus termination3s; after the one timely original frame it is original remaining work plus the existing30s reserve. Later admitted/lease budget events shrink only startup/work authority. Fatal pidfd kill/wait uses at most3s within that fixed original termination bound, never a renewed work/lease origin. Primary TimeoutError and distinct cleanup errors remain preserved; success still requires actual EOF and worker wait. No external PostgreSQL backend cancellation/rollback or Core73 fixed-script composition is qualified. The pinned ordinary run passed all76 named controlled cases, fourteen syntax checks and Ruff. The same three cached-image Linux cases then passed: startup refusal at5.027s, combined log refusal after65,537 bytes, and the original8s detached-grandchild work refusal. Each recorded Source worker return status-9 before harness polling/wait, with no cleanup secondary. All inner fatal EOFflags remainedfalse at forcedclose; external CLI EOF/reap and exact three-container cleanup were separately complete. The prior incomplete Source reap remains historical evidence, superseded only for these controlled cases. No individual descendant post-PID1-exit absence, external PostgreSQL cancellation/rollback, real native imports/allocation, protected secret staging or Core fixed-script composition is qualified.

## Private installer output

The fixed supervisor supplies `FRAPPE_STREAM_LOGGING=1` only to its private
allocation worker. After the validated internal RELEASE gate, that worker
disables Python logging before loading Frappe bindings. This avoids read-only
bench log-file creation and password-bearing DDL warnings; ordinary Frappe
logging is unchanged. Schema-sync progress display is suppressed only around
the private `_new_site` call and restored on success or error. No `CI` setting,
request state, additional writable mount or output-cap increase is used.

The retained ARM64 supplier constructor probe reproduced `OSError` errno30 at
the bench database log before a database connection. Stream logging allowed
construction; fixed logging disable produced zero synthetic DDL output. It
used no SQL or credentials and verified owned cleanup. These constructor and
controlled restoration checks do not qualify a complete native allocation.

## Private budget publication

Every fresh Core owner read still runs. The private worker omits a regular
budget frame only when its deadline exactly equals the last completely
published deadline. Initial admission is published once; every strict decrease
is published immediately. There is no rounding, queue, debounce or renewal.
The existing 1,024-byte budget-channel limit, 2,048-byte supervisor result limit,
watchdog and original work/cleanup clocks are unchanged. Invalid publication,
cap exhaustion and raised or partial writes permanently refuse further
publication in that worker, including an old duplicate deadline.

Private failure JSON may include `cleanup.refusal_code` for one of fourteen
fixed allocation/owner/channel literals only. The exception must be exactly
`Refused` with one exact string argument, or the exact `CleanupFailure` primary
must satisfy that condition. Unknown codes, subclasses and arbitrary text are
omitted; existing primary classes and cleanup counters remain intact. This is
finite error evidence, never action or readiness authority.

Controlled actual-pipe tests preserve 1,000 synthetic fresh reads while omitting
equal deadline frames, retain strict decreases and cap refusal, and verify the
terminal failure latch and literal-only diagnostics. The normal local native
attempt returned `Refused`; its particular code was not retained. These tests
prove the duplicate-publication mechanism, not that attempt's exact cause.
