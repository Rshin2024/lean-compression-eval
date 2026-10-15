#!/usr/bin/env python3
"""Additional workers on the SAME host/filesystem; unchanged verifier owns attempt claims."""
import argparse,concurrent.futures as cf,fcntl,hashlib,json,os,pathlib,subprocess,time
CLOUD=pathlib.Path(__file__).resolve().parent;WORK=CLOUD.parent;ROOT=WORK.parents[1];REPO=ROOT/'outputs/lean-compression-eval'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,j):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(j,indent=2)+'\n');t.replace(p)
def main():
 p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--workers',type=int,default=48);a=p.parse_args()
 if any(os.environ.get(k) for k in ['OPENAI_API_KEY','ANTHROPIC_API_KEY']):raise ValueError('Cloud verifier must not inherit model API credentials')
 plan=json.loads((CLOUD/'verification-sol-burst-v1.json').read_text());matches=[x for x in plan['assignments'] if x['model']==a.model];
 if len(matches)!=1:raise ValueError('Model must have one fleet owner')
 row=matches[0]
 stem=a.model+'-sol-burst-v1'
 if sha(pathlib.Path(__file__))!=plan['checker_worker_sha256']:raise ValueError('Checker scheduler changed')
 if not 1<=a.workers<=row['workers']:raise ValueError('Capacity exceeds assigned plan')
 lock=(CLOUD/(stem+'.lock')).open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 mem=int(next(l for l in pathlib.Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemTotal:')).split()[1])/1024
 if mem<row['host_workers_total']*8192+16384:raise ValueError('Insufficient physical memory for frozen per-attempt limit')
 run=REPO/'runs'/row['directory'];verifier=WORK/'verify_runtime_v2.py'
 if sha(run/'manifest.json')!=row['manifest_sha256'] or sha(verifier)!=plan['verifier_sha256'] or sha(WORK/'runtime-amendment-v2.json')!=plan['amendment_sha256']:raise ValueError('Frozen inputs changed')
 manifest=json.loads((run/'manifest.json').read_text());ids={x['attempt_id'] for x in manifest['slots']};out=run/'evaluations/v2_threads1';out.mkdir(parents=True,exist_ok=True)
 check=subprocess.run(['python3',str(verifier),'validate','--run',str(run)],capture_output=True,text=True,timeout=600)
 save(CLOUD/(stem+'-preflight.json'),{'exit_code':check.returncode,'stdout':check.stdout,'stderr':check.stderr,'at_epoch':time.time()})
 if check.returncode:raise RuntimeError('Frozen verifier preflight failed')
 def count(d):return len({x.stem for x in d.glob('*.json')} & ids)
 def one():
  proc=subprocess.run(['python3',str(verifier),'evaluate','--run',str(run),'--limit','4'],capture_output=True,text=True,timeout=4*(180+900+60)+120)
  try:s=json.loads(proc.stdout)
  except ValueError:s={}
  return {'returncode':proc.returncode,'summary':s,'stderr':proc.stderr[-4000:],'finished_at':time.time()}
 started=time.time();errors=[]
 with cf.ThreadPoolExecutor(max_workers=a.workers) as pool:
  pending=set()
  while True:
   generated=count(run/'generations');checked=count(out)
   available_mib=int(next(l for l in pathlib.Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])/1024
   pressure_hold=available_mib<32768
   while not errors and not pressure_hold and len(pending)<a.workers and generated-checked-len(pending)>0:pending.add(pool.submit(one))
   state='needs_attention' if errors else 'complete' if checked==len(ids) and not pending else 'memory_pressure_hold' if pressure_hold else 'running'
   save(CLOUD/(stem+'-status.json'),{'model':a.model,'state':state,'pid':os.getpid(),'started_at':started,'updated_at':time.time(),'capacity':a.workers,'available_memory_mib':available_mib,'memory_pressure_hold':pressure_hold,'active_workers':len(pending),'generated':generated,'checked':checked,'scheduled':len(ids),'errors':errors})
   if state=='complete' or(errors and not pending):return
   done,_=cf.wait(pending,timeout=3,return_when=cf.FIRST_COMPLETED) if pending else (set(),set())
   for f in done:
    pending.remove(f)
    try:r=f.result()
    except Exception as e:r={'returncode':1,'error':str(e),'summary':{}}
    with (CLOUD/(stem+'-events.jsonl')).open('a') as stream:stream.write(json.dumps(r)+'\n')
    s=r.get('summary',{})
    if r.get('returncode')!=0 or type(s.get('new_evaluations')) is not int or s.get('checker_incidents')!=0 or s.get('kept_claims'):errors.append(r)
   if not pending:time.sleep(3)
if __name__=='__main__':main()
