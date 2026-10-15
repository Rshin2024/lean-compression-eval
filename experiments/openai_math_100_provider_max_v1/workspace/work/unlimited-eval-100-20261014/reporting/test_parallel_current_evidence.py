"""Equivalence on isolated subsets of real evidence; original runs remain read-only."""
from __future__ import annotations
from collections import defaultdict
import json
import shutil
from pathlib import Path
import sys
import tempfile
import time
from run_report import WORK,REPO,load_module
from lean_compression.io import digest,file_hash
import parallel_records


def main():
    verifier=load_module('equivalence_verifier',WORK/'verify_runtime_v2.py')
    summaries=[]
    for model in ('gpt-6-luna','claude-haiku-5-5'):
        source=REPO/'runs'/('om100-max-'+model+'-high-20261014')
        report=REPO/'runs/om100-max-suite-high-20261014'/('report-'+model)/'all_scheduled_attempts.json'
        rows=json.loads(report.read_text());by_status=defaultdict(list)
        for row in rows:by_status[row['validity_status']].append(row['attempt_id'])
        selected=[aid for status,ids in sorted(by_status.items()) for aid in ids[:2]]
        with tempfile.TemporaryDirectory(prefix='parallel-report-equivalence-') as tmp:
            run=Path(tmp);shutil.copyfile(source/'manifest.json',run/'manifest.json')
            (run/'generations').mkdir();checks=run/'evaluations/v2_threads1';checks.mkdir(parents=True)
            shutil.copyfile(source/'evaluations/v2_threads1/policy.json',checks/'policy.json')
            for aid in selected:
                shutil.copyfile(source/'generations'/(aid+'.json'),run/'generations'/(aid+'.json'))
                shutil.copyfile(source/'evaluations/v2_threads1'/(aid+'.json'),checks/(aid+'.json'))
            start=time.monotonic();serial=verifier.load_records(run,amendment_path=WORK/'runtime-amendment-v2.json',graphs=False);serial_seconds=time.monotonic()-start
            start=time.monotonic();parallel=parallel_records.load_records(verifier,run,amendment_path=WORK/'runtime-amendment-v2.json',workers=4);parallel_seconds=time.monotonic()-start
            assert serial==parallel and digest(serial)==digest(parallel)
            summaries.append({'model':model,'scheduled_ordered_records':len(serial),'real_verified_records':len(selected),
                'statuses':sorted(by_status),'exact_records_sha256':digest(serial),'serial_seconds':serial_seconds,
                'parallel_seconds':parallel_seconds,'all_record_fields_identical':True})
            del serial,parallel
    _,function_sha=parallel_records.prepare(verifier)
    result={'status':'pass','kind':'current_evidence_exact_parallel_record_equivalence',
        'records':summaries,'verifier_sha256':file_hash(WORK/'verify_runtime_v2.py'),
        'parallel_reader_sha256':file_hash(Path(parallel_records.__file__)),
        'derived_validation_function_sha256':function_sha,'model_calls':0,'lean_calls':0,
        'original_records_changed':False,'fixtures':'Temporary byte-copied immutable evidence subsets with original full1200slotmanifests; remaining slots are identical pending placeholders.'}
    p=WORK/'reporting/parallel-equivalence.json';p.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
