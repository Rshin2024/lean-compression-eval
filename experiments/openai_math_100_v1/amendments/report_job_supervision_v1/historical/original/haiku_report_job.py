#!/usr/bin/env python3
"""One resumable Haiku report job. Reads frozen evidence; never makes model calls."""
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time

WORK=Path(__file__).resolve().parent.parent
REPO=WORK.parents[1]/'outputs/lean-compression-eval'
MODEL='claude-haiku-5-5'
RUN=REPO/'runs'/('om100-'+MODEL+'-high')
OUT=REPO/'runs/om100-suite-high-20261013'/('report-'+MODEL)
JOB=WORK/'report-jobs'/f'{MODEL}.json'
LOG=WORK/'report-jobs'/f'{MODEL}.log'
EXPECTED_ADAPTER='e84d22a566a123e47c21ed1cd204a42a0a935c779b8051205dfa3f6bba7fd490'

def read(p):return json.loads(p.read_text())
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def stamp():return datetime.now(timezone.utc).isoformat()
def update(state,**fields):
    record={'schema_version':1,'kind':'om100_per_model_report_job','model':MODEL,'state':state,
        'evaluation_version':'v2_threads1','expected_records':1200,'remote_pid':os.getpid(),
        'remote_output':str(OUT),'remote_run':str(RUN),'updated_at_utc':stamp(),
        'report_adapter_sha256':EXPECTED_ADAPTER,'model_calls':0,**fields}
    temp=JOB.with_suffix('.tmp');temp.write_text(json.dumps(record,indent=2)+'\n');temp.replace(JOB)

def validate_output():
    analysis=read(OUT/'analysis.json');data=analysis['models'][MODEL]
    if data['run_complete'] is not True:raise ValueError('Report remains incomplete')
    if analysis['population']['task_count']!=100 or analysis['samples_per_goal_condition']!=4:raise ValueError('Wrong report population')
    if analysis['run_provenance']['evaluation_version']!='v2_threads1':raise ValueError('Wrong verification phase')
    phase=analysis['phase_provenance'];policy=read(OUT/'verification-policy.json')
    if analysis['provenance']['environment_hash']!=phase['verification']['environment_hash']:raise ValueError('Analysis environment differs from amended verification')
    records=read(OUT/'all_scheduled_attempts.json');costs=read(OUT/'costs.json')
    if len(records)!=1200 or len({r['attempt_id'] for r in records})!=1200:raise ValueError('Incomplete/duplicate exported records')
    if any(r['record_kind']!='phase_separated_verified_attempt' or r['evaluation_version']!='v2_threads1' for r in records):raise ValueError('Pending or wrong-phase row exported')
    if any(r['verification_policy_hash']!=phase['verification']['policy_hash'] or r['source_run_hash']!=phase['generation']['run_hash'] for r in records):raise ValueError('Row phase identity mismatch')
    if any(r['generation_identity']['environment_hash']!=phase['generation']['environment_hash'] or r['verification_environment_hash']!=phase['verification']['environment_hash'] for r in records):raise ValueError('Generation/verification environments were mixed')
    if len(costs['attempts'])!=1200 or costs['summary']['scheduled_attempts']!=1200:raise ValueError('Wrong cost population')
    if {r['attempt_id'] for r in records}!={r['attempt_id'] for r in costs['attempts']}:raise ValueError('Cost identities differ from verification identities')
    priced=[r['cost'] for r in costs['attempts'] if r['cost']['status']=='estimated']
    observed=sum((Decimal(r['total_usd']) for r in priced),Decimal(0)) if priced else None
    if observed is not None and observed!=Decimal(costs['summary']['observed_sum_usd']):raise ValueError('Cost subtotal mismatch')
    if len(priced)==1200 and Decimal(costs['summary']['total_usd'])!=observed:raise ValueError('Complete cost total mismatch')
    if len(priced)<1200 and costs['summary']['total_usd'] is not None:raise ValueError('Unpriced calls must not have a complete cost total')
    for name in ['report.html','correctness.csv','paired_tasks.csv','cost_attempts.csv','cost_summary.csv','verification-phase.json','phase-audit-index.json']:
        if not (OUT/name).is_file():raise ValueError('Missing output '+name)
    if 'Generation and verification provenance' not in (OUT/'report.html').read_text():raise ValueError('Missing HTML phase disclosure')
    for condition,stats in data['conditions'].items():
        if stats['scheduled_attempts']!=400:raise ValueError('Wrong condition denominator')
    return {'records':1200,'valid_proofs':sum(x['valid_attempts'] for x in data['conditions'].values()),
        'measurable_proofs':sum(x['measurable_valid_attempts'] for x in data['conditions'].values()),
        'api_cost_estimate_usd':costs['summary']['total_usd'],'observed_api_subtotal_usd':costs['summary']['observed_sum_usd'],
        'priced_attempts':len(priced),'generation_run_hash':phase['generation']['run_hash'],
        'generation_environment_hash':phase['generation']['environment_hash'],
        'verification_environment_hash':phase['verification']['environment_hash'],
        'verification_policy_hash':phase['verification']['policy_hash'],'cohort_hash':phase['cohort_hash']}

def main():
    WORK.joinpath('report-jobs').mkdir(exist_ok=True)
    with (WORK/'report-jobs'/f'{MODEL}.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        try:
            if sha(REPO/'reporting/report_verification_v2.py')!=EXPECTED_ADAPTER:raise ValueError('Frozen report adapter differs from expected source')
            if not (OUT/'analysis.json').exists():
                while True:
                    paths=[p for p in (RUN/'evaluations/v2_threads1').glob('*.json') if re.fullmatch(r'[0-9a-f]{64}',p.stem)]
                    if len(paths)>1200:raise ValueError('Too many v2 record files')
                    update('waiting_for_v2_records',v2_record_files=len(paths))
                    if len(paths)==1200:break
                    time.sleep(25)
                manifest=read(RUN/'manifest.json');expected={r['attempt_id'] for r in manifest['slots']}
                if {p.stem for p in paths}!=expected:raise ValueError('V2 file set differs from frozen schedule')
                for p in paths:
                    row=read(p)
                    if row.get('record_kind')!='phase_separated_verified_attempt' or row.get('evaluation_version')!='v2_threads1' or row.get('attempt_id')!=p.stem:raise ValueError('Not every stored record is explicit terminal v2 evidence')
                update('analyzing',v2_record_files=1200,analysis_started_at_utc=stamp())
                command=[sys.executable,str(REPO/'reporting/report_verification_v2.py'),'--run',str(RUN),
                    '--amendment',str(WORK/'runtime-amendment-v2.json'),'--output',str(OUT)]
                env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1'
                with LOG.open('ab') as log:
                    done=subprocess.run(command,cwd=REPO,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
                if done.returncode:raise RuntimeError('Report adapter exited '+str(done.returncode)+'; inspect the report job log')
            summary=validate_output()
            files=[]
            for p in sorted(OUT.rglob('*')):
                if p.is_symlink():raise ValueError('Unexpected report symlink')
                if p.is_file() and p.name!='report-file-manifest.json':files.append({'path':p.relative_to(OUT).as_posix(),'sha256':sha(p),'bytes':p.stat().st_size})
            (OUT/'report-file-manifest.json').write_text(json.dumps({'model':MODEL,'evaluation_version':'v2_threads1','validated_at_utc':stamp(),'summary':summary,'files':files},indent=2)+'\n')
            update('remote_validated_ready_to_sync',summary=summary,file_count=len(files),report_manifest_sha256=sha(OUT/'report-file-manifest.json'))
        except Exception as error:
            update('failed',error_type=type(error).__name__,error=str(error),log=str(LOG))
            raise

if __name__=='__main__':main()
