#!/usr/bin/env bash
#
# ci-concurrency-slot.sh — counting-semaphore for heavy CI jobs (bug 9a66e42c)
#
# WHY THIS EXISTS:
#   mac-m4-1 is one physical box (M4 Max, 16 cores / 128GB) that hosts FOUR
#   exe-os self-hosted runner agents. Up to 4 exe-os CI jobs can land on it
#   simultaneously, and each heavy job runs a full-suite
#   `npx vitest run --maxWorkers=8` plus daemons/MCP servers. 4 x 8 = 32 test
#   workers + daemons on 16 cores oversubscribed the host to load 90+, which
#   produced FALSE CI timeouts: a loaded-box timeout is a HOST claim, not a
#   code failure. Chasing those wastes agent hours on phantom regressions.
#
#   This script re-establishes backpressure as an in-repo workflow STEP — not
#   a runner pre-hook — so no repo/runner/job allow-list can exempt a job from
#   the bound.
#
# WHY IT IS NOW SHARED WITH exe-build (the 40-workers-on-16-cores bug):
#   The bound above was correct about exe-os and blind to everything else.
#   exe-build ran its OWN limiter on the SAME Mac, leasing from
#   ~/.cache/exe-build-ci-slots (2 slots x CARGO_BUILD_JOBS=8) while this
#   script leased from ~/.cache/exe-os-ci-slots (3 slots x maxWorkers=8).
#   Neither registry knew the other existed, so the true worst case was
#   3x8 + 2x8 = ~40 heavy threads on 16 cores. That is measurable, not
#   theoretical: host loadavg peaked at 75, and this repo's
#   `Test (quarantined set)` logged individual tests taking 60-96s against a
#   --testTimeout=60000 — 869 tests that pass, timing out purely on contention.
#   Two limiters that cannot see each other do not bound a host.
#
#   So the registry is now ONE repo-neutral directory that represents the
#   HOST's capacity — the constraint that actually exists:
#
#       $HOME/.cache/ci-slots-shared
#
#   Neither repo owns it. exe-build's .github/scripts/ci_concurrency_slot.sh
#   leases from the same directory with the same arithmetic, out of the same
#   shared helper file (see scripts/ci-portable.sh, a verbatim copy of
#   exe-build's). It is per-host by construction — the path is on the runner's
#   own filesystem — so the Linux pool and mac-m4-1 stay independent without
#   coordinating.
#
# SLOT MATH (host-derived, not repo-chosen):
#   A heavy job burns CI_HEAVY_SLOT_WORKERS threads for its whole run, so the
#   host carries cores / workers slots and slots x workers never exceeds the
#   core count:
#       mac-m4-1: 16 cores / 8 (vitest --maxWorkers=8) = 2 slots. 2 x 8 = 16.
#   That is down from this repo's old private 3 slots (24 threads) — and, far
#   more importantly, the 2 slots are now the SAME two slots exe-build competes
#   for rather than two more on top of them.
#
# macOS PORTABILITY:
#   macOS has no flock(1), no sha256sum and no `find -printf`, which is why
#   exe-build's original Linux scripts could not run on this host at all. The
#   fix for that (exe-build PR #222) is scripts/ci-portable.sh: a mkdir-based
#   mutex with a stale-lock TTL, plus portable mtime/hash helpers. This script
#   sources that layer rather than carrying a third private variant of it.
#
# FAIL-SAFE, NOT FAIL-CLOSED-FOREVER:
#   Every slot is a TTL lease, so a cancelled or SIGKILLed job that never runs
#   its release step cannot wedge the registry: its lease simply stops being
#   fresh and the slot is reclaimed. The TTL is written INTO the lease and read
#   from there, so exe-os's 45m never evicts an exe-build lease that declared
#   180m — a reader must not apply its own deadline to someone else's work.
#
#   The TTL is the BACKSTOP, not the response. In-process cleanup (`if:
#   always()` release) cannot survive cancel-in-progress — GitHub hard-kills
#   the runner mid-step — so a blocked acquire also runs a LIVENESS sweep (see
#   liveness_reclaim below): it asks the GitHub API whether each holder's run
#   is still in progress and reclaims leases whose runs are positively
#   finished, minutes after the death instead of after the TTL.
#
# MODES:
#   ci-concurrency-slot.sh            # acquire a slot (blocks, ~no CPU, until one is free)
#   ci-concurrency-slot.sh --release  # drop this job's slot by TOKEN (if: always()
#                                     # post-step; robust to a cancelled acquire)
#
# ENV (all optional, with defaults):
#   CI_HEAVY_SLOT_WORKERS        worker threads this job uses, i.e. the cost of
#                                one slot; drives the derived slot count (8)
#   EXE_OS_CI_HEAVY_SLOTS        override the derived slot count — escape hatch
#                                only. The default is host-derived precisely so
#                                it cannot drift out of step with exe-build.
#   EXE_OS_CI_SLOT_TTL_MIN       lease TTL, crash reclaim    (default 45)
#                                # a job holds a slot only for the ~25m vitest run,
#                                # so 45 comfortably exceeds it
#   EXE_OS_CI_SLOT_WAIT_MAX_MIN  max blocking wait, then     (default 20)
#                                # fail loud (never proceed unbounded)
#   EXE_OS_CI_SLOT_GUARD_TTL_SEC stale mkdir-mutex reclaim   (default 60)
#   EXE_OS_CI_SLOT_GH_TOKEN     GitHub API token for LIVENESS-based reclaim.
#                                Threaded from secrets.GITHUB_TOKEN by the
#                                workflow; when unset (a local dev run) the
#                                TTL is the only reclaim path — never assumed
#                                from ambient auth, so the script works
#                                identically offline.
#   EXE_OS_CI_LIVENESS_INTERVAL_SEC  min seconds between liveness sweeps
#                                while blocked (default 60) — the check must
#                                not hammer the API on every 5s poll
#   EXE_OS_CI_SLOT_ROOT          registry dir on the host    (default
#                                $HOME/.cache/ci-slots-shared — shared with
#                                exe-build; overriding it re-creates the blind
#                                spot this fix removed)

set -euo pipefail

# Shared portability + host-capacity helpers (see the PROVENANCE note there).
# shellcheck source=scripts/ci-portable.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ci-portable.sh"

workers_per_slot="${CI_HEAVY_SLOT_WORKERS:-8}"
slots="${EXE_OS_CI_HEAVY_SLOTS:-$(ci_host_heavy_slots "$workers_per_slot")}"
ttl_min="${EXE_OS_CI_SLOT_TTL_MIN:-45}"
wait_max_min="${EXE_OS_CI_SLOT_WAIT_MAX_MIN:-20}"
guard_ttl_sec="${EXE_OS_CI_SLOT_GUARD_TTL_SEC:-60}"
gh_token="${EXE_OS_CI_SLOT_GH_TOKEN:-}"
liveness_interval_sec="${EXE_OS_CI_LIVENESS_INTERVAL_SEC:-60}"
root="${EXE_OS_CI_SLOT_ROOT:-$(ci_shared_slot_root)}"
guard_dir="$root/.slots.lock.d"

# The mkdir mutex lives in ci_portable.sh and takes its stale TTL in MINUTES;
# this script has always exposed the knob in seconds. Convert, rounding up, so
# a 60s setting never becomes a 0-minute (always-stale) lock.
CI_LOCK_STALE_MIN=$(( (guard_ttl_sec + 59) / 60 ))
export CI_LOCK_STALE_MIN

# Identity token: unique per job attempt AND per repository, so --release only
# ever drops the lease THIS attempt holds — never a slot reclaimed after TTL
# and reassigned to a different job, and never another repo's live lease in the
# SHARED registry (a run-id/job/runner collision across repos is otherwise
# possible; GITHUB_REPOSITORY is a default env var present in every step, so it
# survives a cancelled acquire the same way the rest of the token does).
token="${GITHUB_REPOSITORY:-AskExe/exe-os}-${GITHUB_RUN_ID:-nrun}-${GITHUB_RUN_ATTEMPT:-1}-${GITHUB_JOB:-njob}-${RUNNER_NAME:-nrunner}"

mkdir -p "$root"

# lease_is_live <lease-file>: true iff the lease exists AND is younger than the
# TTL ITS OWNER declared (ours as the fallback for older-format leases).
lease_is_live() {
  ci_lease_is_live "$1" "$ttl_min"
}

# try_claim: under the guard, grab the first free (or TTL-expired) slot.
# Prints the slot index on success; returns 1 if the guard was unavailable
# or every slot is live. The guard only ever protects this O(slots) scan, never
# a test run, so it is held for milliseconds; ~6s is a generous wait for it.
try_claim() {
  local i lease eff
  ci_lock "$guard_dir" 6 || return 1
  # Reconcile our idea of the host's capacity with what other repos recorded in
  # this shared registry: take the MINIMUM, never the maximum. If exe-os
  # believed 3 and exe-build believed 2, three leases would be handed out and
  # the cap would silently stop capping — under exactly the contention it exists
  # for. Erring toward under-capacity costs an idle slot; erring the other way
  # is loadavg 75.
  #
  # INSIDE the guard, deliberately: this is a read-modify-write of a file two
  # repos share, and it decides how many slots the scan below may consider, so
  # it belongs in the same critical section as the scan. Done outside it, two
  # jobs starting together against an empty registry both read "no record", the
  # higher one overwrites the lower one's write, and both proceed — the cap
  # failing open under exactly the concurrency it exists for.
  eff="$(ci_registry_capacity "$root" "$slots")"
  for (( i = 0; i < eff; i++ )); do
    lease="$root/slot-$i.lease"
    if ! lease_is_live "$lease"; then
      {
        printf 'token=%s\n' "$token"
        # Declared for OTHER repos reading this shared registry: how long this
        # lease stays live, and how much of the host it is consuming.
        printf 'ttl_min=%s\n' "$ttl_min"
        printf 'workers=%s\n' "$workers_per_slot"
        printf 'repo=%s\n' "${GITHUB_REPOSITORY:-AskExe/exe-os}"
        printf 'claimed=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        printf 'job=%s\nrun=%s\nrunner=%s\n' \
          "${GITHUB_JOB:-njob}" "${GITHUB_RUN_ID:-nrun}" "${RUNNER_NAME:-nrunner}"
      } > "$lease"
      touch "$lease"
      ci_unlock "$guard_dir"
      # "<index> <effective-capacity>": the caller needs the capacity that was
      # actually in force, and it is only known under the guard.
      printf '%s %s\n' "$i" "$eff"
      return 0
    fi
  done
  ci_unlock "$guard_dir"
  # Every slot live. Still report the capacity that was in force, so the
  # "all N busy" message names the bound that actually applied rather than this
  # repo's unclamped opinion of it.
  printf '%s %s\n' "-" "$eff"
  return 1
}

# LIVENESS-BASED RECLAIM (the 2026-09-01 merge-queue freeze):
#   Both slots were held by leases whose runs GitHub had already cancelled, and
#   every acquire in the queue waited out its 20m budget against capacity locked
#   by NOTHING. `if: always()` release cannot be the answer — cancel-in-progress
#   kills the runner before any step runs — and the 45m TTL is too slow to be
#   the only backstop: one cancelled job removes half the host's heavy capacity
#   for 45 minutes, two remove all of it.
#
#   Every lease records its holder's `repo=` and `run=`, so a blocked acquire
#   can ask the GitHub API whether each holder is still in progress and reclaim
#   leases whose runs are POSITIVELY finished — minutes after the death instead
#   of after the TTL. The API path covers FOREIGN leases too: a lease carries
#   its own repo, so an exe-build holder is checked against exe-build's runs
#   (same org, readable by this repo's GITHUB_TOKEN).
#
#   SAFETY INVARIANT — unknown NEVER means reclaim. A slot taken from a LIVE
#   run means two 8-worker jobs on 16 cores: the oversubscription this whole
#   mechanism exists to prevent, and the reason the threshold is "the API said
#   completed", nothing softer. Every failure path — no token, curl error,
#   timeout, 403 rate limit, 404, malformed body, unparseable repo/run — reads
#   as LIVE and leaves the lease to the TTL backstop, which remains in force
#   exactly as before.
#
# gh_run_finished <repo> <run-id>: true ONLY on positive evidence of completion.
# GitHub has exactly one terminal run status ("completed"; success, failure and
# cancelled are all conclusions WITHIN it), so that is the single dead verdict.
gh_run_finished() {
  local repo="$1" run="$2" body status
  body="$(curl -sS --max-time 10 \
    -H "Authorization: Bearer $gh_token" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/$repo/actions/runs/$run" 2>/dev/null)" || return 1
  status="$(printf '%s' "$body" \
    | grep -o '"status"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 \
    | sed 's/.*"\([^"]*\)"$/\1/')"
  [[ "$status" == "completed" ]]
}

# liveness_reclaim: find TTL-fresh leases whose runs are finished and remove
# them. Returns 0 only if it removed something. Phase 1 (the network calls)
# runs OUTSIDE the guard — the mutex protects an O(slots) scan for
# milliseconds and must never wrap a 10s HTTP timeout. Phase 2 re-checks each
# lease's token UNDER the guard before rm, so a lease a concurrent claimant
# rewrote between the phases can never be deleted on stale evidence. On guard
# failure: no-op — same discipline as do_release.
liveness_reclaim() {
  local f repo run tok dead_toks=() removed=0 d
  # No token armed → TTL-only, by design (local dev, offline runs).
  [[ -n "$gh_token" ]] || return 1
  shopt -s nullglob
  # ---- phase 1: read leases, ask GitHub, collect dead TOKENS (not paths).
  for f in "$root"/slot-*.lease; do
    # TTL-expired leases are try_claim's business already; don't spend API
    # calls on them.
    lease_is_live "$f" || continue
    repo="$(sed -n 's/^repo=\([A-Za-z0-9_.-][A-Za-z0-9_.-]*\/[A-Za-z0-9_.-][A-Za-z0-9_.-]*\)$/\1/p' "$f" | head -1)"
    run="$(sed -n 's/^run=\([0-9][0-9]*\)$/\1/p' "$f" | head -1)"
    tok="$(sed -n 's/^token=\(.*\)$/\1/p' "$f" | head -1)"
    # Unparseable holder (older lease format, missing fields, odd repo) =
    # cannot determine liveness = LIVE. Never reclaim on doubt.
    [[ -n "$repo" && -n "$run" && -n "$tok" ]] || continue
    if gh_run_finished "$repo" "$run"; then
      dead_toks+=("$tok")
    fi
  done
  if (( ${#dead_toks[@]} == 0 )); then
    shopt -u nullglob
    return 1
  fi
  # ---- phase 2: under the guard, rm only leases whose token still matches.
  if ! ci_lock "$guard_dir" 6; then
    shopt -u nullglob
    return 1
  fi
  for f in "$root"/slot-*.lease; do
    tok="$(sed -n 's/^token=\(.*\)$/\1/p' "$f" | head -1)"
    for d in "${dead_toks[@]}"; do
      if [[ "$tok" == "$d" ]]; then
        rm -f -- "$f"
        removed=$((removed + 1))
        echo "liveness-reclaimed $f — its run is finished (token $tok); the TTL reaper no longer needs to wait"
        break
      fi
    done
  done
  shopt -u nullglob
  ci_unlock "$guard_dir"
  (( removed > 0 ))
}

# Release this job's slot. Robust to a CANCELLED acquire step.
#
# WHY TOKEN, NOT INDEX (bug da5b4e89):
#   The acquire step records EXE_OS_CI_SLOT_INDEX into $GITHUB_ENV, but GitHub
#   DISCARDS a step's $GITHUB_ENV writes when that step is CANCELLED — they never
#   reach a later step. An auto-cancelled run (new commit on the same PR) can be
#   killed AFTER try_claim wrote its lease to disk but before the acquire step
#   "completed", so this if:always() release then runs with NO index. A release
#   that trusted the index alone would print "nothing to release" and exit green
#   while the lease survived for the full TTL — capacity silently halved. So we
#   reclaim by TOKEN instead: the token is recomputed above from the runner
#   context (present in EVERY step) and is unique per job attempt, so scanning
#   every lease for `token=$token` can only ever match THIS job's own lease —
#   never a slot reclaimed after TTL and reassigned to someone else.
#
# GUARD DISCIPLINE (unchanged): the scan+rm is a critical section serialized
# against try_claim's scan-and-claim. If the guard cannot be taken we must NOT
# proceed and must NOT unlock (ci_unlock is `rm -rf`; unlocking a guard we do not
# own hands the mutex to a second holder and breaks the cap). On guard-acquire
# failure we deliberately LEAK the lease and exit 0 — release runs in an
# if:always() post-step, a non-zero exit there turns a green build red, and the
# lease is a TTL lease that try_claim already reclaims after its declared TTL
# (the same path that recovers a SIGKILLed job that never ran Release at all).
#
# VERIFY, THEN FAIL LOUDLY: after removing our lease under the guard we re-scan
# and confirm it is gone. A release that reports success without checking is the
# exact defect class this bug is about. If a lease bearing our token STILL
# exists after we held the guard and rm'd it, that is a genuine filesystem/logic
# fault (never contention, never normal completion) and DOES fail loudly.
do_release() {
  local f removed=0 still=0
  if ! ci_lock "$guard_dir" 6; then
    echo "WARNING: could not acquire the registry guard; NOT releasing and NOT touching the guard (another job owns it). Leaking this lease; it is reclaimed after the ${ttl_min}m TTL." >&2
    return 0
  fi
  shopt -s nullglob
  for f in "$root"/slot-*.lease; do
    if grep -qxF "token=$token" "$f"; then
      rm -f -- "$f"
      removed=$((removed + 1))
    fi
  done
  # Verify the reclaim actually happened before reporting success.
  for f in "$root"/slot-*.lease; do
    if grep -qxF "token=$token" "$f"; then
      still=$((still + 1))
    fi
  done
  shopt -u nullglob
  ci_unlock "$guard_dir"
  if (( still > 0 )); then
    echo "::error::ci-concurrency-slot: release verified FAILED — ${still} lease(s) bearing this job's token=$token remain under $root after rm while holding the guard" >&2
    return 1
  fi
  if (( removed > 0 )); then
    echo "released ${removed} slot lease(s) held by this job (token=$token)"
  else
    echo "no slot lease held by this job (token=$token); nothing to release"
  fi
  return 0
}

if [[ "${1:-}" == "--release" ]]; then
  do_release
  exit $?
fi

# ACQUIRE mode: block (with jitter) until a slot frees or the wait budget
# runs out — then fail loud. Failing is deliberate backpressure: it converts
# an unbounded queue into a visible, attributable wait.
deadline=$(( $(date +%s) + wait_max_min * 60 ))
announced=0
last_sweep=0
while :; do
  claimed="$(try_claim)" && claimed_ok=1 || claimed_ok=0
  eff="${claimed##* }"
  [[ "$eff" =~ ^[0-9]+$ ]] || eff="$slots"
  if (( claimed_ok )); then
    idx="${claimed%% *}"
    {
      printf 'EXE_OS_CI_SLOT_INDEX=%s\n' "$idx"
      printf 'EXE_OS_CI_SLOT_TOKEN=%s\n' "$token"
    } >> "${GITHUB_ENV:-/dev/null}"
    echo "acquired slot $idx of $eff in $root ($workers_per_slot worker threads x $eff slots <= $(ci_cpu_count) cores; token $token)"
    exit 0
  fi
  now="$(date +%s)"
  # All slots busy. Before idling (and before failing), sweep for holders whose
  # runs are demonstrably finished — the 2026-09-01 freeze was 100% of capacity
  # locked by two dead runs for a full TTL window. Throttled so a fleet of
  # blocked acquires polls the API at ~1 sweep/min each, not per 5s iteration.
  if (( now - last_sweep >= liveness_interval_sec )); then
    last_sweep="$now"
    if liveness_reclaim; then continue; fi
  fi
  if (( now >= deadline )); then
    # One last-chance sweep before failing loud: a holder that died seconds ago
    # should free the slot now, not cost this job its whole wait budget AND the
    # next one's.
    if liveness_reclaim; then continue; fi
    echo "ERROR: all $eff slots busy after ${wait_max_min}m; failing — this is deliberate backpressure, not a build error; the host was saturated by other heavy jobs (in THIS repo or exe-build; the registry is shared: $root)" >&2
    exit 1
  fi
  if (( announced == 0 )); then
    echo "all $eff busy; waiting..."
    announced=1
  fi
  # Jitter so jobs released together don't stampede the registry.
  sleep $(( 5 + RANDOM % 6 ))
done
