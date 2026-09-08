#!/usr/bin/env python3
"""Structural guard: the ERP release lane cannot silently emulate (bug 20973fdd).

Run 34152651936 attempt 4 proved that labels alone do not enforce hardware:
`runs-on: [self-hosted, linux, x64]` scheduled the release build onto
mac-vm-erp-1 — a genuinely aarch64 Lima VM that self-reports a custom `X64`
label GitHub does not verify. The job then built linux/amd64 through bundled
QEMU, which SIGSEGV'd building Frappe:

  qemu: uncaught target signal 11 (Segmentation fault) - core dumped
  Command 'uv venv env --seed --python python3.14' died with SIGSEGV.

CI stayed green because ci-checks.yml's Production Docker build job builds
the runner's NATIVE architecture only (no `platforms:`, `driver: docker`) —
a different driver testing a different question. Green CI on that job never
predicted whether the release job's cross-arch build would work.

This test asserts the durable half of the fix stays in place. It does NOT
re-litigate the routing label (self-hosted-runner labels/registration is not
something a workflow file, or this test, can prove correct) — it asserts the
job cannot silently emulate even if scheduling ever goes wrong again:

  1. `release-image`'s `runs-on` carries a label beyond the bare
     [self-hosted, linux, x64] set — that bare set alone is exactly what let
     a mislabeled ARM runner in (bug 20973fdd finding #1).
  2. `release-image` contains a step that runs `uname -m` and fails when it
     is not x86_64, and it is the job's FIRST step — before checkout, before
     any gate — so a wrong-arch scheduling is refused before spending any
     more of the job's time (the durable guarantee; a label is a request).
  3. `docker/setup-qemu-action` does not appear in this job. Its bundled
     QEMU is the emulator that actually SIGSEGV'd.
  4. The build step's `platforms:` does not silently include a second,
     emulated architecture (i.e. does not contain a comma-separated list).
  5. `docker/setup-buildx-action` for this job pins `driver: docker` — the
     dockerd-integrated builder, which has no bundled QEMU to fall back to.
     The action's own DEFAULT (docker-container driver) installs one.

Stdlib only, no network.
"""

from __future__ import annotations

import pathlib
import re
import sys

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
RELEASE_WORKFLOW = "release-stack-image.yml"
JOB_ID = "release-image"

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


def step_names_in_order(block: str) -> list[tuple[int, str]]:
    """(offset, step-name-or-uses) for every top-level step (8-space `- `)."""
    steps = []
    for m in re.finditer(r"^      - (?:name:\s*(.+)|uses:\s*(.+))$", block, re.MULTILINE):
        steps.append((m.start(), (m.group(1) or m.group(2) or "").strip()))
    return steps


text = read(RELEASE_WORKFLOW)
blocks = job_blocks(text)
block = blocks.get(JOB_ID)
if block is None:
    sys.exit(f"{RELEASE_WORKFLOW}: expected job '{JOB_ID}' not found")

# ── 1. runs-on must not be the bare, non-distinguishing label set ──────────
runs_on_match = re.search(r"^    runs-on:[ \t]*(.+)$", block, re.MULTILINE)
runs_on = runs_on_match.group(1).strip() if runs_on_match else None
bare_generic = {"[self-hosted, linux, x64]", "[self-hosted, x64, linux]"}
if runs_on is None:
    failures.append(f"{RELEASE_WORKFLOW}: job '{JOB_ID}' has no runs-on")
elif runs_on in bare_generic:
    failures.append(
        f"{RELEASE_WORKFLOW}: job '{JOB_ID}' runs-on is the bare label set {runs_on!r}. "
        "This is exactly what let mac-vm-erp-1 — a genuinely aarch64 host with a "
        "self-reported, unverified `X64` label — schedule this job (bug 20973fdd, "
        "run 34152651936 attempt 4). Add a label that identifies the genuine "
        "native-x64 lane unambiguously."
    )
else:
    print(f"  ok   {JOB_ID}: runs-on ({runs_on}) is not the bare generic label set")

# ── 2. a uname -m assertion exists and is the job's FIRST step ─────────────
steps = step_names_in_order(block)
uname_step_offset = None
for m in re.finditer(r'uname -m', block):
    uname_step_offset = m.start()
    break

if uname_step_offset is None:
    failures.append(
        f"{RELEASE_WORKFLOW}: job '{JOB_ID}' has no step asserting `uname -m`. "
        "A label is a request, not a guarantee (bug 20973fdd) — without an "
        "in-job architecture assertion, a mislabeled or relabeled runner "
        "silently falls through to emulation again."
    )
else:
    if not steps:
        failures.append(f"{RELEASE_WORKFLOW}: job '{JOB_ID}' has no steps to check ordering against")
    else:
        first_step_offset = steps[0][0]
        # The uname check must live inside the FIRST step's run: block, i.e.
        # start at-or-after the first step marker and before the second step.
        second_step_offset = steps[1][0] if len(steps) > 1 else len(block)
        if not (first_step_offset <= uname_step_offset < second_step_offset):
            failures.append(
                f"{RELEASE_WORKFLOW}: job '{JOB_ID}' checks `uname -m` but not as "
                f"its FIRST step (first step is {steps[0][1]!r}). This assertion "
                "must run before checkout and before every gate, so a wrong-arch "
                "scheduling is refused before spending any more of the job's time "
                "(bug 20973fdd)."
            )
        else:
            print(f"  ok   {JOB_ID}: `uname -m` assertion is the job's first step")

    if "x86_64" not in block[max(0, uname_step_offset - 200):uname_step_offset + 400]:
        failures.append(
            f"{RELEASE_WORKFLOW}: job '{JOB_ID}' checks `uname -m` but never "
            "compares it against x86_64 nearby — cannot confirm it actually fails "
            "closed on the wrong architecture."
        )

# ── 3. no bundled-QEMU setup in this job ────────────────────────────────────
if re.search(r"^\s*uses:\s*docker/setup-qemu-action", block, re.MULTILINE):
    failures.append(
        f"{RELEASE_WORKFLOW}: job '{JOB_ID}' still uses docker/setup-qemu-action. "
        "Its bundled QEMU is what SIGSEGV'd building Frappe (bug 20973fdd) — "
        "this release lane must not install an emulator it should never use."
    )
else:
    print(f"  ok   {JOB_ID}: docker/setup-qemu-action is not present")

# ── 4. build step platforms: does not silently add a second architecture ───
# Scope to the build-push-action step specifically — docker/setup-qemu-action
# also has a `platforms:` key (its emulated targets), which is a different
# question and must not be conflated with what actually gets built.
build_step_match = re.search(
    r"uses:\s*docker/build-push-action[^\n]*\n((?:\s{8,}.*\n)*)", block
)
build_step_body = build_step_match.group(1) if build_step_match else ""
platforms_match = re.search(r"^\s+platforms:\s*(\S.*)$", build_step_body, re.MULTILINE)
if build_step_match is None:
    failures.append(f"{RELEASE_WORKFLOW}: job '{JOB_ID}' has no recognizable docker/build-push-action step")
elif platforms_match is None:
    failures.append(f"{RELEASE_WORKFLOW}: job '{JOB_ID}' build step has no `platforms:`")
elif "," in platforms_match.group(1):
    failures.append(
        f"{RELEASE_WORKFLOW}: job '{JOB_ID}' build step requests multiple platforms "
        f"({platforms_match.group(1)!r}) with no QEMU setup in the job — on any "
        "hardware we own this means at least one target is impossible to build "
        "(bug 20973fdd/b56fa021). A deliberate multi-arch build needs its own "
        "explicit, reviewed emulation story, not this default."
    )
else:
    print(f"  ok   {JOB_ID}: build step targets a single platform ({platforms_match.group(1)})")

# ── 5. buildx is set up with driver: docker (no bundled-QEMU fallback) ─────
buildx_match = re.search(
    r"- name:.*Docker Buildx.*\n(?:.*\n)*?\s+uses:\s*docker/setup-buildx-action[^\n]*\n((?:\s{8,}.*\n)*)",
    block,
)
if buildx_match is None:
    failures.append(f"{RELEASE_WORKFLOW}: job '{JOB_ID}' has no recognizable 'Set up Docker Buildx' step")
elif "driver: docker" not in buildx_match.group(1):
    failures.append(
        f"{RELEASE_WORKFLOW}: job '{JOB_ID}''s Docker Buildx setup does not pin "
        "`driver: docker`. setup-buildx-action's default is the docker-container "
        "driver, which installs its OWN bundled QEMU the moment a cross-arch "
        "platform is requested — silently, with no visible signal it switched "
        "from building to emulating (bug 20973fdd)."
    )
else:
    print(f"  ok   {JOB_ID}: Docker Buildx is pinned to driver: docker (no bundled-QEMU fallback)")

print()
if failures:
    for failure in failures:
        print(f"  FAIL {failure}")
    print(f"\nrelease native-arch guard: {len(failures)} FAILURE(S)")
    sys.exit(1)

print("release native-arch guard: PASS")
