# Disposable native ERP ACL regression

Run from this repository with Docker available:

```sh
ERP_NATIVE_EXECUTE=true node scripts/run-company-session-native.mjs
```

The runner provisions two fresh PostgreSQL-backed ERPnext sites, runs five native
ACL tests, and removes only its returned container IDs, associated anonymous
volumes, and random internal network. It verifies their absence on every outcome.
It never uses production Compose, shared site storage, published ports, workers,
web servers, providers, dependency installation, or image pulls.

## Prerequisites

The following ARM64 images must already exist locally:

- ERP dependency image: `sha256:b386cb66e7352036bc374f6d29475f74018c5ee6283a1fd8488a6dad019821e2`
- PostgreSQL: `pgvector/pgvector@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc`
- Redis: `redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99`

The ERP image supplies Python and dependencies. Both complete current Frappe and
`apps/erpnext` source trees are mounted read-only; an adapter-only overlay onto a
mismatched framework is insufficient. The runner requires at least 20 GiB free
space and limits fixture allocation to 1 GiB, including source allocation,
448 MiB of tmpfs capacity, database data and retained output. Container memory
limits are ERP 1 GiB, PostgreSQL 512 MiB and Redis 128 MiB.

Setup has a shared 300-second deadline; each new-site command is clamped to that
deadline and at most 140 seconds. Tests have 120 seconds and owned cleanup has
60 seconds. Complete command output is limited to 1 MiB; budget failure still
runs cleanup and remains a failing result.

## Coverage

The five tests use real Frappe Document, permission, PostgreSQL and Redis code
through shipped `frappe.company_session.native_read_current`:

- Owner reads and member Customer list/direct-row restrictions.
- Separate site databases and foreign company/principal/document denial.
- Current native role revocation after warmed permission state.
- Current disabled User denial.
- Current User Permission restrictions despite a deliberately stale real Redis cache.

Three distinct nonadmin Users represent A-owner, A-member and B-owner.
Administrator creates or updates fixture records only; business reads execute
as the mapped User. Fresh sites receive the required Customer Group and
Territory roots through the real ORM, without running an interactive setup wizard.
Tests run from the native sites directory, as required by Frappe's site logging.
No database, model, permission or cache methods are replaced.

The local run passed all five tests in 14.696 seconds with unchanged source
pre/post hashes and successful container, volume and network cleanup. The
runner writes full numbered stdout/stderr files and `result.json` into a fresh
private temporary output directory and prints that path. Check both the test
result and cleanup status; valid test output alone is insufficient.

## Limits of this evidence

Current-company envelopes are explicitly controlled fixture values, not genuine
GoTrue or broker responses. This regression does not establish Auth handoff,
central membership revocation, HTTP/WSGI or browser cookies, Desk behavior,
write permissions, jobs, API/MCP scope, or deployment readiness. PostgreSQL is
covered; MariaDB is not. Separate integration evidence is required for those
paths. Original setup and compatibility diagnostic records are retained outside
product documentation.
