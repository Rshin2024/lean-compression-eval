#!/usr/bin/env python3
"""Durable report-only controllers: two Azure builds, verified local copies.

No provider or Lean calls, frozen-file edits, combined comparison, or VM cleanup.
The unchanged report adapter performs the phase-aware analysis.
"""
from pathlib import Path
from datetime import datetime,timezone
from contextlib import contextmanager
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

JOBS=Path(__file__).resolve().parent
WORK=JOBS.parent
BASE=WORK.parents[1]
REPO=BASE/'outputs/lean-compression-eval'
SUITE=REPO/'runs/om100-suite-high-20261013'
MODELS=('claude-haiku-5-5','claude-sonnet-5-5','claude-opus-5-5','claude-fable-5-1',
        'gpt-6-sol','gpt-6.1-sol','gpt-6-luna','gpt-6-astra')
EXPECTED_ADAPTER='e84d22a566a123e47c21ed1cd204a42a0a935c779b8051205dfa3f6bba7fd490'
REMOTE_JOBS='/home/leanrunner/om100-eval/work/full-eval-100/report-jobs'
REMOTE_SUITE='/home/leanrunner/om100-eval/outputs/lean-compression-eval/runs/om100-suite-high-20261013'
SSH_CONFIG=WORK/'runtime/azure/ssh-config'
HOST='lce-om100-azure'
COMPLETE_LOCAL='complete_synced_verified'
COMPLETE_LOCAL_HOLD='completed_local_integrity_hold'
COMPLETE_REMOTE='remote_validated_ready_to_sync'

def stamp():return datetime.now(timezone.utc).isoformat()
def read(path):return json.loads(path.read_text())
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.write('\n')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def state(model):
    p=JOBS/(model+'.json')
    return read(p) if p.exists() else {}

def alive(pid):
    if type(pid) is not int or pid<=0:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

@contextmanager
def lock(path,blocking=False):
    with path.open('a') as stream:
        try:fcntl.flock(stream,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:yield False;return
        try:yield True
        finally:fcntl.flock(stream,fcntl.LOCK_UN)

def busy(model):
    with lock(JOBS/(model+'.lock')) as acquired:return not acquired

def record_paths(model):
    directory=REPO/'runs'/('om100-'+model+'-high')/'evaluations/v2_threads1'
    return [p for p in directory.glob('*.json') if re.fullmatch(r'[0-9a-f]{64}',p.stem)]

def backend(model,output=None):
    spec=importlib.util.spec_from_file_location('report_job_backend',JOBS/'haiku_report_job.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.MODEL=model;module.RUN=REPO/'runs'/('om100-'+model+'-high')
    module.OUT=Path(output) if output is not None else SUITE/('report-'+model)
    module.JOB=JOBS/(model+'.json');module.LOG=JOBS/(model+'.log')
    module.EXPECTED_ADAPTER=EXPECTED_ADAPTER
    original=module.update
    def update(status,**fields):
        if status in ('analyzing',COMPLETE_REMOTE):fields['v2_record_files']=1200
        original(status,**fields)
    module.update=update
    return module

def verify_files(directory,expected_manifest_hash):
    manifest=directory/'report-file-manifest.json'
    if sha(manifest)!=expected_manifest_hash:raise ValueError('Report manifest differs from remote checkpoint')
    bundle=read(manifest);expected={'report-file-manifest.json'}
    for row in bundle['files']:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe report file path')
        path=directory/relative
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:
            raise ValueError('Report file integrity mismatch: '+relative.as_posix())
        expected.add(relative.as_posix())
    actual={p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual!=expected or any(p.is_symlink() for p in directory.rglob('*')):raise ValueError('Unexpected report contents')
    return bundle

def worker(model):
    if model not in MODELS:raise ValueError('Unknown model')
    existing=state(model);out=SUITE/('report-'+model)
    # Preserve completed report bytes and their file-manifest timestamps.
    if existing.get('state')==COMPLETE_REMOTE and (out/'analysis.json').is_file():
        verify_files(out,existing['report_manifest_sha256']);return
    if busy(model):return
    slot=None
    while slot is None:
        for index in range(2):
            stream=(JOBS/f'build-slot-{index}.lock').open('a')
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);slot=stream;break
            except BlockingIOError:stream.close()
        if slot is None:time.sleep(5)
    try:
        # Another worker may have completed while this one waited for a global
        # build slot. Never enter the builder with an already completed bundle.
        existing=state(model)
        if existing.get('state')==COMPLETE_REMOTE:
            verify_files(out,existing['report_manifest_sha256']);return
        if len(record_paths(model))!=1200:raise ValueError('Report started without exactly 1200 v2 files')
        backend(model).main()
    except Exception as error:
        current=state(model)
        if current.get('state')!='failed' and not busy(model):
            current.update(model=model,state='failed',remote_pid=os.getpid(),updated_at_utc=stamp(),
                error_type=type(error).__name__,error=str(error)[-1200:],model_calls=0)
            save(JOBS/(model+'.json'),current)
        raise
    finally:
        fcntl.flock(slot,fcntl.LOCK_UN);slot.close()

def cloud_snapshot():
    return {m:state(m) for m in MODELS}

def local_env():
    return {'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(Path.home()),'LANG':'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1'}

def cloud_main():
    with lock(JOBS/'controller-cloud.lock') as acquired:
        if not acquired:return
        handles={}
        while True:
            request=JOBS/'resume-report-jobs.json'
            if request.exists():
                data=read(request)
                for model in data.get('models',[]):
                    if model in MODELS and state(model).get('state')=='failed' and not busy(model):
                        value=state(model);value.update(state='waiting_for_v2_records',updated_at_utc=stamp(),resume_requested_at_utc=data.get('issued_at_utc'));save(JOBS/(model+'.json'),value)
                request.rename(JOBS/('resume-report-jobs.applied-'+str(time.time_ns())+'.json'))
            for model,process in list(handles.items()):
                if process.poll() is not None:del handles[model]
            statuses=cloud_snapshot();active=set(handles)
            for model,value in statuses.items():
                if busy(model):active.add(model)
                elif value.get('state') in ('worker_starting','analyzing') and alive(value.get('remote_pid')):active.add(model)
                elif value.get('state') in ('worker_starting','analyzing'):
                    value.update(state='failed',error='Report worker exited without a completed checkpoint',updated_at_utc=stamp());save(JOBS/(model+'.json'),value)
            for model in MODELS:
                value=state(model)
                if value.get('state') in (COMPLETE_REMOTE,'failed') or model in active:continue
                count=len(record_paths(model))
                if count>1200:
                    value.update(model=model,state='failed',error='More than1200 v2 record files',updated_at_utc=stamp());save(JOBS/(model+'.json'),value);continue
                if count<1200 or len(active)>=2:
                    value.update(schema_version=1,kind='om100_per_model_report_job',model=model,evaluation_version='v2_threads1',
                        expected_records=1200,v2_record_files=count,state='waiting_for_v2_records' if count<1200 else 'waiting_for_build_slot',
                        updated_at_utc=stamp(),remote_output=REMOTE_SUITE+'/report-'+model,model_calls=0)
                    save(JOBS/(model+'.json'),value);continue
                log=(JOBS/(model+'.supervisor.log')).open('ab')
                process=subprocess.Popen([sys.executable,str(Path(__file__)),'--worker',model],cwd=JOBS,env=local_env(),
                    stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
                log.close();handles[model]=process;active.add(model)
                # The worker owns its per-model checkpoint as soon as it starts.
                # A separate launch note covers the tiny pre-lock startup interval.
                save(JOBS/(model+'.launch.json'),{'model':model,'remote_pid':process.pid,'launched_at_utc':stamp()})
            statuses=cloud_snapshot();complete=all(v.get('state')==COMPLETE_REMOTE for v in statuses.values())
            failed=[m for m,v in statuses.items() if v.get('state')=='failed']
            save(JOBS/'controller-cloud.json',{'pid':os.getpid(),'updated_at_utc':stamp(),'state':'complete' if complete else 'needs_attention' if failed else 'running',
                'max_concurrent_builds':2,'active_models':sorted(active),'failed_models':failed,'reports':{m:v.get('state','waiting_for_v2_records') for m,v in statuses.items()},'model_calls':0})
            if complete:return
            time.sleep(25)

def command(args,timeout=120):
    done=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if done.returncode:raise RuntimeError((done.stderr or done.stdout)[-1200:])
    return done.stdout

def fetch_remote():
    return json.loads(command(['ssh','-F',str(SSH_CONFIG),HOST,'python3 '+REMOTE_JOBS+'/report_jobs.py --snapshot']))

def sync_model(model,remote):
    path=JOBS/(model+'.json');current=state(model);target=SUITE/('report-'+model)
    if current.get('state') in (COMPLETE_LOCAL,COMPLETE_LOCAL_HOLD):
        try:
            if not (target/'analysis.json').is_file():raise ValueError('Completed local report is missing; root repair required')
            verify_files(target,current['report_manifest_sha256'])
            if remote.get('report_manifest_sha256') not in (None,current['report_manifest_sha256']):
                raise ValueError('Completed local report differs from remote checkpoint; root review required')
        except Exception as error:
            current.update(state=COMPLETE_LOCAL_HOLD,updated_at_utc=stamp(),
                integrity_error={'type':type(error).__name__,'message':str(error)[-1200:]},
                repair_note='Restore the exact completed bundle or explicitly review a new version. No report files were changed.')
            save(path,current)
            raise
        if current['state']==COMPLETE_LOCAL_HOLD:
            current.update(state=COMPLETE_LOCAL,updated_at_utc=stamp(),integrity_revalidated_at_utc=stamp())
            current.pop('integrity_error',None);current.pop('repair_note',None);save(path,current)
        return
    SUITE.mkdir(parents=True,exist_ok=True)
    if target.exists():
        verify_files(target,remote['report_manifest_sha256']);stage=None
    else:
        stage=Path(current.get('local_sync_staging','')) if current.get('local_sync_staging') else None
        if stage is None or not stage.is_dir() or stage.parent!=SUITE or not stage.name.startswith('.report-sync-'+model+'-'):
            stage=Path(tempfile.mkdtemp(prefix='.report-sync-'+model+'-',dir=SUITE))
        current.update(schema_version=1,model=model,state='syncing_validated_report',local_sync_staging=str(stage),updated_at_utc=stamp(),v2_record_files=1200)
        save(path,current)
        ssh=shlex.join(['ssh','-F',str(SSH_CONFIG)])
        command(['rsync','-a','--safe-links','-e',ssh,HOST+':'+REMOTE_SUITE+'/report-'+model+'/',str(stage)+'/'],timeout=900)
        verify_files(stage,remote['report_manifest_sha256'])
    directory=stage if stage is not None else target
    summary=backend(model,directory).validate_output()
    phase=read(directory/'verification-phase.json');policy=read(directory/'verification-policy.json');amendment=read(WORK/'runtime-amendment-v2.json')
    canonical=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if canonical(policy)!=phase['verification']['policy_hash'] or canonical(amendment)!=policy['amendment_hash']:
        raise ValueError('Report phase policy differs from frozen local amendment')
    if stage is not None:stage.rename(target)
    final={**remote,'state':COMPLETE_LOCAL,'v2_record_files':1200,'local_output':str(target),'local_file_hashes_verified':len(read(target/'report-file-manifest.json')['files'])+1,
        'remote_updated_at_utc':remote.get('updated_at_utc'),'updated_at_utc':stamp(),'synced_at_utc':stamp(),
        'local_validation':{'scheduled_records':1200,'explicit_v2_terminal_records':1200,'all_report_file_hashes_match_remote':True,
            'phase_policy_matches_frozen_amendment':True,'costs_reconcile':True,'report_complete':True,**summary}}
    save(path,final)
    save(SUITE/(model+'.json'),{'model':model,'status':'report_complete','evaluation_version':'v2_threads1','report':str(target),
        'report_manifest_sha256':remote['report_manifest_sha256'],'scheduled_attempts':1200,
        'phase_policy_hash':summary['verification_policy_hash'],'api_cost_estimate_usd':summary['api_cost_estimate_usd']})

def local_main():
    with lock(JOBS/'controller-local.lock') as acquired:
        if not acquired:return
        while True:
            error=None
            try:
                remote=fetch_remote()
                model_errors={}
                for model in MODELS:
                    value=remote.get(model,{})
                    try:
                        if state(model).get('state') in (COMPLETE_LOCAL,COMPLETE_LOCAL_HOLD):
                            sync_model(model,value)
                        elif value.get('state')==COMPLETE_REMOTE:
                            sync_model(model,value)
                        else:
                            existing=state(model);existing.update(value);existing.update(local_output=str(SUITE/('report-'+model)),remote_updated_at_utc=value.get('updated_at_utc'),updated_at_utc=stamp())
                            save(JOBS/(model+'.json'),existing)
                    except Exception as exc:
                        model_errors[model]={'type':type(exc).__name__,'message':str(exc)[-1200:]}
                if model_errors:error={'per_model':model_errors}
            except Exception as exc:error={'type':type(exc).__name__,'message':str(exc)[-1500:]}
            states={m:state(m).get('state','waiting_for_v2_records') for m in MODELS};complete=not error and all(v==COMPLETE_LOCAL for v in states.values())
            save(JOBS/'controller-local.json',{'pid':os.getpid(),'updated_at_utc':stamp(),'state':'complete' if complete else 'needs_attention' if error or 'failed' in states.values() else 'running',
                'sync_error':error,'reports':states,'combined_comparison_built':False,'model_calls':0})
            if complete:return
            time.sleep(25)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--cloud',action='store_true');modes.add_argument('--local',action='store_true');modes.add_argument('--snapshot',action='store_true')
    modes.add_argument('--worker',choices=MODELS);modes.add_argument('--resume',nargs='+',choices=MODELS)
    args=parser.parse_args();JOBS.mkdir(parents=True,exist_ok=True)
    if args.snapshot:print(json.dumps(cloud_snapshot()));return
    if args.resume:save(JOBS/'resume-report-jobs.json',{'models':args.resume,'issued_at_utc':stamp()});return
    if args.worker:worker(args.worker)
    elif args.cloud:cloud_main()
    else:local_main()

if __name__=='__main__':main()
