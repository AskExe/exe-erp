// Execute native permission module: V2 ceiling intersects, never creates rights.
const {readFileSync}=require('node:fs'); const vm=require('node:vm'); const assert=require('node:assert/strict');
const frappe={provide(){this.perm={}},boot:{},session:{user:'member'},get_meta:()=>null};
const context={frappe,window:{},$:{extend:Object.assign}};
vm.runInNewContext(readFileSync(require('node:path').join(__dirname,'../frappe/public/js/frappe/model/perm.js'),'utf8'),context);
const native=[{read:1,select:1,write:1,create:0,delete:1,submit:1,cancel:1,share:1}];
assert.equal(frappe.perm.company_editor_ceiling('Customer',native),native);
frappe.boot.company_editor={version:2,writable_doctypes:['Customer','Sales Invoice']};
let result=frappe.perm.company_editor_ceiling('Customer',native)[0];
assert.equal(result.write,1);assert.equal(result.create,0);assert.equal(result.read,1);
for(const right of ['delete','submit','cancel','share'])assert.equal(result[right],0);
assert.equal(native[0].delete,1);
assert.equal(frappe.perm.company_editor_ceiling('User',native)[0].write,0);
frappe.boot.company_editor.writable_doctypes=[];
assert.equal(frappe.perm.company_editor_ceiling('Customer',native)[0].write,0);
frappe.boot.company_editor={version:99,writable_doctypes:['Customer']};
assert.equal(frappe.perm.company_editor_ceiling('Customer',native)[0].write,0);
console.log('6 native UI permission ceiling controls PASS; controlled module, not browser acceptance');

(async () => {
 const path=require('node:path');
 const read=p=>readFileSync(path.join(__dirname,'../frappe/public/js/',p),'utf8');
 for(const localFailure of [false,true]) {
  let calls=0, redirects=[];
  const f={boot:{company_editor:{version:2,auth_origin:'https://auth.exact.example.test'}},csrf_token:'bound-csrf',app:{}};
  const c={frappe:f,window:{frappe:f,location:{assign:url=>redirects.push(url)}},document:{body:{inert:false},querySelector:()=>{throw Error('hosted must not need switcher')}},AbortController,setTimeout,clearTimeout,Promise,fetch:async(url,opts)=>{calls++;assert.equal(url,'/api/method/logout');assert.equal(opts.credentials,'same-origin');assert.equal(opts.headers['X-Frappe-CSRF-Token'],'bound-csrf');if(localFailure)throw Error('controlled local failure');return {ok:true};}};
  vm.runInNewContext(read('frappe/utils/logout.js'),c);
  await f.logout();
  assert.equal(f.company_editor_terminal,true);assert.equal(f.app.logged_out,true);assert.equal(c.document.body.inert,true);
  assert.deepEqual(redirects,['https://auth.exact.example.test/logout']);
  await f.logout();assert.equal(calls,1);
 }
 // Execute the real Desk and mounted switcher method bodies; both dispatch the
 // same hosted handler before any standalone domain/session fallback.
 const native=read('frappe/desk.js');
 let dispatched=0;
 const f={boot:{company_editor:{version:2}},company_editor_logout:()=>{dispatched++;return Promise.resolve();},confirm:(_text,fn)=>fn()};
 const ctxt={frappe:f,__:v=>v,document:{querySelector:()=>{throw Error('fallback')}}};
 const method=vm.runInNewContext('({' + native.slice(native.indexOf('\tlogout() {'),native.indexOf('\thandle_session_expired() {')) + '}).logout',ctxt);
 method.call({});assert.equal(dispatched,1);
 const sw=read('exe-service-switcher.js');
 const end=sw.indexOf('\n  }',sw.indexOf('  async _logout() {'))+4;
 const switcher=vm.runInNewContext('({' + sw.slice(sw.indexOf('  async _logout() {'),end) + '})._logout',{window:{frappe:f}});
 await switcher.call({});assert.equal(dispatched,2);
 // Default-off native utility retains the original local RPC/login path.
 let methodName,location={};
 const legacy={call:opts=>{methodName=opts.method;opts.callback({});}};
 vm.runInNewContext(read('frappe/utils/logout.js'),{frappe:legacy,document:{querySelector:()=>null},window:{location}});
 legacy.logout();assert.equal(methodName,'logout');assert.equal(location.href,'/login');
 console.log('hosted native Desk/switcher/logout failure/terminal/idempotence and standalone controls PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});

// Execute the actual native404 branch; reject optional denied calls without route retargeting.
{
const source=readFileSync(require('node:path').join(__dirname,'../frappe/public/js/frappe/request.js'),'utf8');
const body=source.slice(source.indexOf('\t\t404: function (xhr) {'),source.indexOf('\t\t403: function (xhr) {'));
function probe(version,url,args={}) {
 let rejected=0, messages=[];
 const opts={url,args,error_callback:()=>rejected++};
 const frappe={boot:version===undefined?{}:{company_editor:{version}},msgprint:value=>messages.push(value)};
 const fn=vm.runInNewContext('({'+body+'})[404]',{opts,frappe,__:s=>s});fn({});
 assert.equal(rejected,1);return messages;
}
const methods=['frappe.translate.get_boot_translations','frappe.core.doctype.session_default_settings.session_default_settings.get_session_default_values','frappe.core.doctype.background_task.background_task.get_recent_tasks','frappe.desk.desktop.get_onboarding_data','frappe.desk.search.get_link_title','frappe.model.utils.user_settings.save','frappe.desk.doctype.route_history.route_history.deferred_insert'];
for(const method of methods){
 const url='/api/method/'+method;
 assert.equal(probe(2,url).length,0);
 for(const version of [undefined,1,99])assert.equal(probe(version,url)[0].re_route,true);
 assert.equal(probe(2,url+'?unknown=1')[0].re_route,true);
}
const page='/api/method/frappe.desk.desk_page.getpage';
assert.equal(probe(2,page,{name:'desktop'}).length,0);
for(const name of [undefined,'other-page','Desktop'])assert.equal(probe(2,page,{name})[0].re_route,true);
for(const url of ['/api/method/frappe.desk.form.load.getdoc','/api/method/frappe.desk.form.save.savedocs','/api/method/frappe.client.delete','/api/method/unknown'])assert.equal(probe(2,url)[0].re_route,true);
console.log('43 actual native404 branch controls PASS: exact ancillary rejection only; business/unknown/default-off/v1 messages retained');
}
