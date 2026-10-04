// Ordinary local fixture. OwnedContainers is byte-exact Core d622048 helper.
// Real native sites/ACL; central envelopes remain controlled. No pulls/builds.
import {OwnedContainers} from './lib/owned-containers.mjs'
import {spawnSync,execFileSync} from 'node:child_process'
import {randomBytes,createHash} from 'node:crypto'
import {mkdtempSync,readFileSync,writeFileSync,statfsSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'

if(process.env.ERP_NATIVE_EXECUTE!=='true')throw Error('Local fixture execution remains explicitly held')
const httpMode=process.env.ERP_NATIVE_HTTP==='true'
const source=resolve(fileURLToPath(new URL('..',import.meta.url)))
const output=mkdtempSync(join(tmpdir(),'erp-native-acl-'))
const suffix=randomBytes(16).toString('hex'),prefix='erp-native-acl-'+suffix,network=prefix+'-net'
const a='erp.acl-a-'+suffix.slice(0,12)+'.example.test',b='erp.acl-b-'+suffix.slice(0,12)+'.example.test'
const pgImage='pgvector/pgvector@sha256:00ba258a66dac104fd5171074a0084462a64a1369d8513f3d0a634e2f24d15bc'
const redisImage='redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99'
const erpImage='sha256:b386cb66e7352036bc374f6d29475f74018c5ee6283a1fd8488a6dad019821e2'
const dbPassword=randomBytes(24).toString('hex'),adminPassword=randomBytes(24).toString('hex')
const ids=[],volumes=new Set(),records=[];let raw=0,networkCreated=false,primary=null,secondary=[],cleaning=false,outputOverflow=false
const sourceBytes=Number(execFileSync('du',['-sk',source],{encoding:'utf8'}).trim().split(/\s/)[0])*1024
const setupEnd=Date.now()+300000
let phaseEnd=setupEnd
function run(args,timeout=60000,allowFailure=false){
 const remaining=phaseEnd-Date.now();if(remaining<=0)throw Error('Owned fixture phase deadline')
 const result=spawnSync('docker',args,{encoding:null,timeout:Math.min(timeout,remaining),maxBuffer:1024**2})
 const stdout=result.stdout??Buffer.alloc(0),stderr=result.stderr??Buffer.alloc(0)
 raw+=stdout.length+stderr.length
 const ordinal=records.length+1
 writeFileSync(join(output,ordinal+'.stdout'),stdout);writeFileSync(join(output,ordinal+'.stderr'),stderr)
 records.push({ordinal,args:args.map(x=>x.replaceAll(dbPassword,'[fixture-secret]').replaceAll(adminPassword,'[fixture-secret]')),status:result.status,signal:result.signal,error:result.error?.code??null,stdout:stdout.length,stderr:stderr.length})
 if(raw>1024**2){outputOverflow=true;if(!cleaning)throw Error('Complete fixture raw output exceeds 1MiB')}
 if(!allowFailure&&(result.error||result.status!==0))throw Error('Owned Docker command failed at '+ordinal)
 return {text:stdout.toString('utf8'),status:result.status,stderr:stderr.toString('utf8')}
}
const owned=new OwnedContainers(args=>run(args).text)
const create=args=>{const id=owned.create(args);ids.push(id);return id}
function guard(){
 const disk=statfsSync(output);if(disk.bavail*disk.bsize<20*1024**3)throw Error('Fixture disk free floor20GiB')
 let bytes=1024**2+sourceBytes // Full retained output and complete readonly Source.
 for(const id of ids){
  const inspected=JSON.parse(run(['inspect','--size',id]).text)[0]
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
if(httpMode)sourcePaths.push('scripts/company-session-wsgi.integration.py')
const sourcePins=Object.fromEntries(sourcePaths.map(p=>[p,createHash('sha256').update(readFileSync(join(source,p))).digest('hex')]))
try{
 guard();for(const image of [pgImage,redisImage,erpImage])run(['image','inspect',image])
 run(['network','create','--internal',network]);networkCreated=true
 const pg=create(['run','-d','--name',prefix+'-pg','--network',network,'--network-alias','postgres','--memory=512m','--pids-limit=64','-e','POSTGRES_USER=fixture','-e','POSTGRES_PASSWORD='+dbPassword,'-e','POSTGRES_DB=fixture',pgImage])
 const redis=create(['run','-d','--name',prefix+'-redis','--network',network,'--network-alias','redis','--memory=128m','--pids-limit=32',redisImage,'redis-server','--save','','--appendonly','no'])
 await ready(pg,['pg_isready','-U','fixture','-d','fixture']);await ready(redis,['redis-cli','ping'])
 const erp=create(['run','-d','--name',prefix+'-erp','--network',network,'--user=frappe','--memory=1g','--pids-limit=128','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
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
 phaseEnd=Date.now()+120000
 for(const plane of httpMode?['a','b']:[null]){
  run(['exec','--workdir','/home/frappe/frappe-bench/sites',erp,'/home/frappe/frappe-bench/env/bin/python','-B',
   '/home/frappe/frappe-bench/apps/frappe/scripts/'+(httpMode?'company-session-wsgi.integration.py':'company-session-native.integration.py'),
   '--sites-path','/home/frappe/frappe-bench/sites','--site-a',a,'--site-b',b,'--fixture-id',suffix,...(plane?['--plane',plane]:[])],120000)
 }
 guard()
}catch(error){primary={class:error.constructor.name,message:error.message};process.exitCode=1}
finally{
 cleaning=true
 phaseEnd=Date.now()+60000
 try{owned.removeAll()}catch(error){secondary.push({stage:'container-cleanup',message:error.message});process.exitCode=1}
 for(const id of ids){try{const absent=run(['inspect',id],10000,true);if(absent.status===0||!absent.stderr.includes('No such object'))throw Error('Container remains or absence unproved')}catch(error){secondary.push({stage:'container-absence',id,message:error.message});process.exitCode=1}}
 for(const name of volumes){try{const absent=run(['volume','inspect',name],10000,true);if(absent.status===0||!absent.stderr.includes(': no such volume'))throw Error('Volume remains or absence unproved')}catch(error){secondary.push({stage:'volume-absence',name,message:error.message});process.exitCode=1}}
 if(networkCreated){try{run(['network','rm',network]);const absent=run(['network','inspect',network],10000,true);if(absent.status===0||!absent.stderr.includes('network '+network+' not found'))throw Error('Network remains or absence unproved')}catch(error){secondary.push({stage:'network-cleanup',message:error.message});process.exitCode=1}}
 const postPins=Object.fromEntries(sourcePaths.map(p=>[p,createHash('sha256').update(readFileSync(join(source,p))).digest('hex')]))
 if(JSON.stringify(postPins)!==JSON.stringify(sourcePins)){secondary.push({stage:'source-stability'});process.exitCode=1}
 if(outputOverflow){secondary.push({stage:'complete-raw-budget'});process.exitCode=1}
 writeFileSync(join(output,'result.json'),JSON.stringify({scope:httpMode?'actual ERP WSGI/private HTTP/native ACL; controlled central authority':'actual native ERP ACL; controlled central envelopes',primary,secondary,ids,volumes:[...volumes],network,raw,sourceBytes,sourcePins,postPins,records},null,2))
 console.log(JSON.stringify({passed:!primary&&!secondary.length,output,primary,secondary,containers:ids.length,raw}))
}
