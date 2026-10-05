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
