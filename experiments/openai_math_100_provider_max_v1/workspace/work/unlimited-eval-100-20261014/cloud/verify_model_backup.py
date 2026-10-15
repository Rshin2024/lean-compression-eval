#!/usr/bin/env python3
"""Read-only whole-model cloud/local generation and verification hash comparison."""
import argparse,concurrent.futures as cf,datetime,hashlib,json,pathlib,shlex,subprocess,time
CLOUD=pathlib.Path(__file__).resolve().parent;WORK=CLOUD.parent;ROOT=WORK.parents[1];REPO=ROOT/'outputs/lean-compression-eval';REMOTE_REPO='/home/leanrunner/om100-unlimited/outputs/lean-compression-eval'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return h.hexdigest()
def local_inventory(run):
 files=[p for sub in ['generations','evaluations/v2_threads1'] for p in (run/sub).rglob('*') if p.is_file() and not p.name.startswith('.') and p.suffix in ['.json','.jsonl']]
 def one(p):return p.relative_to(run).as_posix(),{'bytes':p.stat().st_size,'sha256':sha(p)}
 with cf.ThreadPoolExecutor(max_workers=4) as pool:return dict(pool.map(one,files))
def main():
 p=argparse.ArgumentParser();p.add_argument('--model',required=True);a=p.parse_args();plan=json.loads((CLOUD/'verification-fleet-plan.json').read_text());row=next(r for r in plan['assignments'] if r['model']==a.model);run=REPO/'runs'/row['directory'];manifest=json.loads((run/'manifest.json').read_text());ids={s['attempt_id'] for s in manifest['slots']}
 for sub in ['generations','evaluations/v2_threads1']:
  got={f.stem for f in (run/sub).glob('*.json') if len(f.stem)==64}
  if got!=ids:raise ValueError('Model not complete locally: '+sub)
 remote=REMOTE_REPO+'/runs/'+row['directory'];code='''import concurrent.futures as cf,hashlib,json,pathlib
run=pathlib.Path(PATH)
files=[p for sub in ['generations','evaluations/v2_threads1'] for p in (run/sub).rglob('*') if p.is_file() and not p.name.startswith('.') and p.suffix in ['.json','.jsonl']]
def one(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024*1024),b''):h.update(b)
 return p.relative_to(run).as_posix(),{'bytes':p.stat().st_size,'sha256':h.hexdigest()}
with cf.ThreadPoolExecutor(max_workers=4) as pool: print(json.dumps(dict(pool.map(one,files)),sort_keys=True))
'''.replace('PATH',repr(remote))
 started=time.time()
 with cf.ThreadPoolExecutor(max_workers=2) as pool:
  localf=pool.submit(local_inventory,run)
  r=subprocess.run(['ssh','-F',str(CLOUD/'ssh-config'),row['ssh_alias'],'nice -n 19 ionice -c 3 python3 -c '+shlex.quote(code)],capture_output=True,text=True,timeout=1800)
  if r.returncode:raise RuntimeError(r.stderr[-3000:])
  cloud=json.loads(r.stdout);local=localf.result()
 missing=sorted(set(cloud)-set(local));extra=sorted(set(local)-set(cloud));different=sorted(k for k in set(cloud)&set(local) if cloud[k]!=local[k]);out=WORK/'final-backups';out.mkdir(exist_ok=True)
 for label,value in [('cloud',cloud),('local',local)]:(out/(a.model+'-'+label+'-inventory.json')).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
 result={'model':a.model,'verified_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'duration_seconds':time.time()-started,'status':'pass' if not(missing or extra or different) else 'mismatch','files':len(cloud),'bytes':sum(x['bytes'] for x in cloud.values()),'missing':missing,'extra':extra,'different':different,'cloud_inventory_sha256':sha(out/(a.model+'-cloud-inventory.json')),'local_inventory_sha256':sha(out/(a.model+'-local-inventory.json')),'run_manifest_sha256':sha(run/'manifest.json'),'source_run_hash':manifest['run_hash'],'no_model_calls':True,'no_lean_calls':True,'all_graphs_preserved':True}
 (out/(a.model+'-verification.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));raise SystemExit(0 if result['status']=='pass' else 1)
if __name__=='__main__':main()
