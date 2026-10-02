#!/usr/bin/env bash
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
task_tmp="$(mktemp -d)"
trap 'rm -rf "$task_tmp"' EXIT
mkdir -p "$task_tmp/sites/primary.example.com/logs" "$task_tmp/sites/demo.example.com" "$task_tmp/sites/assets"
printf '{}' > "$task_tmp/sites/primary.example.com/site_config.json"
printf '{}' > "$task_tmp/sites/demo.example.com/site_config.json"
printf 'existing log preserved' > "$task_tmp/sites/primary.example.com/logs/frappe.log"
export FRAPPE_BENCH="$task_tmp" EXE_ERP_ENTRYPOINT_NO_MAIN=1
source "$repo/entrypoint.sh"
ensure_site_log_directories
for root in "$task_tmp" "$task_tmp/sites"; do
    for site in primary.example.com demo.example.com; do
        test -d "$root/$site/logs"
    done
done
test ! -e "$task_tmp/assets/logs"
test ! -e "$task_tmp/sites/assets/logs"
test "$(cat "$task_tmp/sites/primary.example.com/logs/frappe.log")" = 'existing log preserved'
ensure_site_log_directories
printf 'multi-site startup log directories: PASS\n'
