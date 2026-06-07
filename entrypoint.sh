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

# ── First boot: create site ──────────────────────────────────
create_site() {
    echo "First boot — creating site: ${SITE_NAME}"
    bench new-site "${SITE_NAME}" \
        --db-type postgres \
        --db-host "${DB_HOST}" \
        --db-port "${DB_PORT:-5432}" \
        --db-name "${DB_NAME:-exe_erp}" \
        --db-password "${DB_PASSWORD}" \
        --admin-password "${ADMIN_PASSWORD:-admin}" \
        --no-mariadb-socket

    bench --site "${SITE_NAME}" install-app erpnext
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

    if [ ! -d "${SITE_DIR}" ]; then
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
            # Use Python to safely merge GoTrue config into site_config.json
            python3 -c "
import json, sys
try:
    with open('${site_config}') as f:
        config = json.load(f)
    config['gotrue_url'] = '${GOTRUE_URL}'
    if '${EXE_ADMIN_TOKEN:-}':
        config['exe_admin_token'] = '${EXE_ADMIN_TOKEN}'
    with open('${site_config}', 'w') as f:
        json.dump(config, f, indent=2)
    print('GoTrue SSO configured in site_config.json')
except Exception as e:
    print(f'Warning: Could not configure GoTrue — {e}', file=sys.stderr)
"
        fi
    fi

    # Hand off to the command (gunicorn, worker, scheduler, etc.)
    exec "$@"
}

main "$@"
