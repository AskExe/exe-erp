#!/usr/bin/env bash
#
# ci-workflow-slot-guard.test.sh — regression guard for bug 4187ed64
# (no cross-repo CI concurrency limit).
#
# Mirrors AskExe/exe-os tests/ci/ci-workflow-slot-guard-test.sh and
# AskExe/exe-wiki scripts/ci-workflow-slot-guard-test.sh. Asserts that
# .github/workflows/ci-checks.yml's mac-fast "validate" job still
# acquires/releases from the host-wide shared slot registry, independent of
# scripts/ci-concurrency-slot.sh's own behavior — a workflow can be
# restructured to stop calling a script while the script itself keeps passing
# its own tests (see exe-crm bug d859577c). Pure text assertions, no
# yq/python dependency.

set -euo pipefail

WORKFLOW="${CI_WORKFLOW_GUARD_TEST_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.github/workflows/ci-checks.yml}"

FAILURES=0
TOTAL=0
pass() { TOTAL=$((TOTAL + 1)); echo "PASS: $1"; }
fail() { TOTAL=$((TOTAL + 1)); FAILURES=$((FAILURES + 1)); echo "FAIL: $1"; }

[[ -f "$WORKFLOW" ]] || { echo "FAIL: workflow file not found: $WORKFLOW"; exit 1; }

if grep -q 'run: bash scripts/ci-concurrency-slot.sh$' "$WORKFLOW"; then
  pass "validate job acquires from scripts/ci-concurrency-slot.sh"
else
  fail "no step runs 'bash scripts/ci-concurrency-slot.sh' (acquire) in $WORKFLOW"
fi

if grep -q 'run: bash scripts/ci-concurrency-slot.sh --release' "$WORKFLOW"; then
  pass "validate job releases via 'ci-concurrency-slot.sh --release'"
else
  fail "no step runs 'bash scripts/ci-concurrency-slot.sh --release' in $WORKFLOW"
fi

if grep -B10 -- '--release' "$WORKFLOW" | grep -q 'if: always()'; then
  pass "release step is guarded with if: always()"
else
  fail "release step is missing 'if: always()' — a killed job would leak its lease for the full TTL"
fi

if grep -qE '^\s*CI_HOST_RESERVED_CORES:\s*"' "$WORKFLOW"; then
  pass "validate job sets CI_HOST_RESERVED_CORES (accounts for permanent Docker Desktop + ci-linux VM overhead)"
else
  fail "CI_HOST_RESERVED_CORES: \"...\" env assignment not found in $WORKFLOW"
fi

if grep -qE '^\s*CI_HEAVY_SLOT_WORKERS:\s*"' "$WORKFLOW"; then
  pass "validate job declares CI_HEAVY_SLOT_WORKERS"
else
  fail "CI_HEAVY_SLOT_WORKERS: \"...\" env assignment not found in $WORKFLOW"
fi

echo
echo "$((TOTAL - FAILURES))/$TOTAL passed"
(( FAILURES == 0 )) || exit 1
