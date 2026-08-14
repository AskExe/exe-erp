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
    # reads this marker, so no DB is needed.
    touch "${site_dir}/.exe_install_complete"

    if [ "${marker_mode}" = "marker" ]; then
        # Any value that is not the hash of ${password} reads as "the operator
        # changed ERP_ADMIN_PASSWORD".
        printf '%s' "0000000000000000000000000000000000000000000000000000000000000000" \
            > "${site_dir}/.exe_admin_pw_hash"
    fi

    BENCH_LOG="${BOOT_DIR}/bench.log"
    cat > "${BOOT_DIR}/bin/bench" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "${BENCH_LOG}"
exit 0
STUB
    printf '#!/usr/bin/env bash\nexit 0\n' > "${BOOT_DIR}/bin/pg_isready"
    chmod +x "${BOOT_DIR}/bin/bench" "${BOOT_DIR}/bin/pg_isready"
    : > "${BENCH_LOG}"

    set +e
    BOOT_OUT="$(
        PATH="${BOOT_DIR}/bin:${PATH}" \
        FRAPPE_BENCH="${bench_root}" \
        SITE_NAME="${site}" \
        ADMIN_PASSWORD="${password}" \
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

echo
if [ "${FAILURES}" -eq 0 ]; then
    echo "entrypoint admin-password regression: PASS"
    exit 0
fi
echo "entrypoint admin-password regression: ${FAILURES} FAILURE(S)"
exit 1
