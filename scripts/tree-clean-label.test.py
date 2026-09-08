#!/usr/bin/env python3
"""Workflow-contract test: org.exe.tree_clean must be MEASURED, not asserted.

Bug b46bc7fd — release-stack-image.yml's `docker/build-push-action` labels
block stamped `org.exe.tree_clean=true` as a literal. A label that cannot
fail certifies nothing: every published image asserted a clean-tree
provenance claim that was never evaluated, indistinguishable from one that
was earned by an actual `git status` check.

This test fails RED against that literal, and PASSES once the label is wired
to a computed step output (the pattern exe-os's scripts/bake-version.sh
uses: `git status --porcelain`, fail-closed on a dirty tree or an
unreadable git status). It also asserts a dedicated cleanliness-measuring
step exists and that it fails the job when the tree is dirty, so the check
cannot be satisfied by wiring the label to some OTHER, unrelated computed
value while quietly never actually gating the release.

Stdlib only, no network.
"""

from __future__ import annotations

import pathlib
import re
import sys

WORKFLOWS = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"
TARGET = "release-stack-image.yml"


def read(name: str) -> str:
    path = WORKFLOWS / name
    if not path.is_file():
        sys.exit(f"missing workflow: {path}")
    return path.read_text(encoding="utf-8")


failures: list[str] = []

text = read(TARGET)

# ── Find the org.exe.tree_clean assignment inside a `labels: |` block ───────
label_match = re.search(r"^[ \t]*org\.exe\.tree_clean=(.+?)[ \t]*$", text, re.MULTILINE)
if label_match is None:
    failures.append(f"{TARGET} no longer sets org.exe.tree_clean at all -- "
                     "the provenance label was removed instead of fixed.")
else:
    value = label_match.group(1)

    # A bare `true`/`false`/`True`/`1` etc. (no `${{ ... }}` expression) is a
    # LITERAL: it cannot vary with the actual state of the working tree, so it
    # cannot fail, so it certifies nothing (bug b46bc7fd).
    if not (value.startswith("${{") and value.endswith("}}")):
        failures.append(
            f"{TARGET} sets org.exe.tree_clean={value!r}, a LITERAL rather than "
            "a computed expression. A hardcoded value can never disagree with "
            "the working tree, so the label certifies a property nobody "
            "checked (bug b46bc7fd). It must read a step output derived from "
            "`git status --porcelain`, e.g. "
            "`${{ steps.tree_clean.outputs.clean }}`."
        )
    else:
        expr = value[3:-2].strip()
        # Must reference a step OUTPUT (steps.<id>.outputs.<name>), not a
        # constant expression like `${{ true }}` or an unrelated context value
        # that happens to also be an expression.
        if not re.fullmatch(r"steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_-]+", expr):
            failures.append(
                f"{TARGET} sets org.exe.tree_clean to the expression "
                f"'${{{{ {expr} }}}}', which is not a `steps.<id>.outputs.<name>` "
                "reference. Wrapping a constant in `${{ }}` (e.g. `${{ true }}`) "
                "is still a literal in disguise -- it must read the OUTPUT of a "
                "step that actually measured the tree (bug b46bc7fd)."
            )
        else:
            step_id = expr.split(".")[1]
            output_name = expr.split(".")[-1]

            # ── The referenced step must exist, run `git status --porcelain`,
            #    and fail the job (non-zero exit) on a dirty or unreadable tree.
            #
            # Line-scan rather than one big regex: a workflow this size run
            # through nested `(?:[ \t]+.*\n)*?` groups is catastrophic-
            # backtracking territory. Find `id: <step_id>`, then walk forward
            # to the step's `run: |` block and collect its body by indentation
            # (stop at the first line that dedents back to the step's own
            # `- name:` / `- uses:` level or shallower).
            lines = text.split("\n")
            id_line_re = re.compile(rf"^[ \t]+id:\s*{re.escape(step_id)}\s*$")
            id_idx = next((i for i, line in enumerate(lines) if id_line_re.match(line)), None)

            body = None
            if id_idx is not None:
                run_re = re.compile(r"^([ \t]+)run:\s*\|\s*$")
                for i in range(id_idx, min(id_idx + 30, len(lines))):
                    run_match = run_re.match(lines[i])
                    if run_match:
                        run_indent = len(run_match.group(1))
                        body_lines = []
                        for j in range(i + 1, len(lines)):
                            line = lines[j]
                            if line.strip() == "":
                                body_lines.append(line)
                                continue
                            indent = len(line) - len(line.lstrip(" \t"))
                            if indent <= run_indent:
                                break
                            body_lines.append(line)
                        body = "\n".join(body_lines)
                        break

            if body is None:
                failures.append(
                    f"{TARGET} references step id {step_id!r} for "
                    "org.exe.tree_clean, but no step with that id and a "
                    "`run: |` block was found."
                )
            else:
                if "git status --porcelain" not in body:
                    failures.append(
                        f"{TARGET} step {step_id!r} (feeding org.exe.tree_clean) "
                        "does not call `git status --porcelain` -- it cannot be "
                        "measuring the actual working tree the way exe-os's "
                        "scripts/bake-version.sh does."
                    )
                if f'>> "$GITHUB_OUTPUT"' not in body and f'>> \"$GITHUB_OUTPUT\"' not in body:
                    failures.append(
                        f"{TARGET} step {step_id!r} never writes to "
                        "$GITHUB_OUTPUT, so its measurement can't be the "
                        f"source of steps.{step_id}.outputs.{output_name}."
                    )
                if "exit 1" not in body:
                    failures.append(
                        f"{TARGET} step {step_id!r} has no `exit 1` path -- a "
                        "dirty (or unreadable) tree must FAIL the release lane, "
                        "not just be recorded in a label nobody enforces "
                        "(bug b46bc7fd)."
                    )
                # set -e (bare or in `set -euo pipefail`) is what makes that
                # `exit 1` actually abort the step instead of a caller ignoring
                # a non-zero $? from a later line.
                if not re.search(r"set\s+-[a-z]*e[a-z]*\b", body):
                    failures.append(
                        f"{TARGET} step {step_id!r} does not `set -e` (or "
                        "equivalent), so its exit-1 guard for a dirty tree is "
                        "not fail-closed by construction."
                    )

print()
if failures:
    for failure in failures:
        print(f"  FAIL {failure}")
    print(f"\ntree-clean label contract: {len(failures)} FAILURE(S)")
    sys.exit(1)

print("tree-clean label contract: PASS -- org.exe.tree_clean is measured, not asserted")
