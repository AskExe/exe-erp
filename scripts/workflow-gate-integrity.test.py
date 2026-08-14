#!/usr/bin/env python3
"""Structural guards on the CI/release workflows.

Two defects, both of which made a gate LOOK green while evaluating nothing:

  b7496cb1 — a reusable workflow that shares its caller's concurrency group.
             `github.workflow` inside a called workflow resolves to the
             CALLER's name, so an unprefixed group is byte-identical to the
             caller's. With cancel-in-progress that means the callee cancels
             its own caller: every workflow_dispatch of "Release stack image"
             died within seconds and never built anything.

  98fbb1a0 — jobs in the shared suite gated on `github.event_name == 'push' &&
             github.ref == 'refs/heads/main'`. Because the context is the
             caller's, those jobs were SKIPPED on pull_request AND on the
             release gate. GitHub counts a skipped required context as
             satisfied, so they reported as passing without running.

Both assertions are absence assertions. Stdlib only, no network.
"""

from __future__ import annotations

import pathlib
import re
import sys

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"

# The shared suite, and every workflow that calls it.
REUSABLE = "ci-checks.yml"
CALLERS = ("pr-checks.yml", "release-stack-image.yml")

# Jobs in the shared suite that exist to be able to FAIL a PR or a release.
# Anything here must run on every call site.
UNCONDITIONAL_JOBS = ("validate", "secret-scan", "trivy-scan", "docker-build")


def read(name: str) -> str:
    path = WORKFLOWS / name
    if not path.is_file():
        sys.exit(f"missing workflow: {path}")
    return path.read_text(encoding="utf-8")


def concurrency_group(text: str) -> str | None:
    """Top-level (column-0) concurrency group, ignoring job-level ones."""
    match = re.search(r"^concurrency:\n(?:[ \t]+.*\n)*?[ \t]+group:[ \t]*(.+)$",
                      text, re.MULTILINE)
    return match.group(1).strip() if match else None


def job_blocks(text: str) -> dict[str, str]:
    """Map job id -> its raw block, from the `jobs:` mapping."""
    jobs_at = text.find("\njobs:\n")
    if jobs_at == -1:
        return {}
    body = text[jobs_at + len("\njobs:\n"):]
    starts = [(m.start(), m.group(1))
              for m in re.finditer(r"^  ([A-Za-z0-9_-]+):[ \t]*$", body, re.MULTILINE)]
    blocks: dict[str, str] = {}
    for index, (offset, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(body)
        blocks[name] = body[offset:end]
    return blocks


failures: list[str] = []

# ── b7496cb1: the callee's group must not collide with any caller's ──────────
reusable_text = read(REUSABLE)
reusable_group = concurrency_group(reusable_text)

if reusable_group is None:
    print(f"  ok   {REUSABLE} declares no top-level concurrency (cannot collide)")
else:
    if "${{ github.workflow }}" in reusable_group and not reusable_group.startswith("ci-checks-"):
        failures.append(
            f"{REUSABLE} concurrency group {reusable_group!r} interpolates "
            "github.workflow without a distinguishing literal prefix. Inside a "
            "reusable workflow that resolves to the CALLER's name, so the "
            "callee shares the caller's group and cancels it (bug b7496cb1)."
        )
    for caller in CALLERS:
        caller_group = concurrency_group(read(caller))
        if caller_group is not None and caller_group == reusable_group:
            failures.append(
                f"{caller} and {REUSABLE} declare the SAME concurrency group "
                f"{caller_group!r}; the reusable workflow will cancel its "
                "caller (bug b7496cb1)."
            )
    if not any("b7496cb1" in f for f in failures):
        print(f"  ok   {REUSABLE} concurrency group is distinct from every caller's")

# ── 98fbb1a0: gate jobs must not be conditioned away on PRs / releases ───────
blocks = job_blocks(reusable_text)
missing = [name for name in UNCONDITIONAL_JOBS if name not in blocks]
if missing:
    failures.append(f"{REUSABLE} is missing expected gate job(s): {', '.join(missing)}")

for name in UNCONDITIONAL_JOBS:
    block = blocks.get(name)
    if block is None:
        continue
    guard = re.search(r"^    if:[ \t]*(.+)$", block, re.MULTILINE)
    if guard is None:
        print(f"  ok   {name} runs on every call site (no job-level if:)")
        continue
    failures.append(
        f"{REUSABLE} job {name!r} is guarded by `if: {guard.group(1).strip()}`. "
        "The github context in a reusable workflow is the CALLER's, so an "
        "event/ref guard skips this job on pull_request and on the release "
        "gate — and a skipped required context counts as satisfied. A gate "
        "that cannot fail is not a gate (bug 98fbb1a0)."
    )

print()
if failures:
    for failure in failures:
        print(f"  FAIL {failure}")
    print(f"\nworkflow gate integrity: {len(failures)} FAILURE(S)")
    sys.exit(1)

print("workflow gate integrity: PASS")
