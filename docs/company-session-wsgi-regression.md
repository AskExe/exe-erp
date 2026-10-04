# Disposable company ERP WSGI regression

Run the bounded local fixture:

```sh
ERP_NATIVE_EXECUTE=true ERP_NATIVE_HTTP=true node scripts/run-company-session-native.mjs
```

This extends the same two-site fixture in `company-session-native-regression.md`.
Its already-local ARM64 image inputs, complete readonly source mounts, private
PostgreSQL/Redis, nonroot ERP process, owned cleanup and storage/raw/memory limits
are unchanged. Setup remains 300 seconds, both WSGI test processes share one
120-second test deadline, and cleanup has 60 seconds. No new dependencies, web
worker, scheduler, provider, published port or production host change is needed.

The complete shipped `frappe.app.application` runs through Werkzeug's WSGI client;
no request handler, `private_call`, `native_read_current`, DB, model or ACL method
is replaced. Each site has its own process and actual startup-loaded immutable
registration, private 0600 credential/bindings files and distinct native database.
The application makes real HTTP requests through its private transport to one
bounded 127.0.0.1 authority server in that same process. That server validates
its exact route, credential, Host, body and finite fixture token before replying.
It stops and closes on every outcome. Company mode is explicitly enabled for
this fixture; production's default-off configuration is unchanged. Browser mode
remains off.

Eight registered tests (six A, two B) cover real owner/member reads and row
restrictions; reciprocal foreign principals and wrong company/Host denial;
Origin and identity-header refusals before introspection; central revocation on
the final post-read introspection; native role revocation and User Permission
changes between the first native read and second native read. Changes use real
ORM/SQL commits from the authority thread's separate connection after it receives
the second introspection, without patching any application function. Negative
responses must contain only `error: unavailable`, never previously read data.
Actual WSGI response iteration/close invokes the shipped after-response cleanup.

Central registration/session envelopes and revocation are explicitly controlled
loopback authority responses. This does not qualify genuine Core/GoTrue session
issuance, browser cookies/Desk, API/MCP writes, jobs, deployment or customer state.
The local WSGI run passed six A-plane tests in 11.866 seconds and two B-plane
tests in 1.914 seconds, with unchanged source pre/post hashes and verified owned
container/volume/network cleanup. Full numbered stdout/stderr and result records
are retained outside the checkout. This is separate from the native five-case
result; that earlier result is not adopted as WSGI evidence.
