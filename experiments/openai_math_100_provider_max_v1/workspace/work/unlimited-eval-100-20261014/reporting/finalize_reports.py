"""Wait for eight hash-verified bundles, then build and audit the final cohort.

No provider calls, Lean executions, cleanup, or partial-final publication.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

HERE=Path(__file__).resolve().parent;WORK=HERE.parent
REPO=WORK.parents[1]/'outputs/lean-compression-eval';SUITE=REPO/'runs/om100-max-suite-high-20261014'

def save(value):
    p=HERE/'finalization-status.json';tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps({**value,'updated_at':time.time(),'pid':os.getpid(),'model_calls':0,'lean_calls':0},indent=2)+'\n');tmp.replace(p)
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def verify_bundle(model,state):
    directory=SUITE/('report-'+model);manifest=directory/'report-file-manifest.json'
    if sha(manifest)!=state['manifest_sha256']:raise ValueError('Downloaded report manifest changed: '+model)
    bundle=json.loads(manifest.read_text());expected={'report-file-manifest.json'}
    for row in bundle['files']:
        p=directory/row['path']
        if p.is_symlink() or not p.resolve().is_relative_to(directory.resolve()) or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('Downloaded report changed: '+str(p))
        expected.add(row['path'])
    if {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()}!=expected:raise ValueError('Unexpected report files')

def run_script(script,args,log):
    with log.open('ab') as output:
        result=subprocess.run(['python3',str(script),*args],stdout=output,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError(str(script.name)+' failed; inspect '+str(log))

def main():
    with (HERE/'finalization.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        existing=HERE/'finalization-status.json'
        if existing.exists() and json.loads(existing.read_text()).get('state')=='complete':return
        plan=json.loads((WORK/'launch_ready.json').read_text());models=[r['model'] for r in plan['runs']]
        try:
            while True:
                states={}
                for model in models:
                    p=HERE/'jobs'/(model+'-local.json')
                    states[model]=json.loads(p.read_text()) if p.exists() else {'state':'not_started'}
                if any(s['state']=='failed' for s in states.values()):raise RuntimeError('A per-model report failed')
                ready=[m for m,s in states.items() if s['state']=='complete_synced_verified']
                if len(ready)==8:break
                save({'state':'waiting_for_reports','completed_models':ready,
                      'model_states':{m:s['state'] for m,s in states.items()}});time.sleep(30)
            save({'state':'verifying_all_report_bundles'})
            for model,state in states.items():verify_bundle(model,state)
            save({'state':'building_complete_comparison'})
            run_script(HERE/'build_suite.py',[],HERE/'final-comparison-build.log')
            audit=HERE/'audit-results'/('final-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()))
            save({'state':'auditing_complete_cohort','audit_output':str(audit)})
            run_script(HERE/'audit_streams.py',['--require-complete','--output',str(audit)],HERE/'final-cohort-audit.log')
            result=json.loads((audit/'summary.json').read_text())
            if result['status']!='pass_complete' or result['generations_audited']!=9600 or not result['cohort_complete']:raise ValueError('Final audit did not confirm exact9600completecohort')
            data=json.loads((SUITE/'comparison-data.json').read_text())
            if len(data['attempts'])!=9600 or len({a['attemptId'] for a in data['attempts']})!=9600:raise ValueError('Combined schedule identity mismatch')
            if data['generation']['outputTokenCap']!=128000 or data['questionViewer']['messageSets']!=300:raise ValueError('Combined budget/question coverage mismatch')
            save({'state':'complete','audit_output':str(audit),'audit_summary_sha256':sha(audit/'summary.json'),
                'suite':str(SUITE),'index_sha256':sha(SUITE/'index.html'),'data_sha256':sha(SUITE/'comparison-data.json'),
                'valid_proofs':sum(a['status']=='success' for a in data['attempts']),
                'api_token_estimate_usd':result['recorded_response_token_estimate_usd'],
                'original_unknown_charge_incidents':len(result['authorized_overload_recoveries']),
                'browser_qa_required_before_user_delivery':True})
        except Exception as exc:
            save({'state':'failed','error_type':type(exc).__name__,'error':str(exc)});raise

if __name__=='__main__':main()
