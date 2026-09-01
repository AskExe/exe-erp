#!/usr/bin/env python3
"""Structural timeout guards on the CI/release workflows (bug 3136f36b).

`docker build --check` performs a REGISTRY metadata fetch with no built-in
timeout. It was observed stalling indefinitely at "#2 [internal] load metadata
for docker.io/library/python:3.14-slim-bookworm", holding the repo's only
mac-fast runner for 2h19m (run 33319026352) while every queued job starved —
a hung job looks exactly like a slow job until the runner pool is empty.

The fix bounded the step (`timeout-minutes: 5` + an in-script 120s watchdog
that degrades to a Dockerfile presence check). This script keeps it bounded:

  1. The `docker build --check` step in ci-checks.yml must keep a step-level
     `timeout-minutes` — removing the bounding reintroduces the P0.

  2. Every job that runs on a SELF-HOSTED runner (any workflow) must declare a
     job-level `timeout-minutes`. GitHub-hosted jobs get the platform's 360-min
     default; self-hosted ones get nothing, and a wedged job takes OUR machine
     out of rotation for as long as it likes.

Modeled on workflow-gate-integrity.test.py. Stdlib only, no network.
"""

from __future__ import annotations

import pathlib
import re
import sys

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
CI_CHECKS = "ci-checks.yml"

failures: list[str] = []


def read(name: str) -> str:
    path = WORKFLOWS / name
    if not path.is_file():
        sys.exit(f"missing workflow: {path}")
    return path.read_text(encoding="utf-8")


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


def job_level(block: str, key: str) -> str | None:
    """A job's own mapping key: exactly 4-space indent (step keys sit at 8)."""
    match = re.search(rf"^    {key}:[ \t]*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None


def docker_check_step_has_timeout(text: str) -> bool:
    """The step that runs `docker build --check` carries its own bound."""
    lines = text.splitlines()
    target = next((i for i, line in enumerate(lines)
                   if "docker build --check" in line and "run:" not in line), None)
    if target is None:
        return False
    start = max((i for i in range(target) if re.match(r"^      - ", lines[i])), default=None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^      - ", lines[i])),
               len(lines))
    return any(re.match(r"^        timeout-minutes:", line) for line in lines[start:end])


# ── 3136f36b, part 1: the docker build --check step stays bounded ───────────
ci_text = read(CI_CHECKS)
if docker_check_step_has_timeout(ci_text):
    print(f"  ok   {CI_CHECKS}: 'Validate Dockerfile is parseable' step is timeout-bounded")
else:
    failures.append(
        f"{CI_CHECKS}: the `docker build --check` step has no step-level timeout-minutes. "
        "It performs an unbounded registry metadata fetch; when it stalls it holds the "
        "only mac-fast runner indefinitely (bug 3136f36b, run 33319026352)."
    )

# ── 3136f36b, part 2: every self-hosted job declares a job-level timeout ────
for path in sorted(WORKFLOWS.glob("*.yml")):
    for job_id, block in job_blocks(path.read_text(encoding="utf-8")).items():
        runs_on = job_level(block, "runs-on")
        if runs_on is None or "self-hosted" not in runs_on:
            continue  # reusable-workflow caller or GitHub-hosted: platform caps it
        if job_level(block, "timeout-minutes") is None:
            failures.append(
                f"{path.name}: job '{job_id}' runs on self-hosted runners "
                f"({runs_on}) with no job-level timeout-minutes — a wedged step "
                "takes the machine out of rotation for as long as it likes."
            )
        else:
            print(f"  ok   {path.name}: '{job_id}' self-hosted + timeout-bounded")

if failures:
    print()
    for failure in failures:
        print(f"  FAIL {failure}")
    sys.exit(1)
print()
print("workflow timeout guard: all clear")
