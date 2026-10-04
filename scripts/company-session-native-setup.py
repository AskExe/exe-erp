"""Provision only two new caller-owned disposable sites using existing Bench CLI."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SITES = Path('/home/frappe/frappe-bench/sites')
END = time.monotonic() + 300
if os.geteuid() == 0 or list(SITES.iterdir()):
	raise RuntimeError('Fresh empty native-owned sites directory required')
fixture = os.environ['NATIVE_ACL_FIXTURE_ID']
sites = [os.environ['NATIVE_ACL_SITE_A'], os.environ['NATIVE_ACL_SITE_B']]
if len(set(sites)) != 2:
	raise RuntimeError('Distinct sites required')
(SITES / 'apps.txt').write_text('frappe\nerpnext\n')
(SITES / 'common_site_config.json').write_text(json.dumps({
	'redis_cache': 'redis://redis:6379/0', 'redis_queue': 'redis://redis:6379/1',
	'redis_socketio': 'redis://redis:6379/2', 'disable_scheduler': 1,
}))
for index, site in enumerate(sites):
	args = [sys.executable, '-B', '-m', 'frappe.utils.bench_helper', 'frappe', 'new-site', site,
		'--db-type', 'postgres', '--db-host', 'postgres', '--db-port', '5432',
		'--db-root-username', 'fixture', '--db-root-password', os.environ['NATIVE_ACL_DB_PASSWORD'],
		'--db-name', 'acl_' + fixture + '_' + str(index), '--db-password', os.environ['NATIVE_ACL_DB_PASSWORD'],
		'--admin-password', os.environ['NATIVE_ACL_ADMIN_PASSWORD'], '--install-app', 'erpnext']
	remaining = END - time.monotonic()
	if remaining <= 0:
		raise TimeoutError('Site provisioning deadline')
	subprocess.run(args, cwd=SITES, check=True, timeout=min(140, remaining))
	path = SITES / site / 'site_config.json'
	config = json.loads(path.read_text())
	config.update(allow_tests=True, company_acl_fixture=fixture)
	path.write_text(json.dumps(config))
print('Two disposable PostgreSQL ERPnext sites installed; no web/worker/provider started.')
