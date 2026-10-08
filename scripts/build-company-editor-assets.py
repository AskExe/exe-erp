"""Build genuine current-source native bundles in a fresh owned local bench.

Usage: python3 scripts/build-company-editor-assets.py /absolute/new/off-repo/output
Then ERP_NATIVE_ASSETS=<output>/bench/sites/assets with the native fixture.
Requires Node>=24/Corepack. Uses existing frozen Yarn1 locks, no images or live bench.
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent
if not Path(sys.argv[1]).is_absolute():
	raise ValueError('Absolute output path required')
OUTPUT = Path(sys.argv[1]).resolve()
if not OUTPUT.is_absolute() or OUTPUT.is_relative_to(SOURCE):
	raise ValueError('Exclusive off-repo build directory required')
OUTPUT.mkdir(mode=0o700, exist_ok=False)
END = time.monotonic() + 300
RECORDS = []
LINKS = []
RAW = 0


def group_rows(pgid):
	probe = subprocess.run(['ps', '-axo', 'pid=,uid=,ppid=,pgid=,comm='], capture_output=True, timeout=2, check=True)
	if len(probe.stdout) > 256 * 1024:
		raise RuntimeError('Bounded process inventory exceeded')
	rows = []
	for line in probe.stdout.decode().splitlines():
		parts = line.strip().split(None, 4)
		if len(parts) == 5 and int(parts[3]) == pgid:
			rows.append(dict(zip(('pid', 'uid', 'ppid', 'pgid', 'comm'), [int(value) for value in parts[:4]] + [parts[4]], strict=True)))
	return rows


def run(args, cwd):
	global RAW
	if os.statvfs(OUTPUT).f_bavail * os.statvfs(OUTPUT).f_frsize < 20 * 1024**3:
		raise RuntimeError('Build disk floor20GiB')
	ordinal = len(RECORDS) + 1
	stdout, stderr = OUTPUT / f'{ordinal}.stdout', OUTPUT / f'{ordinal}.stderr'
	with stdout.open('xb') as out, stderr.open('xb') as err:
		process = subprocess.Popen(args, cwd=cwd, stdout=out, stderr=err, start_new_session=True)
		record = {'args': args, 'pid': process.pid, 'pgid': process.pid, 'uid': os.getuid(), 'status': None, 'reason': None, 'reaped': False, 'group_closed': False}
		RECORDS.append(record)
		try:
			while process.poll() is None:
				if time.monotonic() >= END - 4:
					record['reason'] = 'deadline'
					break
				if RAW + stdout.stat().st_size + stderr.stat().st_size > 1024**2:
					record['reason'] = 'output'
					break
				time.sleep(0.1)
		finally:
			if process.poll() is not None:
				process.wait()
				record['reaped'] = True
			# Compiler helpers normally exit with their leader; independently
			# observe their numeric group rather than signal a vanished group.
			rows = group_rows(process.pid)
			record['observed_group'] = rows
			if process.poll() is not None:
				grace = time.monotonic() + 1
				while rows and time.monotonic() < grace:
					time.sleep(0.1)
					rows = group_rows(process.pid)
			if rows:
				if any(row['uid'] != os.getuid() for row in rows):
					raise RuntimeError('Owned group UID mismatch')
				try:
					os.killpg(process.pid, signal.SIGTERM)
				except PermissionError:
					record['reason'] = 'signal_permission'
					raise
			try:
				process.wait(timeout=2)
			except subprocess.TimeoutExpired:
				os.killpg(process.pid, signal.SIGKILL)
				process.wait()
			record['reaped'] = True
			rows = group_rows(process.pid)
			if rows:
				os.killpg(process.pid, signal.SIGKILL)
				for _ in range(10):
					rows = group_rows(process.pid)
					if not rows:
						break
					time.sleep(0.1)
			record['remaining_group'] = rows
			record['group_closed'] = not rows
			record['status'] = process.returncode
	RAW += stdout.stat().st_size + stderr.stat().st_size
	if record['reason'] or process.returncode != 0 or not record['group_closed'] or RAW > 1024**2:
		raise RuntimeError('Bounded source asset build failed')


try:
	for link in (SOURCE / 'node_modules', SOURCE / 'frappe/public/node_modules', SOURCE / 'apps/erpnext/node_modules'):
		if link.exists() or link.is_symlink():
			raise RuntimeError('Use a clean source worktree without existing dependency links')
	run(['node', '--version'], SOURCE)
	if int((OUTPUT / '1.stdout').read_text().strip().split('.')[0].removeprefix('v')) < 24:
		raise RuntimeError('Current source requires Node>=24')
	for app, path in [('frappe', SOURCE), ('erpnext', SOURCE / 'apps/erpnext')]:
		modules = OUTPUT / app / 'node_modules'
		run(['corepack', 'yarn@1.22.22', 'install', '--frozen-lockfile', '--non-interactive', '--ignore-scripts', '--modules-folder', str(modules)], path)
		(path / 'node_modules').symlink_to(modules)
		LINKS.append((path / 'node_modules', modules))
		if app == 'frappe':
			(path / 'frappe/public/node_modules').symlink_to(modules)
			LINKS.append((path / 'frappe/public/node_modules', modules))
		(OUTPUT / 'bench/apps').mkdir(parents=True, exist_ok=True)
		(OUTPUT / 'bench/apps' / app).symlink_to(path)
	(OUTPUT / 'bench/sites/assets').mkdir(parents=True)
	for app, path in [('frappe', SOURCE / 'frappe/public'), ('erpnext', SOURCE / 'apps/erpnext/erpnext/public')]:
		shutil.copytree(path, OUTPUT / 'bench/sites/assets' / app, ignore=shutil.ignore_patterns('node_modules', 'dist'))
	os.environ['FRAPPE_BENCH_ROOT'] = str(OUTPUT / 'bench')
	run(['node', 'esbuild', '--production', '--apps', 'frappe,erpnext'], SOURCE)
	assets = OUTPUT / 'bench/sites/assets'
	pins = {str(p.relative_to(assets)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(assets.rglob('*')) if p.is_file()}
	if 'assets.json' not in pins:
		raise RuntimeError('Native build did not emit real assets manifest')
	(OUTPUT / 'asset-pins.json').write_text(json.dumps(pins, indent=2))
	provenance = {p: hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() for p in ('package.json', 'yarn.lock', 'apps/erpnext/package.json', 'apps/erpnext/yarn.lock', 'apps/erpnext/erpnext/accounts/party.py', 'apps/erpnext/erpnext/stock/get_item_details.py')}
	(OUTPUT / 'source-pins.json').write_text(json.dumps(provenance, indent=2))
	print(str(assets))
finally:
	cleanup = []
	for link, target in reversed(LINKS):
		if link.is_symlink() and link.readlink() == target:
			link.unlink()
			cleanup.append({'link': str(link), 'removed': True})
		else:
			cleanup.append({'link': str(link), 'removed': False})
	(OUTPUT / 'result.json').write_text(json.dumps({'records': RECORDS, 'cleanup': cleanup, 'raw': RAW, 'deadline_s': 300, 'floor_bytes': 20 * 1024**3}, indent=2))
