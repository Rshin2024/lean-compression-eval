#!/usr/bin/env python3
"""Deploy reviewed same-host capacity addition and aggregate without double-counting."""
import concurrent.futures as cf,fcntl,hashlib,json,pathlib,shlex,subprocess,time
CLOUD=pathlib.Path(__file__).resolve().parent;WORK=CLOUD.parent;RW='/home/leanrunner/om100-unlimited/work/'+WORK.name+'/cloud/'
SSH=['ssh','-F',str(CLOUD/'ssh-config')]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,j):
 t=p.with_suffix('.tmp');t.write_text(json.dumps(j,indent=2)+'\n');t.replace(p)
def remote(alias,command,**kw):
 p=subprocess.run([*SSH,alias,command],capture_output=True,timeout=180,**kw)
 if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-3000:])
 return p.stdout
def deploy(alias,rows,plan):
 for name in ['checker_aux_v1.py','verification-aux-plan-v1.json']:
  data=(CLOUD/name).read_bytes();dest=RW+name
  remote(alias,'cat > '+shlex.quote(dest+'.upload')+' && mv '+shlex.quote(dest+'.upload')+' '+shlex.quote(dest),input=data)
  got=remote(alias,'sha256sum '+shlex.quote(dest)).decode().split()[0]
  if got!=hashlib.sha256(data).hexdigest():raise ValueError('Auxsource transfer mismatch')
 result=[]
 for r in rows:
  cmd=['python3',RW+'checker_aux_v1.py','--model',r['model'],'--workers',str(r['workers'])]
  pid=remote(alias,'nohup '+shlex.join(cmd)+' >'+shlex.quote(RW+r['model']+'-aux-v1-worker.log')+' 2>&1 < /dev/null & echo $!').decode().strip();result.append({'model':r['model'],'ssh_alias':alias,'pid':pid,'workers':r['workers'],'started_at_epoch':time.time()})
 return result
def main():
 lock=(CLOUD/'aux-controller-v1.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 plan=json.loads((CLOUD/'verification-aux-plan-v1.json').read_text());base=json.loads((CLOUD/'verification-fleet-plan.json').read_text())
 if sha(CLOUD/'checker_aux_v1.py')!=plan['checker_worker_sha256'] or sha(CLOUD/'verification-fleet-plan.json')!=plan['base_plan_sha256']:raise ValueError('Pinned capacity sources changed')
 for host in {r['ssh_alias'] for r in base['assignments']}:
  if sum(r['workers'] for r in base['assignments']+plan['assignments'] if r['ssh_alias']==host)!=60:raise ValueError('Host total must equal60')
 save(CLOUD/'aux-controller-v1-process.json',{'pid':__import__('os').getpid(),'started_at_epoch':time.time(),'plan_sha256':sha(CLOUD/'verification-aux-plan-v1.json')})
 with cf.ThreadPoolExecutor(max_workers=6) as pool:
  jobs={pool.submit(deploy,alias,[r for r in plan['assignments'] if r['ssh_alias']==alias],plan):alias for alias in {r['ssh_alias'] for r in plan['assignments']}}
  receipts=[]
  for future in cf.as_completed(jobs):receipts.extend(future.result());save(CLOUD/'aux-deployment-v1.json',{'started_at_epoch':time.time(),'workers':receipts})
 def inspect(r):
  try:
   raw=remote(r['ssh_alias'],'cat '+shlex.quote(RW+r['model']+'-aux-v1-status.json'));s=json.loads(raw);save(CLOUD/(r['model']+'-aux-v1-cloud-status.json'),s);return s
  except Exception as e:return {'model':r['model'],'state':'status_unavailable','error':str(e)}
 with cf.ThreadPoolExecutor(max_workers=8) as pool:
  while True:
   rows=list(pool.map(inspect,plan['assignments']));base_status=json.loads((WORK/'fleet-verification-status.json').read_text());by={x['model']:x for x in rows};models=[]
   for b in base_status['models']:
    a=by.get(b['model'],{});c=b.get('cloud',{});models.append({'model':b['model'],'checked':max(c.get('checked',0),a.get('checked',0)),'saved':b.get('saved',0),'base_active':c.get('active_workers',0),'aux_active':a.get('active_workers',0),'aux_state':a.get('state'),'aux_memory_pressure_hold':a.get('memory_pressure_hold',False),'aux_available_memory_mib':a.get('available_memory_mib'),'base_error':b.get('error') or b.get('sync_error'),'aux_errors':a.get('errors') or a.get('error'),'base_updated_at':c.get('updated_at'),'aux_updated_at':a.get('updated_at')})
   j={'updated_at':time.time(),'total_worker_capacity':360,'base_capacity':288,'auxiliary_capacity':72,'checked_total':sum(x['checked'] for x in models),'saved_total':sum(x['saved'] for x in models),'active_workers':sum(x['base_active']+x['aux_active'] for x in models),'models':models,'counting':'Eachmodelcheckedcountismaxofsamefilesystembase/auxsnapshots,neveradded; savedcountfromsingleexistinglocalsync.'};save(WORK/'fleet-verification-status-with-aux.json',j)
   if j['saved_total']==9600:return
   time.sleep(10)
if __name__=='__main__':main()
