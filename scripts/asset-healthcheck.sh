#!/usr/bin/env sh
# ──────────────────────────────────────────────────────────────
# Lightweight container healthcheck: asset bundle resolution (bug f6552d32).
#
# WHY: a ping-200 healthcheck proves only that the HTTP stack answers.
# After an image upgrade the desk can serve pages whose content-hashed
# CSS/JS bundles 404 — the erp_assets volume / Redis asset map diverges
# from the image's dist/ — while /api/method/ping stays 200 and every
# existing signal reports "healthy" (the 2026-08-10 failure mode was
# every CSS bundle 404ing with an unstyled-but-up UI). A 200 page with
# 404ing bundles must be RED. This check fetches one page and asserts
# each referenced bundle actually resolves.
#
# This is the cheap every-30s cousin of scripts/asset-resolution-smoke.sh
# (single page, short timeouts, no reporting) — the bundle regex is
# identical so both detect the same set of assets.
#
# Runs inside the exe-erp image (curl, grep, sort only).
#
# Usage:
#   asset-healthcheck.sh [BASE_URL] [PAGE]
#
#   BASE_URL  defaults to $ERP_HEALTHCHECK_URL, else http://localhost:8080
#   PAGE      defaults to /login
#
# Exit 0 = page fetched and every referenced bundle returned 200.
# Exit 1 = page unfetchable, no bundles referenced, no CSS referenced,
#          or any bundle returned non-200.
# ──────────────────────────────────────────────────────────────
set -eu

BASE_URL="${1:-${ERP_HEALTHCHECK_URL:-http://localhost:8080}}"
BASE_URL="${BASE_URL%/}"
PAGE="${2:-/login}"

html=$(curl -sf --max-time 8 "$BASE_URL$PAGE" 2>/dev/null) || {
	echo "unhealthy: could not fetch $BASE_URL$PAGE" >&2
	exit 1
}

# Content-hashed bundles emitted into dist/ — the ones that go stale.
# Same regex as scripts/asset-resolution-smoke.sh.
assets=$(printf '%s' "$html" \
	| grep -oE '/assets/[A-Za-z0-9_-]+/dist/(css|js)/[^"'"'"' >]+' \
	| sort -u || true)

if [ -z "$assets" ]; then
	echo "unhealthy: $PAGE referenced NO dist/ bundles — page shell is not rendering" >&2
	exit 1
fi

if ! printf '%s\n' "$assets" | grep -q '/css/'; then
	echo "unhealthy: $PAGE referenced no CSS bundles at all" >&2
	exit 1
fi

# URLs contain no whitespace by construction, so word-splitting iteration is safe.
for asset in $assets; do
	code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 \
		"$BASE_URL$asset" || echo 000)
	if [ "$code" != "200" ]; then
		echo "unhealthy: $code $asset" >&2
		exit 1
	fi
done

echo "ok: all referenced asset bundles resolve"
