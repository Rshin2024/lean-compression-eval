"""One locked model report, waiting for all original frozen outcomes on its VM."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import time
from run_report_parallel import WORK,REPO,load_reporter
from lean_compression.io import write_json,file_hash


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--model',required=True);args=parser.parse_args()
    plan=json.loads((WORK/'cloud/verification-fleet-plan.json').read_text())
    entries=[r for r in plan['assignments'] if r['model']==args.model]
    if len(entries)!=1:raise ValueError('Model is not assigned to this cohort')
    entry=entries[0];directory=WORK/'reporting/jobs';directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/(args.model+'.json')
    def update(state,**extra):
        write_json(state_path,{'state':state,'model':args.model,'pid':os.getpid(),'updated_at':time.time(),
             'new_model_calls':0,'lean_calls':0,**extra}, immutable=False)
    with (directory/(args.model+'.lock')).open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        output=REPO/'runs/om100-max-suite-high-20261014'/('report-'+args.model)
        if (output/'report-file-manifest.json').is_file():
            bundle=json.loads((output/'report-file-manifest.json').read_text())
            for item in bundle['files']:
                path=output/item['path']
                if file_hash(path)!=item['sha256']:raise ValueError('Existing report changed')
            update('complete',output=str(output),manifest_sha256=file_hash(output/'report-file-manifest.json'));return
        try:
            run=REPO/'runs'/entry['directory']
            while True:
                gens=sum(1 for p in (run/'generations').glob('*.json') if re.fullmatch('[0-9a-f]{64}',p.stem))
                checks=sum(1 for p in (run/'evaluations/v2_threads1').glob('*.json') if re.fullmatch('[0-9a-f]{64}',p.stem))
                if gens>1200 or checks>1200:raise ValueError('Unexpected extra outcomes')
                if gens==checks==1200:break
                update('waiting_for_complete_model',generations=gens,verification_records=checks);time.sleep(20)
            update('building',generations=gens,verification_records=checks)
            verifier,phase=load_reporter()
            report=phase.create_report(verifier,run,WORK/'runtime-amendment-v2.json',output)
            model=report['models'][args.model]
            if model['run_complete'] is not True:raise ValueError('Report is not complete')
            amendment=WORK/'reporting/parallel-report-amendment.json'
            write_json(output/'report-execution.json',{'kind':'parallel_pure_record_validation_v1','amendment_sha256':file_hash(amendment),'workers':16,'canonical_record_order_preserved':True,'model_calls':0,'lean_calls':0})
            files=[{'path':p.relative_to(output).as_posix(),'bytes':p.stat().st_size,'sha256':file_hash(p)}
                   for p in sorted(output.rglob('*')) if p.is_file()]
            write_json(output/'report-file-manifest.json',{'schema_version':1,'model':args.model,
                 'run_hash':entry['run_hash'],'files':files,'new_model_calls':0,'lean_calls':0})
            update('complete',output=str(output),manifest_sha256=file_hash(output/'report-file-manifest.json'),
                   valid_proofs=sum(c['valid_attempts'] for c in model['conditions'].values()),
                   api_token_estimate_usd=report['costs']['summary']['total_usd'])
        except Exception as exc:
            update('failed',error_type=type(exc).__name__,error=str(exc));raise


if __name__=='__main__':main()
