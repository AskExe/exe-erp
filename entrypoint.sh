#!/bin/bash
set -eo pipefail

# ──────────────────────────────────────────────────────────────
# Exe ERP — Container entrypoint
# Handles first-boot site creation + subsequent-boot migrations
# ──────────────────────────────────────────────────────────────

FRAPPE_BENCH="/home/frappe/frappe-bench"
SITES_DIR="${FRAPPE_BENCH}/sites"
SITE_NAME="${SITE_NAME:-erp.askexe.com}"
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
    echo "Site created and erpnext installed."
}

# ── Subsequent boot: run migrations ──────────────────────────
run_migrations() {
    echo "Existing site found — running migrations..."
    bench --site "${SITE_NAME}" migrate
    echo "Migrations complete."
}

# ── Main ─────────────────────────────────────────────────────
main() {
    wait_for_db

    if [ -n "${REDIS_CACHE}" ]; then
        wait_for_redis "${REDIS_CACHE}"
    fi

    configure_site_config

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

    # Write currentsite.txt so Frappe knows the default site
    echo "${SITE_NAME}" > "${SITES_DIR}/currentsite.txt"

    # ── Configure GoTrue SSO (if GOTRUE_URL is set) ─────────────
    # Enables single sign-on across exe-crm, exe-wiki, and exe-erp.
    # The exe_auth module reads these from site_config.json.
    if [ -n "${GOTRUE_URL:-}" ]; then
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
