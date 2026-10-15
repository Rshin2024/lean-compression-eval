"""Scoped16-process report validation; same statistics, outputs and pure validators."""
from __future__ import annotations
import json
from pathlib import Path
from run_report import WORK,REPO,load_reporter as serial_load_reporter
from lean_compression.io import file_hash
import parallel_records

ALLOWED={'claude-opus-5-5','claude-sonnet-5-5','claude-fable-5-1'}


def load_reporter():
    amendment=json.loads((WORK/'reporting/parallel-report-amendment.json').read_text())
    if amendment['workers']!=16 or set(amendment['allowed_models'])!=ALLOWED:
        raise ValueError('Parallel report scope changed')
    for relative,expected in amendment['source_sha256'].items():
        if file_hash(WORK/relative)!=expected:raise ValueError('Parallel report source changed')
    descriptor=amendment['full_equivalence'];path=WORK/descriptor['path']
    if file_hash(path)!=descriptor['sha256']:raise ValueError('Parallel equivalence evidence changed')
    evidence=json.loads(path.read_text())
    if evidence['status']!='pass' or evidence['verified_records']!=1200 or not evidence['analysis_identical'] or not evidence['all_scheduled_attempts_byte_identical']:
        raise ValueError('Parallel reporting lacks complete output equivalence evidence')
    verifier,phase=serial_load_reporter()
    def load_records(run,*,amendment_path=verifier.DEFAULT_AMENDMENT,graphs=False):
        manifest=verifier.load_run(run)
        if manifest['model_snapshot'] not in ALLOWED:raise ValueError('Model outside scoped parallel report amendment')
        return parallel_records.load_records(verifier,run,amendment_path=amendment_path,graphs=graphs,workers=amendment['workers'])
    verifier.load_records=load_records
    return verifier,phase
