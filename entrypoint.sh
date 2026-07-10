#!/bin/bash
set -eo pipefail

# ──────────────────────────────────────────────────────────────
# Exe ERP — Container entrypoint
# Handles first-boot site creation + subsequent-boot migrations
# ──────────────────────────────────────────────────────────────

FRAPPE_BENCH="/home/frappe/frappe-bench"
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
validate_admin_password() {
    local pw="${ADMIN_PASSWORD:-}"
    if [ -z "${pw}" ]; then
        echo "ERROR: ADMIN_PASSWORD (ERP_ADMIN_PASSWORD) is required but not set."
        echo "Set ERP_ADMIN_PASSWORD in your .env or docker-compose override."
        exit 1
    fi
    local len=${#pw}
    if [ "${len}" -lt 12 ]; then
        echo "ERROR: ADMIN_PASSWORD must be at least 12 characters (got ${len})."
        exit 1
    fi
    # Check against common weak defaults
    local weak
    for weak in admin password changeme admin123 password123 administrator; do
        if [ "${pw}" = "${weak}" ]; then
            echo "ERROR: ADMIN_PASSWORD cannot be a common default ('${weak}')."
            exit 1
        fi
    done
    # Require at least one special character
    if ! echo "${pw}" | grep -qP '[^a-zA-Z0-9]'; then
        echo "ERROR: ADMIN_PASSWORD must contain at least one special character."
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
# or empty. Idempotent: a no-op once assets are present.
ASSETS_BACKUP="/opt/exe-erp-assets"
restore_prebuilt_assets() {
    local assets_dir="${SITES_DIR}/assets"
    # Already populated (manifest present) → nothing to do.
    if [ -f "${assets_dir}/assets.json" ]; then
        return 0
    fi
    if [ ! -d "${ASSETS_BACKUP}" ] || [ ! -f "${ASSETS_BACKUP}/assets.json" ]; then
        echo "WARNING: no prebuilt asset backup at ${ASSETS_BACKUP}; skipping asset restore."
        return 0
    fi
    echo "Prebuilt assets missing under volume — restoring from ${ASSETS_BACKUP}..."
    mkdir -p "${assets_dir}"
    cp -a "${ASSETS_BACKUP}/." "${assets_dir}/"
    # Re-establish the frappe asset symlink → live app public dir (the baked
    # symlink target is outside the volume and resolves at runtime).
    rm -f "${assets_dir}/frappe"
    ln -sf "${FRAPPE_BENCH}/apps/frappe/frappe/public" "${assets_dir}/frappe"
    echo "Prebuilt assets restored into ${assets_dir}."
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
    printf '%s' "${1}:${SITE_NAME}" | sha256sum | cut -d' ' -f1
}

rotate_admin_password_if_changed() {
    local current_hash stored_hash=""
    current_hash="$(admin_password_hash "${ADMIN_PASSWORD}")"

    if [ -f "${ADMIN_PW_MARKER}" ]; then
        stored_hash="$(cat "${ADMIN_PW_MARKER}" 2>/dev/null || true)"
    fi

    if [ "${current_hash}" = "${stored_hash}" ]; then
        # Unchanged — do not reset every boot.
        return 0
    fi

    if [ -n "${stored_hash}" ]; then
        echo "ERP_ADMIN_PASSWORD changed — rotating Administrator password..."
    else
        echo "No admin-password marker found — applying ERP_ADMIN_PASSWORD to Administrator..."
    fi

    # Validate the new password before applying (same rules as first boot).
    validate_admin_password
    bench --site "${SITE_NAME}" set-admin-password "${ADMIN_PASSWORD}"
    # Refresh the marker only after a successful reset (set -e aborts on failure).
    printf '%s' "${current_hash}" > "${ADMIN_PW_MARKER}"
    echo "Administrator password rotated."
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
    if ! is_erpnext_installed; then
        if [ -d "${SITE_DIR}" ]; then
            echo "Site dir exists but erpnext is NOT installed — running create/repair."
        fi
        # Validate admin password on the create/repair path (site bootstrap).
        validate_admin_password
        create_site
    else
        run_migrations
    fi

    # Apply a changed ERP_ADMIN_PASSWORD to the live Administrator (bug 43854b31).
    # Only meaningful where the password is provided (the configurator service);
    # migrate-only services (gunicorn/worker/scheduler) don't pass ADMIN_PASSWORD,
    # so skip rotation there rather than fail validation.
    if [ -n "${ADMIN_PASSWORD:-}" ]; then
        rotate_admin_password_if_changed
    fi

    # Write currentsite.txt so Frappe knows the default site
    echo "${SITE_NAME}" > "${SITES_DIR}/currentsite.txt"

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

main "$@"
