#!/usr/bin/env python3
"""
Stack manifest pin gate for exe-erp.

THE INVARIANT: a published exe-erp release manifest must reference its image by
IMMUTABLE DIGEST. A bare tag is mutable — ghcr.io/askexe/exe-erp:v0.2.0-final8
was overwritten after release, so the manifest's tag half and digest half named
different content (see 5659b47). Anything pulling that tag silently got
unreviewed bytes. The same class of failure poisoned exe-gateway's v0.9.28.

WHY THIS GATE HAS TWO PHASES (and is not simply "always require @sha256")
------------------------------------------------------------------------
A digest is an OUTPUT of a build; it cannot be an INPUT to the build that
produces it. exe-crm learned this the expensive way (bug cc8feeee): its release
gate demanded a digest in stack.release.json *before* the build, with the
documented remedy "commit the digest, then move the tag". That remedy cannot
converge — the build stamps the commit sha into the image config, so the re-pin
commit changes github.sha, which changes the labels, which changes the config,
which changes the digest. No exe-crm release went green for two months.

exe-erp would fail even earlier than exe-crm did. Its release workflow feeds
`components.erp` to docker/build-push-action as a PUSH TAG, and `tag@sha256:…`
is not a pushable reference — so a blanket pre-build digest requirement does not
merely fail the gate, it breaks the build outright.

So the digest requirement is enforced where it is satisfiable AND where it
actually protects consumers — after the push, against the digest the build just
produced:

  --phase pre    what a human can legally commit. Every component must name this
                 repo at this release's tag, `:v<version>`, optionally already
                 carrying a digest (a re-pin of an existing release, or the
                 pinned manifest a previous run wrote back). All components must
                 be byte-identical, because exe-erp runs four roles (erp,
                 websocket, queue, scheduler) off ONE image.

  --phase post   fail-closed digest enforcement. EVERY component must carry
                 `@sha256:<64 hex>` and every one must equal the digest actually
                 built and pushed by this run. This is the phase that refuses a
                 tag-only manifest.

Negative cases are exercised by `--self-test` (run in CI), so the gate is proven
to refuse rather than assumed to. A guard nobody has watched refuse is not a
guard.

Stdlib only — no pip install in CI.
"""

import argparse
import json
import re
import sys

REPO = "ghcr.io/askexe/exe-erp"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# repo[:tag][@sha256:<64hex>]
REF_RE = re.compile(
    r"^(?P<repo>[a-z0-9._/-]+?)"
    r"(?::(?P<tag>[A-Za-z0-9._-]+))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?$"
)
REQUIRED_COMPONENTS = ("erp", "erp-websocket", "erp-queue", "erp-scheduler")


def parse_ref(ref):
    """Split an image reference into (repo, tag, digest). None on malformed."""
    if not isinstance(ref, str) or not ref.strip():
        return None
    m = REF_RE.match(ref.strip())
    if not m:
        return None
    return m.group("repo"), m.group("tag"), m.group("digest")


def check_pre(components, version, repo=REPO):
    """Pre-build: committable shape. Returns a list of (component, reason)."""
    failures = []
    expected_tag = f"v{version}"

    for name in REQUIRED_COMPONENTS:
        if name not in components:
            failures.append((name, f"missing from components (expected {len(REQUIRED_COMPONENTS)} roles off one image)"))

    for name, ref in sorted(components.items()):
        parsed = parse_ref(ref)
        if parsed is None:
            failures.append((name, f"malformed image reference: {ref!r}"))
            continue
        got_repo, tag, digest = parsed
        if got_repo != repo:
            failures.append((name, f"repo is {got_repo!r}, expected {repo!r}"))
        if tag is None:
            failures.append((name, f"no tag in {ref!r} — a release ref must carry :v{version}"))
        elif tag != expected_tag:
            hint = ""
            if digest is not None:
                # exe-os stack-release.ts rebaseImageTag() returns digest-pinned
                # refs UNCHANGED, so a version bump leaves the OLD tag+digest
                # behind. That stale pin describes the PREVIOUS release.
                hint = (" — this ref still carries the PREVIOUS release's digest pin. "
                        "A version bump does not rebase a digest-pinned ref, so strip "
                        f"the '@{digest}' suffix and let this release's build re-pin it.")
            failures.append((name, f"tag is {tag!r}, expected {expected_tag!r} "
                                   f"(manifest version {version}){hint}"))
        if digest is not None and not DIGEST_RE.match(digest):
            failures.append((name, f"malformed digest {digest!r}"))

    # All four roles run the SAME image; divergence means someone edited one.
    distinct = {components[n] for n in REQUIRED_COMPONENTS if n in components}
    if len(distinct) > 1:
        failures.append(("*", "components are not identical — all four roles must run ONE image: "
                              + ", ".join(sorted(distinct))))
    return failures


def check_post(components, version, built_digest, repo=REPO):
    """Post-build: fail-closed digest enforcement. Returns [(component, reason)]."""
    failures = []

    if not DIGEST_RE.match(built_digest or ""):
        return [("*", f"built digest {built_digest!r} is not a sha256:<64 hex> value — "
                      "refusing to validate against an unusable digest")]

    # Shape must still hold after the rewrite.
    failures.extend(check_pre(components, version, repo=repo))

    for name, ref in sorted(components.items()):
        parsed = parse_ref(ref)
        if parsed is None:
            continue  # already reported by check_pre
        _, _, digest = parsed
        if digest is None:
            failures.append((name, f"NOT DIGEST-PINNED: {ref!r} names a MUTABLE TAG. "
                                   "A tag can be overwritten after release, so the manifest would no "
                                   "longer describe the bytes it claims. Expected @" + built_digest))
        elif digest != built_digest:
            failures.append((name, f"digest {digest} does not match the image this run built "
                                   f"({built_digest})"))
    return failures


def load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def report(failures, phase, path):
    if not failures:
        return 0
    print(f"::error::stack pin gate FAILED ({phase}) for {path}", file=sys.stderr)
    print("", file=sys.stderr)
    for name, reason in failures:
        print(f"  [{name}] {reason}", file=sys.stderr)
    print("", file=sys.stderr)
    if phase == "post":
        print("::error::Refusing to publish a release whose manifest is not pinned to the "
              "digest this run built. Every component must end with @sha256:<64 hex>.",
              file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Self-tests: prove the gate REFUSES. Each case asserts a specific failure key.
# ---------------------------------------------------------------------------
GOOD_DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def _components(ref):
    return {n: ref for n in REQUIRED_COMPONENTS}


def self_test():
    cases = []

    def case(name, failures, want_fail):
        ok = bool(failures) == want_fail
        cases.append((name, ok, failures))
        return ok

    v = "0.3.0"
    tag_only = _components(f"{REPO}:v{v}")
    pinned = _components(f"{REPO}:v{v}@{GOOD_DIGEST}")

    # --- post phase must REFUSE ---
    case("post: tag-only manifest is refused",
         check_post(tag_only, v, GOOD_DIGEST), True)
    case("post: digest that is not the built digest is refused",
         check_post(_components(f"{REPO}:v{v}@{OTHER_DIGEST}"), v, GOOD_DIGEST), True)
    mixed = dict(pinned)
    mixed["erp-queue"] = f"{REPO}:v{v}"
    case("post: ONE tag-only component among four is refused",
         check_post(mixed, v, GOOD_DIGEST), True)
    case("post: missing component is refused",
         check_post({k: val for k, val in pinned.items() if k != "erp-scheduler"}, v, GOOD_DIGEST), True)
    case("post: malformed built digest is refused",
         check_post(pinned, v, "not-a-digest"), True)
    case("post: empty built digest is refused",
         check_post(pinned, v, ""), True)
    case("post: truncated digest in manifest is refused",
         check_post(_components(f"{REPO}:v{v}@sha256:abc123"), v, GOOD_DIGEST), True)
    case("post: foreign registry is refused",
         check_post(_components(f"ghcr.io/evil/exe-erp:v{v}@{GOOD_DIGEST}"), v, GOOD_DIGEST), True)
    case("post: wrong version tag is refused",
         check_post(_components(f"{REPO}:v9.9.9@{GOOD_DIGEST}"), v, GOOD_DIGEST), True)
    case("post: components pointing at different images are refused",
         check_post({**pinned, "erp-websocket": f"{REPO}:v{v}@{OTHER_DIGEST}"}, v, GOOD_DIGEST), True)
    case("post: empty components map is refused",
         check_post({}, v, GOOD_DIGEST), True)

    # --- pre phase must REFUSE ---
    case("pre: untagged bare repo is refused",
         check_pre(_components(REPO), v), True)
    case("pre: wrong version tag is refused",
         check_pre(_components(f"{REPO}:v0.2.0"), v), True)
    case("pre: foreign registry is refused",
         check_pre(_components(f"ghcr.io/evil/exe-erp:v{v}"), v), True)
    # exe-os rebaseImageTag() leaves digest-pinned refs untouched on a version
    # bump, so this is the exact shape a bumped-but-unstripped manifest takes.
    case("pre: stale pin from the PREVIOUS release is refused after a bump",
         check_pre(_components(f"{REPO}:v0.2.0@{GOOD_DIGEST}"), v), True)

    # --- must PASS (proves the gate is not simply always-red) ---
    case("post: correctly pinned manifest PASSES",
         check_post(pinned, v, GOOD_DIGEST), False)
    case("pre: tag-only manifest PASSES pre-build (digest not knowable yet)",
         check_pre(tag_only, v), False)
    case("pre: already-pinned manifest PASSES pre-build (re-pin / rebuild)",
         check_pre(pinned, v), False)

    width = max(len(n) for n, _, _ in cases)
    failed = 0
    refusals = 0
    for name, ok, failures in cases:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        if failures:
            refusals += 1
        print(f"  [{mark}] {name.ljust(width)}  ({len(failures)} finding(s))")
    print()
    if failed:
        print(f"::error::{failed}/{len(cases)} self-test case(s) FAILED — "
              "the pin gate does not behave as specified.", file=sys.stderr)
        return 1
    print(f"All {len(cases)} self-test cases behaved as specified "
          f"({refusals} refusals, {len(cases) - refusals} acceptances).")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="stack.release.json")
    ap.add_argument("--phase", choices=("pre", "post"))
    ap.add_argument("--built-digest", default="",
                    help="sha256:<64 hex> produced by docker/build-push-action (post phase)")
    ap.add_argument("--self-test", action="store_true",
                    help="run negative-case self-tests and exit")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.phase:
        ap.error("--phase is required unless --self-test is given")

    rel = load_manifest(args.manifest)
    version = rel.get("version")
    if not version:
        print("::error::stack.release.json has no version", file=sys.stderr)
        return 1
    components = rel.get("components") or {}

    if args.phase == "pre":
        failures = check_pre(components, version)
    else:
        failures = check_post(components, version, args.built_digest)

    rc = report(failures, args.phase, args.manifest)
    if rc == 0:
        print(f"stack pin gate OK ({args.phase}) — {args.manifest} @ v{version}")
        for name in sorted(components):
            print(f"  {name}: {components[name]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
