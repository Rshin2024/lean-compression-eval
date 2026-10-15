#!/usr/bin/env python3
"""Deploy and synchronize credential-free model partitions on the new VM fleet."""
import concurrent.futures as cf,fcntl,hashlib,json,os,pathlib,shlex,subprocess,tarfile,time,threading
CLOUD=pathlib.Path(__file__).resolve().parent;WORK=CLOUD.parent;ROOT=WORK.parents[1];REPO=ROOT/'outputs/lean-compression-eval'
REMOTE='/home/leanrunner/om100-unlimited';REMOTE_WORK=REMOTE+'/work/'+WORK.name;REMOTE_REPO=REMOTE+'/outputs/lean-compression-eval'
SSH=['ssh','-F',str(CLOUD/'ssh-config')]
HOST_LOCKS={}
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,j):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(j,indent=2)+'\n');t.replace(p)
def run(cmd,timeout=600,**kw):
 p=subprocess.run(cmd,capture_output=True,timeout=timeout,**kw)
 if p.returncode:raise RuntimeError((p.stderr.decode(errors='replace') if isinstance(p.stderr,bytes) else p.stderr)[-3000:])
 return p.stdout
def remote(alias,cmd,**kw):return run([*SSH,alias,cmd],**kw)
def rsync(source,dest,*,immutable=False,logs=False):
 args=['rsync','-az','--exclude=.*','--include=*/','--include=*.jsonl' if logs else '--include=*.json','--exclude=*']
 if immutable:args.append('--ignore-existing')
 return run([*args,'-e',shlex.join(SSH),source,dest],timeout=600)
def bundle(row,plan):
 paths=list((REPO/'src').rglob('*.py'))+[REPO/'lean/Evaluator.lean',REPO/'runs'/row['directory']/'manifest.json',WORK/'verify_runtime_v2.py',WORK/'runtime-amendment-v2.json',CLOUD/'checker_worker.py',CLOUD/'verification-fleet-plan.json']
 amendment=json.loads((WORK/'runtime-amendment-v2.json').read_text())
 for key in ['verification_environment','runtime_build','reference_index']:
  descriptor=amendment[key];p=WORK/descriptor['path']
  if not p.resolve().is_relative_to(WORK.resolve()) or sha(p)!=descriptor['sha256']:raise ValueError('Bound runtime artifact mismatch')
  paths.append(p)
 if sha(REPO/'runs'/row['directory']/'manifest.json')!=row['manifest_sha256']:raise ValueError('Changed manifest before upload')
 path=CLOUD/('bundle-'+row['model']+'.tar.gz')
 with tarfile.open(path,'w:gz',compresslevel=1) as out:
  for p in paths:out.add(p,arcname=p.relative_to(ROOT).as_posix(),recursive=False)
 receipt={'paths':{p.relative_to(ROOT).as_posix():sha(p) for p in paths},'bundle_sha256':sha(path),'bytes':path.stat().st_size,'contains_api_credentials':False}
 save(CLOUD/(row['model']+'-bundle.json'),receipt)
 return path,receipt
def partition(row,plan):
 model=row['model'];alias=row['ssh_alias'];statusfile=CLOUD/(model+'-local-sync.json');state={'model':model,'region':row['region'],'state':'awaiting_runtime','updated_at':time.time()};save(statusfile,state)
 while True:
  f=CLOUD/(row['region']+'-runtime.json')
  if f.exists() and json.loads(f.read_text()).get('state')=='ready':break
  time.sleep(5)
 state['state']='deploying';save(statusfile,state)
 try:
  with HOST_LOCKS[alias]:
   archive,receipt=bundle(row,plan)
   remote(alias,'mkdir -p '+shlex.quote(REMOTE))
   with archive.open('rb') as stream:run([*SSH,alias,'tar xzf - --skip-old-files -C '+shlex.quote(REMOTE)],stdin=stream)
   # Compare every source and manifest byte before executing a checker.
   code='import hashlib,json,pathlib;root=pathlib.Path('+repr(REMOTE)+');pairs='+repr(receipt['paths'])+';bad=[p for p,h in pairs.items() if hashlib.sha256((root/p).read_bytes()).hexdigest()!=h];print(json.dumps({"checked":len(pairs),"bad":bad}));assert not bad'
   check=remote(alias,'python3 -c '+shlex.quote(code));save(CLOUD/(model+'-cloud-source-check.json'),json.loads(check))
  command=['python3',REMOTE_WORK+'/cloud/checker_worker.py','--model',model,'--workers',str(row['workers'])]
  log=REMOTE_WORK+'/cloud/'+model+'-worker.log'
  pid=remote(alias,'nohup '+shlex.join(command)+' >'+shlex.quote(log)+' 2>&1 < /dev/null & echo $!').decode().strip()
  state.update(state='syncing',worker_pid=pid,deployed_at=time.time(),bundle_sha256=receipt['bundle_sha256']);save(statusfile,state)
  runpath=REPO/'runs'/row['directory'];source=runpath/'generations';source.mkdir(exist_ok=True)
  target=runpath/'evaluations/v2_threads1';target.mkdir(parents=True,exist_ok=True)
  ids={s['attempt_id'] for s in json.loads((runpath/'manifest.json').read_text())['slots']}
  while True:
   try:
    rsync(str(source)+'/',alias+':'+REMOTE_REPO+'/runs/'+row['directory']+'/generations/',immutable=True)
    rsync(alias+':'+REMOTE_REPO+'/runs/'+row['directory']+'/evaluations/v2_threads1/',str(target)+'/',immutable=True)
    rsync(alias+':'+REMOTE_REPO+'/runs/'+row['directory']+'/evaluations/v2_threads1/',str(target)+'/',logs=True)
    raw=remote(alias,'cat '+shlex.quote(REMOTE_WORK+'/cloud/'+model+'-status.json'));cloud=json.loads(raw);save(CLOUD/(model+'-cloud-status.json'),cloud)
    local=len({p.stem for p in target.glob('*.json')} & ids)
    state.update(state=cloud['state'],cloud=cloud,saved=local,updated_at=time.time(),sync_error=None);save(statusfile,state)
    if cloud['state']=='complete' and local==len(ids):return state
    if cloud['state']=='needs_attention' and cloud['active_workers']==0:return state
   except Exception as e:
    state.update(updated_at=time.time(),sync_error=str(e)[-3000:]);save(statusfile,state)
   time.sleep(10)
 except Exception as e:
  state.update(state='needs_attention',updated_at=time.time(),error=str(e)[-3000:]);save(statusfile,state);return state

def main():
 lock=(CLOUD/'fleet-controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 plan=json.loads((CLOUD/'verification-fleet-plan.json').read_text())
 for row in plan['assignments']:HOST_LOCKS.setdefault(row['ssh_alias'],threading.Lock())
 if len(plan['assignments'])!=8 or len({x['model'] for x in plan['assignments']})!=8:raise ValueError('Must have8disjoint model owners')
 for host in {r['ssh_alias'] for r in plan['assignments']}:
  if sum(r['workers'] for r in plan['assignments'] if r['ssh_alias']==host)>48:raise ValueError('Host worker budget exceeded')
 if sha(CLOUD/'checker_worker.py')!=plan['checker_worker_sha256']:raise ValueError('Worker source not pinned')
 save(CLOUD/'fleet-controller-process.json',{'pid':os.getpid(),'started_at':time.time(),'plan_sha256':sha(CLOUD/'verification-fleet-plan.json')})
 with cf.ThreadPoolExecutor(max_workers=8) as pool:
  pending={pool.submit(partition,r,plan):r['model'] for r in plan['assignments']}
  while pending:
   done,_=cf.wait(pending,timeout=5,return_when=cf.FIRST_COMPLETED)
   for f in done:
    m=pending.pop(f)
    try:r=f.result()
    except Exception as e:r={'model':m,'state':'needs_attention','error':str(e)}
    print(json.dumps(r),flush=True)
   states=[]
   for p in CLOUD.glob('*-local-sync.json'):states.append(json.loads(p.read_text()))
   save(WORK/'fleet-verification-status.json',{'updated_at':time.time(),'models':states,'checked_total':sum(s.get('cloud',{}).get('checked',0) for s in states),'saved_total':sum(s.get('saved',0) for s in states),'active_workers':sum(s.get('cloud',{}).get('active_workers',0) for s in states)})
if __name__=='__main__':main()
