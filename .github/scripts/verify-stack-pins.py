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

  --phase compose  reconcile docker-compose.yml against stack.release.json.
                 EVERY `ghcr.io/askexe/exe-erp` image the deploy manifest pins
                 must be BYTE-IDENTICAL to the release manifest's `erp` ref.
                 This closes the gap behind the ERP login outage (bugs
                 129a0495 / 96e6b8b6): the release manifest was re-pinned to the
                 fixed image while docker-compose.yml — the file that runs on the
                 host — kept the pre-fix pin, and NOTHING reconciled the two, so
                 source/test/manifest were all green while production served the
                 pre-fix bytes. Fails closed: a compose file that names no ERP
                 image (moved file or broken extractor) is REFUSED, not passed.

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


# ---------------------------------------------------------------------------
# docker-compose.yml <-> stack.release.json reconciliation
#
# THE GAP THIS CLOSES (bugs 129a0495 / 96e6b8b6): the release manifest and the
# deploy manifest independently declare which image ships, and were allowed to
# disagree silently. On the v0.3.0 cut, stack.release.json was re-pinned to the
# fixed image while docker-compose.yml — the file that actually runs on the host
# — was left pinning v0.2.0-final8, an image built BEFORE the SSO cookie fix. The
# source, its regression test, and the manifest were all green; production served
# the pre-fix bytes for the entire window. Every gate we had inspected the half
# that was correct. Nothing reconciled compose against the manifest, so this
# script — the dedicated pin gate — was blind to the exact file that shipped the
# outage. This phase makes the pin gate read docker-compose.yml too and fail
# closed on any drift, so the one guarding unittest is no longer the only thing
# standing between a compose-pin regression and production.
#
# This mirrors, byte for byte, the invariant asserted by the unittest in
# apps/erpnext/erpnext/exe_auth/test_sso_cookie_contract.py — same regex, same
# "compose ref must equal the released ref" rule — but runs it as a required
# stdlib gate step in CI and at release, independent of the Frappe test runner.
# ---------------------------------------------------------------------------

# Every `image:` mapping value in the compose file. Group `val` is the raw
# value, which may be quoted or carry a trailing `# comment`; those forms are
# normalized in _clean_image_value below. Matching ALL image lines (not only
# ones that already look like the ERP repo) is deliberate: a drifted service
# whose ref is quoted or commented must still be SEEN, or the gate would pass
# over the exact regression it exists to catch.
COMPOSE_ANY_IMAGE_RE = re.compile(r"^[ \t]*image:[ \t]*(?P<val>\S.*?)[ \t]*$", re.MULTILINE)

# The exact ERP repo, boundary-anchored: `exe-erp` must be followed by `:tag`,
# `@digest`, or end-of-ref. This deliberately does NOT match a hypothetical
# sidecar repo like `ghcr.io/askexe/exe-erp-init` (which would be a different
# image, not a drifted pin) — anchoring here avoids a false-fail on such a ref.
_ERP_REPO_RE = re.compile(r"^ghcr\.io/askexe/exe-erp(?=[:@]|$)")


def _clean_image_value(raw):
    """Normalize a raw compose `image:` value to the bare image reference.

    Strips YAML quoting and any trailing inline `# comment`. Image references
    never contain whitespace or `#`, so the ref is the first quoted span or the
    first whitespace-delimited token.
    """
    v = raw.strip()
    if v[:1] in ("\"", "'"):
        q = v[0]
        end = v.find(q, 1)
        return v[1:end] if end != -1 else v[1:].strip()
    return v.split()[0] if v.split() else ""


def extract_compose_refs(compose_text):
    """Every exe-erp image ref the deploy manifest pins.

    Sees quoted and inline-commented forms, not just bare unquoted refs, so a
    drifted service cannot hide behind formatting. Duplicates are KEPT: the
    deploy manifest declares six services (configurator, erp, websocket, queue,
    scheduler, nginx) off ONE image; the caller compares the DISTINCT set, and a
    single drifted service yields a distinct ref that survives that set.
    """
    refs = []
    for m in COMPOSE_ANY_IMAGE_RE.finditer(compose_text):
        val = _clean_image_value(m.group("val"))
        if _ERP_REPO_RE.match(val):
            refs.append(val)
    return refs


def check_compose(components, version, compose_text, repo=REPO):
    """Reconcile docker-compose.yml against the released manifest refs.

    Returns [(name, reason)] failures; empty == pass. Fails CLOSED: a malformed
    manifest, a missing 'erp' component, or an extractor that finds nothing can
    never produce a vacuous pass.
    """
    failures = []

    # 1. The manifest must itself be well-formed before it can be a reconcile
    #    target — otherwise we would "agree" with garbage. Surface every
    #    pre-phase manifest failure, prefixed so its source is unambiguous.
    for name, reason in check_pre(components, version, repo=repo):
        failures.append((f"manifest:{name}", reason))

    # 2. The 'erp' component is the canonical released ref (check_pre already
    #    requires all four components identical). Without it there is nothing
    #    sound to reconcile against — refuse.
    canonical = components.get("erp")
    if canonical is None:
        failures.append(("manifest", "no 'erp' component in stack.release.json to "
                                     "reconcile docker-compose.yml against"))
        return failures

    # 3. GUARD-THE-GUARD: an extractor that matches nothing must FAIL, never
    #    pass vacuously. Zero refs means either docker-compose.yml moved / was
    #    renamed, or the regex no longer matches how images are pinned — the
    #    precise way a silent gate stops guarding.
    compose_refs = extract_compose_refs(compose_text)
    if not compose_refs:
        failures.append(("docker-compose.yml",
                         "no ghcr.io/askexe/exe-erp image found — the deploy manifest "
                         "moved or this extractor is broken; refusing to pass vacuously"))
        return failures

    # 4. Every distinct compose ref must equal the canonical released ref as a
    #    plain string (byte identity — a tag can be re-pointed, a digest cannot,
    #    so `repo:tag` and `repo:tag@digest` are DIFFERENT pins). Any mismatch
    #    means the compose pin lags stack.release.json and the host serves old
    #    code after the fix merges (bugs 129a0495 / 96e6b8b6).
    for ref in sorted(set(compose_refs)):
        if ref != canonical:
            failures.append(("docker-compose.yml",
                             f"pins {ref!r}, which is NOT the released image "
                             f"{canonical!r} from stack.release.json. exe-erp runs every "
                             "role off ONE image; a compose pin that lags the manifest "
                             "keeps the host serving old code after the fix merges — the "
                             "exact shape of bugs 129a0495 / 96e6b8b6. Re-pin "
                             "docker-compose.yml to the released ref."))
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

    # --- compose reconciliation must REFUSE (bugs 129a0495 / 96e6b8b6) ---
    def _compose(*refs):
        # Six services, mirroring docker-compose.yml (configurator, erp,
        # websocket, queue, scheduler, nginx), so a single drifted service is
        # exercised rather than deduped away.
        body = "\n".join(f"  svc{i}:\n    image: {r}" for i, r in enumerate(refs))
        return f"services:\n{body}\n"

    good_ref = f"{REPO}:v{v}@{GOOD_DIGEST}"
    case("compose: different digest than manifest is refused",
         check_compose(pinned, v, _compose(*[f"{REPO}:v{v}@{OTHER_DIGEST}"] * 6)), True)
    case("compose: pre-fix tag v0.2.0-final8 is refused (the literal outage)",
         check_compose(pinned, v, _compose(*[f"{REPO}:v0.2.0-final8@{OTHER_DIGEST}"] * 6)), True)
    case("compose: tag-only while manifest is digest-pinned is refused",
         check_compose(pinned, v, _compose(*[f"{REPO}:v{v}"] * 6)), True)
    case("compose: ONE drifted service among six identical is refused",
         check_compose(pinned, v, _compose(good_ref, good_ref, good_ref,
                                           f"{REPO}:v0.2.0-final8@{OTHER_DIGEST}",
                                           good_ref, good_ref)), True)
    case("compose: foreign registry -> extractor finds nothing -> refused",
         check_compose(pinned, v, _compose(*[f"ghcr.io/evil/exe-erp:v{v}@{GOOD_DIGEST}"] * 6)), True)
    case("compose: empty file is refused (guard-the-guard)",
         check_compose(pinned, v, ""), True)
    case("compose: no image: lines is refused (guard-the-guard)",
         check_compose(pinned, v, "services:\n  erp:\n    build: .\n"), True)
    case("compose: reconciling against a malformed manifest is refused",
         check_compose(_components(f"{REPO}:v0.2.0"), v, _compose(*[good_ref] * 6)), True)
    # A drifted service must not hide behind YAML formatting: the extractor sees
    # quoted values and values with a trailing inline comment.
    case("compose: a drifted QUOTED ref among clean ones is refused",
         check_compose(pinned, v, _compose(good_ref, good_ref, good_ref,
                                           f'"{REPO}:v0.2.0-final8@{OTHER_DIGEST}"',
                                           good_ref, good_ref)), True)
    case("compose: a drifted ref hidden behind an inline comment is refused",
         check_compose(pinned, v, _compose(good_ref, good_ref, good_ref,
                                           f"{REPO}:v0.2.0-final8@{OTHER_DIGEST}  # rollback",
                                           good_ref, good_ref)), True)

    # --- must PASS (proves the gate is not simply always-red) ---
    case("compose: pins byte-identical to digest-pinned manifest PASSES",
         check_compose(pinned, v, _compose(*[good_ref] * 6)), False)
    case("compose: tag-only compose equal to tag-only manifest PASSES pre-build",
         check_compose(tag_only, v, _compose(*[f"{REPO}:v{v}"] * 6)), False)
    # Quoted refs and trailing comments are normalized before comparison, so a
    # correctly-pinned-but-quoted/commented compose still PASSES (no false-fail).
    case("compose: correct QUOTED + inline-commented pins PASS (normalized)",
         check_compose(pinned, v, _compose(f'"{good_ref}"', f"{good_ref}  # erp",
                                           good_ref, good_ref, good_ref, good_ref)), False)
    # A different repo that merely starts with 'exe-erp' is NOT the ERP image and
    # must be ignored, not reconciled — proves the boundary anchor (no false-fail).
    case("compose: unrelated exe-erp-init sidecar image is ignored (PASSES)",
         check_compose(pinned, v, _compose(*[good_ref] * 6)
                       + f"  sidecar:\n    image: ghcr.io/askexe/exe-erp-init:v1\n"), False)
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
    ap.add_argument("--phase", choices=("pre", "post", "compose"))
    ap.add_argument("--built-digest", default="",
                    help="sha256:<64 hex> produced by docker/build-push-action (post phase)")
    ap.add_argument("--compose", default="docker-compose.yml",
                    help="deploy manifest reconciled against the release manifest (compose phase)")
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
    elif args.phase == "post":
        failures = check_post(components, version, args.built_digest)
    else:  # compose: reconcile docker-compose.yml against the release manifest
        try:
            with open(args.compose, encoding="utf-8") as fh:
                compose_text = fh.read()
        except OSError as exc:
            # A missing/unreadable deploy manifest is a fail-closed condition:
            # the reconciliation cannot be performed, so the gate must be RED.
            print(f"::error::stack pin gate FAILED (compose) — cannot read "
                  f"{args.compose!r}: {exc}", file=sys.stderr)
            return 1
        failures = check_compose(components, version, compose_text)

    rc = report(failures, args.phase, args.manifest)
    if rc == 0:
        if args.phase == "compose":
            print(f"stack pin gate OK (compose) — {args.compose} agrees with "
                  f"{args.manifest} @ v{version}")
            print(f"  released ref: {components.get('erp')}")
        else:
            print(f"stack pin gate OK ({args.phase}) — {args.manifest} @ v{version}")
            for name in sorted(components):
                print(f"  {name}: {components[name]}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
