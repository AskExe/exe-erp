"""Dedicated Chrome/native Desk trace; fixed owned HTTP broker, not genuine SSO.

Requires httpx and websockets. No secrets enter DOM, goals or evidence. Native
HTTPS/Host/Origin/CSRF remain intact through exact owned loopback forwarding.
"""
import asyncio
import base64
import json
import os
import shutil
import signal
import socket as socket_module
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import websockets

if os.environ.get('ERP_NATIVE_BROWSER_EXECUTE') != 'true':
	raise RuntimeError('Explicit owned native browser fixture required')
CONFIG = json.loads(Path(sys.argv[1]).read_text())
if len(CONFIG['sites']) != 2:
	raise RuntimeError('Exactly two owned native sites required')
for host, entry in CONFIG['sites'].items():
	endpoint = urlsplit(entry['endpoint'])
	if entry['origin'] != 'https://' + host or endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port or endpoint.path or endpoint.query:
		raise RuntimeError('Exact owned loopback transport required')
	with socket_module.create_connection(('127.0.0.1', endpoint.port), timeout=2):
		pass  # Preflight selected bound listeners before starting Chrome.
OUTPUT = Path(sys.argv[2]).resolve()
OUTPUT.mkdir(mode=0o700, exist_ok=False)
END = time.monotonic() + 105
OUTPUT_LIMIT = int(os.environ['ERP_BROWSER_OUTPUT_LIMIT'])
REQUESTS, FAILURES = [], []
CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'


class Browser:
	def __init__(self, socket):
		self.socket, self.next, self.pending, self.tasks = socket, 0, {}, set()
		self.session = None
		self.client = httpx.AsyncClient(timeout=9, follow_redirects=False, trust_env=False)

	async def send(self, method, params=None, session=None):
		self.next += 1
		future = asyncio.get_running_loop().create_future()
		self.pending[self.next] = future
		message = {'id': self.next, 'method': method, 'params': params or {}}
		if session:
			message['sessionId'] = session
		await self.socket.send(json.dumps(message))
		return await asyncio.wait_for(future, max(0.1, min(15, END - time.monotonic())))

	async def receive(self):
		async for raw in self.socket:
			message = json.loads(raw)
			if message.get('id') in self.pending:
				future = self.pending.pop(message['id'])
				if 'error' in message:
					future.set_exception(RuntimeError('Browser protocol refused ' + str(message['error'].get('code'))))
				else:
					future.set_result(message.get('result', {}))
			elif message.get('method') == 'Runtime.exceptionThrown':
				detail = message['params']['exceptionDetails']
				FAILURES.append({'operation': 'native-js', 'class': detail.get('exception', {}).get('className'), 'frames': [{'function': row['functionName'], 'line': row['lineNumber']} for row in detail.get('stackTrace', {}).get('callFrames', [])[:12]]})
			elif message.get('method') == 'Fetch.requestPaused':
				task = asyncio.create_task(self.forward(message['params'], message['sessionId']))
				self.tasks.add(task)
				task.add_done_callback(self.tasks.discard)

	async def forward(self, params, session):
		request, identifier = params['request'], params['requestId']
		parsed = urlsplit(request['url'])
		body, headers, status = b'', [], 404
		try:
			if parsed.hostname in CONFIG['sites'] and parsed.scheme == 'https':
				entry = CONFIG['sites'][parsed.hostname]
				selected = {key: value for key, value in request['headers'].items() if key.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
				selected['Host'] = parsed.hostname
				forwarded = self.client.build_request(request['method'], entry['endpoint'] + parsed.path + ('?' + parsed.query if parsed.query else ''), headers=selected, content=request.get('postData', '').encode())
				# Never allow httpx's loopback cookie jar to retarget another native
				# site: only the exact Chrome request's cookie header is forwarded.
				forwarded.headers.pop('cookie', None)
				for key, value in selected.items():
					if key.lower() == 'cookie':
						forwarded.headers['Cookie'] = value
				response = await self.client.send(forwarded)
				body, status = response.content, response.status_code
				headers = [{'name': key, 'value': value} for key, value in response.headers.multi_items() if key.lower() not in ('content-length', 'content-encoding', 'transfer-encoding', 'connection')]
			elif parsed.hostname == 'auth.platform.example.test' and parsed.path == '/company-session/authorize':
				query = parse_qs(parsed.query)
				client = query.get('client_id', [''])[0]
				entry = next(value for value in CONFIG['sites'].values() if value['client'] == client)
				location = entry['origin'] + '/company-session/callback?' + urlencode({'code': 'exc_' + 'b' * 43, 'state': query['state'][0]})
				status, headers = 303, [{'name': 'Location', 'value': location}]
			elif parsed.hostname == 'auth.platform.example.test' and parsed.path == '/logout':
				status, body = 200, b'<h1>Controlled Auth logout landing</h1><p>Genuine parent seam is separately unqualified.</p>'
				headers = [{'name': 'Content-Type', 'value': 'text/html'}]
			shape = parse_qs(parsed.query)
			if request.get('postData'):
				try:
					posted=json.loads(request['postData'])
					if isinstance(posted,dict):
						shape.update({key:[value] for key,value in posted.items()})
				except ValueError:
					shape.update(parse_qs(request['postData']))
			public_keys=sorted(key for key in shape if isinstance(key,str) and key.isidentifier() and len(key)<=64)[:40]
			selectors={}
			for key in ('doctype','reference_doctype'):
				value=shape.get(key,[None])[0]
				if value in ('Customer','Sales Invoice','Sales Invoice Item','Sales Taxes and Charges','Account','Cost Center','Company','Item','UOM','Currency','Customer Group','Territory','Price List'):
					selectors[key]=value
			REQUESTS.append({'host': parsed.hostname, 'path': parsed.path, 'method': request['method'], 'status': status,'argument_keys':public_keys,'public_native_selectors':selectors})
			if len(REQUESTS) > 1000:
				raise RuntimeError('Native browser request cap')
			await self.send('Fetch.fulfillRequest', {'requestId': identifier, 'responseCode': status, 'responseHeaders': headers, 'body': base64.b64encode(body).decode()}, session)
		except Exception as error:
			FAILURES.append({'operation': 'forward', 'class': type(error).__name__, 'host': parsed.hostname, 'path': parsed.path})
			await self.send('Fetch.failRequest', {'requestId': identifier, 'errorReason': 'Failed'}, session)

	async def evaluate(self, source, session):
		result = await self.send('Runtime.evaluate', {'expression': source, 'returnByValue': True, 'awaitPromise': True}, session)
		if result.get('exceptionDetails'):
			details = result['exceptionDetails']
			FAILURES.append({'operation': 'driver-evaluate', 'class': details.get('exception', {}).get('className'), 'line': details.get('lineNumber'), 'frames': [{'function': frame.get('functionName'), 'line': frame.get('lineNumber')} for frame in details.get('stackTrace', {}).get('callFrames', [])[:8]]})
			raise RuntimeError('Native DOM assertion failed')
		return result.get('result', {}).get('value')

	async def wait(self, expression, session, timeout=15):
		limit = min(END, time.monotonic() + timeout)
		while time.monotonic() < limit:
			if await self.evaluate(expression, session):
				return
			await asyncio.sleep(0.2)
		raise RuntimeError('Native DOM condition not reached')

	async def snapshot(self, name, session):
		value = await self.evaluate("({title:document.title,url:location.href,text:document.body.innerText.slice(0,6000),native_ready:Boolean(window.frappe?.app?.link_preview),route:window.frappe?.get_route?.(),list_kind:window.cur_list?.doctype,form_kind:window.cur_frm?.doctype,ajax_count:window.frappe?.request?.ajax_count})", session)
		# Remove query values from retained URL (callback state is never evidence).
		value['url'] = value['url'].split('?')[0]
		(OUTPUT / (name + '.json')).write_text(json.dumps(value, indent=2))
		if 'edited-reloaded' in name or name == 'failure':
			picture = await self.send('Page.captureScreenshot', {'format': 'jpeg', 'quality': 55, 'captureBeyondViewport': False}, session)
			(OUTPUT / (name + '.jpg')).write_bytes(base64.b64decode(picture['data']))
		if sum(path.stat().st_size for path in OUTPUT.iterdir() if path.is_file()) > OUTPUT_LIMIT:
			raise RuntimeError('Owned browser evidence output cap')

	async def flow(self):
		records=[]
		for entry in CONFIG['sites'].values():
			records.append(await self.flow_site(entry))
		for current,other in ((records[0],records[1]),(records[1],records[0])):
			for kind,name in (('Customer',other['customer']),('Sales Invoice',other['invoice'])):
				path='/api/method/frappe.desk.form.load.getdoc?'+urlencode({'doctype':kind,'name':name})
				status=await self.evaluate('fetch('+json.dumps(path)+',{credentials:"same-origin"}).then(r=>r.status)',current['session'])
				if status not in (401,403,404):
					raise RuntimeError('Foreign native record read was not denied')
			# Visible mounted native switcher reaches the configured central landing.
			await self.evaluate("(()=>{const button=document.querySelector('exe-service-switcher')?.shadowRoot?.querySelector('.exe-ss-logout');if(!button)throw Error('Mounted native logout missing');button.click();})()",current['session'])
			await self.wait("location.origin==='https://auth.platform.example.test' && location.pathname==='/logout'",current['session'])
			await self.snapshot('native-logout-'+current['plane'],current['session'])


	async def flow_site(self,entry):
		plane = 'a' if entry['client'] == 'erp-a' else 'b'
		label = plane.upper()
		target = await self.send('Target.createTarget', {'url': 'about:blank'})
		session = (await self.send('Target.attachToTarget', {'targetId': target['targetId'], 'flatten': True}))['sessionId']
		self.session = session
		await self.send('Page.enable', session=session)
		await self.send('Runtime.enable', session=session)
		await self.send('Network.enable', session=session)
		await self.send('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session)
		await self.send('Page.navigate', {'url': entry['origin'] + '/company-session/start'}, session)
		await self.wait("location.pathname.startsWith('/desk') && Boolean(window.frappe?.app?.link_preview) && Boolean(frappe.get_route()?.length) && !frappe.request.ajax_count", session, 25)
		await self.snapshot('desk-'+plane, session)
		await self.wait("window.cur_list?.doctype === 'Customer' && frappe.get_route()?.[0]==='List' && frappe.get_route()?.[1]==='Customer' && location.pathname.startsWith('/desk/customer') && Boolean(cur_list.page?.btn_primary?.[0]?.offsetParent) && !frappe.request.ajax_count", session)
		await self.snapshot('customer-list-'+plane, session)
		# Execute only the observed native New button. Missing RPCs are recorded
		# before admitting any additional server capability.
		await self.evaluate("cur_list.page.btn_primary[0].click()", session)
		await self.wait("Boolean(window.cur_frm?.doctype === 'Customer') || [...document.querySelectorAll('.modal.show button')].some(b=>b.innerText==='Edit Full Form')", session)
		await self.evaluate("[...document.querySelectorAll('.modal.show button')].find(b=>b.innerText==='Edit Full Form')?.click()", session)
		await self.wait("Boolean(window.cur_frm?.doctype === 'Customer' && document.querySelector('[data-fieldname=customer_name] input'))", session)
		await self.snapshot('customer-new-'+plane, session)
		# Drive actual visible native controls and their input/change/blur handlers.
		await self.evaluate("(()=>{for(const [field,value] of Object.entries("+json.dumps({'customer_name':'Browser Editor '+label,'customer_group':'ACL '+plane,'territory':'ACL '+plane})+")){const input=cur_frm.fields_dict[field].$input[0];input.focus();input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();}})()", session)
		await self.wait("cur_frm.doc.customer_name==="+json.dumps('Browser Editor '+label)+" && cur_frm.doc.customer_group==="+json.dumps('ACL '+plane)+" && cur_frm.doc.territory==="+json.dumps('ACL '+plane),session)
		await self.evaluate("cur_frm.page.btn_primary[0].click()", session)
		await self.wait("cur_frm.doc.doctype==='Customer' && !cur_frm.doc.__islocal && !cur_frm.doc.__unsaved && cur_frm.doc.customer_name==="+json.dumps('Browser Editor '+label),session)
		name = await self.evaluate('cur_frm.doc.name', session)
		await self.snapshot('customer-created-'+plane, session)
		await self.evaluate("(()=>{const input=cur_frm.fields_dict.customer_name.$input[0];input.focus();input.value="+json.dumps("Browser Edited "+label)+";input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();})()", session)
		await self.wait("cur_frm.doc.customer_name==="+json.dumps('Browser Edited '+label)+" && cur_frm.doc.__unsaved", session)
		await self.evaluate("cur_frm.page.btn_primary[0].click()", session)
		await self.wait("!cur_frm.doc.__unsaved && cur_frm.doc.customer_name==="+json.dumps('Browser Edited '+label), session)
		await self.evaluate('window.__owned_native_pre_reload = true', session)
		await self.send('Page.reload', session=session)
		await self.wait("!window.__owned_native_pre_reload && window.cur_frm?.doc.name===" + json.dumps(name) + " && cur_frm.doc.customer_name==="+json.dumps('Browser Edited '+label)+" && !cur_frm.doc.__unsaved", session, 20)
		await self.snapshot('customer-edited-reloaded-'+plane, session)
		await self.evaluate("(()=>{const link=[...document.querySelectorAll('.standard-sidebar-item a[href], .sidebar-item a[href], a[href]')].find(a=>a.offsetParent && a.getAttribute('href')==='/desk/sales-invoice');if(!link)throw Error('Missing visible native Sales Invoice link');link.click();})()",session)
		await self.wait("window.cur_list?.doctype==='Sales Invoice' && frappe.get_route()?.[0]==='List' && frappe.get_route()?.[1]==='Sales Invoice' && location.pathname.startsWith('/desk/sales-invoice') && Boolean(cur_list.page?.btn_primary?.[0]?.offsetParent) && !frappe.request.ajax_count",session)
		await self.evaluate("cur_list.page.btn_primary[0].click()",session)
		await self.wait("window.cur_frm?.doctype==='Sales Invoice' && Boolean(cur_frm.fields_dict.customer.$input?.[0])",session)
		await self.snapshot('invoice-new-'+plane,session)
		await self.evaluate("(()=>{const input=cur_frm.fields_dict.customer.$input[0];input.focus();input.value="+json.dumps(name)+";input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();})()",session)
		await self.wait("cur_frm.doc.customer==="+json.dumps(name)+" && cur_frm.doc.company==="+json.dumps('Editor '+plane)+" && cur_frm.doc.debit_to && cur_frm.doc.currency==='USD'",session)
		await self.evaluate("(()=>{const grid=cur_frm.fields_dict.items.grid;if(!grid.grid_rows.length)grid.wrapper.find('.grid-add-row')[0].click();const cell=grid.grid_rows[0].row.find('[data-fieldname=item_code]')[0];if(!cell)throw Error('Missing visible item cell');cell.click();})()",session)
		await self.wait("Boolean(cur_frm.fields_dict.items.grid.grid_rows[0].columns.item_code?.field?.$input?.[0])",session)
		await self.evaluate("(()=>{const input=cur_frm.fields_dict.items.grid.grid_rows[0].columns.item_code.field.$input[0];if(!input.offsetParent)throw Error('Item input not visible');input.focus();input.value="+json.dumps('Editor-Service-'+plane)+";input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();})()",session)
		await self.wait("cur_frm.doc.items[0].item_code==="+json.dumps('Editor-Service-'+plane)+" && cur_frm.doc.items[0].rate===100 && cur_frm.doc.items[0].income_account && cur_frm.doc.items[0].cost_center && !frappe.request.ajax_count",session)
		await self.evaluate("cur_frm.page.btn_primary[0].click()",session)
		await self.wait("cur_frm.doc.doctype==='Sales Invoice' && !cur_frm.doc.__islocal && !cur_frm.doc.__unsaved && cur_frm.doc.docstatus===0",session)
		invoice=await self.evaluate('cur_frm.doc.name',session)
		await self.snapshot('invoice-created-'+plane,session)
		await self.evaluate("(()=>{const input=cur_frm.fields_dict.po_no.$input[0];if(!input.offsetParent)cur_frm.fields_dict.po_no.$wrapper.closest('.form-section').find('.section-head')[0]?.click();})()",session)
		await self.evaluate("(()=>{const input=cur_frm.fields_dict.po_no.$input[0];if(!input.offsetParent)throw Error('PO input not visible');input.focus();input.value="+json.dumps('Browser Edited '+label)+";input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();})()",session)
		await self.wait("cur_frm.doc.po_no==="+json.dumps('Browser Edited '+label)+" && cur_frm.doc.__unsaved",session)
		await self.evaluate("(()=>{const row=cur_frm.fields_dict.items.grid.grid_rows[0];const cell=row.row.find('[data-fieldname=qty]')[0];if(!cell)throw Error('Missing visible quantity cell');cell.click();})()",session)
		await self.wait("Boolean(cur_frm.fields_dict.items.grid.grid_rows[0].columns.qty?.field?.$input?.[0])",session)
		await self.evaluate("(()=>{const input=cur_frm.fields_dict.items.grid.grid_rows[0].columns.qty.field.$input[0];if(!input.offsetParent)throw Error('Quantity input not visible');input.focus();input.value='2';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();})()",session)
		await self.wait("cur_frm.doc.items[0].qty===2 && cur_frm.doc.items[0].rate===100 && !frappe.request.ajax_count && cur_frm.doc.__unsaved",session)
		await self.evaluate("cur_frm.page.btn_primary[0].click()",session)
		await self.wait("!cur_frm.doc.__unsaved && cur_frm.doc.items[0].qty===2 && cur_frm.doc.po_no==="+json.dumps('Browser Edited '+label),session)
		await self.evaluate('window.__owned_native_pre_reload = true',session)
		await self.send('Page.reload',session=session)
		await self.wait("!window.__owned_native_pre_reload && window.cur_frm?.doc.name==="+json.dumps(invoice)+" && cur_frm.doc.po_no==="+json.dumps('Browser Edited '+label)+" && cur_frm.doc.docstatus===0 && cur_frm.doc.items[0].qty===2 && cur_frm.doc.items[0].item_code==="+json.dumps('Editor-Service-'+plane)+" && !cur_frm.doc.__unsaved",session,20)
		await self.snapshot('invoice-edited-reloaded-'+plane,session)
		return {'plane':plane,'session':session,'customer':name,'invoice':invoice}




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


async def main():
	profile = OUTPUT / 'chrome-profile'
	process = subprocess.Popen([CHROME, '--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + str(profile), '--disable-background-networking', '--no-first-run', '--no-default-browser-check', '--no-proxy-server', '--disk-cache-size=16777216', '--window-size=1280,900', 'about:blank'], stdout=(OUTPUT / 'chrome.stdout').open('xb'), stderr=(OUTPUT / 'chrome.stderr').open('xb'), start_new_session=True)
	browser, receiver, outcome = None, None, None
	closure = {'pid': process.pid, 'pgid': process.pid, 'uid': os.getuid(), 'reaped': False, 'group_closed': False}
	try:
		active = profile / 'DevToolsActivePort'
		for _ in range(50):
			if active.exists():
				break
			await asyncio.sleep(0.1)
		port, path = active.read_text().splitlines()[:2]
		async with websockets.connect('ws://127.0.0.1:' + port + path, max_size=8 * 1024**2, proxy=None) as socket:
			browser = Browser(socket)
			receiver = asyncio.create_task(browser.receive())
			try:
				await browser.flow()
				outcome = {'passed': True, 'scope': 'actual native A/B Customer and draft Sales Invoice UI create/edit/reload, foreign record read denial and native logout central landing; controlled authority; no genuine parent proof'}
			except Exception:
				if browser.session:
					await browser.snapshot('failure', browser.session)
				raise
			finally:
				await browser.send('Browser.close')
				await browser.client.aclose()
				receiver.cancel()
	except Exception as error:
		outcome = {'passed': False, 'class': type(error).__name__}
	finally:
		try:
			try:
				process.wait(timeout=3)
			except subprocess.TimeoutExpired:
				rows = group_rows(process.pid)
				if rows:
					if any(row['uid'] != os.getuid() for row in rows):
						raise RuntimeError('Owned browser group UID mismatch')
					os.killpg(process.pid, signal.SIGTERM)
				try:
					process.wait(timeout=2)
				except subprocess.TimeoutExpired:
					rows = group_rows(process.pid)
					if any(row['uid'] != os.getuid() for row in rows):
						raise RuntimeError('Owned browser group UID mismatch')
					if rows:
						os.killpg(process.pid, signal.SIGKILL)
					process.wait(timeout=2)
			closure['reaped'] = True
			rows = group_rows(process.pid)
			closure['observed_group'] = rows
			for _ in range(10):
				if not rows:
					break
				await asyncio.sleep(0.1)
				rows = group_rows(process.pid)
			if rows:
				if any(row['uid'] != os.getuid() for row in rows):
					raise RuntimeError('Owned browser group UID mismatch')
				os.killpg(process.pid, signal.SIGKILL)
				await asyncio.sleep(0.1)
				rows = group_rows(process.pid)
			closure['remaining_group'] = rows
			closure['group_closed'] = not rows
			if rows:
				raise RuntimeError('Owned browser group remains')
			# Only this exclusive profile is removed after independently closed
			# Chrome; it holds temporary native cookies, never review evidence.
			closure['profile_bytes'] = sum(path.stat().st_size for path in profile.rglob('*') if path.is_file())
			shutil.rmtree(profile)
			closure['profile_removed'] = not profile.exists()
			if closure['profile_bytes'] > 64 * 1024**2:
				raise RuntimeError('Owned browser profile budget exceeded')
		except Exception as error:
			closure['error'] = type(error).__name__
			outcome = {'passed': False, 'class': type(error).__name__}
		(OUTPUT / 'result.json').write_text(json.dumps({'outcome': outcome, 'requests': REQUESTS, 'failures': FAILURES, 'chrome': closure}, indent=2))
		if sum(path.stat().st_size for path in OUTPUT.iterdir() if path.is_file()) > OUTPUT_LIMIT:
			outcome = {'passed': False, 'class': 'OutputBudget'}
			(OUTPUT / 'result.json').write_text(json.dumps({'outcome': outcome, 'requests': REQUESTS, 'failures': FAILURES, 'chrome': closure}, indent=2))
		print(json.dumps({'outcome': outcome, 'output': str(OUTPUT)}))
	if not outcome or not outcome.get('passed'):
		sys.exit(1)


asyncio.run(main())
