#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Regression test for bug df3264de: exe-erp's orphan-buildx cleanup was
# killing OTHER repos' in-flight builds once runners moved onto one
# shared Docker host.
#
# Runs with NO real docker daemon and touches NO live container: `docker`
# is replaced on PATH with a fixture-driven stub that answers `ps` from a
# fixed list of containers and records `rm -f` calls to a log file instead
# of removing anything.
#
# Three fixtures:
#   (a) buildx_buildkit_erp-release-buildx0  -- THIS job's own builder,
#       left behind by an earlier, already-finished-or-cancelled run.
#       Must be reaped.
#   (b) buildx_buildkit_builder-a1b2c3d4      -- another repo's builder
#       (e.g. exe-crm), using the same auto-generated default name every
#       repo gets when it doesn't set an explicit one. THIS IS THE BUG:
#       the old predicate matched this and force-removed it out from under
#       a concurrent build. Must NOT be reaped.
#   (c) buildx_buildkit_builder-9f8e7d6c      -- a container matching the
#       same generic default-name pattern with no identifying information
#       at all (could be any repo, or a hand-created container on the
#       host). Ownership can't be positively established. Must NOT be
#       reaped -- proving the fix is a positive allow-list of our own
#       name, not a denylist of "known other repos" that would still eat
#       an unrecognized name.
# ──────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${HERE}/cleanup-orphaned-buildx-builders.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

fail=0
check() { # desc expected actual
	if [ "$2" = "$3" ]; then
		echo "PASS: $1"
	else
		echo "FAIL: $1 (expected '$2', got '$3')"
		fail=1
	fi
}

# ── Fixture containers (id name status) ──────────────────────────────────
FIXTURE="${WORK}/containers.txt"
cat > "${FIXTURE}" <<'EOF'
c1a11 buildx_buildkit_erp-release-buildx0 Exited (0) 12 minutes ago
c2b22 buildx_buildkit_builder-a1b2c3d4 Up 3 minutes
c3c33 buildx_buildkit_builder-9f8e7d6c Exited (137) 40 minutes ago
EOF

REMOVED_LOG="${WORK}/removed.log"
: > "${REMOVED_LOG}"

# ── Mock `docker` on PATH ─────────────────────────────────────────────────
# Understands only what the cleanup script actually calls: `ps -aq
# --filter name=<pattern>`, `ps -a --filter name=<pattern> --format ...`,
# and `rm -f <ids...>`. <pattern> is an anchored extended-regex fragment
# (the script always writes "^prefix"); matched with `grep -E`.
BIN="${WORK}/bin"
mkdir -p "${BIN}"
cat > "${BIN}/docker" <<EOF
#!/usr/bin/env bash
set -euo pipefail
FIXTURE="${FIXTURE}"
REMOVED_LOG="${REMOVED_LOG}"

if [ "\$1" = "ps" ]; then
	pattern=""
	want_q=0
	for arg in "\$@"; do
		case "\$arg" in
			name=*) pattern="\${arg#name=}" ;;
			-*q*) want_q=1 ;;
		esac
	done
	matches="\$(awk -v p="\$pattern" '\$2 ~ p' "\${FIXTURE}")"
	if [ "\${want_q}" = "1" ]; then
		printf '%s\n' "\${matches}" | awk 'NF{print \$1}'
	else
		printf '%s\n' "\${matches}" | awk 'NF{printf "  %s (%s %s %s %s)\n", \$2, \$3, \$4, \$5, \$6}'
	fi
	exit 0
fi

if [ "\$1" = "rm" ]; then
	shift
	for arg in "\$@"; do
		case "\$arg" in
			-f) ;;
			*) echo "\$arg" >> "\${REMOVED_LOG}" ;;
		esac
	done
	exit 0
fi

echo "unhandled mock docker invocation: \$*" >&2
exit 1
EOF
chmod +x "${BIN}/docker"

run_cleanup() {
	: > "${REMOVED_LOG}"
	PATH="${BIN}:${PATH}" "${SCRIPT}" >"${WORK}/out.log" 2>&1
}

removed() { # id
	grep -qx "$1" "${REMOVED_LOG}" && echo "removed" || echo "kept"
}

# ── Exercise the real, unmodified script (fix in place) ──────────────────
run_cleanup

check "(a) own stale builder is reaped" "removed" "$(removed c1a11)"
check "(b) another repo's builder is NOT reaped" "kept" "$(removed c2b22)"
check "(c) builder with indeterminate ownership is NOT reaped" "kept" "$(removed c3c33)"

if [ "${fail}" -ne 0 ]; then
	echo "--- cleanup script output ---"
	cat "${WORK}/out.log"
fi

exit "${fail}"
