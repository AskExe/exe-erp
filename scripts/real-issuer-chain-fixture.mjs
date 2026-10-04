// Ordinary owned ERP fixture. Actual WSGI/ORM/ACL, genuine Core replies.
import {OwnedContainers} from './lib/owned-containers.mjs'
import {spawnSync,execFileSync} from 'node:child_process'
import {randomBytes,createHash} from 'node:crypto'
import {mkdtempSync,readFileSync,writeFileSync,appendFileSync,statfsSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
export function allocateERPChain(){
 const suffix=randomBytes(16).toString('hex');
 return {suffix,sites:{a:'erp.acl-a-'+suffix.slice(0,12)+'.example.test',b:'erp.acl-b-'+suffix.slice(0,12)+'.example.test'}};
}
export async function createERPChain(allocation,nativeID,deadline,outerEnd,beginCleanup){
const source=resolve(fileURLToPath(new URL('..',import.meta.url)))
const output=mkdtempSync(join(tmpdir(),'erp-native-acl-'))
const suffix=allocation.suffix,prefix='erp-native-acl-'+suffix,network=prefix+'-net'
const {a,b}=allocation.sites
const pgImage='pgvector/pgvector@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc'
const redisImage='redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99'
const erpImage='sha256:b386cb66e7352036bc374f6d29475f74018c5ee6283a1fd8488a6dad019821e2'
const dbPassword=randomBytes(24).toString('hex'),adminPassword=randomBytes(24).toString('hex')
const ledgerPath=join(output,'commands.jsonl');writeFileSync(ledgerPath,'',{flag:'wx',mode:0o600});
const ids=[],volumes=new Set(),records=[],publicationErrors=[],quotaFailures=[];let omittedGroups=0,ledgerIncomplete=false;let raw=0,networkCreated=false,primary=null,secondary=[],cleaning=false,outputOverflow=false
const sourceBytes=Number(execFileSync('du',['-sk',source],{encoding:'utf8'}).trim().split(/\s/)[0])*1024
const setupEnd=Math.min(deadline,Date.now()+300000)
let phaseEnd=setupEnd
let commandInput;
function run(args,timeout=60000,allowFailure=false){
 const remaining=phaseEnd-Date.now();if(remaining<=0)throw Error('Owned fixture phase deadline')
 const redactedArgs=args.map(x=>x.replaceAll(dbPassword,'[fixture-secret]').replaceAll(adminPassword,'[fixture-secret]'))
 const allowance=1024**2-65536-raw,metadataReserve=Buffer.byteLength(JSON.stringify(redactedArgs))+1024
 const captureAllowance=Math.max(0,allowance-metadataReserve)
 if(!cleaning&&captureAllowance<130){outputOverflow=true;throw Error('Insufficient diagnostic allowance for complete owned ID before spawn')}
 // maxBuffer applies per stream. After exhaustion still attempt physical
 // cleanup, with bounded capture and no possible quota-affected proof.
 const result=spawnSync('docker',args,{input:commandInput,encoding:null,timeout:Math.min(timeout,remaining),maxBuffer:Math.max(1,Math.floor(captureAllowance/2))})
 const stdout=result.stdout??Buffer.alloc(0),stderr=result.stderr??Buffer.alloc(0)
 const ordinal=records.length+1
 const row={ordinal,args:redactedArgs,status:result.status,signal:result.signal,error:result.error?(result.error.code??'UnknownSpawnError'):null,stdout:stdout.length,stderr:stderr.length,quota_failed:false}
 // Charge complete output AND the redacted ledger exactly once; retain the same
 // 64KiB terminal reservation. An overflowing execution remains a failure.
 row.quota_failed=captureAllowance===0||['ENOBUFS','ERR_CHILD_PROCESS_STDIO_MAXBUFFER'].includes(row.error)||raw+stdout.length+stderr.length+Buffer.byteLength(JSON.stringify(row)+'\n')>1024**2-65536
 const ledgerRow=JSON.stringify(row)+'\n',groupBytes=stdout.length+stderr.length+Buffer.byteLength(ledgerRow)
 records.push(row)
 if(row.quota_failed){outputOverflow=true;ledgerIncomplete=true;omittedGroups++;if(quotaFailures.length<16)quotaFailures.push({ordinal,stdout:stdout.length,stderr:stderr.length,ledger_bytes:Buffer.byteLength(ledgerRow),status:row.status,signal:row.signal,error:row.error,remaining:allowance})}
 else raw+=groupBytes // Atomic admission of the entire retained group.
 const commandFailure=!allowFailure&&(result.error||result.status!==0)?(result.error??Error('Owned Docker command failed at '+ordinal)):null
 let publicationFailure
 const publish=(stage,action)=>{try{action()}catch(error){publicationFailure??=error;publicationErrors.push({stage,ordinal,class:error.constructor.name,code:error.code??null})}}
 if(!row.quota_failed){
  publish('stdout',()=>writeFileSync(join(output,ordinal+'.stdout'),stdout,{mode:0o600}))
  publish('stderr',()=>writeFileSync(join(output,ordinal+'.stderr'),stderr,{mode:0o600}))
  publish('command-ledger',()=>appendFileSync(ledgerPath,ledgerRow))
 }
 // A command's original failure precedes diagnostic publication failure.
 if(commandFailure)throw commandFailure
 if(publicationFailure)throw publicationFailure
 if(row.quota_failed&&!cleaning)throw Error('Complete fixture diagnostics exceed reserved 1MiB budget')
 return {text:stdout.toString('utf8'),status:result.status,stderr:stderr.toString('utf8'),signal:row.signal,error:row.error,quotaFailed:row.quota_failed||outputOverflow}
}
function absence(result,expected){
 if(!Number.isInteger(result.status)||result.status<=0||result.signal!==null||result.error!==null||result.quotaFailed||!result.stderr.includes(expected))throw Error('Owned resource absence unproved')
}
const owned=new OwnedContainers(args=>run(args).text)
const create=args=>{const id=owned.create(args);ids.push(id);for(const mount of JSON.parse(run(['inspect',id]).text)[0].Mounts??[])if(mount.Type==='volume'){if(!/^[a-f0-9]{64}$/.test(mount.Name))throw Error('Owned anonymous volume required');volumes.add(mount.Name)}return id}
function guard(){
 const disk=statfsSync(output);if(disk.bavail*disk.bsize<20*1024**3)throw Error('Fixture disk free floor20GiB')
 let bytes=1024**2+sourceBytes // Full retained output and complete readonly Source.
 for(const id of ids){
  const inspected=JSON.parse(run(['inspect','--size','--format','{"SizeRw":{{json .SizeRw}},"Mounts":{{json .Mounts}},"Config":{"User":{{json .Config.User}},"Env":{{json .Config.Env}}},"State":{"Running":{{json .State.Running}}}}',id]).text)
  bytes+=inspected.SizeRw??0
  for(const mount of inspected.Mounts??[])if(mount.Type==='volume'){
   if(!/^[0-9a-f]{64}$/.test(mount.Name))throw Error('Unexpected nonanonymous fixture volume')
   volumes.add(mount.Name)
  }
  if(inspected.State.Running&&inspected.Config.Env.includes('POSTGRES_USER=fixture'))bytes+=Number(run(['exec',id,'du','-sk','/var/lib/postgresql/data']).text.trim().split(/\s/)[0])*1024
  // Conservatively reserve every ERP tmpfs, including all sites/logs/tmp.
  if(inspected.Config.User==='frappe')bytes+=448*1024**2
 }
 if(!Number.isSafeInteger(bytes)||bytes>1024**3)throw Error('Fixture storage unavailable or above1GiB')
 return bytes
}
const pause=ms=>new Promise(r=>setTimeout(r,ms))
async function ready(id,args){
 for(let i=0;i<40;i++){
  if(Date.now()>=setupEnd)throw Error('Fixture setup deadline')
  if(run(['exec',id,...args],1000,true).status===0)return
  await pause(250)
 }
 throw Error('Owned prerequisite not ready')
}
const sourcePaths=['frappe/app.py','frappe/company_session.py','frappe/core/doctype/system_settings/system_settings.py','frappe/core/doctype/user_permission/user_permission.py','frappe/model/document.py','frappe/model/meta.py','frappe/permissions.py','frappe/utils/background_jobs.py','frappe/utils/scheduler.py','frappe/utils/task_queue.py','apps/erpnext/erpnext/exe_auth/hosted_site.py','apps/erpnext/erpnext/exe_auth/test_hosted_site.py','scripts/company-session-native.integration.py','scripts/company-session-native-setup.py']
sourcePaths.push('scripts/company-session-real-chain.py','scripts/real-issuer-chain-fixture.mjs')
const sourcePins=Object.fromEntries(sourcePaths.map(p=>[p,createHash('sha256').update(readFileSync(join(source,p))).digest('hex')]))
let erp,attached=false,address,networkID;
try{
 guard();for(const image of [pgImage,redisImage,erpImage])run(['image','inspect',image])
 networkID=run(['network','create','--internal',network]).text.trim();if(!/^[a-f0-9]{64}$/.test(networkID))throw Error('Owned network ID required');networkCreated=true
 const networkRow=JSON.parse(run(['network','inspect',networkID]).text)[0];if(networkRow.Id!==networkID||networkRow.Internal!==true)throw Error('Exact internal network required')
 const pg=create(['run','-d','--name',prefix+'-pg','--network',network,'--network-alias','postgres','--memory=512m','--pids-limit=64','-e','POSTGRES_USER=fixture','-e','POSTGRES_PASSWORD='+dbPassword,'-e','POSTGRES_DB=fixture',pgImage])
 const redis=create(['run','-d','--name',prefix+'-redis','--network',network,'--network-alias','redis','--memory=128m','--pids-limit=32',redisImage,'redis-server','--save','','--appendonly','no'])
 await ready(pg,['pg_isready','-U','fixture','-d','fixture']);await ready(redis,['redis-cli','ping'])
 erp=create(['run','-d','--name',prefix+'-erp','--network',network,'--user=frappe','--memory=1g','--pids-limit=128','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
  '--tmpfs','/tmp:rw,nosuid,size=64m','--tmpfs','/home/frappe/frappe-bench/sites:rw,uid=1000,gid=1000,size=256m',
  '--tmpfs','/home/frappe/frappe-bench/logs:rw,uid=1000,gid=1000,size=64m','--tmpfs','/home/frappe/logs:rw,uid=1000,gid=1000,size=64m',
  '--mount','type=bind,src='+source+',dst=/home/frappe/frappe-bench/apps/frappe,readonly',
  '--mount','type=bind,src='+join(source,'apps/erpnext')+',dst=/home/frappe/frappe-bench/apps/erpnext,readonly',
  '-e','PYTHONDONTWRITEBYTECODE=1','--entrypoint=/bin/sleep',erpImage,'infinity'])
 guard()
 const remaining=setupEnd-Date.now();if(remaining<=0)throw Error('Fixture setup deadline')
 run(['exec','-e','NATIVE_ACL_FIXTURE_ID='+suffix,'-e','NATIVE_ACL_SITE_A='+a,'-e','NATIVE_ACL_SITE_B='+b,'-e','NATIVE_ACL_DB_PASSWORD='+dbPassword,'-e','NATIVE_ACL_ADMIN_PASSWORD='+adminPassword,
  erp,'/home/frappe/frappe-bench/env/bin/python','-B','/home/frappe/frappe-bench/apps/frappe/scripts/company-session-native-setup.py'],remaining)
 guard()

 if(!/^[a-f0-9]{64}$/.test(nativeID))throw Error('Successful owned Core-native ID required');
 run(['network','connect',networkID,nativeID]);attached=true;
 const topology=JSON.parse(run(['inspect',nativeID]).text)[0];
 if(topology.Id!==nativeID)throw Error('Exact Core-native identity required');
 const attachment=topology.NetworkSettings.Networks[network];if(attachment?.NetworkID!==networkID)throw Error('Exact owned attachment required');address=attachment.IPAddress;
 const octets=address?.split('.').map(Number);
 if(!octets||octets.length!==4||octets.some(n=>!Number.isInteger(n)||n<0||n>255)||address!==octets.join('.')||!(octets[0]===10||(octets[0]===172&&octets[1]>=16&&octets[1]<=31)||(octets[0]===192&&octets[1]===168)))throw Error('Owned internal IPv4 required');

 return {output,sites:allocation.sites,
  beginTests(end){if(end>Date.now()+120000)throw Error('Test phase bound');phaseEnd=end},
  call(plane,profile,secret,operation,extra={}){
   try{guard();commandInput=JSON.stringify({operation,plane,profile,client_secret:secret,native_address:address,sites:allocation.sites,fixture_id:suffix,...extra});
   const text=run(['exec','-i','--workdir','/home/frappe/frappe-bench/sites',erp,'/home/frappe/frappe-bench/env/bin/python','-B','/home/frappe/frappe-bench/apps/frappe/scripts/company-session-real-chain.py'],120000).text;
    const lines=text.split('\n').filter(line=>line.startsWith('CHAIN_RESULT='));if(lines.length!==1)throw Error('Exact native result required');return JSON.parse(lines[0].slice(13));
   }catch(error){primary??={class:error.constructor.name,message:error.message};throw error}finally{commandInput=undefined}
  },
  close:cleanup};
 }catch(error){primary={class:error.constructor.name,message:error.message};try{cleanup(beginCleanup())}catch{}throw error}
 function cleanup(cleanupEnd){
  cleaning=true;phaseEnd=Math.min(cleanupEnd,outerEnd);
  const attempt=(stage,f)=>{try{f()}catch(error){secondary.push({stage,message:error.message})}};
  attempt('container-cleanup',()=>owned.removeAll());
  for(const id of ids)attempt('container-absence',()=>{const result=run(['inspect',id],10000,true);absence(result,'No such object: '+id)});
  for(const name of volumes)attempt('volume-absence',()=>{const result=run(['volume','inspect',name],10000,true);absence(result,name+': no such volume')});
  if(attached){attempt('native-detach',()=>run(['network','disconnect',networkID,nativeID]));attempt('native-attachment-absence',()=>{const row=JSON.parse(run(['inspect',nativeID]).text)[0];if(row.Id!==nativeID||Object.values(row.NetworkSettings.Networks).some(value=>value.NetworkID===networkID))throw Error('Native attachment absence unproved')})}
  if(networkCreated)attempt('network-cleanup',()=>{run(['network','rm',networkID]);const result=run(['network','inspect',networkID],10000,true);absence(result,'network '+networkID+' not found')});
  let postPins;attempt('source-stability',()=>{postPins=Object.fromEntries(sourcePaths.map(p=>[p,createHash('sha256').update(readFileSync(join(source,p))).digest('hex')]));if(JSON.stringify(postPins)!==JSON.stringify(sourcePins))throw Error('Source changed')});
  if(outputOverflow)secondary.push({stage:'complete-raw-budget'});
  attempt('cleanup-receipt',()=>{const ledger=readFileSync(ledgerPath);const command_ledger={path:ledgerPath,bytes:ledger.length,sha256:createHash('sha256').update(ledger).digest('hex'),records:records.length,complete:!ledgerIncomplete&&!publicationErrors.some(row=>row.stage==='command-ledger')};const receipt=JSON.stringify({scope:'real ERP WSGI/Core HTTP/native ACL; operator bootstrap; synthetic commerce/technical',primary,secondary,publicationErrors,quotaFailures,omittedGroups,ids,volumes:[...volumes],network,networkID,nativeID,attached,raw,sourceBytes,sourcePins,postPins,command_ledger})+'\n';if(Buffer.byteLength(receipt)>65536||raw+Buffer.byteLength(receipt)>1024**2)throw Error('Complete native diagnostic bound');writeFileSync(join(output,'result.json'),receipt,{mode:0o600})});
  if(secondary.length||publicationErrors.length){const error=Error('Owned ERP cleanup/reporting failed');error.cleanup=secondary;error.publication=publicationErrors;error.quota={omittedGroups,failures:quotaFailures};throw error}

 }
}
