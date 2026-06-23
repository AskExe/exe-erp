<div align="center" markdown="1">
	<h1>Exe ERP</h1>

 **Self-hosted ERP for the Exe stack — a hardened fork of Frappe/ERPNext**
</div>

<div align="center">
    <a href="https://askexe.com">Website</a>
    -
    <a href="https://askexe.com/docs/erp">Documentation</a>
</div>

## About

Exe ERP is a hard fork of [Frappe Framework](https://github.com/frappe/frappe) and
[ERPNext](https://github.com/frappe/erpnext), packaged to run as part of the Exe
stack alongside exe-db, exe-crm, exe-wiki, and exe-gateway. It ships with Exe
single sign-on (GoTrue), cross-database event bridging to `exedb`, and the Exe
Foundry Bold design system.

> This README documents the **production / operator** workflow for Exe ERP. It is
> intentionally not the upstream Frappe developer guide — do **not** follow generic
> Frappe Docker instructions or use upstream default credentials.

## Production deployment

Exe ERP is deployed as containers via `docker-compose.yml`, connecting to the
shared `exe-db` (PostgreSQL) and `redis` services on the `exe-net` network. The
recommended path is through the Exe stack updater, which performs preflight
validation of required environment variables before deploying.

### 1. Configure environment

Copy the example file and set every value. There are no safe defaults for these:

```
cp .env.example .env
```

Required variables (enforced by `stack.release.json` preflight and by
`docker-compose.yml`; the stack will refuse to deploy if any is missing):

| Variable | Description |
| --- | --- |
| `SITE_NAME` | Your ERP site domain, e.g. `erp.acme.com`. No localhost/AskExe default in production. |
| `POSTGRES_PASSWORD` | Password for the `exe-db` PostgreSQL user (12+ chars, mixed case, numbers, special). |
| `ERP_ADMIN_PASSWORD` | Initial Administrator password (12+ chars, must include a special character; common defaults are rejected at boot). |

Optional integration variables (GoTrue SSO, Exe Bridge, monitor/error
forwarding, Sentry) are documented inline in `.env.example`.

### 2. Deploy

```
docker compose up -d
```

On first boot the configurator container creates the site for `SITE_NAME`,
installs the ERPNext app, and writes `common_site_config.json`. Subsequent boots
run `bench migrate`. The backend, websocket, queue worker, and scheduler each
have healthchecks so silent failures surface during stack updates.

### 3. Run migrations (during upgrades)

The stack updater runs migrations automatically for the configured site:

```
docker compose exec -T exe-erp bench --site "$SITE_NAME" migrate
```

## Security notes for operators

- **No default credentials.** Unlike upstream Frappe, Exe ERP does **not** ship
  with a working `Administrator` / `admin` login. The Administrator password is
  taken from `ERP_ADMIN_PASSWORD`; the entrypoint rejects short passwords,
  passwords without a special character, and common defaults
  (`admin`, `password`, `changeme`, `administrator`, …).
- **Set a real domain.** `SITE_NAME` must be your own domain. Do not deploy with
  `erp.localhost` (development only) or any AskExe-owned host in production.
- **Single sign-on.** When `GOTRUE_URL` is configured, the login page resolves
  your SSO tenant from the request host (e.g. `erp.acme.com` → `auth.acme.com`).
  Override with `EXE_AUTH_URL` (full URL) or `AUTH_DOMAIN` (bare host) if your
  auth domain differs.
- **Rotate the admin password** after first login and create per-user accounts
  with appropriate roles rather than sharing the Administrator account.
- **Bind ports locally.** The compose file binds the API and websocket ports to
  `127.0.0.1`; expose them only behind your own TLS-terminating reverse proxy.

## Local development

For local development only, you may set `SITE_NAME=erp.localhost`. This is never
appropriate for a production or customer install.

## Attribution & licensing

Exe ERP is derived from Frappe Framework and ERPNext. See [`attributions.md`](attributions.md),
[`LICENSE`](LICENSE), and [`LICENSE.frappe`](LICENSE.frappe) for the full
attribution and license terms.
</content>
</invoke>
