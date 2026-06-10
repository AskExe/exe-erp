# Exe ERP — Stack Contracts

Living reference for environment variables, container conventions, and cross-stack compliance.

## Admin Token

| Standard Env Var | Legacy (accepted) | Description |
|---|---|---|
| `EXE_ERP_ADMIN_TOKEN` | `EXE_ADMIN_TOKEN` | Shared secret for exe-os daemon/MCP admin access |

The entrypoint reads `EXE_ERP_ADMIN_TOKEN` first, falling back to `EXE_ADMIN_TOKEN`.
Stored as `exe_admin_token` in Frappe's `site_config.json`. New deployments should use `EXE_ERP_ADMIN_TOKEN`.

## Container Hardening

| Requirement | Status | Evidence |
|---|---|---|
| DB ports not published | Compliant | Uses external exe-db, no DB service in compose |
| App binds to 127.0.0.1 | Compliant | `127.0.0.1:8069:8000`, `127.0.0.1:9069:9000` |
| No `:latest` tags | Compliant | `ghcr.io/askexe/exe-erp:v0.2.0-final3` |
| Named volumes | Compliant | `erp-sites` |
| Non-root user | Compliant | Runs as `frappe` user in Dockerfile |
| Healthcheck | Compliant | `curl -sf http://localhost:8000/api/method/ping` |

## GoTrue Policy

When `GOTRUE_URL` is configured:
- Signup MUST be disabled on the GoTrue instance
- `MAILER_AUTOCONFIRM` must be `false`
- JWT audience must match service expectations
- First-user auto-promotion only in `ERP_BOOTSTRAP_MODE=true` (disabled by default)

## Error Forwarding

Errors are forwarded to exe-monitor-hub via the `exe_bridge` tracing module:
- Sentry SDK captures exceptions
- `exe_bridge.events` writes trace events to `raw.raw_events` with `X-Trace-Id` propagation
- Controlled by `SENTRY_DSN` environment variable

## UI Section States

All major UI sections must implement:
- **loading** — data is being fetched
- **ready** — data loaded and displayed
- **empty** — no data exists (distinguish from error)
- **error** — fetch/operation failed with human-readable message and retry affordance
- **degraded** — partial function available, names what is unavailable

## Progress Events

Long-running operations (ERP setup, data import, bench migrations) must emit events with:

| Field | Required | Description |
|---|---|---|
| `operationId` | Yes | Unique ID for the operation (maps to Frappe task_id) |
| `phase` | Yes | Current phase name |
| `label` | Yes | Human-readable description |
| `status` | Yes | queued / running / blocked / degraded / succeeded / failed / cancelled |
| `updatedAt` | Yes | ISO timestamp |
| `current` / `total` | Optional | Only when accurately measurable |

Fake, timer-only, or cosmetic progress is forbidden.

## Cross-Repo Dependencies

| Dependency | Relationship |
|---|---|
| **exe-os** | Orchestration layer. Launches exe-erp via stack compose. |
| **exe-db** | Shared PostgreSQL instance (schema: `exe_erp`). |
| **exe-monitor-hub** | Receives error/degradation alerts via exe_bridge. |
| **Redis** | Shared Redis instance (databases 3/4/5 for cache/queue/socketio). |
