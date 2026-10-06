/** Only successful detached run/create outputs establish cleanup ownership.
 * Container IDs, not names/labels/timestamps/discovery, are retained locally.
 * --volumes removes that container's anonymous volumes; named volumes remain
 * the fixture's separate explicitly tracked responsibility (Docker CLI contract).
 */
export class OwnedContainers {
 #ids=new Set()
 constructor(run){if(typeof run!=='function')throw Error('Owned Docker executor required');this.run=run}
 create(args){
  if(!Array.isArray(args)||args.some(a=>typeof a!=='string')||!['run','create'].includes(args[0])||!args.includes('--name')||args.includes('--rm')||args[0]==='run'&&!args.includes('-d')&&!args.includes('--detach'))throw Error('Owned detached container creation required')
  const name=args[args.indexOf('--name')+1]
  if(!name||!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,200}$/.test(name))throw Error('Invalid owned container name')
  const id=this.run(args).trim()
  if(!/^[0-9a-f]{64}$/.test(id))throw Error('Container creation did not return an exact owned ID')
  this.#ids.add(id)
  return id
 }
 removeAll(){
  let failed=false
  for(const id of [...this.#ids].reverse()){
   try{this.run(['rm','--force','--volumes',id]);this.#ids.delete(id)}catch{failed=true}
  }
  if(failed)throw Error('Owned container cleanup incomplete')
 }
}

/** Cleanup is a CI gate. Preserve an existing failure without exposing executor
 * errors (which can contain private runtime arguments or engine diagnostics). */
export function finishOwnedContainers(containers){
 try{containers.removeAll();return true}catch{console.error('Owned container cleanup failed');if(!process.exitCode)process.exitCode=1;return false}
}
