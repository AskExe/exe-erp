#!/usr/bin/env bash
#
# Regression test for bug 593fe59f — "Entrypoint crash-loops the whole service
# when existing ERP_ADMIN_PASSWORD fails new validation".
#
# Both cases assert an ABSENCE:
#   1. On an existing install with NO admin-password marker and a password that
#      fails a newer rule, the boot must NOT die and must NOT rotate anything.
#      Pre-fix: validate_admin_password exits 1 under `set -e` after migrations,
#      so currentsite.txt is never written — the container crash-loops.
#   2. When the verdict IS fatal (marker present, operator set a bad new
#      password), it must be reached BEFORE migrations. Pre-fix `bench migrate`
#      had already run by the time the lint killed the boot, so every restart
#      cycle re-ran multi-minute migrations.
#
# Runs entrypoint.sh end-to-end against a throwaway bench root with `bench` and
# `pg_isready` stubbed. No container, no database.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRYPOINT="${REPO_ROOT}/entrypoint.sh"
FAILURES=0

# The venv-python stub delegates the encryption-key program to a real
# interpreter so the test exercises the actual file semantics.
TEST_SYSTEM_PYTHON="$(command -v python3 || command -v python)"
if [ -z "${TEST_SYSTEM_PYTHON}" ]; then
    echo "FATAL: python3 is required to run these tests" >&2
    exit 1
fi

# Portable SHA-256 — the harness must not depend on GNU coreutils either.
_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum | cut -d' ' -f1
    else shasum -a 256 | cut -d' ' -f1
    fi
}

pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

# Boot the entrypoint in a sandbox.
#   $1 = "marker" to pre-seed the admin-password marker with a DIFFERENT hash
#        (simulating an operator who deliberately changed the password),
#        "no-marker" otherwise.
#   $2 = ERP_ADMIN_PASSWORD to configure.
# Exports: BOOT_DIR, BOOT_RC, BOOT_OUT, BENCH_LOG.
run_boot() {
    local marker_mode="$1" password="$2"

    BOOT_DIR="$(mktemp -d)"
    local bench_root="${BOOT_DIR}/frappe-bench"
    local site="erp.test.local"
    local site_dir="${bench_root}/sites/${site}"
    mkdir -p "${site_dir}" "${BOOT_DIR}/bin"

    # An existing, fully installed site: the fast path in is_erpnext_installed
    # reads this marker, so no DB is needed. "fresh" omits it so the boot takes
    # the create_site path instead.
    if [ "${marker_mode}" != "fresh" ]; then
        touch "${site_dir}/.exe_install_complete"
    fi

    # A real EXISTING site always has one; it carries db_name/db_password
    # alongside the encryption_key, which is why the provisioner must preserve
    # other keys. A "fresh" site has none — and seeding one there would make
    # is_site_db_initialized report an initialized site, so `bench new-site`
    # would be skipped and case 7 would silently stop testing the bootstrap
    # path it exists to cover.
    if [ "${marker_mode}" = "fresh" ]; then
        :
    elif [ -z "${SITE_CONFIG_SEED:-}" ]; then
        printf '%s' '{"db_name": "exe_erp", "db_password": "fixture-not-a-real-secret"}' \
            > "${site_dir}/site_config.json"
    else
        printf '%s' "${SITE_CONFIG_SEED}" > "${site_dir}/site_config.json"
    fi
    SITE_CONFIG="${site_dir}/site_config.json"

    if [ "${marker_mode}" = "marker" ]; then
        # Any value that is not the hash of ${password} reads as "the operator
        # changed ERP_ADMIN_PASSWORD".
        printf '%s' "0000000000000000000000000000000000000000000000000000000000000000" \
            > "${site_dir}/.exe_admin_pw_hash"
    elif [ "${marker_mode}" = "marker-matching" ]; then
        # The marker AGREES with the configured password — the state that made
        # bug 293a3c8f permanent. Computed the same way entrypoint.sh does.
        printf '%s' "$(printf '%s' "${password}:${site}" | _sha256)" \
            > "${site_dir}/.exe_admin_pw_hash"
    fi

    BENCH_LOG="${BOOT_DIR}/bench.log"
    cat > "${BOOT_DIR}/bin/bench" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "${BENCH_LOG}"
exit 0
STUB

    # The password checker runs the bench venv python DIRECTLY with the password
    # on STDIN — never through `bench`, never via argv — so the stub lives at
    # the interpreter path and reads stdin exactly as the real one does.
    # CHECK_VERDICT selects the token it emits:
    #   ok        — authenticates
    #   authfail  — definitively wrong
    #   unknown   — the checker itself broke (emits NO token at all)
    # It also records the password EXACTLY as received, so a test can prove the
    # value survived the trip byte-for-byte.
    mkdir -p "${bench_root}/env/bin"
    PW_SEEN="${BOOT_DIR}/pw_seen.txt"
    cat > "${bench_root}/env/bin/python" <<STUB
#!/usr/bin/env bash
# Two callers reach this stub, distinguished the same way the real interpreter
# would be: the encryption-key provisioner passes the config path as an
# argument, the password checker passes the site name.
if [ "\$1" = "-" ] && [ -n "\$2" ] && case "\$2" in */site_config.json) true ;; *) false ;; esac; then
  # Run the REAL provisioning program on the REAL system python so the test
  # exercises the actual JSON read/modify/atomic-write, not a paraphrase of it.
  # cryptography may be absent locally; fall back to a stand-in generator that
  # produces a Fernet-shaped key so the test still proves the FILE semantics.
  prog="\$(cat)"
  printf '%s' "\$prog" | ${TEST_SYSTEM_PYTHON} - "\$2" 2>&1 || {
    printf '%s' "\$prog" \
      | sed 's/^from cryptography.fernet import Fernet\$/import base64, os/' \
      | sed 's/Fernet.generate_key().decode()/base64.urlsafe_b64encode(os.urandom(32)).decode()/' \
      | ${TEST_SYSTEM_PYTHON} - "\$2" 2>&1
  }
  exit 0
fi
cat > "${PW_SEEN}"
case "${CHECK_VERDICT:-ok}" in
  ok)       echo "EXE_ADMIN_PW:OK" ;;
  authfail) echo "EXE_ADMIN_PW:WRONG" ;;
  unknown)  echo "ImportError: cannot import name frappe" >&2; exit 1 ;;
esac
exit 0
STUB
    chmod +x "${bench_root}/env/bin/python"
    printf '#!/usr/bin/env bash\nexit 0\n' > "${BOOT_DIR}/bin/pg_isready"
    chmod +x "${BOOT_DIR}/bin/bench" "${BOOT_DIR}/bin/pg_isready"
    : > "${BENCH_LOG}"

    set +e
    BOOT_OUT="$(
        PATH="${BOOT_DIR}/bin:${PATH}" \
        FRAPPE_BENCH="${bench_root}" \
        SITE_NAME="${site}" \
        ADMIN_PASSWORD="${password}" \
        CHECK_VERDICT="${CHECK_VERDICT:-ok}" \
        DB_HOST="db.invalid" \
        REDIS_CACHE="" REDIS_QUEUE="" REDIS_SOCKETIO="" \
        GOTRUE_URL="" GOTRUE_EXTERNAL_URL="" \
        bash "${ENTRYPOINT}" true 2>&1
    )"
    BOOT_RC=$?
    set -e
    CURRENTSITE="${bench_root}/sites/currentsite.txt"
}

# ── Case 1: no marker + password failing the special-character rule ──────────
# A pre-existing install upgraded into an image that added the rule.
echo "case 1: pre-existing install, no marker, password predates a newer rule"
run_boot no-marker "LegacyPassword123"

if [ -f "${CURRENTSITE}" ]; then
    pass "boot proceeded past the password step (currentsite.txt written)"
else
    fail "boot died on the password lint — currentsite.txt never written"
    printf '%s\n' "${BOOT_OUT}" | sed 's/^/       | /'
fi

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    fail "rotated the Administrator password despite an invalid configured value"
else
    pass "no rotation attempted (bench set-admin-password never invoked)"
fi

if [ -f "${BOOT_DIR}/frappe-bench/sites/erp.test.local/.exe_admin_pw_hash" ]; then
    fail "wrote an admin-password marker for a value it refused to apply"
else
    pass "no marker written for the skipped rotation"
fi

case "${BOOT_OUT}" in
    *"SKIPPING"*) pass "skip is announced loudly in the boot log" ;;
    *)            fail "skip is silent — operators get no warning" ;;
esac
rm -rf "${BOOT_DIR}"

# ── Case 2: marker present + operator set an invalid NEW password ────────────
# Still fatal, but the verdict must land before any migration work.
echo "case 2: operator changed ERP_ADMIN_PASSWORD to an invalid value"
run_boot marker "LegacyPassword123"

if [ "${BOOT_RC}" -ne 0 ]; then
    pass "boot fails closed on a deliberate rotation to an invalid password"
else
    fail "accepted an invalid deliberate rotation (rc=${BOOT_RC})"
fi

if grep -q '^--site .* migrate$' "${BENCH_LOG}"; then
    fail "ran migrations before the fatal password check — every restart re-runs them"
    sed 's/^/       | /' "${BENCH_LOG}"
else
    pass "no migration ran before the fatal verdict"
fi

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    fail "applied an invalid password"
else
    pass "invalid password never applied"
fi
rm -rf "${BOOT_DIR}"

# ── Case 3 (bug 293a3c8f): the marker agrees but the password does NOT work ──
# erp.askexe.com's live state. The marker matched the configured password
# exactly, so the preflight concluded "nothing to do" — while that password
# returned 401 against the site. Nothing verified the marker, so no boot could
# ever converge it and the operator lockout was permanent by construction.
echo "case 3: marker matches but the password does not authenticate (stale marker)"
CHECK_VERDICT=authfail run_boot marker-matching "Str0ng-Valid-Pass!"

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    pass "stale marker detected — Administrator password reconverged"
else
    fail "trusted the stale marker and never rotated — the lockout stays permanent"
    printf '%s\n' "${BOOT_OUT}" | sed 's/^/       | /'
fi

case "${BOOT_OUT}" in
    *"does NOT authenticate"*) pass "stale marker is announced in the boot log" ;;
    *)                         fail "stale marker was reconverged silently" ;;
esac
rm -rf "${BOOT_DIR}"

# ── Case 4 (bug 293a3c8f): "could not check" must NEVER mean "wrong" ─────────
# A read that fails for an UNRECOGNISED reason must change nothing. Treating it
# as a negative would reset the Administrator password every time the site was
# briefly unreachable at boot.
echo "case 4: verification fails for an unrecognised reason — must change nothing"
CHECK_VERDICT=unknown run_boot marker-matching "Str0ng-Valid-Pass!"

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    fail "rotated on an INDETERMINATE result — 'could not check' was read as 'wrong'"
else
    pass "indeterminate verification changed nothing"
fi

# Same signal case 1 uses: currentsite.txt is written only after the password
# step completes. BOOT_RC is not usable here — this sandbox cannot create the
# hardcoded /home/frappe path, so the boot always ends non-zero for a reason
# unrelated to anything under test.
if [ -f "${CURRENTSITE}" ]; then
    pass "boot stayed up despite being unable to verify"
else
    fail "an unverifiable password check took the service down"
    printf '%s\n' "${BOOT_OUT}" | sed 's/^/       | /'
fi
rm -rf "${BOOT_DIR}"

# ── Case 5 (bug 293a3c8f): a long alphanumeric secret is not "weak" ──────────
# erp.askexe.com's ERP_ADMIN_PASSWORD is 32 alphanumeric characters (~190 bits).
# The special-character rule rejected it, which closed the ONLY path that could
# repair the lockout. Length now substitutes for the character class.
echo "case 5: 32-char alphanumeric secret is accepted and can reconverge"
CHECK_VERDICT=authfail run_boot marker-matching "aB3xK9mQ7pL2rT5vW8yZ4nC6hJ1sD0gF"

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    pass "long alphanumeric secret passes the lint and reconverges"
else
    fail "long alphanumeric secret still refused — the lockout cannot be repaired"
    printf '%s\n' "${BOOT_OUT}" | sed 's/^/       | /'
fi

if [ -f "${CURRENTSITE}" ]; then
    pass "boot stayed up"
else
    fail "boot died on a strong 32-char secret"
    printf '%s\n' "${BOOT_OUT}" | sed 's/^/       | /'
fi
rm -rf "${BOOT_DIR}"

# ── Case 6 (PR #106 review, P1): the password must never be parsed as code ───
# The first version of the checker interpolated the password into a Python
# expression passed to `bench execute --args`, which the vendored executor
# evaluates. A `"` made the expression invalid — so the call failed in an
# UNRECOGNISED way, the checker returned "could not determine", and THE STALE
# MARKER WAS TRUSTED: this bug's own failure mode, still open for any password
# containing a quote. A backslash escape silently CHANGED the password, so a
# correctly configured one read as wrong and was reset on every boot.
echo "case 6: password containing quotes and backslashes survives byte-for-byte"
TRICKY='pa"ss\word'"'"'x $(id) `id`'
CHECK_VERDICT=authfail run_boot marker-matching "${TRICKY}"

if [ -f "${PW_SEEN}" ] && [ "$(cat "${PW_SEEN}")" = "${TRICKY}" ]; then
    pass "checker received the password unmodified (no escape processing, no eval)"
else
    fail "password was mangled or never reached the checker"
    printf '       | expected: %s\n' "${TRICKY}"
    printf '       | received: %s\n' "$(cat "${PW_SEEN}" 2>/dev/null)"
fi

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    pass "stale marker still detected with an awkward password"
else
    fail "awkward password broke the checker — stale marker went undetected"
fi
rm -rf "${BOOT_DIR}"

# ── Case 7 (PR #106 review, P2): fresh bootstrap must not re-apply ──────────
# On a fresh site the preflight sets ADMIN_PW_ACTION=rotate, and create_site
# already applies the password via `bench new-site` and seeds the marker.
# Gating the early-return on `ADMIN_PW_ACTION != rotate` could never fire here,
# so every successful bootstrap ran a redundant `set-admin-password`; if that
# failed, the one-shot configurator exited non-zero and every dependent service
# stayed blocked on a site that had been created correctly.
echo "case 7: fresh bootstrap does not re-apply the password after new-site"
run_boot fresh "Str0ng-Valid-Pass!"

if grep -q 'new-site' "${BENCH_LOG}"; then
    pass "fresh bootstrap ran new-site"
else
    fail "fresh bootstrap did not run new-site — the case is not exercising what it claims"
    sed 's/^/       | /' "${BENCH_LOG}"
fi

if grep -q 'set-admin-password' "${BENCH_LOG}"; then
    fail "re-applied the password after new-site had already set it"
    sed 's/^/       | /' "${BENCH_LOG}"
else
    pass "no redundant set-admin-password after bootstrap"
fi
rm -rf "${BOOT_DIR}"

# ── Case 8 (bug bd1458f5): encryption_key must be provisioned, not lazy ─────
# Frappe generates encryption_key lazily, in-process, keyed on
# `"encryption_key" not in frappe.local.conf` — the process's IN-MEMORY conf.
# A gunicorn worker that started while the key was absent never sees a later
# one, generates its OWN, and writes it over another process's. With N workers
# plus a bench CLI you get up to N+1 competing keys, which is exactly the
# reported "first request succeeds, all subsequent fail to decrypt".
# Provisioning it in the one-shot configurator, before any worker exists,
# makes that lazy branch unreachable.
echo "case 8: a site with no encryption_key gets one provisioned at boot"
run_boot marker-matching "Str0ng-Valid-Pass!"

if "${TEST_SYSTEM_PYTHON}" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("encryption_key") else 1)' "${SITE_CONFIG}"; then
    pass "encryption_key provisioned"
else
    fail "no encryption_key written — Frappe will lazily generate a per-process key"
    printf '%s\n' "${BOOT_OUT}" | tail -12 | sed 's/^/       | /'
fi

# The file also carries db_name/db_password; a clobbering write would take the
# site down harder than the bug it fixes.
if "${TEST_SYSTEM_PYTHON}" -c 'import json,sys; c=json.load(open(sys.argv[1])); sys.exit(0 if c.get("db_name")=="exe_erp" and c.get("db_password") else 1)' "${SITE_CONFIG}"; then
    pass "existing site_config keys preserved"
else
    fail "provisioning clobbered other site_config keys"
fi
rm -rf "${BOOT_DIR}"

# ── Case 9 (bug bd1458f5): provisioning is idempotent ───────────────────────
# Rotating an existing key would make every already-encrypted value
# undecryptable — a worse outage than the one being prevented.
echo "case 9: an existing encryption_key is never rotated"
SITE_CONFIG_SEED='{"db_name": "exe_erp", "db_password": "fixture-not-a-real-secret", "encryption_key": "PRE-EXISTING-KEY-DO-NOT-TOUCH"}' \
    run_boot marker-matching "Str0ng-Valid-Pass!"

if [ "$("${TEST_SYSTEM_PYTHON}" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("encryption_key"))' "${SITE_CONFIG}")" = "PRE-EXISTING-KEY-DO-NOT-TOUCH" ]; then
    pass "existing key left untouched"
else
    fail "rotated an existing encryption_key — every stored secret becomes undecryptable"
fi
unset SITE_CONFIG_SEED
rm -rf "${BOOT_DIR}"

# ── Case 10 (bug bd1458f5): only the configurator writes ────────────────────
# exe-erp (gunicorn) runs this same entrypoint. If it also provisioned, two
# containers could race and write different keys — reintroducing the very
# mismatch this closes. ADMIN_PASSWORD is the existing "I am the configurator"
# discriminator; migrate-only services never receive it.
echo "case 10: a migrate-only service does not write an encryption_key"
# An empty ADMIN_PASSWORD is exactly what a migrate-only service gets.
run_boot marker-matching ""

if "${TEST_SYSTEM_PYTHON}" -c 'import json,sys; sys.exit(1 if json.load(open(sys.argv[1])).get("encryption_key") else 0)' "${SITE_CONFIG}"; then
    pass "migrate-only service wrote nothing"
else
    fail "a non-configurator service provisioned a key — two writers can race"
fi
rm -rf "${BOOT_DIR}"

echo
if [ "${FAILURES}" -eq 0 ]; then
    echo "entrypoint admin-password regression: PASS"
    exit 0
fi
echo "entrypoint admin-password regression: ${FAILURES} FAILURE(S)"
exit 1
