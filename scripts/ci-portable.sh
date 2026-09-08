#!/usr/bin/env bash
#
# Portability shims for the CI host-state scripts.
#
# PROVENANCE — this file is a VERBATIM copy (below this header) of
# AskExe/exe-build `.github/scripts/ci_portable.sh`, introduced there by PR #222.
# It is copied rather than reimplemented on purpose: exe-os and exe-build now
# lease heavy-CI slots from the SAME host registry (~/.cache/ci-slots-shared),
# so the two repos must agree byte-for-byte on how a lock is taken, how a lease
# is aged, and how many slots the host has. A second implementation of that
# arithmetic is a second opinion about the host, and two opinions about the host
# is the bug this whole change exists to remove. If you edit these helpers, edit
# them in exe-build and re-copy — do not fork them.
#
# WHY THIS EXISTS  (bug 90afb4d4)
# ------------------------------
# Both scripts were written for the Linux pool and used GNU-only tools:
# `sha256sum`, `find -printf`, and `flock`. macOS has none of them. That is not
# a cosmetic problem — it is why `Build & Test (mac)` was the ONE lane in this
# workflow that called neither script, and therefore the one lane with no
# target-dir isolation and no concurrency bound at all. A guard that cannot run
# on a host is a guard that host does not have.
#
# So: source this, use these, and both scripts run identically on the Linux
# runners and on mac-m4-1.
#
# shellcheck shell=bash

# sha256 of stdin, first 64 hex chars. GNU coreutils ships `sha256sum`; macOS
# ships `shasum -a 256`. Both print "<hash>  -".
ci_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum
  else
    shasum -a 256
  fi
}

# Modification time of a path, as whole seconds since the epoch. BSD `stat`
# takes -f, GNU `stat` takes -c. Prints nothing if the path does not exist.
#
# The output MUST be validated rather than streamed straight through, because
# `-f` means something else entirely to GNU stat: "filesystem status", with the
# format string read as a FILE argument. `stat -f '%m' /path` on Linux prints a
# seven-line filesystem block for /path and only THEN exits 1 — so an
# `if stat -f ...; then return 0; fi` form falls through to the GNU branch with
# that block already on stdout, and the caller's arithmetic gets
# "  File: ..." instead of an integer. Caught by the semaphore's own test suite
# failing on `linux-build-check` (leases read as neither live nor free), which
# is what a guard is for.
ci_mtime() {
  local path="$1" t
  t=$(stat -f '%m' "$path" 2>/dev/null || true)
  if [[ "$t" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$t"
    return 0
  fi
  t=$(stat -c '%Y' "$path" 2>/dev/null || true)
  [[ "$t" =~ ^[0-9]+$ ]] && printf '%s\n' "$t"
  return 0
}

# True when $1 exists and was last modified less than $2 minutes ago.
# Replaces `find "$p" -maxdepth 0 -mmin "-$n" -print`, which BSD find supports
# but which is easier to reason about — and to test — as explicit arithmetic.
ci_mtime_within_min() {
  local path="$1" minutes="$2" mtime now
  [[ -e "$path" ]] || return 1
  mtime=$(ci_mtime "$path")
  # Numeric, not merely non-empty: a non-integer here would make the arithmetic
  # below a bash syntax error, and a lock or lease whose age cannot be computed
  # must read as "cannot confirm it is fresh", never as an arithmetic accident.
  [[ "$mtime" =~ ^[0-9]+$ ]] || return 1
  now=$(date +%s)
  (( now - mtime < minutes * 60 ))
}

# Print "<mtime-seconds>\t<path>" for every directory at exactly $2 levels below
# $1, oldest first. Replaces `find -printf '%T@\t%p\n' | sort -n`, which is
# GNU-only. Ties are broken by path so the order is deterministic — a test that
# depends on which of two same-second directories is evicted would otherwise be
# flaky.
ci_dirs_by_mtime() {
  local root="$1" depth="$2" d mtime
  while IFS= read -r d; do
    [[ -n "$d" ]] || continue
    mtime=$(ci_mtime "$d")
    [[ -n "$mtime" ]] || continue
    printf '%s\t%s\n' "$mtime" "$d"
  done < <(find "$root" -mindepth "$depth" -maxdepth "$depth" -type d 2>/dev/null || true) |
    sort -k1,1n -k2,2
}

# --- mutual exclusion ------------------------------------------------------
#
# `flock` is not installed on macOS. The previous behaviour was to skip the
# prune (setup_cargo_target_dir.sh) or refuse outright (ci_concurrency_slot.sh)
# — i.e. on the one host that actually needed them, one guard silently did
# nothing and the other could not run.
#
# `mkdir` is atomic on every filesystem either host uses, so it is a correct
# mutex. The only thing it lacks versus flock is automatic release when the
# holder dies, so the lock carries a TTL: a lock directory older than
# CI_LOCK_STALE_MIN is broken and re-taken. Both critical sections guarded here
# run entirely inside their script's own lifetime and take milliseconds, so a
# multi-minute TTL can only ever fire after a genuine crash.
CI_LOCK_STALE_MIN="${CI_LOCK_STALE_MIN:-10}"

# ci_lock <lock-dir> <wait-seconds>; returns 0 when held, 1 on timeout.
ci_lock() {
  local lock="$1" wait_s="${2:-300}" deadline
  deadline=$(( $(date +%s) + wait_s ))
  while :; do
    if mkdir "$lock" 2>/dev/null; then
      printf '%s\n' "${GITHUB_RUN_ID:-local}-$$" > "$lock/owner" 2>/dev/null || true
      return 0
    fi
    # Break a lock whose holder died without releasing it.
    if ! ci_mtime_within_min "$lock" "$CI_LOCK_STALE_MIN"; then
      echo "warning: breaking stale CI lock (>${CI_LOCK_STALE_MIN}m old): $lock" >&2
      rm -rf -- "$lock"
      continue
    fi
    (( $(date +%s) < deadline )) || return 1
    sleep 1
  done
}

ci_unlock() {
  rm -rf -- "$1" 2>/dev/null || true
}

# --- host capacity: the SHARED, repo-neutral slot registry -----------------
#
# WHY THIS IS HERE  (bug: 40 heavy workers on 16 cores)
# ----------------------------------------------------
# exe-build and exe-os each ran a concurrency limiter on the SAME physical Mac
# and were mutually invisible to each other: exe-build leased from
# ~/.cache/exe-build-ci-slots (2 slots x CARGO_BUILD_JOBS=8) and exe-os from
# ~/.cache/exe-os-ci-slots (3 slots x vitest --maxWorkers=8). Each was correct
# about itself and wrong about the box: worst case 5 concurrent slots x 8 =
# ~40 heavy threads on 16 cores. Measured loadavg peaked at 75, and exe-os's
# quarantined vitest lane logged individual tests taking 60-96s against a
# 60s testTimeout — 869 tests that pass, failing purely on contention.
#
# Two limiters that cannot see each other do not bound a host; they bound their
# own repo and lie about the rest. So the registry is now ONE directory that
# belongs to neither repo and represents the HOST's capacity, which is the
# constraint that actually exists:
#
#     $HOME/.cache/ci-slots-shared
#
# It is per-host by construction (a path on the runner's own filesystem), so
# the Linux pool and mac-m4-1 keep separate registries without needing to know
# about each other, and any third repo that adopts these helpers joins the same
# bound instead of adding a fourth invisible one.
ci_shared_slot_root() {
  printf '%s\n' "${CI_SHARED_SLOT_ROOT:-$HOME/.cache/ci-slots-shared}"
}

# Logical CPUs on this host. BSD sysctl, then GNU nproc, then a deliberately
# small fallback (guessing high would over-provision, which is the failure this
# whole file exists to stop).
ci_cpu_count() {
  local n
  n=$(sysctl -n hw.ncpu 2>/dev/null || true)
  [[ "$n" =~ ^[0-9]+$ ]] || n=$(nproc 2>/dev/null || true)
  [[ "$n" =~ ^[0-9]+$ ]] || n=4
  printf '%s\n' "$n"
}

# ci_host_heavy_slots <worker-threads-per-slot>
#
# The arithmetic, stated once for every repo: a heavy job burns
# <worker-threads-per-slot> CPU threads for its whole run, so the number of
# slots the host can carry is cores / threads-per-slot. Slots x workers then
# never exceeds the core count, which is the invariant we want and the one the
# two per-repo limiters could not express.
#
#   mac-m4-1  16 cores, 8 threads/slot (vitest --maxWorkers=8, CARGO_BUILD_JOBS=8)
#             -> 2 slots. 2 x 8 = 16 = cores.
#   linux pool 4 cores,  2 threads/slot (CARGO_BUILD_JOBS=2)
#             -> 2 slots. 2 x 2 =  4 = cores.  (unchanged from the pinned 2)
#
# Floor of 1: a host smaller than one job's worker count still has to run the
# job, just strictly one at a time.
#
# RESERVED CORES (bug 4187ed64 — no cross-repo CI concurrency limit):
#   mac-m4-1 also permanently hosts two Virtualization.framework VMs that are
#   NOT CI jobs and never show up as a lease in this registry: Docker Desktop
#   (measured ~111% CPU) and the ci-linux Lima VM (measured ~92% CPU) — both
#   required for other work on this box, neither optional, neither killable to
#   make room. `cores / per_slot` alone (16 / 8 = 2) treats all 16 cores as
#   available to CI, which is exactly the assumption that let two exe-os
#   vitest jobs plus that VM overhead push loadavg to 110+ with only 19 worker
#   processes running. CI_HOST_RESERVED_CORES lets a caller that KNOWS it runs
#   natively on such a host (never the default — a Linux runner or a job
#   running inside one of those VMs has no such reservation to make) subtract
#   that overhead before dividing, so slots x workers stays inside the cores
#   actually left for CI rather than the cores physically present.
#     mac-m4-1 native macOS lane, reserved=4: (16 - 4) / 8 = 1 slot.
# Callers opt in explicitly (see ci.yml's mac-fast heavy lane); the default of
# 0 leaves every other host's arithmetic exactly as before.
ci_host_heavy_slots() {
  local per_slot="${1:-8}" cores reserved effective slots
  [[ "$per_slot" =~ ^[0-9]+$ ]] && (( per_slot > 0 )) || per_slot=8
  cores=$(ci_cpu_count)
  reserved="${CI_HOST_RESERVED_CORES:-0}"
  [[ "$reserved" =~ ^[0-9]+$ ]] || reserved=0
  effective=$(( cores - reserved ))
  (( effective < 0 )) && effective=0
  slots=$(( effective / per_slot ))
  (( slots < 1 )) && slots=1
  printf '%s\n' "$slots"
}

# ci_registry_capacity <root> <my-slot-count>
#
# Guards the one way a SHARED registry can be worse than two private ones: two
# repos disagreeing about how many slots exist. If exe-os believed 3 and
# exe-build believed 2, three leases would be handed out and the cap would
# silently stop capping — under exactly the contention it exists for.
#
# So capacity is recorded in the registry and every claimant uses the MINIMUM of
# what it believes and what is recorded. Disagreement therefore errs toward
# UNDER-capacity (a slot idle) and never toward over-capacity (the failure mode
# that produced loadavg 75).
#
# The subtlety is what gets WRITTEN BACK, and both obvious answers are wrong:
#
#   * Write the clamped value  -> the clamp is self-perpetuating. One stale or
#     transient low record pins the host at that number for good, so a 2-slot
#     box sits at 1 and jobs wait out the whole acquire budget beside genuinely
#     free capacity.
#   * Write our own value      -> a live lower-capacity repo clamps only the
#     FIRST higher claimant. That claimant rewrites the record upward and the
#     next one takes a slot the lower bound never intended to exist, which is
#     the bound failing open — the exact thing this file exists to prevent.
#
# So: a lower record is honoured AND LEFT ALONE while it is FRESH, and a
# claimant only records its own number when there is no fresh lower one. A repo
# that genuinely believes the smaller number re-writes it on every claim, so it
# keeps clamping for as long as it is actually running; a record nobody is
# refreshing ages past CI_CAPACITY_FRESH_MIN and decays. Freshness defaults to
# 180 minutes: the longest lease TTL on the fleet, i.e. the longest a recorded
# capacity could still be protecting a lease taken under it.
CI_CAPACITY_FRESH_MIN="${CI_CAPACITY_FRESH_MIN:-180}"

ci_registry_capacity() {
  local root="$1" mine="$2" recorded=""
  local file="$root/capacity"
  [[ "$mine" =~ ^[0-9]+$ ]] && (( mine > 0 )) || mine=1
  if [[ -f "$file" ]] && ci_mtime_within_min "$file" "$CI_CAPACITY_FRESH_MIN"; then
    recorded=$(sed -n 's/^slots=\([0-9][0-9]*\)$/\1/p' "$file" 2>/dev/null | head -1)
  fi
  if [[ "$recorded" =~ ^[0-9]+$ ]] && (( recorded > 0 && recorded < mine )); then
    echo "warning: registry $root records capacity $recorded, this job computed $mine; using $recorded (never the larger) and leaving the record alone" >&2
    printf '%s\n' "$recorded"
    return 0
  fi
  printf 'slots=%s\nby=%s\ncores=%s\nat=%s\n' \
    "$mine" "${GITHUB_REPOSITORY:-local}/${GITHUB_JOB:-njob}" "$(ci_cpu_count)" \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$file" 2>/dev/null || true
  printf '%s\n' "$mine"
}

# ci_lease_is_live <lease-file> <fallback-ttl-minutes>
#
# A lease is live while its OWNER says it is. The TTL is read from the lease
# itself (`ttl_min=`), not from the reader's own config, because the repos
# sharing this registry hold slots for very different lengths of time: exe-os's
# vitest lane runs ~25m (TTL 45), exe-build's db-backed gateway tests ~105m
# (TTL 180). A reader applying its own 45m TTL to a foreign lease would evict a
# job still compiling — turning the shared registry into a cause of the very
# oversubscription it prevents. Leases without the field (older format) fall
# back to the reader's value.
ci_lease_is_live() {
  local lease="$1" fallback="${2:-180}" ttl
  [[ -f "$lease" ]] || return 1
  ttl=$(sed -n 's/^ttl_min=\([0-9][0-9]*\)$/\1/p' "$lease" 2>/dev/null | head -1)
  [[ "$ttl" =~ ^[0-9]+$ ]] || ttl="$fallback"
  ci_mtime_within_min "$lease" "$ttl"
}
