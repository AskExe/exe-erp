#!/usr/bin/env python3
"""Release tag immutability guard (bug 2fe79166).

The registry tag `ghcr.io/askexe/exe-erp:v0.2.0-final8` was pushed over at
least THREE times, so over its lifetime it pointed at three different builds.
Anything referencing ERP by that tag is not reproducible: two hosts pulling
"the same" tag at different times ran different code, and a rollback to it did
not restore the bits that were running.

Root cause: the release workflow pushed the version tag UNCONDITIONALLY, so
re-running a release with an unchanged version silently overwrote an
already-published tag with new content.

This script fails the release BEFORE any image is pushed if the target tag
already resolves in the registry.

Fail-closed: only an authoritative HTTP 404 "manifest not found" from the
registry is treated as "tag absent". HTTP 200 means the tag exists. Everything
else — 401, 403, 429, any other 4xx, 5xx (after retries), malformed responses
and network/DNS errors — means existence could NOT be proven and aborts the
release. Never treat a non-404 as absence.

Standard library only: the self-hosted runner has python3 but may not have
pip packages (no requests, no docker SDK).

Usage:
  python3 scripts/assert-release-tag-unpublished.py \
      --repository askexe/exe-erp --tag v0.2.0

Env:
  GHCR_TOKEN                  required — PAT with read:packages. GHCR accepts a
                              base64-encoded PAT as a bearer token for pull
                              scopes. Never printed, nor its encoded form.
  EXE_ALLOW_TAG_OVERWRITE=1   documented break-glass. Defaults OFF. Only for a
                              deliberate re-push of a tag nothing has pinned;
                              record why in the release PR.
  EXE_GHCR_REGISTRY_BASE      registry base URL override (tests only)
  EXE_TAG_GUARD_RETRY_MS      sleep between retries, ms (default 2000)

Exit codes: 0 = tag is free (or break-glass), 1 = tag exists / cannot prove
absence.
"""

import argparse
import base64
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_ACCEPT = ",".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

NETWORK_RETRIES = 3


def fail(message):
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def retry_delay_ms():
    try:
        return int(os.environ.get("EXE_TAG_GUARD_RETRY_MS", "2000"))
    except ValueError:
        return 2000


def probe_tag(base, repository, tag, token):
    """Return 'exists' or 'absent'; raise if existence cannot be proven.

    urllib.request.urlopen RAISES urllib.error.HTTPError for 4xx/5xx rather
    than returning them — a 404 caught here is the SUCCESS (absent) path.
    URLError / socket errors are network errors and are retried.
    """
    url = f"{base.rstrip('/')}/v2/{repository}/manifests/{urllib.parse.quote(tag, safe='')}"
    # GHCR accepts a base64-encoded PAT as a bearer token for pull scopes.
    bearer = base64.b64encode(token.encode("utf-8")).decode("ascii")

    last_error = None
    for attempt in range(1, NETWORK_RETRIES + 1):
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": MANIFEST_ACCEPT, "Authorization": f"Bearer {bearer}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
        except urllib.error.HTTPError as e:
            status = e.code
        except (urllib.error.URLError, OSError) as e:
            last_error = f"network error contacting registry: {e}"
            if attempt < NETWORK_RETRIES:
                print(
                    f"Registry probe attempt {attempt} failed ({last_error}); retrying..."
                )
                time.sleep(retry_delay_ms() / 1000.0)
                continue
            raise RuntimeError(last_error)

        if status == 200:
            return "exists"
        if status == 404:
            return "absent"

        # 5xx is transient; everything else (401/403/429/...) is an
        # authoritative non-answer. Neither may be read as "tag absent".
        last_error = f"registry returned HTTP {status} for {repository}:{tag}"
        if 500 <= status < 600 and attempt < NETWORK_RETRIES:
            print(
                f"Registry probe attempt {attempt} failed ({last_error}); retrying..."
            )
            time.sleep(retry_delay_ms() / 1000.0)
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error or "registry probe exhausted retries")


def main():
    parser = argparse.ArgumentParser(description="Assert a release tag is unpublished (fail-closed).")
    parser.add_argument("--repository", required=True, help="repository as <owner/name>")
    parser.add_argument("--tag", required=True, help="tag to check, e.g. v0.2.0")
    args = parser.parse_args()

    repository, tag = args.repository, args.tag
    image = f"ghcr.io/{repository}:{tag}"

    if os.environ.get("EXE_ALLOW_TAG_OVERWRITE") == "1":
        print(
            f"::warning::EXE_ALLOW_TAG_OVERWRITE=1 — tag immutability guard bypassed for {image}. "
            "This may overwrite an image customers have already validated.",
            file=sys.stderr,
        )
        sys.exit(0)

    token = os.environ.get("GHCR_TOKEN")
    if not token:
        fail(
            "GHCR_TOKEN is required to verify release tag immutability. Refusing to publish "
            f"{image} without proving the tag is unused (fail-closed guard, bug 2fe79166)."
        )

    base = os.environ.get("EXE_GHCR_REGISTRY_BASE") or "https://ghcr.io"
    try:
        state = probe_tag(base, repository, tag, token)
    except RuntimeError as e:
        fail(
            f"Could not determine whether {image} already exists: {e}. "
            "Refusing to publish — the tag immutability guard fails closed (bug 2fe79166)."
        )
        return

    if state == "exists":
        fail(
            f"{image} ALREADY EXISTS in the registry. Released tags are immutable — pushing "
            "would overwrite an image customers have already validated and digest-pinned "
            "(bug 2fe79166: v0.2.0-final8 was overwritten at least three times).\n"
            'Fix: bump "version" in stack.release.json and re-run the release.\n'
            "Deliberate re-push (rare): set EXE_ALLOW_TAG_OVERWRITE=1 and state why in the release PR."
        )

    print(f"{image} is not published yet — safe to build and push.")
    sys.exit(0)


if __name__ == "__main__":
    main()
