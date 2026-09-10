#!/bin/bash
set -eo pipefail

# ──────────────────────────────────────────────────────────────
# Exe ERP — Container entrypoint
# Handles first-boot site creation + subsequent-boot migrations
# ──────────────────────────────────────────────────────────────

# Overridable ONLY so the entrypoint can be exercised by tests against a
# throwaway bench root. Production containers never set it, so the default
# below is what actually ships.
FRAPPE_BENCH="${FRAPPE_BENCH:-/home/frappe/frappe-bench}"
SITES_DIR="${FRAPPE_BENCH}/sites"
# SITE_NAME must be set per-deployment (e.g. erp.acme.com). Production stacks
# enforce this via docker-compose (${SITE_NAME:?...}) and stack.release.json
# requiredEnv preflight. When the container is run directly without SITE_NAME,
# fall back to the neutral dev host erp.localhost — never an AskExe-branded
# default, which would break white-label/customer installs.
if [ -z "${SITE_NAME:-}" ]; then
    echo "WARNING: SITE_NAME not set — falling back to dev default 'erp.localhost'."
    echo "         Set SITE_NAME to your ERP domain (e.g. erp.acme.com) for production."
fi
SITE_NAME="${SITE_NAME:-erp.localhost}"
SITE_DIR="${SITES_DIR}/${SITE_NAME}"

cd "${FRAPPE_BENCH}"

# ── Validate admin password ─────────────────────────────────
# check_admin_password is the pure predicate: it NEVER exits. It prints the
# reason on stdout and returns 1 when the configured password fails a rule.
# Callers decide whether that is fatal. Splitting the decision out of the rule
# is the whole fix for bug 593fe59f — see admin_password_preflight below.
check_admin_password() {
    local pw="${ADMIN_PASSWORD:-}"
    if [ -z "${pw}" ]; then
        echo "ADMIN_PASSWORD (ERP_ADMIN_PASSWORD) is required but not set."
        return 1
    fi
    local len=${#pw}
    if [ "${len}" -lt 12 ]; then
        echo "ADMIN_PASSWORD must be at least 12 characters (got ${len})."
        return 1
    fi
    # Check against common weak defaults
    local weak
    for weak in admin password changeme admin123 password123 administrator; do
        if [ "${pw}" = "${weak}" ]; then
            echo "ADMIN_PASSWORD cannot be a common default ('${weak}')."
            return 1
        fi
    done
    # Require at least one special character — UNLESS the password is long
    # enough that the character class buys nothing (bug 293a3c8f).
    #
    # A 24-char random alphanumeric secret carries ~143 bits of entropy; a
    # 12-char password with one special character carries far less. Rejecting
    # the former while accepting the latter is backwards, and it is not
    # academic: erp.askexe.com's configured ERP_ADMIN_PASSWORD is a 32-char
    # alphanumeric secret, so this rule was the ONLY thing standing between the
    # rotation path and a fix for a live operator lockout. The rule has already
    # cost one outage on its own (bug 593fe59f); it should not also be the
    # reason a lockout cannot be repaired.
    #
    # Shorter passwords keep the original requirement, and the weak-defaults
    # check above still applies at every length.
    if [ "${len}" -lt 24 ]; then
        case "${pw}" in
            *[!a-zA-Z0-9]*) ;;
            *)
                echo "ADMIN_PASSWORD must contain at least one special character (or be at least 24 characters)."
                return 1
                ;;
        esac
    fi
    return 0
}

# Fatal wrapper. Use only where an invalid password must stop the boot —
# i.e. site bootstrap, where this value BECOMES the Administrator credential.
validate_admin_password() {
    local reason
    if ! reason="$(check_admin_password)"; then
        echo "ERROR: ${reason}"
        echo "Set ERP_ADMIN_PASSWORD in your .env or docker-compose override."
        exit 1
    fi
}

# ── Wait for Postgres ────────────────────────────────────────
wait_for_db() {
    local retries=30
    echo "Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT:-5432}..."
    while ! pg_isready -h "${DB_HOST}" -p "${DB_PORT:-5432}" -q 2>/dev/null; do
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
            echo "ERROR: PostgreSQL not reachable after 30 attempts"
            exit 1
        fi
        sleep 2
    done
    echo "PostgreSQL is ready."
}

# ── Wait for Redis ───────────────────────────────────────────
wait_for_redis() {
    local redis_url="${1}"
    local host port redis_pass
    # Extract host:port from redis://[:password@]host:port/db
    host=$(echo "${redis_url}" | sed -E 's|redis://([^@]+@)?([^:]+):([0-9]+)/.*|\2|')
    port=$(echo "${redis_url}" | sed -E 's|redis://([^@]+@)?([^:]+):([0-9]+)/.*|\3|')
    redis_pass=$(echo "${redis_url}" | sed -nE 's|redis://:([^@]+)@.*|\1|p')
    local retries=30
    local auth_args=""
    [ -n "${redis_pass}" ] && auth_args="-a ${redis_pass}"
    echo "Waiting for Redis at ${host}:${port}..."
    while ! redis-cli -h "${host}" -p "${port}" ${auth_args} ping >/dev/null 2>&1; do
        retries=$((retries - 1))
        if [ "$retries" -le 0 ]; then
            echo "ERROR: Redis not reachable after 30 attempts"
            exit 1
        fi
        sleep 2
    done
    echo "Redis at ${host}:${port} is ready."
}

# ── Restore prebuilt assets shadowed by the sites volume ─────
# The image bakes assets to sites/assets, but the erp-sites volume mounts over
# sites/ and SHADOWS them on a fresh volume — the desk UI then 404s on CSS/JS
# (bug 29a22993). The Dockerfile keeps a volume-safe backup at
# /opt/exe-erp-assets; restore it into the volume when sites/assets is missing
# OR when the volume's manifest no longer matches the image's (stale after an
# image upgrade — bug 3938ac3d). Idempotent: a no-op when they already match.
ASSETS_BACKUP="/opt/exe-erp-assets"
# Set to 1 by restore_prebuilt_assets when it actually rewrote the volume, so
# main() knows the Redis asset map must be flushed (see bug 3938ac3d).
ASSETS_REFRESHED=0
restore_prebuilt_assets() {
    local assets_dir="${SITES_DIR}/assets"

    if [ ! -d "${ASSETS_BACKUP}" ] || [ ! -f "${ASSETS_BACKUP}/assets.json" ]; then
        echo "WARNING: no prebuilt asset backup at ${ASSETS_BACKUP}; skipping asset restore."
        return 0
    fi

    # STALENESS CHECK (bug 3938ac3d), not just an existence check.
    #
    # The old guard was `[ -f assets.json ] && return 0`. That is only correct
    # on a FRESH volume. On an image UPGRADE the persistent erp_assets volume
    # keeps the PREVIOUS image's assets.json while `assets/<app>` resolves to
    # the NEW image's dist/ — so the manifest advertises content-hashed
    # filenames that no longer exist on disk. Observed live 2026-08-10:
    # assets.json dated Jun 17, dist/ dated Jul 1, 11 of 46 entries missing,
    # ALL of them CSS. The desk rendered completely unstyled.
    #
    # Comparing the volume's manifest against the image's baked manifest makes
    # the restore idempotent on identical images and self-healing on upgrade.
    if [ -f "${assets_dir}/assets.json" ]; then
        if cmp -s "${assets_dir}/assets.json" "${ASSETS_BACKUP}/assets.json"; then
            return 0
        fi
        echo "Asset manifest in volume differs from image (stale after upgrade) — refreshing..."
    else
        echo "Prebuilt assets missing under volume — restoring from ${ASSETS_BACKUP}..."
    fi

    mkdir -p "${assets_dir}"
    cp -a "${ASSETS_BACKUP}/." "${assets_dir}/"
    # Re-establish the frappe asset symlink → live app public dir (the baked
    # symlink target is outside the volume and resolves at runtime).
    rm -f "${assets_dir}/frappe"
    ln -sf "${FRAPPE_BENCH}/apps/frappe/frappe/public" "${assets_dir}/frappe"
    ASSETS_REFRESHED=1
    echo "Prebuilt assets restored into ${assets_dir}."
}

# ── Flush Frappe's cache through its own cache client ────────
# `bench clear-cache` CANNOT clear the asset map (bug f6552d32) — it exits 0
# having changed nothing, which is why stale bundles kept serving after an
# upgrade. Two reasons, both visible in this repo:
#
#   1. Its "clear everything" branch (frappe/cache_manager.py:clear_cache)
#      deletes only `frappe.cache.get_keys("")`, and get_keys() prefixes every
#      key with the site's db_name (frappe/utils/redis_wrapper.py:make_key).
#      The resolved bundle manifest is cached as `assets_json` with
#      shared=True (frappe/utils/__init__.py:get_assets_json), i.e. WITHOUT
#      that prefix — so it is never in the delete set. (clear_global_cache has
#      to delete bench_cache_keys with shared=True explicitly for this reason;
#      the clear-cache path does not go through it.)
#   2. Even if the Redis key went away, every gunicorn worker holds its own
#      in-process ClientCache copy. Those are dropped only on a Redis
#      client-side-cache invalidation message.
#
# FLUSHDB on the cache client fixes both: it removes shared keys the
# site-prefixed sweep misses, and it fires an invalidation with a null key
# list, which each worker's ClientCache turns into a full local clear
# (redis_wrapper.py:_handle_invalidation). This is exactly what Frappe itself
# does after rebuilding bundles — frappe/build.py calls frappe.cache.flushdb()
# at the end of bundle(). flushdb (not flushall) keeps the blast radius to the
# cache DB and leaves the queue/socketio DBs on the same Redis untouched.
#
# frappe.init() alone wires up frappe.cache (see frappe/__init__.py:init →
# setup_redis_cache_connection), so no DB connection is needed here.
flush_frappe_cache() {
    local py="${FRAPPE_BENCH}/env/bin/python"
    [ -x "${py}" ] || py="python3"

    if FLUSH_SITE_NAME="${SITE_NAME}" FLUSH_SITES_DIR="${SITES_DIR}" "${py}" - <<'PYFLUSH'
import os
import sys

import frappe

try:
    frappe.init(site=os.environ["FLUSH_SITE_NAME"], sites_path=os.environ["FLUSH_SITES_DIR"])
    frappe.cache.flushdb()
    print("Frappe cache DB flushed (asset map + rendered page cache).")
except Exception as exc:  # noqa: BLE001 - reported to the caller via exit code
    print(f"cache flush failed: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    try:
        frappe.destroy()
    except Exception:  # noqa: BLE001
        pass
PYFLUSH
    then
        return 0
    fi

    echo "WARNING: Frappe cache flush failed; served asset hashes may be stale."
    return 0
}

# ── Configure common_site_config.json ────────────────────────
configure_site_config() {
    echo "Writing common_site_config.json..."
    cat > "${SITES_DIR}/common_site_config.json" <<EOF
{
    "db_host": "${DB_HOST}",
    "db_port": ${DB_PORT:-5432},
    "db_type": "postgres",
    "redis_cache": "${REDIS_CACHE}",
    "redis_queue": "${REDIS_QUEUE}",
    "redis_socketio": "${REDIS_SOCKETIO}",
    "socketio_port": 9000
}
EOF
}

# Marker written only after a fully successful create + install-app erpnext.
# Used as a fast-path so we don't hit the DB on every boot; the authoritative
# check is is_erpnext_installed() (queries the site DB).
INSTALL_MARKER="${SITE_DIR}/.exe_install_complete"

# ── Is erpnext actually installed in the site DB? ────────────
# Directory existence is NOT proof of a working site: `bench new-site` may have
# created the dir, then `install-app erpnext` failed — leaving a half-installed
# site that pings but has no desk/data. Ask the DB, which is the source of truth.
is_erpnext_installed() {
    # Fast path: completion marker from a prior successful boot.
    if [ -f "${INSTALL_MARKER}" ]; then
        return 0
    fi
    # Authoritative: query installed apps from the site DB.
    # `bench list-apps` connects to the site DB and lists installed apps.
    if bench --site "${SITE_NAME}" list-apps 2>/dev/null | grep -qiw "erpnext"; then
        # Backfill the marker so future boots take the fast path.
        touch "${INSTALL_MARKER}" 2>/dev/null || true
        return 0
    fi
    return 1
}

# ── Does the Frappe framework site itself exist (DB created)? ─
# `bench new-site` writes site_config.json with the db_name once the framework
# is bootstrapped. Used to decide whether new-site must run.
is_site_db_initialized() {
    [ -f "${SITE_DIR}/site_config.json" ] && grep -q '"db_name"' "${SITE_DIR}/site_config.json" 2>/dev/null
}

# ── First boot / repair: create site + install erpnext ───────
# Idempotent: safe to re-run after a partially failed previous boot.
#   - If the framework site DB isn't initialized yet, run `bench new-site`.
#   - If new-site already ran but erpnext install failed, skip new-site and
#     (re-)run install-app erpnext to repair the half-installed site.
create_site() {
    if is_site_db_initialized; then
        echo "Site framework already initialized but erpnext not installed — repairing install..."
    else
        echo "First boot — creating site: ${SITE_NAME}"
        # Use the same DB user (exe) that owns the exe_erp database.
        # --db-root-username tells bench to use this user for DDL operations
        # instead of creating a new per-site user.
        # --force lets us re-run new-site if a prior attempt left a stale dir
        # without an initialized DB (e.g. crash mid-bootstrap).
        bench new-site "${SITE_NAME}" \
            --db-type postgres \
            --db-host "${DB_HOST}" \
            --db-port "${DB_PORT:-5432}" \
            --db-name "${DB_NAME:-exe_erp}" \
            --db-root-username "${POSTGRES_USER:-exe}" \
            --db-root-password "${DB_PASSWORD}" \
            --db-password "${DB_PASSWORD}" \
            --admin-password "${ADMIN_PASSWORD}" \
            --no-mariadb-socket \
            --force
    fi

    bench --site "${SITE_NAME}" install-app erpnext
    # Only mark complete once install-app actually succeeded (set -e aborts above
    # on failure, so reaching here means the install returned 0).
    touch "${INSTALL_MARKER}"
    # Seed the admin-password marker so the rotation check (bug 43854b31) treats
    # the just-created password as current and won't spuriously rotate next boot.
    printf '%s' "$(admin_password_hash "${ADMIN_PASSWORD}")" > "${ADMIN_PW_MARKER}"
    echo "Site created and erpnext installed."
}

# ── Subsequent boot: run migrations ──────────────────────────
run_migrations() {
    echo "Existing site found — running migrations..."
    bench --site "${SITE_NAME}" migrate
    echo "Migrations complete."
}

# ── Rotate Administrator password when ERP_ADMIN_PASSWORD changes ─
# The Administrator password is only set at first `bench new-site`. If an
# operator later changes ERP_ADMIN_PASSWORD, the running site keeps the OLD
# password forever — confusing and a lockout/security risk (bug 43854b31).
#
# We rotate ONLY when the value actually changed, detected via a SHA-256 marker
# of the current password (never the plaintext) stored beside the site. On a
# match we no-op (no needless reset every boot); on a mismatch — or no marker —
# we run `bench set-admin-password` and refresh the marker.
#
# Note: the marker is seeded on first creation (in create_site) so existing
# installs that already match don't get a spurious first-boot rotation. If the
# marker is absent on an upgraded install, we conservatively rotate once to
# converge state to the configured password, then write the marker.
ADMIN_PW_MARKER="${SITE_DIR}/.exe_admin_pw_hash"

admin_password_hash() {
    # SHA-256 of the password; salted with the site name so the marker isn't a
    # bare reusable hash. Reads the password from stdin to keep it off argv.
    #
    # PORTABLE + FAIL-CLOSED (bug 293a3c8f). `sha256sum` is GNU coreutils and is
    # absent on BSD/macOS — the same portability trap this file already
    # documents avoiding for `grep -P`. When it was missing this function
    # printed NOTHING, and an empty hash compares equal to an empty/absent
    # marker, so the preflight concluded "already applied" and never rotated:
    # the precise failure mode of this bug, reached by a second route. It also
    # made scripts/entrypoint-admin-password.test.sh — the suite guarding the
    # 593fe59f outage — report that outage on any non-GNU machine.
    #
    # An unusable hasher must stop the boot, not silently produce a marker that
    # matches everything.
    local hasher
    if command -v sha256sum >/dev/null 2>&1; then
        hasher="sha256sum"
    elif command -v shasum >/dev/null 2>&1; then
        hasher="shasum -a 256"
    else
        echo "ERROR: neither sha256sum nor shasum is available; cannot compute the" >&2
        echo "       admin-password marker. Refusing to boot rather than treat an" >&2
        echo "       empty hash as a match (bug 293a3c8f)." >&2
        exit 1
    fi
    printf '%s' "${1}:${SITE_NAME}" | ${hasher} | cut -d' ' -f1
}

read_admin_pw_marker() {
    if [ -f "${ADMIN_PW_MARKER}" ]; then
        cat "${ADMIN_PW_MARKER}" 2>/dev/null || true
    fi
}

# ── Is the configured password ACTUALLY the Administrator password? ───────
# The marker records what we last INTENDED to apply, never what is true. Bug
# 293a3c8f is what that costs: on erp.askexe.com the marker matched the
# configured password exactly, so the preflight below concluded "nothing to
# do" — while that password returned 401 AuthenticationError against the live
# site, verified both over HTTP and through Frappe's own check_password. The
# marker was a cache that nothing ever validated, so NO boot could converge
# the state. The lockout was permanent by construction.
#
# Exit codes are deliberately three-valued, because "I could not check" must
# never be read as "the password is wrong":
#   0 — authenticates
#   1 — definitively WRONG (Frappe raised AuthenticationError)
#   2 — COULD NOT DETERMINE (bench missing, site down, migration pending,
#       any unrecognised error). Callers must change nothing on a 2.
admin_password_authenticates() {
    # THE PASSWORD IS NEVER INTERPOLATED INTO CODE OR ARGV.
    #
    # The first version of this used
    #   bench ... execute frappe.utils.password.check_password --args "[\"Administrator\", \"${ADMIN_PASSWORD}\"]"
    # which builds a Python expression by string interpolation, and
    # frappe/commands/execute.py evaluates that value directly. Two real
    # failures, both reachable now that the lint above accepts any special
    # character (Codex review, PR #106):
    #   * a `"` makes --args syntactically invalid, so the call fails with an
    #     unrecognised error, this function returns 2, and a STALE MARKER IS
    #     TRUSTED — the exact bug this fix exists to close, still open for
    #     those passwords;
    #   * a `\n` or `\t` is changed by escape processing, so a CORRECTLY
    #     configured password reads as wrong and is reset on EVERY boot.
    # It is also an eval of an operator-supplied string, which should not
    # exist regardless of who controls the value.
    #
    # The password goes in on STDIN instead — off argv (so it never appears in
    # `ps`) and never parsed as code. Only the site name, which is not secret,
    # is passed as an argument.
    #
    # The verdict is carried by an explicit TOKEN rather than an exit code or a
    # traceback substring, so "the checker itself broke" can never be mistaken
    # for "the password is wrong".
    local out rc=0
    out="$(printf '%s' "${ADMIN_PASSWORD}" | (
        cd "${FRAPPE_BENCH}/sites" 2>/dev/null || exit 97
        "${FRAPPE_BENCH}/env/bin/python" -c '
import sys
site = sys.argv[1]
pw = sys.stdin.read()
import frappe
from frappe.utils.password import check_password
frappe.init(site=site)
frappe.connect()
try:
    check_password("Administrator", pw)
    print("EXE_ADMIN_PW:OK")
except Exception as exc:
    if type(exc).__name__ == "AuthenticationError":
        print("EXE_ADMIN_PW:WRONG")
    else:
        print("EXE_ADMIN_PW:UNKNOWN:%s" % type(exc).__name__)
' "${SITE_NAME}"
    ) 2>&1)" || rc=$?

    case "${out}" in
        *EXE_ADMIN_PW:OK*)    return 0 ;;
        *EXE_ADMIN_PW:WRONG*) return 1 ;;
    esac
    # No token at all: interpreter missing, site unreachable, import failure,
    # a non-zero exit we did not recognise (rc=${rc}). Unknown, not absent.
    return 2
}

# ── Admin-password preflight (bug 593fe59f) ──────────────────
# AVAILABILITY MUST NOT HINGE ON A PASSWORD LINT.
#
# Production incident 2026-08-07 (exe-db-jkt, 0.9.33 flip): an install that
# predates the special-character rule was upgraded into an image carrying it.
# It had no marker, so the rotation path ran validate_admin_password, which
# exited 1 under `set -e` — AFTER a full `bench migrate`. The container died,
# restarted, re-ran multi-minute migrations, and died again: erp.askexe.com
# 502 for ~25 minutes. No credential was ever wrong; only the lint was.
#
# This runs BEFORE any migration so a genuinely fatal verdict costs seconds
# rather than a migration cycle, and it decides ONCE what the later rotation
# step is allowed to do:
#   none         — configured password already matches the marker; nothing to do
#   rotate       — changed (or never recorded) and valid; apply it
#   skip-invalid — NO MARKER and invalid: a pre-existing install whose
#                  long-standing, working password predates a newer rule.
#                  Warn loudly, change nothing, stay up.
# A marker that EXISTS and disagrees means an operator deliberately set a new
# password. An invalid new password there is a real operator error and is still
# fatal — but now it is fatal before migrations, not after them.
ADMIN_PW_ACTION="none"
# Set ONLY when a matching marker was proven stale. `ADMIN_PW_ACTION=rotate` is
# NOT a usable substitute: the fresh-bootstrap path also sets it, and there
# `create_site` has already applied the password via `bench new-site` and
# seeded the marker (Codex review, PR #106).
ADMIN_PW_MARKER_STALE=0
admin_password_preflight() {
    local erpnext_installed="${1}"

    # Migrate-only services (gunicorn/worker/scheduler) never receive
    # ADMIN_PASSWORD, so there is nothing to validate or rotate.
    if [ -z "${ADMIN_PASSWORD:-}" ]; then
        ADMIN_PW_ACTION="none"
        return 0
    fi

    # Site bootstrap has its own fatal gate (this value becomes the credential),
    # and it runs before create_site, so no migrations are at stake there.
    if [ "${erpnext_installed}" != "1" ]; then
        ADMIN_PW_ACTION="rotate"
        return 0
    fi

    local current_hash stored_hash reason
    current_hash="$(admin_password_hash "${ADMIN_PASSWORD}")"
    stored_hash="$(read_admin_pw_marker)"

    if [ "${current_hash}" = "${stored_hash}" ]; then
        # The marker agrees — but agreement is not proof (bug 293a3c8f).
        # Verify against the live site before trusting it. Same `set -e` care as
        # inside the helper: a bare call would abort the boot on a non-zero.
        local verdict=0
        admin_password_authenticates || verdict=$?
        case "${verdict}" in
            0)
                ADMIN_PW_ACTION="none"
                return 0
                ;;
            2)
                echo "WARNING: could not verify the Administrator password (site not"
                echo "         reachable, migration pending, or an unrecognised error)."
                echo "         Trusting the marker and changing NOTHING this boot."
                ADMIN_PW_ACTION="none"
                return 0
                ;;
        esac

        ADMIN_PW_MARKER_STALE=1
        echo "WARNING: the admin-password marker says ERP_ADMIN_PASSWORD is already"
        echo "         applied, but it does NOT authenticate against this site."
        echo "         The marker is stale (bug 293a3c8f) — reconverging."
        # Fall through to the same validate-then-rotate decision as a changed
        # password. A stale marker must not be a shortcut past the lint.
    fi

    if reason="$(check_admin_password)"; then
        ADMIN_PW_ACTION="rotate"
        return 0
    fi

    if [ -n "${stored_hash}" ]; then
        echo "ERROR: ${reason}"
        echo "ERP_ADMIN_PASSWORD was changed to a value that fails validation."
        echo "Set a compliant ERP_ADMIN_PASSWORD; refusing to rotate."
        exit 1
    fi

    ADMIN_PW_ACTION="skip-invalid"
    echo "WARNING: ${reason}"
    echo "WARNING: no admin-password marker on an existing install — SKIPPING"
    echo "         Administrator password rotation to keep the service available"
    echo "         (bug 593fe59f). NOTHING IS CHANGED: the site keeps the"
    echo "         Administrator password it already has, which is very likely"
    echo "         the working one. Set a compliant ERP_ADMIN_PASSWORD to rotate."
    return 0
}

rotate_admin_password_if_changed() {
    # The verdict was reached in admin_password_preflight, before migrations.
    [ "${ADMIN_PW_ACTION}" = "rotate" ] || return 0

    local current_hash stored_hash
    current_hash="$(admin_password_hash "${ADMIN_PASSWORD}")"
    stored_hash="$(read_admin_pw_marker)"

    # create_site seeds the marker on the bootstrap path, so by the time we get
    # here on a fresh install the password is already applied.
    #
    # NOTE: this early-return is safe ONLY because the preflight already
    # verified a matching marker against the live site (bug 293a3c8f). The one
    # case where a MATCHING marker must NOT short-circuit is a marker the
    # preflight proved stale — and that is exactly what ADMIN_PW_MARKER_STALE
    # records. Gating on `ADMIN_PW_ACTION != rotate` instead was wrong: the
    # fresh-bootstrap path also sets rotate, so the guard could never fire
    # there and every successful `bench new-site` was followed by a redundant
    # `set-admin-password`. If that extra command failed, the one-shot
    # configurator exited non-zero and every dependent service stayed blocked
    # on a site that had in fact been created correctly (Codex review, PR #106).
    if [ "${current_hash}" = "${stored_hash}" ] && [ "${ADMIN_PW_MARKER_STALE}" != "1" ]; then
        return 0
    fi

    if [ -n "${stored_hash}" ]; then
        echo "ERP_ADMIN_PASSWORD changed — rotating Administrator password..."
    else
        echo "No admin-password marker found — applying ERP_ADMIN_PASSWORD to Administrator..."
    fi

    bench --site "${SITE_NAME}" set-admin-password "${ADMIN_PASSWORD}"
    # Refresh the marker only after a successful reset (set -e aborts on failure).
    printf '%s' "${current_hash}" > "${ADMIN_PW_MARKER}"
    echo "Administrator password rotated."
}

# ── Provision the site encryption key BEFORE any worker starts ───────────
# Bug bd1458f5: "Failed to decrypt key User.Administrator.api_secret /
# Encryption key is invalid".
#
# Frappe NEVER provisions encryption_key at site creation. It is generated
# lazily on first use, in frappe/utils/password.py:
#
#     def get_encryption_key():
#         if "encryption_key" not in frappe.local.conf:
#             encryption_key = Fernet.generate_key().decode()
#             update_site_config("encryption_key", encryption_key)
#             frappe.local.conf.encryption_key = encryption_key
#         return frappe.local.conf.encryption_key
#
# The test is against frappe.local.conf — the process's IN-MEMORY conf, loaded
# once at startup. A gunicorn worker that started while the key was absent has
# it absent from its cached conf FOREVER, so it generates its OWN key and
# writes it over whatever another process wrote. With N workers plus a bench
# CLI process you get up to N+1 different keys written in sequence, each
# process encrypting and decrypting with its own.
#
# That is exactly the reported signature: `bench ... generate_keys` encrypts
# api_secret with the CLI's key, the FIRST request happens to land on a worker
# holding the same one and succeeds, and every subsequent request lands on a
# worker holding a different one and fails. Inspecting site_config.json
# afterwards shows an encryption_key present — the last one written, matching
# almost nobody — which is why "the key is not missing" was true and the
# mismatch was real at the same time.
#
# Writing the key to disk here removes the race by construction rather than by
# timing: after this runs, `"encryption_key" not in frappe.local.conf` is never
# true in any process, so the lazy branch above can never execute and no
# process can generate a competing key.
#
# SAFE TO DO WITHOUT LOCKING, and only here. docker-compose.yml makes
# exe-erp-configurator a one-shot service that every other service waits on:
#   exe-erp            depends_on exe-erp-configurator (service_completed_successfully)
#   websocket/queue/scheduler/nginx  depend_on exe-erp (service_healthy)
# so this runs to completion before any worker exists. It is additionally
# gated on ADMIN_PASSWORD, the same discriminator this file already uses to
# mean "this is the configurator, not a migrate-only service" — exe-erp runs
# this entrypoint too and must NOT write.
ensure_encryption_key() {
    # Migrate-only services (gunicorn/worker/scheduler) never receive
    # ADMIN_PASSWORD. Only the configurator provisions.
    if [ -z "${ADMIN_PASSWORD:-}" ]; then
        return 0
    fi

    local cfg="${SITE_DIR}/site_config.json"
    if [ ! -f "${cfg}" ]; then
        echo "WARNING: ${cfg} does not exist — cannot provision encryption_key."
        return 0
    fi

    # Idempotent: a site that already has a key keeps it. Rotating it would
    # make every already-encrypted value undecryptable, which is a far worse
    # outage than the one this prevents.
    local out rc=0
    out="$("${FRAPPE_BENCH}/env/bin/python" - "${cfg}" 2>&1 <<'PY'
import json, os, sys, tempfile

cfg = sys.argv[1]
with open(cfg) as fh:
    conf = json.load(fh)

if conf.get("encryption_key"):
    print("EXE_ENCKEY:PRESENT")
    raise SystemExit(0)

from cryptography.fernet import Fernet

conf["encryption_key"] = Fernet.generate_key().decode()

# Atomic replace: a torn site_config.json would take the whole site down, and
# this file also carries db_name/db_password.
d = os.path.dirname(cfg) or "."
fd, tmp = tempfile.mkstemp(dir=d, prefix=".site_config.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as fh:
        json.dump(conf, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, cfg)
except BaseException:
    try:
        os.unlink(tmp)
    except OSError:
        pass
    raise
print("EXE_ENCKEY:PROVISIONED")
PY
)" || rc=$?

    case "${out}" in
        *EXE_ENCKEY:PRESENT*)
            return 0
            ;;
        *EXE_ENCKEY:PROVISIONED*)
            echo "Provisioned site encryption_key (bug bd1458f5) — no worker can now generate a competing one."
            return 0
            ;;
    esac

    # No token: interpreter missing, unreadable config, import failure. Do NOT
    # fail the boot — this is the one-shot configurator that every other
    # service waits on with service_completed_successfully, so exiting non-zero
    # here takes the ENTIRE stack down. Frappe still works without this; it
    # just falls back to the lazy generation this exists to prevent. Same
    # availability lesson as bug 593fe59f.
    echo "WARNING: could not provision encryption_key (rc=${rc}). The site will"
    echo "         fall back to Frappe's lazy generation, which can produce a"
    echo "         per-process key mismatch (bug bd1458f5). Details:"
    printf '%s\n' "${out}" | sed 's/^/         /'
    return 0
}

# ── Main ─────────────────────────────────────────────────────
main() {
    wait_for_db

    if [ -n "${REDIS_CACHE}" ]; then
        wait_for_redis "${REDIS_CACHE}"
    fi

    configure_site_config

    # Restore prebuilt assets if the sites volume shadowed them (bug 29a22993).
    restore_prebuilt_assets

    # Decide create/repair vs migrate based on ACTUAL install state, not just
    # directory existence. A dir can exist from a `bench new-site` that ran but
    # whose `install-app erpnext` then failed — that site must be repaired, not
    # migrated (migrating a half-installed site leaves desk/data broken while
    # ping still passes).
    local erpnext_installed=0
    if is_erpnext_installed; then
        erpnext_installed=1
    fi

    # Decide the admin-password verdict BEFORE any migration runs (bug 593fe59f):
    # a lint failure must cost seconds, never a re-run of multi-minute migrations
    # on every restart cycle.
    admin_password_preflight "${erpnext_installed}"

    if [ "${erpnext_installed}" != "1" ]; then
        if [ -d "${SITE_DIR}" ]; then
            echo "Site dir exists but erpnext is NOT installed — running create/repair."
        fi
        # Validate admin password on the create/repair path (site bootstrap).
        validate_admin_password
        create_site
    else
        run_migrations
    fi

    # Provision the site encryption key before any worker can lazily generate a
    # competing one (bug bd1458f5). After the branch above, site_config.json
    # exists on both paths.
    ensure_encryption_key

    # Apply a changed ERP_ADMIN_PASSWORD to the live Administrator (bug 43854b31).
    # Only meaningful where the password is provided (the configurator service);
    # migrate-only services (gunicorn/worker/scheduler) don't pass ADMIN_PASSWORD,
    # so skip rotation there rather than fail validation.
    if [ -n "${ADMIN_PASSWORD:-}" ]; then
        rotate_admin_password_if_changed
    fi

    # Write currentsite.txt so Frappe knows the default site
    echo "${SITE_NAME}" > "${SITES_DIR}/currentsite.txt"

    # Flush the Redis asset map whenever assets were rewritten (bug 3938ac3d).
    # Frappe caches the resolved bundle filenames in Redis. Without this flush
    # the served HTML keeps advertising a THIRD generation of content hashes —
    # neither the volume's nor the image's — and every reference 404s. Observed
    # live 2026-08-10: 11 CSS bundles unreachable, desk rendered unstyled.
    if [ "${ASSETS_REFRESHED}" = "1" ]; then
        echo "Assets were refreshed — flushing Frappe's cache DB..."
        flush_frappe_cache
    fi

    # ── Configure GoTrue SSO (if GOTRUE_URL or GOTRUE_EXTERNAL_URL set) ──
    # Enables single sign-on across exe-crm, exe-wiki, and exe-erp.
    # The exe_auth module reads these from site_config.json.
    # GOTRUE_URL alone (internal address) or GOTRUE_EXTERNAL_URL alone (public
    # redirect target) is enough to need the config written — handle either.
    if [ -n "${GOTRUE_URL:-}" ] || [ -n "${GOTRUE_EXTERNAL_URL:-}" ]; then
        echo "Configuring GoTrue SSO..."
        local site_config="${SITE_DIR}/site_config.json"
        if [ -f "${site_config}" ]; then
            # Use Python with os.environ to avoid shell injection via variable values
            SITE_CONFIG_PATH="${site_config}" python3 -c "
import json, sys, os
try:
    config_path = os.environ['SITE_CONFIG_PATH']
    with open(config_path) as f:
        config = json.load(f)
    config['gotrue_url'] = os.environ.get('GOTRUE_URL', '')
    # Public GoTrue URL for browser SSO redirects (https://auth.<customer-domain>).
    # Distinct from gotrue_url (internal service address). Only write when set so
    # login.py:get_exe_auth_url() reads the customer's own auth domain instead of
    # falling back to host-derivation or the auth.askexe.com default (bug effc3a14).
    _gotrue_external = os.environ.get('GOTRUE_EXTERNAL_URL', '')
    if _gotrue_external:
        config['gotrue_external_url'] = _gotrue_external
    _admin_token = os.environ.get('EXE_ERP_ADMIN_TOKEN', '') or os.environ.get('EXE_ADMIN_TOKEN', '')
    if _admin_token:
        config['exe_admin_token'] = _admin_token
    _gotrue_admin_token = os.environ.get('GOTRUE_ADMIN_TOKEN', '')
    if _gotrue_admin_token:
        config['gotrue_admin_token'] = _gotrue_admin_token
    # ── Unified permissions (bug 842ede7c) ──────────────────────────────
    # exe_org_id activates managed/centralized role enforcement in
    # exe_auth/exe_perms.py. Without it, roles stay Frappe-local (enforcement
    # is inert) OR, if claims are stamped, ORG_DENY_UNRESOLVED denies+disables
    # every user. Wire it from the deployment so enforcement is actually live.
    _exe_org_id = os.environ.get('EXE_ORG_ID', '')
    if _exe_org_id:
        config['exe_org_id'] = _exe_org_id
    # ── SSO auto-provisioning allowlist (bug b6f1cd7d) ───────────────────
    # First SSO login is fail-closed until the tenant declares which email
    # domains may auto-provision (api.py:_assert_provisioning_allowed).
    # ALLOWED_EMAIL_DOMAINS is a CSV; write it as a JSON list so the
    # membership check (email_domain not in allowed_domains) is exact.
    _allowed_domains_raw = os.environ.get('ALLOWED_EMAIL_DOMAINS', '')
    if _allowed_domains_raw:
        config['allowed_email_domains'] = [
            d.strip().lower() for d in _allowed_domains_raw.split(',') if d.strip()
        ]
    # Explicit single-tenant opt-in: trust every GoTrue user's domain.
    _allow_all = os.environ.get('GOTRUE_ALLOW_ALL_DOMAINS', '').strip().lower()
    if _allow_all in ('1', 'true', 'yes', 'on'):
        config['gotrue_allow_all_domains'] = True
    # ── SSO callback CSRF state (bug adf77179) ───────────────────────────
    # gotrue_require_callback_state defaults TRUE (secure) in api.py — the auth
    # domain MUST echo the `state` nonce. Only written here when an operator
    # explicitly sets it (mid-rollout escape hatch); absence preserves the
    # secure default. Never silently weaken CSRF.
    _require_state = os.environ.get('GOTRUE_REQUIRE_CALLBACK_STATE', '').strip().lower()
    if _require_state in ('0', 'false', 'no', 'off'):
        config['gotrue_require_callback_state'] = False
    elif _require_state in ('1', 'true', 'yes', 'on'):
        config['gotrue_require_callback_state'] = True
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print('GoTrue SSO configured in site_config.json')
except Exception as e:
    print(f'Warning: Could not configure GoTrue — {e}', file=sys.stderr)
"
        fi
    fi

    # Add bench virtualenv to PATH so gunicorn/bench/python resolve
    export PATH="/home/frappe/frappe-bench/env/bin:${PATH}"

    # Frappe reads assets/assets.json from CWD, not SITES_PATH.
    # Symlink bench/assets → bench/sites/assets so bundled_asset() works.
    ln -sf "${SITES_DIR}/assets" "${FRAPPE_BENCH}/assets"

    # Ensure log directories exist at ALL locations Frappe might look:
    # 1. /home/frappe/logs (global fallback)
    # 2. sites/<site>/logs (SITES_PATH-based)
    # 3. <bench>/<site>/logs (CWD-based — Frappe logging uses this)
    mkdir -p /home/frappe/logs
    for site_dir in "${SITES_DIR}"/*/; do
        [ -d "${site_dir}" ] && mkdir -p "${site_dir}/logs"
    done
    # Frappe's logging resolves log path relative to CWD, not SITES_PATH
    [ -d "${SITES_DIR}/${SITE_NAME}" ] && mkdir -p "${FRAPPE_BENCH}/${SITE_NAME}/logs"

    # Set SITES_PATH so Frappe finds sites at the absolute path.
    export SITES_PATH="${SITES_DIR}"

    # Hand off to the command (gunicorn, worker, scheduler, etc.)
    exec "$@"
}

# Sourcing this file with EXE_ERP_ENTRYPOINT_NO_MAIN=1 loads the functions
# without booting, so they can be exercised directly by tests.
if [ -z "${EXE_ERP_ENTRYPOINT_NO_MAIN:-}" ]; then
    main "$@"
fi
