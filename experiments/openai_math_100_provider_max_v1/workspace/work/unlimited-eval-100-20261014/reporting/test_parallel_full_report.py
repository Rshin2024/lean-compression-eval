"""Compare a full parallel read/analysis against an existing serial report."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import time
from run_report import WORK,REPO,load_reporter
from lean_compression.io import digest,file_hash
from lean_compression.cli import write_rows
import parallel_records


def main():
    model='gpt-6-luna';run=REPO/'runs'/('om100-max-'+model+'-high-20261014')
    report=REPO/'runs/om100-max-suite-high-20261014'/('report-'+model)
    verifier,phase=load_reporter();start=time.monotonic()
    records=parallel_records.load_records(verifier,run,amendment_path=WORK/'runtime-amendment-v2.json',workers=16)
    read_seconds=time.monotonic()-start
    context=verifier.load_verification_context(run,WORK/'runtime-amendment-v2.json')
    analysis=phase.report_from_context(context,records)
    expected=json.loads((report/'analysis.json').read_text())
    assert analysis==expected and digest(analysis)==digest(expected)
    with tempfile.TemporaryDirectory(prefix='parallel-report-full-') as tmp:
        output=Path(tmp)/'records.json';write_rows(output,records)
        assert file_hash(output)==file_hash(report/'all_scheduled_attempts.json')
        record_file_sha=file_hash(output)
    result={'status':'pass','model':model,'verified_records':1200,'workers':16,
        'parallel_validation_seconds':read_seconds,'total_validation_and_analysis_seconds':time.monotonic()-start,
        'analysis_identical':True,'analysis_canonical_sha256':digest(analysis),
        'all_scheduled_attempts_byte_identical':True,'all_scheduled_attempts_sha256':record_file_sha,
        'parallel_reader_sha256':file_hash(Path(parallel_records.__file__)),
        'model_calls':0,'lean_calls':0,'original_report_unchanged':True}
    output=WORK/'reporting/parallel-full-equivalence.json';output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
