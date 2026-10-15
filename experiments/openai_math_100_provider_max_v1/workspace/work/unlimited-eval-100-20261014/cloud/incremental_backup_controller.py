#!/usr/bin/env python3
"""Finalize immutable whole-model backups after all its checkers drain."""
import concurrent.futures as cf,fcntl,json,pathlib,shlex,subprocess,time,os
C=pathlib.Path(__file__).resolve().parent;W=C.parent;ROOT=W.parents[1];REPO=ROOT/'outputs/lean-compression-eval';RR='/home/leanrunner/om100-unlimited/outputs/lean-compression-eval/runs/';RW='/home/leanrunner/om100-unlimited/work/'+W.name+'/cloud/';SSH=['ssh','-F',str(C/'ssh-config')]
def save(p,j):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(j,indent=2)+'\n');t.replace(p)
def remote(row,cmd):
 p=subprocess.run([*SSH,row['ssh_alias'],cmd],capture_output=True,text=True,timeout=180)
 if p.returncode:raise RuntimeError(p.stderr[-2000:])
 return p.stdout
def final_sync(row):
 local=REPO/'runs'/row['directory']/'evaluations/v2_threads1';source=row['ssh_alias']+':'+RR+row['directory']+'/evaluations/v2_threads1/'
 for ext,immutable in [('.json',True),('.jsonl',False)]:
  args=['rsync','-az','--exclude=.*','--include=*/','--include=*'+ext,'--exclude=*']
  if immutable:args.append('--ignore-existing')
  p=subprocess.run([*args,'-e',shlex.join(SSH),source,str(local)+'/'],capture_output=True,text=True,timeout=600)
  if p.returncode:raise RuntimeError(p.stderr[-2000:])
def backup(row):
 m=row['model'];statuses=[]
 for suffix in ['','-aux-v1']+(['-sol-burst-v1'] if m=='gpt-6-sol' and (C/'sol-burst-v1-deployment.json').exists() else []):
  s=json.loads(remote(row,'cat '+shlex.quote(RW+m+suffix+'-status.json')));statuses.append(s)
  if s.get('checked')!=1200 or s.get('active_workers')!=0 or s.get('state')!='complete':return {'model':m,'state':'awaiting_drain'}
 save(C/(m+'-backup-drain-gate.json'),{'at_epoch':time.time(),'model':m,'statuses':statuses})
 final_sync(row)
 log=C/(m+'-backup-verification.log')
 with log.open('wb') as f:
  p=subprocess.run(['nice','-n','15','python3',str(C/'verify_model_backup.py'),'--model',m],stdout=f,stderr=f,timeout=1800)
 return {'model':m,'state':'pass' if p.returncode==0 else 'needs_attention','returncode':p.returncode,'finished_at':time.time(),'log':str(log)}
def main():
 lock=(C/'incremental-backup-controller.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);plan=json.loads((C/'verification-fleet-plan.json').read_text());results={}
 save(C/'incremental-backup-controller-process.json',{'pid':os.getpid(),'started_at':time.time()})
 with cf.ThreadPoolExecutor(max_workers=2) as pool:
  pending={}
  while True:
   status=json.loads((W/'fleet-verification-status-with-aux.json').read_text());by={x['model']:x for x in status['models']}
   for row in plan['assignments']:
    m=row['model'];s=by.get(m,{})
    if len(pending)<2 and m not in results and m not in pending.values() and s.get('checked')==1200 and s.get('saved')==1200 and s.get('base_active')==0 and s.get('aux_active')==0:pending[pool.submit(backup,row)]=m
   done,_=cf.wait(pending,timeout=5,return_when=cf.FIRST_COMPLETED) if pending else (set(),set())
   for f in done:
    m=pending.pop(f)
    try:r=f.result()
    except Exception as e:r={'model':m,'state':'needs_attention','error':str(e)[-2000:]}
    if r['state']!='awaiting_drain':results[m]=r
   save(W/'incremental-backup-status.json',{'updated_at':time.time(),'active':list(pending.values()),'results':results,'passed_models':sum(x['state']=='pass' for x in results.values())})
   if len(results)==8 and not pending:return
   if not pending:time.sleep(10)
if __name__=='__main__':main()
