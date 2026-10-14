#!/usr/bin/env python3
"""Archive reviewed OM100 inputs and small frozen metadata without reading graphs.

This is packaging only. It never writes the live workspace, creates a launch
marker, opens a reference-anchor graph, runs Lean, or calls a provider.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'src'))
from lean_compression.io import digest, task_input_hash, verification_request_hash

MODELS = sorted(['gpt-6.1-sol','gpt-6-sol','gpt-6-luna','gpt-6-astra',
                 'claude-fable-5-1','claude-opus-5-5','claude-sonnet-5-5','claude-haiku-5-5'])
CONDITIONS = ['standard','mathematical_compression','source_concision']
COPIES = {
    'private/tasks.jsonl': 'ready_v1/tasks.jsonl',
    'private/references.jsonl': 'ready_v1/references.jsonl',
    'root-review.json': 'ready_v1/root-review.json',
    'audits/independent-input-audit.json': 'final-input-independent-audit.json',
    'audits/premise-audit.json': 'premises/audit_final_v3_fast/audit.json',
    'audits/family-audit.json': 'family-audit.json',
    'audits/materialization-summary.json': 'materialized_final_v1/summary.json',
    'audits/official-reference-summary.json': 'reference-check-workers-v1/summary.json',
    'audits/official-reference-policy.json': 'reference-checks-v1/policy.json',
    'audits/premise-export-policy.json': 'premises/export_final_v3_fast/policy.json',
    'audits/sort-optimization-equivalence.json': 'premises/audit_final_v3_fast/sort_optimization_equivalence.json',
    'audits/frozen-run-plan.json': 'launch_proposed.json',
}
SECRET = re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-(?:ant-|proj-)[A-Za-z0-9_-]{20,}')


def read(path):
    return json.loads(path.read_text())


def jsonl(path):
    return [json.loads(line) for line in path.read_text().split('\n') if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def publish_staged(stage, destination):
    if destination.exists():
        actual = {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob('*') if p.is_file()}
        expected = {p.relative_to(stage).as_posix(): sha(p) for p in stage.rglob('*') if p.is_file()}
        if actual != expected:
            raise ValueError(f'Existing prepared snapshot differs: {destination}; create another version explicitly')
        shutil.rmtree(stage)
    else:
        stage.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    work = args.workspace.resolve()/'work/full-eval-100'
    if not (work/'launch_proposed.json').is_file():
        raise ValueError('Wait until all eight runs finish freezing and launch_proposed.json exists')
    plan, review = read(work/'launch_proposed.json'), read(work/'ready_v1/root-review.json')
    tasks, refs = jsonl(work/'ready_v1/tasks.jsonl'), jsonl(work/'ready_v1/references.jsonl')
    expected = [f'OM100-{i:03d}' for i in range(1,101)]
    if ([t['task_id'] for t in tasks] != expected or [r['task_id'] for r in refs] != expected
        or review.get('all_100_ready') is not True
        or review['tasks_sha256'] != sha(work/'ready_v1/tasks.jsonl')
        or review['references_sha256'] != sha(work/'ready_v1/references.jsonl')
        or any(t.get('model_generation_enabled') is not True for t in tasks)):
        raise ValueError('Ready input identity/readiness does not match root review')
    if (sorted(r['model'] for r in plan['runs']) != MODELS or len(plan['runs']) != 8
        or plan['scheduled_attempts'] != 9600 or plan['conditions'] != CONDITIONS
        or plan['tasks_sha256'] != review['tasks_sha256']
        or plan['preparation_audit_sha256'] != sha(work/'ready_v1/root-review.json')
        or plan['environment_sha256'] != sha(work/'environment.azure.json')):
        raise ValueError('Frozen cohort plan does not match reviewed inputs')
    for key, source in [('family_audit_sha256','family-audit.json'),
                        ('independent_input_audit_sha256','final-input-independent-audit.json'),
                        ('premise_audit_sha256','premises/audit_final_v3_fast/audit.json')]:
        if review[key] != sha(work/source):
            raise ValueError(f'Root review evidence changed: {key}')
    independent = read(work/'final-input-independent-audit.json')
    premises = read(work/'premises/audit_final_v3_fast/audit.json')
    official = read(work/'reference-check-workers-v1/summary.json')
    if independent['status'] != 'pass' or independent['tasks'] != 100 or premises['passed_count'] != 100 or official['checked'] != 100 or official['measurement'] != {'available':100}:
        raise ValueError('Preparation/audit completeness mismatch')
    anchors = {r['task_id']: r for r in independent['official_anchors']}
    if sorted(anchors) != expected or len(independent['official_anchors']) != 100:
        raise ValueError('Independent audit must identify all100 official anchors')
    if len({t['family_id'] for t in tasks}) != 19:
        raise ValueError('Family population changed')
    environment = read(work/'environment.azure.json')
    if environment['environment_hash'] != digest({k:v for k,v in environment.items() if k != 'environment_hash'}):
        raise ValueError('Frozen environment hash mismatch')
    target = REPO/'data/prepared/openai_math_100_v1'
    config_target = REPO/'configs/om100'
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.om100-package-',dir=target.parent))
    config_stage = Path(tempfile.mkdtemp(prefix='.om100-config-',dir=config_target.parent))
    files = []
    def copy(source, destination, repo_path):
        source = work/source
        if source.is_symlink() or not source.is_file() or source.stat().st_size > 16_000_000:
            raise ValueError('Only bounded regular input/config/metadata files may be packaged')
        content = source.read_bytes()
        if SECRET.search(content):
            raise ValueError('Recognizable credential material detected; refusing packaging')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        if source.read_bytes() != content:
            raise ValueError('Source changed while packaging')
        files.append({'path':repo_path, 'source_path':'work/full-eval-100/'+source.relative_to(work).as_posix(),
            'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content),'copy_mode':'exact_bytes'})
    try:
        for destination, source in COPIES.items():
            copy(source,stage/destination,(target.relative_to(REPO)/destination).as_posix())
        copy('environment.azure.json',config_stage/'environment.azure.json','configs/om100/environment.azure.json')
        copy('config.reference-final.json',config_stage/'reference-policy.json','configs/om100/reference-policy.json')
        config_records=[]
        for model in MODELS:
            config=read(work/'frozen-configs'/f'{model}.json')
            if (config['generation']['model_snapshot'] != model or config['generation']['reasoning_setting'] != 'high'
                or config['generation']['max_output_tokens'] != 8192 or config['conditions'] != CONDITIONS
                or config['samples_per_goal_condition'] != 4 or not config.get('cost_tracking_required')):
                raise ValueError('Frozen model configuration differs from the approved cohort')
            destination=f'configs/om100/models/{model}.json'
            copy('frozen-configs/'+model+'.json',config_stage/'models'/f'{model}.json',destination)
            config_records.append({'model':model,'path':destination,'config_hash':digest(config),
                'sha256':sha(work/'frozen-configs'/f'{model}.json')})
        index={'schema_version':1,'kind':'prepared_question_index_v1','task_count':100,'family_count':19,
            'selection_groups':dict(Counter(t['selection_group'] for t in tasks)),
            'questions':[{'task_id':t['task_id'],'family_id':t['family_id'],'selection_group':t['selection_group'],
                'domain':t['domain'],'target_name':t['target_name'],'preparation_status':t['preparation_status'],
                'model_generation_enabled':True,'task_input_hash':task_input_hash(t),
                'verification_request_hash':verification_request_hash(t),
                'official_anchor_sha256_from_independent_audit':anchors[t['task_id']]['official_anchor_sha256']}
                for t in tasks],
            'anchor_note':'Hashes reproduced from the independently reviewed audit; large anchor graphs are not included or re-read by this packager.'}
        write(stage/'index.json',index)
        readme='''# Prepared OpenAI Math 100 snapshot

This versioned snapshot contains the exact ready inputs reviewed for the eight-model, 9,600-attempt evaluation. All 100 standalone targets, official reference checks, premise audits and the root final review have passed. It is separate from the historical selection adoption under `data/question_sets/openai_math_100_v1`.

`private/tasks.jsonl` contains compiler contexts and model-visible premise interfaces; `private/references.jsonl` contains reference proofs. The directory name is an organizational boundary, not encryption. Do not feed entire task/reference files to a model. The harness constructs messages from the frozen prompt fields only. Source proof bodies are trusted checker inputs, not model prompts.

`root-review.json` is copied unchanged. `index.json` maps all100 IDs, 19 families, mathematical input hashes and independently reported reference-anchor hashes. `audits/` preserves the stage-specific metadata unchanged, so earlier audit files can legitimately retain generation-disabled or pending-later-review statements. The final root review binds these stages together. `manifest.json` binds the packaged files, eight frozen configurations and run identities.

Frozen environments/configurations are in `configs/om100/` relative to repository root. The eight local runs are `runs/om100-<model>-high/`; their manifests and later responses remain in ignored run storage. `audits/frozen-run-plan.json` is the proposed freeze result, not the live launch marker. Packaging never authorizes or starts model calls. The coordinator separately releases `work/full-eval-100/launch_ready.json` after checking the frozen runs.

The roughly3.1GB official reference-anchor graphs are deliberately omitted. Their independently recorded hashes and policy are retained, but this smaller package alone cannot replace the full anchor evidence when freezing another run. Keep the original anchors or independently regenerate them with the pinned source/runtime and re-audit the resulting evidence. The Docker image and provider responses are also not included.

Reference-informed premise retrieval and the exploratory/family limitations remain disclosed in the final review and main documentation. No claim of certified-unseen training data is made.
'''
        (stage/'README.md').write_text(readme)
        for name in ['index.json','README.md']:
            files.append({'path':(target.relative_to(REPO)/name).as_posix(),'sha256':sha(stage/name),
                          'bytes':(stage/name).stat().st_size,'copy_mode':'generated_metadata'})
        # Freeze time is inherited from the already-frozen plan file for idempotence.
        manifest={'schema_version':1,'kind':'prepared_question_snapshot_v1','status':'reviewed_ready_with_frozen_runs',
            'source_freeze_plan_sha256':sha(work/'launch_proposed.json'),'model_generation_enabled':True,
            'launch_authorized_by_this_snapshot':False,'historical_adoption':'data/question_sets/openai_math_100_v1',
            'task_count':100,'family_count':19,'scheduled_attempts':9600,'source_tasks_sha256':review['tasks_sha256'],
            'source_references_sha256':review['references_sha256'],'root_review_sha256':sha(work/'ready_v1/root-review.json'),
            'environment_hash':environment['environment_hash'],'docker_image':environment['docker_image'],
            'frozen_runs':plan['runs'],'frozen_configs':config_records,'files':sorted(files,key=lambda x:x['path']),
            'excluded':'Large anchor graphs, Docker images, credentials, private cloud access files, API responses and live launch/status records.'}
        write(stage/'manifest.json',manifest)
        publish_staged(config_stage,config_target)
        publish_staged(stage,target)
    finally:
        if stage.exists():shutil.rmtree(stage)
        if config_stage.exists():shutil.rmtree(config_stage)
    active_path=REPO/'configs/active-question-set.json';active=read(active_path)
    active['prepared_snapshot']={'kind':'prepared_question_snapshot_v1','path':'../data/prepared/openai_math_100_v1',
        'manifest_sha256':sha(target/'manifest.json'),'status':'reviewed_ready_with_frozen_runs',
        'launch_authorized_by_this_pointer':False,'historical_adoption_status_unchanged':True}
    write(active_path,active)
    print(json.dumps({'prepared_snapshot':str(target),'config_directory':str(config_target),
        'tasks':100,'frozen_models':8,'packaged_files':len(files),'bytes':sum(f['bytes'] for f in files),
        'anchor_graphs_read':0,'model_calls':0}))


if __name__ == '__main__':
    main()
