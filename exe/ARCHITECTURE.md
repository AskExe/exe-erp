# exe-erp — System Architecture

> Employees: read this before every task. Update it when you change system structure.
> Last updated: 2026-06-06

## Overview

Exe ERP is a **hard fork of Frappe Framework + ERPNext**, rebranded as "Exe ERP" with AskExe customizations (GoTrue SSO, admin token auth). It provides full-suite ERP capabilities: accounting, sales, purchasing, inventory, manufacturing, projects, assets, quality management.

**Key difference from other exe services:** exe-erp runs its own PostgreSQL database (`exe_erp`) via Frappe's ORM, NOT the shared `exedb` instance used by exe-db/exe-crm/exe-wiki/exe-gateway. This is by design — Frappe manages its own schema via DocType metadata + bench migrations.

## Key Components

| Component | Port | Purpose |
|-----------|------|---------|
| exe-erp (gunicorn) | 8069→8000 | REST API v2 + web UI |
| exe-erp-websocket | 9069→9000 | Socket.io realtime events |
| exe-erp-queue | — | RQ background workers (default/short/long) |
| exe-erp-scheduler | — | Cron-like scheduled tasks |
| exe-erp-configurator | — | First-boot site creation (runs once) |

**External dependencies:**
- `exe-db` (PostgreSQL) — database server (but uses its OWN database `exe_erp`)
- `redis` — cache (db 3), queue (db 4), socketio (db 5)
- GoTrue — SSO authentication (via `gotrue_url` in site_config)

## Data Flow

```
                    ┌─────────────┐
                    │   exe-db    │ (PostgreSQL server)
                    │             │
   ┌────────────────┼─────────────┼────────────────────┐
   │  database:     │  database:  │  database:          │
   │  exedb         │  exe_erp    │  exe_erp            │
   │  (schemas:     │  (public    │  (same server,      │
   │   graph, wiki, │   schema,   │   different DB)     │
   │   gateway,     │   Frappe    │                     │
   │   crm, raw,    │   DocTypes) │                     │
   │   billing)     │             │                     │
   └────────────────┴─────────────┴─────────────────────┘
         ↑                  ↑
   exe-crm, wiki,      exe-erp
   gateway, os          (Frappe ORM)
```

### Frappe DocType tables (in `exe_erp` database):
- `tabDocType`, `tabDocField`, `tabDocPerm` — metadata
- `tabSeries` — auto-increment naming
- `tabSessions`, `tabSingles`, `__Auth` — system
- `tabFile`, `tabDefaultValue` — files/defaults
- 100+ module tables for: accounts, selling, buying, stock, manufacturing, etc.

### API endpoints:
- `GET/POST/PUT/DELETE /api/v2/document/{doctype}[/{name}]` — CRUD
- `/api/method/{method}` — RPC calls
- `/api/method/erpnext.exe_auth.gotrue_login` — GoTrue SSO
- `/api/method/erpnext.exe_auth.admin_token` — exe-os daemon access

## Invariants

1. **Separate database** — exe-erp uses `exe_erp` database, NOT `exedb`. Never merge.
2. **Frappe manages its own schema** — DocType metadata drives table creation. Never use Prisma for exe-erp tables.
3. **GoTrue SSO is shared** — Same GoTrue instance authenticates both exe-erp and exe-crm users.
4. **Redis databases are partitioned** — db 3/4/5 for exe-erp. Other services use db 0/1/2.
5. **exe-net network is shared** — All services communicate on the same Docker network.
6. **Sentry is the primary error tracker** — `FRAPPE_SENTRY_DSN` for backend, `@sentry/browser` for frontend.

## Dependencies

| If I change... | Also affected... |
|----------------|-----------------|
| GoTrue config | exe-crm, exe-wiki auth |
| Redis db numbers | exe-os daemon, other services using same Redis |
| PostgreSQL server (exe-db) | All services |
| exe-net network | All Docker services |
| Port 8069 | Reverse proxy (Caddy/nginx) |
| admin_token secret | exe-os daemon API access |

## Integration Status — Stack Readiness

### Current State (2026-06-14) — PRODUCTION DEPLOYED
- ✅ Docker image `v0.2.0-final7` — live at erp.askexe.com via Cloudflare tunnel
- ✅ Full CRUD REST API verified — 28/28 tests pass (Customer, Item, Supplier, Sales Invoice)
- ✅ GoTrue SSO integration — login + admin token endpoints, domain allowlist support
- ✅ Admin tokens wired — EXE_ERP_ADMIN_TOKEN + GOTRUE_ADMIN_TOKEN in site_config
- ✅ Exe branding complete — email footer, website footer, help menu, login page, about dialog
- ✅ Master data bootstrapped — Company (Exe AI), territories, customer groups, UOMs, price lists
- ✅ Sentry error tracking — backend + frontend
- ✅ `stack.release.json` v0.2.0-final7 — participating in exe-os stack management
- ✅ `erp` schema in exe-db — bridge tables ready (customer_bridge, item_bridge, financial_snapshot, event_log)
- ✅ `exe_bridge` app — doc_events hooks emit to raw.raw_events via cross-DB psycopg2 connection
- ✅ Source registry entries — 32 ERP event types registered for projection workers
- ✅ exe-monitor integration — error forwarding via `after_request_error` hook
- ✅ Prometheus `/metrics` endpoint — `GET /api/method/erpnext.exe_bridge.metrics.get_metrics`
- ✅ Enhanced health endpoint — `GET /api/method/erpnext.exe_monitor.health.check`
- ✅ P0/P1 audit remediation — entrypoint safety, shell injection fix, auth hardening
- ⚠️ Projection workers — not yet built (Phase 3: process source='erp' events)
- ⚠️ TUI/Desktop monitoring — not yet integrated (Phase 4)

### Phase 1 Components (Built 2026-06-06)

**exe_bridge** (`apps/erpnext/erpnext/exe_bridge/`):
- `connection.py` — Thread-local psycopg2 pool to exedb, lazy-init, graceful fallback
- `events.py` — Doc event handlers (after_insert, on_update, on_submit, on_cancel, on_trash)
- `metrics.py` — Prometheus exposition format endpoint (users, revenue, queues, errors, DB size)
- Allowlist: 30 business-relevant doctypes (Customer, Sales Order, Item, Employee, etc.)
- Safety: fire-and-forget (never blocks ERP), 64KB payload cap, excluded fields (passwords, internal)

**exe_monitor** (`apps/erpnext/erpnext/exe_monitor/`):
- `error_reporter.py` — Forward 5xx errors to exe-monitor-hub (rate-limited 30/min)
- `health.py` — Component-level health (database, redis_cache, redis_queue, scheduler, bridge)

**exe-db changes** (`~/exe-db/`):
- `init-schemas.sql` — Added `erp` schema
- `init-roles.sh` — Added `erp` to search_path
- `prisma/schema.prisma` — ErpCustomerBridge, ErpItemBridge, ErpFinancialSnapshot, ErpEventLog models
- `prisma/migrations/20260606120000_erp_bridge_tables/` — Tables + 32 source registry entries

### Integration Roadmap
- **Phase 2 (Telemetry):** exe-monitor scraping, alert thresholds, OpenTelemetry tracing
- **Phase 3 (Data Bridge):** Projection workers for source='erp' events, customer sync
- **Phase 4 (Stack Integration):** exe-os stack manifest (add to stack-update), daemon health checks
- **Deferred:** TUI/Desktop ERP monitoring tab — wait for Slack-like redesign (Mode 2/3 rearchitecture, 2026-06-06 founder decision)
