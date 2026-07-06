# Exe ERP

Exe ERP is AskExe's self-hosted ERP service: a hardened fork of Frappe Framework + ERPNext packaged for the Exe stack. It provides ERPNext capabilities (accounting, sales, purchasing, inventory, manufacturing, projects, assets, quality, HR) with AskExe additions for GoTrue SSO, admin-token access, event emission into the Company Brain, monitoring, and config-driven branding.

This repo is for the AskExe-maintained stack service. Do **not** follow generic upstream Frappe Docker defaults for production.

## Architecture and data flow

```text
Browser / reverse proxy
        |
        v
exe-erp (Gunicorn web/API, 127.0.0.1:8069 -> 8000)
        |-- exe-erp-websocket (Socket.io, 127.0.0.1:9069 -> 9000)
        |-- exe-erp-queue (RQ worker: default, short, long)
        |-- exe-erp-scheduler (scheduled jobs)
        `-- exe-erp-configurator (first-boot site setup)

Shared stack dependencies on exe-net:
  exe-db PostgreSQL server
    - database `exe_erp`: Frappe/ERPNext DocType tables managed by bench migrations
    - database `exedb`: Company Brain landing pad; optional raw.raw_events writes via EXE_BRIDGE_DATABASE_URL
  redis
    - db 3 cache, db 4 queue, db 5 socket.io
  GoTrue / auth service for SSO when configured
```

Important invariants:

- Exe ERP uses its own PostgreSQL database, `exe_erp`; it does **not** store DocType tables in `exedb`.
- Frappe owns its schema through DocTypes and `bench migrate`; do not manage ERP tables with Prisma.
- `exe_bridge` emits selected ERP document events to `raw.raw_events` in `exedb` when explicitly configured; it fails closed when unset and must not block ERP operations.
- Production containers use pinned GHCR image digests, named volume `exe-erp-sites`, non-root user, local-only port bindings, dropped Linux capabilities, and healthchecks.

## Key directories and files

| Path | Purpose |
| --- | --- |
| `frappe/` | AskExe fork of Frappe Framework. |
| `apps/erpnext/` | AskExe fork of ERPNext. |
| `apps/erpnext/erpnext/exe_auth/` | GoTrue login and admin-token API endpoints. |
| `apps/erpnext/erpnext/exe_bridge/` | Cross-database event bridge, tracing, telemetry, Prometheus metrics. |
| `apps/erpnext/erpnext/exe_monitor/` | Health and error forwarding hooks. |
| `apps/erpnext/erpnext/exe_setup/` | AskExe bootstrap roles, departments, workflows, user provisioning. |
| `apps/erpnext/erpnext/exe_templates/` | AskExe email, print, and naming templates. |
| `Dockerfile` | Multi-stage production image build. |
| `docker-compose.yml` | Production compose services for the stack. |
| `entrypoint.sh` | Site creation, config writing, migrations, and startup guardrails. |
| `.env.example` | Environment contract for local or stack deployment. |
| `stack.release.json` | Stack release contract: image digests, health checks, migrations, env vars. |
| `CONTRACTS.md` | Cross-stack operational contracts. |
| `exe/ARCHITECTURE.md` | Deeper architecture notes for agents and maintainers. |

## Environment variables

Copy `.env.example` to `.env` for local compose work. Never commit `.env` or secrets.

Required:

| Variable | Meaning |
| --- | --- |
| `SITE_NAME` | Frappe site name/domain. Use a real domain in production; `erp.localhost` is local-only. |
| `POSTGRES_PASSWORD` | Password for the ERP database user on the shared PostgreSQL service. |
| `ERP_ADMIN_PASSWORD` | Initial Frappe Administrator password. The entrypoint rejects weak/common defaults. |

Common optional variables:

| Variable | Meaning |
| --- | --- |
| `EXE_ERP_ADMIN_TOKEN` | Preferred shared secret for exe-os daemon/admin access. |
| `EXE_ADMIN_TOKEN` | Legacy fallback for the same admin token. |
| `GOTRUE_URL`, `GOTRUE_ADMIN_TOKEN` | SSO integration. Login can derive auth domain from host or use `EXE_AUTH_URL` / `AUTH_DOMAIN`. |
| `EXE_BRIDGE_DATABASE_URL` | DSN for writing ERP events to `exedb.raw.raw_events`; unset disables bridge emission visibly. |
| `MONITOR_ERROR_URL`, `MONITOR_API_KEY`, `ERROR_REPORTING_ENABLED` | Error forwarding to monitor hub. |
| `FRAPPE_SENTRY_DSN`, `ENABLE_SENTRY_DB_MONITORING`, `SENTRY_TRACING_SAMPLE_RATE`, `SENTRY_PROFILING_SAMPLE_RATE` | Sentry backend/frontend monitoring. |
| `EXE_APP_*` | Config-driven white-label metadata: title, publisher, description, email, license, source link, logo, color, support links. |

## Local development

The repo is large because it vendors the Frappe and ERPNext forks. For most AskExe work, use compose to run the same shape as production:

```bash
cp .env.example .env
# edit SITE_NAME=erp.localhost and set strong local passwords
# ensure shared services/network exist (exe-db, redis, exe-net)
docker compose --env-file .env up -d
```

Useful local commands:

```bash
# API health
docker compose exec -T exe-erp curl -fsS http://localhost:8000/api/method/ping

# Run migrations for the configured site
docker compose exec -T exe-erp bench --site "$SITE_NAME" migrate

# Open a bench shell / run bench commands
docker compose exec exe-erp bash
```

Node asset work uses the root `package.json` scripts:

```bash
yarn install
npm run build          # node esbuild
npm run production     # node esbuild --production
npm run watch          # node esbuild --watch
```

## Build and test

Primary CI is in `.github/workflows/ci-checks.yml` and runs on AskExe self-hosted Linux runners. It validates compose/stack contracts, lints Exe fork code, compiles Python modules, runs migration smoke checks, audits Python/Node dependencies, scans secrets, and performs a production Docker build.

Local checks worth running before handoff:

```bash
# Compose contract interpolation; requires required env vars in .env
docker compose --env-file .env -f docker-compose.yml config --quiet

# Stack release JSON is valid
python3 -m json.tool stack.release.json >/dev/null

# Exe fork Python syntax/lint subset
python3 -m compileall -q apps/erpnext/erpnext/exe_auth apps/erpnext/erpnext/exe_bridge apps/erpnext/erpnext/exe_monitor apps/erpnext/erpnext/exe_setup apps/erpnext/erpnext/exe_templates

# Production image build smoke (expensive)
docker build .
```

## Deployment notes

- Customer/production shipping should go through the Exe stack release path, not ad-hoc source builds on a VPS.
- `docker-compose.yml` and `stack.release.json` pin `ghcr.io/askexe/exe-erp` images by tag **and** SHA256 digest; do not replace with `:latest` or an unpinned tag.
- The stack expects external `exe-net`, `exe-db`, and `redis` services. ERP ports are bound to `127.0.0.1` and should be exposed only through a TLS-terminating reverse proxy or tunnel.
- First boot creates/configures the site through `exe-erp-configurator`; upgrades run `bench --site "$SITE_NAME" migrate`.
- Persistent site data lives in the named Docker volume `exe-erp-sites`; never remove volumes during an update.
- Health signals: `GET /api/method/ping`, websocket TCP on 9000, queue/scheduler process health, enhanced health at `GET /api/method/erpnext.exe_monitor.health.check`, metrics at `GET /api/method/erpnext.exe_bridge.metrics.get_metrics`.

## Operational and security gotchas

- No production defaults: set a real `SITE_NAME`, strong `POSTGRES_PASSWORD`, and strong `ERP_ADMIN_PASSWORD`.
- Do not publish PostgreSQL, Redis, or ERP container ports directly to the internet; terminate TLS at the reverse proxy/tunnel.
- Rotate the initial Administrator password after first login and use named users/roles.
- Keep GoTrue signup disabled for production tenants unless a specific onboarding flow requires it.
- `EXE_BRIDGE_DATABASE_URL` is intentionally explicit. If it is missing, ERP still works but event projection to the Company Brain is disabled.
- Do not merge `exe_erp` and `exedb`. The former is Frappe-owned operational ERP data; the latter is the shared Company Brain/raw landing pad.
- Do not overwrite customer-local data, site config, identities, behaviors, or procedures during updates.
- Do not introduce secrets, API keys, customer data, or hardcoded AskExe/customer domains into code. Branding must stay config-driven through `EXE_APP_*` variables.
- In ESM TypeScript areas, avoid `require()`; this repo also contains Frappe/Node runtime files where CommonJS may be intentional (for example socket.io health/runtime paths), so verify the target runtime before changing module style.

## Licensing and attribution

Exe ERP is derived from Frappe Framework and ERPNext. See [`LICENSE`](LICENSE), [`LICENSE.frappe`](LICENSE.frappe), and [`attributions.md`](attributions.md).
