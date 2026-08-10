#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Post-deploy asset resolution smoke test (bug 3938ac3d).
#
# The desk can be "healthy" by every existing signal — /api/method/ping 200,
# container healthcheck green, JS bundles serving — while EVERY CSS bundle
# 404s and the UI renders completely unstyled. That is exactly what happened
# on 2026-08-10: the persistent erp_assets volume kept a Jun 17 assets.json
# while the image's dist/ was Jul 1, and Redis served a third generation of
# hashes on top. Eleven CSS bundles, all unreachable, and nothing alerted.
#
# This script closes that gap: it fetches a real page and asserts that every
# asset URL the page references actually resolves. A page whose stylesheets
# 404 is a broken deploy, not a healthy one.
#
# Usage:
#   scripts/asset-resolution-smoke.sh [BASE_URL] [PATH ...]
#
#   BASE_URL  defaults to $ERP_BASE_URL, else http://localhost:8069
#   PATH ...  page paths to sweep; defaults to /login
#
# Exit 0 = every referenced asset returned 200.
# Exit 1 = at least one asset did not, with the offenders listed.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

BASE_URL="${1:-${ERP_BASE_URL:-http://localhost:8069}}"
BASE_URL="${BASE_URL%/}"
shift || true

if [ "$#" -gt 0 ]; then
	PAGES=("$@")
else
	PAGES=("/login" "/app")
fi

failures=0
checked=0

for page in "${PAGES[@]}"; do
	echo "── Sweeping ${BASE_URL}${page}"

	html=$(curl -fsSL --max-time 20 "${BASE_URL}${page}" 2>/dev/null) || {
		echo "FAIL: could not fetch ${BASE_URL}${page}"
		failures=$((failures + 1))
		continue
	}

	# Content-hashed bundles emitted into dist/ — the ones that go stale.
	assets=$(printf '%s' "$html" \
		| grep -oE '/assets/[A-Za-z0-9_-]+/dist/(css|js)/[^"'"'"' >]+' \
		| sort -u || true)

	if [ -z "$assets" ]; then
		echo "FAIL: ${page} referenced NO dist/ assets — the page shell is not rendering bundles"
		failures=$((failures + 1))
		continue
	fi

	css_count=$(printf '%s\n' "$assets" | grep -c '/css/' || true)
	if [ "${css_count:-0}" -eq 0 ]; then
		echo "FAIL: ${page} referenced no CSS bundles at all"
		failures=$((failures + 1))
	fi

	while IFS= read -r asset; do
		[ -z "$asset" ] && continue
		checked=$((checked + 1))
		code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${BASE_URL}${asset}" || echo 000)
		if [ "$code" != "200" ]; then
			echo "FAIL: ${code} ${asset}"
			failures=$((failures + 1))
		fi
	done <<< "$assets"
done

if [ "$failures" -gt 0 ]; then
	echo ""
	echo "🔴 asset resolution smoke FAILED — ${failures} problem(s) across ${checked} asset(s)."
	echo "   Likely cause: the erp_assets volume's assets.json has diverged from the"
	echo "   image's dist/, and/or Redis is serving a stale asset map."
	echo "   Remedy: bench build && bench clear-cache && restart exe-erp."
	exit 1
fi

echo ""
echo "🟢 asset resolution smoke PASSED — ${checked} asset(s) all returned 200."
