"""Deploy report-only jobs and hash-verify downloaded per-model bundles."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tarfile
import time

HERE=Path(__file__).resolve().parent;WORK=HERE.parent;BASE=WORK.parents[1]
REPO=BASE/'outputs/lean-compression-eval';SUITE=REPO/'runs/om100-max-suite-high-20261014'
REMOTE='/home/leanrunner/om100-unlimited';RWORK=REMOTE+'/work/'+WORK.name
RREPO=REMOTE+'/outputs/lean-compression-eval';SSH=['ssh','-F',str(WORK/'cloud/ssh-config')]

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)
def command(args,**kwargs):
    p=subprocess.run(args,capture_output=True,timeout=kwargs.pop('timeout',600),**kwargs)
    if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace')[-2000:])
    return p.stdout
def remote(alias,program):return command([*SSH,alias,'python3 -'],input=program.encode(),timeout=60)

def deploy():
    pins=json.loads((HERE/'source-pins.json').read_text())
    paths=[WORK/p for p in pins['work_files']]+[REPO/p for p in pins['repo_files']]
    paths += [HERE/'source-pins.json',HERE/'cloud_report_job_v2.py']
    for root,key in ((WORK,'work_files'),(REPO,'repo_files')):
        for rel,expected in pins[key].items():
            if sha(root/rel)!=expected:raise ValueError('Report dependency changed before deployment')
    bundle=HERE/'report-assets.tar.gz'
    with tarfile.open(bundle,'w:gz') as archive:
        for path in paths:archive.add(path,arcname=str(path.relative_to(BASE)),recursive=False)
    receipt={'files':{str(p.relative_to(BASE)):sha(p) for p in paths},'bundle_sha256':sha(bundle),
             'contains_credentials':False,'model_calls':0,'lean_calls':0}
    save(HERE/'report-assets.json',receipt)
    plan=json.loads((WORK/'cloud/verification-fleet-plan.json').read_text())
    def host(alias):
        with bundle.open('rb') as f:command([*SSH,alias,'tar xzf - -C '+shlex.quote(REMOTE)],stdin=f)
        code='import pathlib,hashlib,json;root=pathlib.Path('+repr(REMOTE)+');files='+repr(receipt['files'])+';bad=[p for p,s in files.items() if hashlib.sha256((root/p).read_bytes()).hexdigest()!=s];assert not bad,bad;print(json.dumps({"verified_files":len(files)}))'
        remote(alias,code)
    with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(host,sorted({r['ssh_alias'] for r in plan['assignments']})))
    for row in plan['assignments']:
        model=row['model'];log=RWORK+'/reporting/'+model+'-report.log'
        script='import pathlib,subprocess,json;log=pathlib.Path('+repr(log)+');log.parent.mkdir(parents=True,exist_ok=True);f=log.open("ab");p=subprocess.Popen(["python3",'+repr(RWORK+'/reporting/cloud_report_job_v2.py')+',"--model",'+repr(model)+'],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True);print(json.dumps({"pid":p.pid}))'
        launch=json.loads(remote(row['ssh_alias'],script));save(HERE/'jobs'/(model+'-launch.json'),{**row,**launch,'assets_sha256':receipt['bundle_sha256']})
    return plan

def sync_one(row):
    model=row['model'];alias=row['ssh_alias'];state=HERE/'jobs'/(model+'-local.json')
    while True:
        try:
            status_path=RWORK+'/reporting/jobs/'+model+'.json'
            raw=remote(alias,'from pathlib import Path; p=Path('+repr(status_path)+');print(p.read_text() if p.exists() else "{}")')
            status=json.loads(raw)
            if status and time.time()-status.get('updated_at',0)>90 and status.get('state') not in ('building','complete','failed'):
                raise RuntimeError('Remote report watcher status is stale')
            save(state,{**status,'ssh_alias':alias})
            if status.get('state')=='failed':return status
            if status.get('state')=='complete':
                output=SUITE/('report-'+model);output.mkdir(parents=True,exist_ok=True)
                command(['rsync','-az','--ignore-existing','-e',shlex.join(SSH),
                    alias+':'+RREPO+'/runs/om100-max-suite-high-20261014/report-'+model+'/',str(output)+'/'])
                manifest=output/'report-file-manifest.json'
                if sha(manifest)!=status['manifest_sha256']:raise ValueError('Downloaded report manifest differs')
                bundle=json.loads(manifest.read_text());expected={'report-file-manifest.json'}
                for item in bundle['files']:
                    p=output/item['path']
                    if p.is_symlink() or not p.resolve().is_relative_to(output.resolve()) or p.stat().st_size!=item['bytes'] or sha(p)!=item['sha256']:raise ValueError('Downloaded report file differs: '+str(p))
                    expected.add(item['path'])
                if {str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()}!=expected:raise ValueError('Unexpected local report files')
                save(state,{**status,'state':'complete_synced_verified','ssh_alias':alias,'local_output':str(output)})
                return status
        except Exception as exc:save(state,{'state':'retrying_report_sync','model':model,'error':str(exc),'updated_at':time.time()})
        time.sleep(30)

def main():
    with (HERE/'report-fleet.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        plan=deploy()
        with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(sync_one,plan['assignments']))
        save(HERE/'report-fleet-result.json',{'states':results,'all_complete':all(r.get('state')=='complete' for r in results),'model_calls':0,'lean_calls':0})

if __name__=='__main__':main()
