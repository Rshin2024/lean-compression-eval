"""Copy a frozen, explicit v2 source/metadata allowlist. Never runs a checker/API.

The original source archive is untouched. Provider responses, hidden reference
records/index, graph data, credentials and cloud access material are excluded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

WORK_SOURCES = (
    'supervise_verification_v2.py','test_verification_supervision.py','snapshot_verification_files.py',
    'live/server.py','live/index.html','verify_runtime_v2.py',
    'verification_v2/build_runtime.py','verification_v2/diagnose_replay.py',
    'verification_v2/prepare_amendment.py','verification_v2/test_verifier.py',
)
WORK_METADATA = (
    'runtime-amendment-v2.json','verification-launch-v2.json',
    'verification_v2/image-context/Dockerfile','verification_v2/image-context/run-lean',
    'verification_v2/environment.json','verification_v2/reference-config.json',
    'verification_v2/runtime-build.json','verification_v2/runtime-verification.json',
    'verification_v2/preflight.json','verification_v2/diagnostic-summary.json',
)
OPTIONAL_METADATA = ('verification_v2/reference-workers/summary.json','verification_v2/reference-checks/policy.json')
REPORT_SOURCES = ('reporting/report_verification_v2.py','reporting/test_phase_reporting.py',
                  'reporting/build_comparison.py','reporting/comparison.html','reporting/README.md')
SECRET = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-(?:ant-|proj-)[A-Za-z0-9_-]{20,}')
FORBIDDEN_KEYS = {'body_base64','raw_response','provider_response','reference_records','proof','nodes','premise_context','context'}


def sha(data):return hashlib.sha256(data).hexdigest()


def read(path):return json.loads(Path(path).read_text())


def metadata_shape(value):
    pending=[value]
    while pending:
        row=pending.pop()
        if isinstance(row,dict):
            if FORBIDDEN_KEYS.intersection(row):raise ValueError('Metadata contains excluded proof/response/graph fields')
            pending.extend(row.values())
        elif isinstance(row,list):pending.extend(row)


def package(workspace, reporting_freeze):
    workspace=Path(workspace).resolve();work=workspace/'work/full-eval-100'
    repo=workspace/'outputs/lean-compression-eval';archive=repo/'experiments/openai_math_100_v1'
    target=archive/'amendments/runtime_threads1_v2'
    freeze=read(reporting_freeze)
    if freeze.get('state')!='source_frozen' or set(freeze.get('files',{}))!=set(REPORT_SOURCES):
        raise ValueError('Wait for the complete reporting source freeze')
    amendment=read(work/'runtime-amendment-v2.json');launch=read(work/'verification-launch-v2.json')
    if (sha((work/'runtime-amendment-v2.json').read_bytes()) != 'a9b8e3254e7f61138837e06a87ee68ea21d89802a9693671c49717ccb5e367b0'
            or sha((work/'verify_runtime_v2.py').read_bytes()) != '55f5931a9d4a2cfa92338e4079206477abd7d792a94b758b75b61237d33a5ac0'):
        raise ValueError('Protocol source/amendment differs from the reviewed freeze')
    if launch.get('version')!='v2_threads1' or launch.get('ready') is not True:
        raise ValueError('Verification launch is not the reviewed v2 phase')
    for name,expected in launch['source_files'].items():
        if Path(name).name!=name or sha((work/name).read_bytes())!=expected:
            raise ValueError('Root supervisor/verifier source differs from the launch pins')
    target.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.runtime_threads1_v2-',dir=target.parent));entries=[]
    try:
        def copy(root,relative,destination,kind,expected=None):
            source=root/relative
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
                raise ValueError('Allowlisted input must be an in-root regular file: '+relative)
            data=source.read_bytes()
            if len(data)>(16_000_000 if kind=='metadata' else 2_000_000) or SECRET.search(data):
                raise ValueError('Bounded source/metadata or credential check failed: '+relative)
            if expected is not None and sha(data)!=expected:raise ValueError('Reviewed source changed: '+relative)
            if kind=='metadata' and source.suffix=='.json':metadata_shape(json.loads(data))
            dest=stage/destination;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
            if source.read_bytes()!=data:raise ValueError('Source changed while copying: '+relative)
            entries.append({'path':destination,'source_path':source.relative_to(workspace).as_posix(),
                            'kind':kind,'sha256':sha(data),'bytes':len(data),'copy_mode':'exact_bytes'})
        for name in WORK_SOURCES:copy(work,name,'workspace/work/full-eval-100/'+name,'source')
        for name in WORK_METADATA:copy(work,name,'workspace/work/full-eval-100/'+name,'metadata')
        missing=[]
        for name in OPTIONAL_METADATA:
            if (work/name).is_file():copy(work,name,'workspace/work/full-eval-100/'+name,'metadata')
            else:missing.append(name)
        for name in REPORT_SOURCES:copy(repo,name,'repository/'+name,'source',freeze['files'][name])
        copy(work,'runtime_amendment_archive/package_amendment.py','package_amendment.py','source')
        readme=(work/'runtime_amendment_archive/README.final.md').read_bytes()
        if SECRET.search(readme):raise ValueError('Credential content in documentation')
        (stage/'README.md').write_bytes(readme)
        entries.append({'path':'README.md','sha256':sha(readme),'bytes':len(readme),'kind':'documentation'})
        manifest={'schema_version':1,'kind':'outside_frozen_core_verification_amendment',
            'amendment_id':'runtime_threads1_v2','generation_core_changed':False,
            'generation_manifests_and_responses_unchanged':True,'launch_performed_by_packager':False,
            'model_calls':0,'checker_calls':0,'full_run_completion_claimed':False,
            'base_source_manifest_sha256':sha((archive/'source-manifest.json').read_bytes()),
            'verification_amendment_sha256':sha((work/'runtime-amendment-v2.json').read_bytes()),
            'verification_launch_sha256':sha((work/'verification-launch-v2.json').read_bytes()),
            'reporting_freeze':freeze,'excluded_reference_index':amendment['reference_index'],
            'optional_metadata_not_present_locally':missing,'files':sorted(entries,key=lambda x:x['path']),
            'excluded':['hidden reference index/proofs/source spans','full reference checks/dependency graphs',
                        'diagnostic result bodies','provider responses/captures/reasoning','API credentials',
                        'SSH folder and cloud access metadata','container images/tars','runtime/event/status logs']}
        (stage/'amendment-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
        expected={p.relative_to(stage).as_posix():sha(p.read_bytes()) for p in stage.rglob('*') if p.is_file()}
        if target.exists():
            actual={p.relative_to(target).as_posix():sha(p.read_bytes()) for p in target.rglob('*') if p.is_file()}
            if actual!=expected:raise ValueError('Existing amendment differs; create a new version explicitly')
        else:stage.rename(target)
        return {'archive':str(target),'packaged_files':len(entries),'bytes':sum(e['bytes'] for e in entries),
                'excluded_reference_index_read':False,'model_calls':0,'checker_calls':0}
    finally:
        if stage.exists():shutil.rmtree(stage)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--reporting-freeze',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(package(args.workspace,args.reporting_freeze),indent=2))
