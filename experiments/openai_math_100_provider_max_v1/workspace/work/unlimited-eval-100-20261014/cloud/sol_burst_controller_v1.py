#!/usr/bin/env python3
"""Reuse only fully drained Luna checker capacity for Sol on the same host."""
import fcntl,hashlib,json,pathlib,shlex,subprocess,time,os
C=pathlib.Path(__file__).resolve().parent;W=C.parent;R='/home/leanrunner/om100-unlimited/work/'+W.name+'/cloud/';A='lce-u100-southcentralus'
def save(p,j):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(j,indent=2)+'\n');t.replace(p)
def remote(cmd,**kw):
 p=subprocess.run(['ssh','-F',str(C/'ssh-config'),A,cmd],capture_output=True,timeout=180,**kw)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
 return p.stdout
def status(model,suffix=''):
 return json.loads(remote('cat '+shlex.quote(R+model+suffix+'-status.json')))
def main():
 lock=(C/'sol-burst-controller-v1.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 plan=json.loads((C/'verification-sol-burst-v1.json').read_text())
 if hashlib.sha256((C/'checker_sol_burst_v1.py').read_bytes()).hexdigest()!=plan['checker_worker_sha256']:raise ValueError('Burst source changed')
 save(C/'sol-burst-controller-v1-process.json',{'pid':os.getpid(),'started_at':time.time(),'state':'waiting_luna_drain'})
 while True:
  b=status('gpt-6-luna');a=status('gpt-6-luna','-aux-v1')
  if all(s.get('checked')==1200 and s.get('active_workers')==0 and s.get('state')=='complete' for s in [b,a]):break
  time.sleep(5)
 save(C/'sol-burst-v1-drain-gate.json',{'observed_at':time.time(),'luna_base':b,'luna_aux':a,'same_host':A,'gate':'Both1200completeandzeroactivebeforeadding30Solworkers'})
 for name in ['checker_sol_burst_v1.py','verification-sol-burst-v1.json']:
  data=(C/name).read_bytes();dest=R+name;remote('cat > '+shlex.quote(dest+'.upload')+' && mv '+shlex.quote(dest+'.upload')+' '+shlex.quote(dest),input=data)
  if remote('sha256sum '+shlex.quote(dest)).decode().split()[0]!=hashlib.sha256(data).hexdigest():raise ValueError('Burst source transfer changed')
 cmd=['python3',R+'checker_sol_burst_v1.py','--model','gpt-6-sol','--workers','30'];pid=remote('nohup '+shlex.join(cmd)+' >'+shlex.quote(R+'gpt-6-sol-sol-burst-v1-worker.log')+' 2>&1 < /dev/null & echo $!').decode().strip();save(C/'sol-burst-v1-deployment.json',{'pid':pid,'at_epoch':time.time(),'model':'gpt-6-sol','base_workers':24,'aux_v1_workers':6,'burst_workers':30,'host_total_live_checker_capacity':60,'same_host':A})
 while True:
  try:
   s=status('gpt-6-sol','-sol-burst-v1');save(C/'gpt-6-sol-sol-burst-v1-cloud-status.json',s)
   base=json.loads((W/'fleet-verification-status-with-aux.json').read_text());old=next(x for x in base['models'] if x['model']=='gpt-6-sol');oldcount=old['checked'];old.update(checked=max(oldcount,s.get('checked',0)),burst_active=s.get('active_workers',0),burst_errors=s.get('errors'),burst_memory_pressure_hold=s.get('memory_pressure_hold',False),burst_updated_at=s.get('updated_at'))
   base.update(updated_at=time.time(),checked_total=base['checked_total']-oldcount+old['checked'],active_workers=base['active_workers']+s.get('active_workers',0),capacity_note='Luna30workersdrainedandreusedforSol;max60perVM/360fleetunchanged',sol_burst_state=s.get('state'));save(W/'fleet-verification-status-with-burst.json',base)
   if s.get('state')=='complete' and base['saved_total']==9600:return
  except Exception as e:save(C/'sol-burst-v1-sync-error.json',{'at_epoch':time.time(),'error':str(e)})
  time.sleep(10)
if __name__=='__main__':main()
