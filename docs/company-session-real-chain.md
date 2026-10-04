# Genuine Auth/Core to ERP native chain

This direct-HTTP/WSGI fixture passed the nine-group run described below. It leaves the existing Auth eight groups, Wiki nine groups and ERP eight WSGI tests unchanged.

Run from the Auth fixture branch with `AUTH_COMPANY_CORE_ROOT` pointing to the reviewed Core checkout and `AUTH_COMPANY_ERP_ROOT` pointing to this ERP fixture checkout. The Auth entry is `scripts/test-erp-real-chain.mjs`; the ERP helper is `scripts/real-issuer-chain-fixture.mjs`. The command is:

```sh
AUTH_COMPANY_CORE_ROOT=/path/to/reviewed-core AUTH_COMPANY_ERP_ROOT=/path/to/erp-chain node --max-old-space-size=512 scripts/test-erp-real-chain.mjs
```

The command builds exact Auth145 a3bff84 and Core41 d622048 sources using the same cached pinned bases and stock build paths as the passing issuer/Wiki chain. ERP production sources remain ERP134 e04ff67. It requires the cached ERP dependency image b386cb66, pinned PostgreSQL and Redis images, Docker, and 20 GiB free storage. No pulls, shared dependency writes, workers, public ERP ports or native image build are needed.

The owned ERP network is internal. Only the successful owned Core-native container is attached; its actual address on that exact network supplies the fixed numeric private endpoint. Neither DNS nor an operator-supplied host is authority. A 0600 per-company address file is rechecked on each request. ERP database users must be distinct, non-superuser and non-bypass; role observations are retained privately. Auth remains reached through the existing stock HTTP transport with an explicit Host header.

Two separate registered ERP clients, secrets, callback origins, company/binding/generation tuples and commercial profiles are used. Genuine emailed signup and actual Auth-issued cookies obtain opaque Core sessions. The disposable operator validates each session through the restricted native plane before creating fixed native Users and the immutable UUID-to-native User binding. A new one-use code then traverses the real ERP start, Auth issuer and ERP callback. This operator step is a public onboarding gap; it is not self-service provisioning.

Nine ordered groups cover real confirmation/cookies; A owner; A member row restrictions; B owner and separate Customer data; foreign parent refusal at A; unchanged A session at B; committed native role and row-permission changes before fresh reads; real Core membership revocation; and actual GoTrue parent logout. Native reads use `frappe.app.application`, the production private HTTP client, PostgreSQL ORM and current permission paths. No business reads or central identity replies are mocked. The mutation process enables the production company-mode background refusal. It retains and restores the exact owned `Has Role` child row through committed deletion/`db_insert()`, never `User.save()` or a background-job bypass. Native permissions are restored and a successful current read is required before the membership negative, avoiding a masked refusal.

Auth/Core build and setup share a 120-second absolute end; ERP setup has a separate maximum of 300 seconds, both within the same 420-second setup end. Test work is bounded by 120 seconds and all cleanup by one shared 60-second deadline inside a 600-second outer window. Existing Docker memory/PID bounds remain: Core/Auth 256 MiB each; ERP PostgreSQL 512 MiB/64 PIDs, Redis 128 MiB/32 PIDs, ERP 1 GiB/128 PIDs, nonroot/read-only/cap-drop with 448 MiB total writable tmpfs. Core fixture storage and ERP source/output/container/tmpfs accounting each retain their 1 GiB bound. Auth public diagnostics are 64 KiB; private failure logs are 1 MiB. ERP complete command logs plus terminal receipt are 1 MiB, with a 64 KiB terminal reservation. Logs can contain test cookies or responses and must remain private, never copied into a PR body.

Cleanup independently removes/checks owned containers and anonymous volumes, detaches the exact Core-native network attachment, removes/checks the internal network, then cleans the original Core/Auth resources and successfully built image tags under the same absolute cleanup end. Failures remain separate from the first error. Source hashes, native roles and cleanup results are retained.

Commercial responses are explicit synthetic responses bound separately to the actual A/B subscriptions and payment intents. Technical acceptance evidence remains synthetic. Direct HTTP/WSGI does not qualify browser Secure-cookie enforcement, native Desk, mid-read races, production capacity, self-service onboarding or live commercial providers.

## Observed validation

Run5 passed all nine groups and exited 0 with complete owned cleanup. The tested fixture heads were Auth `d49d2e5599a2b7fe17bdbe814fcb841c8c8e267e` and ERP `2f740e1aa83d6147eaa5501dde65d72458c70980`; later documentation-only commits do not change that tested runtime. Production Auth145 `a3bff84b9392c4bd2d2ef77fe7a13bd769abf4e4`, Core41 `d622048eab4083f62a000ed59b19b0422566222c`, and ERP134 `e04ff67ad2866e82352591b16ada1de3c44d3b4a` application sources were unchanged.

All six fixture source hashes and identities matched before and after the invocation. The native receipt retained no primary, cleanup, publication or quota errors, with a complete 187-command private redacted ledger. Distinct native database roles were non-superuser and non-bypass. Cleanup verified the three native containers, two anonymous volumes, internal network and exact Core-native attachment absent; the Auth/Core cleanup also completed.

Four earlier failures remain preserved: Core hosting identity refusal; a stock HTTP socket hang up whose cause was not measured; command diagnostic quota refusal; and run4, where all nine functional groups passed but the overall result failed because native terminal reporting was absent. The historical reporting cause was not measured. No assertion was skipped or weakened. Original Auth8, Wiki9 and ERP8 tests remain unchanged and were not repeated for this chain.
