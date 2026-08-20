#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Regression test for the stale-asset-volume upgrade path (bug f6552d32 /
# 3938ac3d). Proves entrypoint.sh's restore_prebuilt_assets:
#   1. REFRESHES the volume when its assets.json diverges from the image's
#      baked manifest (the image-upgrade case), and flags ASSETS_REFRESHED=1
#      so main() knows to flush the Redis asset map.
#   2. NO-OPs (ASSETS_REFRESHED stays 0) when volume and image already match.
#   3. Seeds a fresh/empty volume from the image backup.
#
# Runs with no container: it sources restore_prebuilt_assets out of the real
# entrypoint.sh (with main() stubbed) and drives it against temp dirs.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${HERE}/../entrypoint.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

fail=0
check() { # desc expected actual
	if [ "$2" = "$3" ]; then
		echo "PASS: $1"
	else
		echo "FAIL: $1 (expected '$2', got '$3')"
		fail=1
	fi
}

# Source restore_prebuilt_assets via the entrypoint's own no-boot test hook.
load_entrypoint() {
	# Provide the vars the function closes over.
	SITES_DIR="${WORK}/sites"
	FRAPPE_BENCH="${WORK}/bench"
	mkdir -p "${FRAPPE_BENCH}/apps/frappe/frappe/public"
	# EXE_ERP_ENTRYPOINT_NO_MAIN=1 loads the functions without booting.
	local restore_u=""; case "$-" in *u*) restore_u="u";; esac
	set +u
	# shellcheck disable=SC1090
	EXE_ERP_ENTRYPOINT_NO_MAIN=1 source "${ENTRYPOINT}"
	[ -n "${restore_u}" ] && set -u
	# Point the backup at our fixture (overrides the /opt default in the file).
	ASSETS_BACKUP="${WORK}/image-backup"
}

seed_backup() { # manifest-content
	rm -rf "${ASSETS_BACKUP}"
	mkdir -p "${ASSETS_BACKUP}/erpnext/dist/css"
	printf '%s' "$1" > "${ASSETS_BACKUP}/assets.json"
	printf 'body{color:red}' > "${ASSETS_BACKUP}/erpnext/dist/css/erpnext.${1}.css"
}

seed_volume() { # manifest-content  (empty string => no volume assets yet)
	local dir="${SITES_DIR}/assets"
	rm -rf "${dir}"
	if [ -n "$1" ]; then
		mkdir -p "${dir}/erpnext/dist/css"
		printf '%s' "$1" > "${dir}/assets.json"
		printf 'body{color:red}' > "${dir}/erpnext/dist/css/erpnext.$1.css"
	fi
}

load_entrypoint

# ── Case 1: image upgrade — volume manifest is STALE vs image ────────────────
seed_backup "NEWHASH"      # image ships v0.3.0 manifest
seed_volume "OLDHASH"      # persistent volume still holds v0.2.0 manifest
ASSETS_REFRESHED=0
restore_prebuilt_assets >/dev/null
check "upgrade: ASSETS_REFRESHED set" "1" "${ASSETS_REFRESHED}"
check "upgrade: volume manifest now matches image" \
	"NEWHASH" "$(cat "${SITES_DIR}/assets/assets.json")"
check "upgrade: new CSS bundle now present in volume" \
	"present" "$([ -f "${SITES_DIR}/assets/erpnext/dist/css/erpnext.NEWHASH.css" ] && echo present || echo missing)"
check "upgrade: frappe symlink re-established" \
	"link" "$([ -L "${SITES_DIR}/assets/frappe" ] && echo link || echo nolink)"

# ── Case 2: identical image — must be a no-op (no needless flush) ────────────
# Volume now equals the image after Case 1; re-run against the same backup.
ASSETS_REFRESHED=0
restore_prebuilt_assets >/dev/null
check "steady-state: ASSETS_REFRESHED stays 0 (no-op)" "0" "${ASSETS_REFRESHED}"

# ── Case 3: fresh/empty volume — seed from image backup ──────────────────────
seed_backup "FRESHHASH"
seed_volume ""             # brand-new empty volume, no assets.json
ASSETS_REFRESHED=0
restore_prebuilt_assets >/dev/null
check "fresh volume: ASSETS_REFRESHED set" "1" "${ASSETS_REFRESHED}"
check "fresh volume: manifest seeded from image" \
	"FRESHHASH" "$(cat "${SITES_DIR}/assets/assets.json")"

if [ "${fail}" -ne 0 ]; then
	echo "🔴 asset-volume-refresh regression FAILED"
	exit 1
fi
echo "🟢 asset-volume-refresh regression PASSED"
