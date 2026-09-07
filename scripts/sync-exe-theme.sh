#!/usr/bin/env bash
#
# Sync @askexenow/exe-theme into this repo.
#
# @askexenow/exe-theme is not published to any registry, so its tokens are
# vendored into frappe/public/css/exe-theme/ and committed. That keeps the
# build hermetic: nothing here runs at build time, and CI never needs network
# access to render the desk theme.
#
# Run this script only when you deliberately want to pull newer upstream
# tokens, then review and commit the diff.
#
#   ./scripts/sync-exe-theme.sh                  # sync at the pinned ref
#   EXE_THEME_REF=main ./scripts/sync-exe-theme.sh
#
set -euo pipefail

# Pinned by default so a resync is reproducible rather than "whatever is on the
# branch today". Override with EXE_THEME_REF for a deliberate upgrade.
EXE_THEME_REF="${EXE_THEME_REF:-47d54b58}"
EXE_THEME_BRANCH="fix/exe-theme-reconcile-crm"
SRC_REPO="AskExe/exe-os"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${REPO_ROOT}/frappe/public/css/exe-theme"
mkdir -p "${DEST}"

# Prefer a local exe-os checkout (fast, works offline); fall back to the GitHub
# API. exe-os is private, so an unauthenticated raw fetch will not work.
LOCAL_EXE_OS="${EXE_OS_PATH:-${HOME}/exe-os}"

fetch() {
  # $1 = path within exe-os, prints file contents on stdout
  if [ -d "${LOCAL_EXE_OS}/.git" ] \
     && git -C "${LOCAL_EXE_OS}" cat-file -e "${EXE_THEME_REF}:$1" 2>/dev/null; then
    git -C "${LOCAL_EXE_OS}" show "${EXE_THEME_REF}:$1"
  elif command -v gh >/dev/null 2>&1; then
    gh api "repos/${SRC_REPO}/contents/$1?ref=${EXE_THEME_REF}" \
      --jq '.content' | base64 -d
  else
    echo "sync-exe-theme: cannot read $1 — no local exe-os checkout at ${LOCAL_EXE_OS} and no gh CLI" >&2
    return 1
  fi
}

json_header() {
  cat <<JSON
{
  "_provenance": {
    "source": "${SRC_REPO}",
    "path": "packages/exe-theme/tokens.json",
    "branch": "${EXE_THEME_BRANCH}",
    "commit": "${EXE_THEME_REF}",
    "resync": "run scripts/sync-exe-theme.sh",
    "note": "Generated file - do not edit by hand. Edit @askexenow/exe-theme upstream and resync."
  },
JSON
}

css_header() {
  cat <<CSS
/* =============================================================================
 * VENDORED FILE - DO NOT EDIT BY HAND.
 *
 * Source : ${SRC_REPO}  packages/exe-theme/src/tokens.css
 * Branch : ${EXE_THEME_BRANCH}
 * Commit : ${EXE_THEME_REF}
 * Resync : run scripts/sync-exe-theme.sh
 *
 * @askexenow/exe-theme is not published to a registry, so it is vendored here
 * to keep the build hermetic (no network access at build time). These values
 * are reconciled against the founder-approved reference look in
 * exe-crm/packages/twenty-ui/src/theme/constants/ExeFoundryBold.ts.
 * ========================================================================== */

CSS
}

# tokens.json — splice our provenance block in place of the opening brace.
{ json_header; fetch "packages/exe-theme/tokens.json" | tail -n +2; } > "${DEST}/tokens.json"

# tokens.css — prepend the provenance header.
{ css_header; fetch "packages/exe-theme/src/tokens.css"; } > "${DEST}/tokens.css"

echo "sync-exe-theme: wrote ${DEST}/tokens.json and ${DEST}/tokens.css at ref ${EXE_THEME_REF}"
echo "sync-exe-theme: review 'git diff frappe/public/css/exe-theme/' before committing."
