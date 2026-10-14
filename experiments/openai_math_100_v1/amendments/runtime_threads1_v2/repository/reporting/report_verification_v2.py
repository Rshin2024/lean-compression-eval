"""Report a complete amended verification without rewriting generation identity.

The external verifier validates original generations and nested verification
results. Only temporary analysis projections enter the unchanged statistics
engine; exported attempt records retain both phases verbatim, without graphs.
"""
from __future__ import annotations
import argparse
import copy
import html
import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
from lean_compression.analysis import analyze, write_report
from lean_compression.cli import staged_report, write_rows
from lean_compression.io import digest, write_json

VERSION = 'v2_threads1'
COMMON_KEYS = ('version','amendment_hash','environment_hash','docker_image','measurement_hash',
               'engine_hash','evaluator_source_hash','reference_policy_hash','reference_index_sha256','limits')


def phase_provenance(context):
    manifest, policy, config = context['manifest'], context['policy'], context['config']
    limits = {key:config['validation'][key] for key in ('cpu_quota','memory_mib','wall_seconds')}
    limits.update(extraction_wall_seconds=config['extraction']['wall_seconds'],lean_threads=1)
    generation = {'run_hash':manifest['run_hash'],'manifest_sha256':policy['source_manifest_sha256'],
        'environment_hash':manifest['environment']['environment_hash'],
        'docker_image':manifest['environment']['docker_image'],'config_hash':manifest['config_hash']}
    verification = {'version':VERSION,'policy_hash':digest(policy),'amendment_hash':policy['amendment_hash'],
        'environment_hash':policy['verification_environment_hash'],'docker_image':policy['verification_docker_image'],
        'config_hash':policy['verification_config_hash'],'measurement_hash':policy['measurement_hash'],
        'engine_hash':policy['engine_hash'],'evaluator_source_hash':policy['evaluator_source_hash'],
        'reference_policy_hash':policy['reference_policy_hash'],'reference_index_sha256':policy['reference_index_sha256'],
        'limits':limits,'required_attempts':len(manifest['slots'])}
    return {'schema_version':1,'kind':'separate_generation_and_verification', 'generation':generation,
        'verification':verification,'cohort_hash':digest({k:verification[k] for k in COMMON_KEYS}),
        'generation_replayed':True,'new_model_calls':False,
        'projection_note':'Statistics use in-memory verification projections. Exported attempts preserve original generation_identity and nested verification_result.'}


def require_complete_phase(context, records):
    """A truncated response also needs its own explicit terminal v2 record."""
    slots={s['attempt_id']:s for s in context['manifest']['slots']}
    ids=[row.get('attempt_id') for row in records]
    if len(ids)!=len(slots) or len(set(ids))!=len(ids) or set(ids)!=set(slots):
        raise ValueError('Phase report requires exact, unique frozen schedule coverage')
    policy_hash=digest(context['policy'])
    for row in records:
        slot=slots[row['attempt_id']]
        if row.get('record_kind')!='phase_separated_verified_attempt':
            raise ValueError('Every scheduled attempt needs a stored terminal v2 verification record, including truncated responses')
        if (row.get('evaluation_version')!=VERSION or row.get('verification_policy_hash')!=policy_hash
            or row.get('source_run_hash')!=context['manifest']['run_hash']
            or row.get('verification_environment_hash')!=context['environment']['environment_hash']
            or row.get('generation_identity')!={k:v for k,v in slot.items() if k!='messages'}):
            raise ValueError('Phase record identity/policy mismatch')
        if row.get('generation') is None or row.get('generation_hash')!=digest(row['generation']):
            raise ValueError('Phase record does not retain its original generation')
        link=row.get('verification_record') or {}
        if link.get('path')!=f'evaluations/{VERSION}/{slot["attempt_id"]}.json' or not link.get('sha256'):
            raise ValueError('Graph-free exports must bind the complete verification record')


def analysis_projection(context, record):
    """Derived analysis input only; never written as a replacement run record."""
    result=copy.deepcopy(record['verification_result'])
    identity=record['generation_identity']
    if result.get('environment_hash') not in (None,context['environment']['environment_hash']):
        raise ValueError('Verification result is attributed to a different environment')
    # Do not spread the original slot: it contains the generation environment.
    result.update({key:identity[key] for key in ('attempt_id','task_id','condition','sample_index','model_snapshot')})
    result.update(record_kind='analysis_projection_of_phase_separated_attempt',
        generation=record['generation'],generation_hash=record['generation_hash'],
        environment_hash=context['environment']['environment_hash'],
        engine_hash=context['policy']['engine_hash'],evaluator_source_hash=context['policy']['evaluator_source_hash'],
        generation_identity=identity,verification_policy_hash=record['verification_policy_hash'])
    return result


def report_from_context(context, records):
    manifest=context['manifest']
    if (len(manifest['slots'])!=1200 or len(manifest['tasks'])!=100
        or {t['task_id'] for t in manifest['tasks']}!={f'OM100-{i:03d}' for i in range(1,101)}):
        raise ValueError('The final OM100 report requires all100 questions and1200 scheduled attempts per model')
    require_complete_phase(context,records)
    projections=[analysis_projection(context,row) for row in records]
    references=context['references']
    if len(references)!=len(manifest['tasks']) or any(r.get('environment_hash')!=context['environment']['environment_hash'] for r in references.values()):
        raise ValueError('All references must belong to the amended verification environment')
    result=analyze(manifest['tasks'],projections,references,context['config'])
    if not result['models'][manifest['model_snapshot']]['run_complete']:
        raise ValueError('A supposedly complete phase still contains unresolved outcomes')
    phases=phase_provenance(context)
    result['phase_provenance']=phases
    result['run_provenance']={'run_hash':manifest['run_hash'],'development':manifest['development'],
        'split':manifest['split'],'experiment':manifest['config']['experiment'],
        'protocol_version':manifest['config']['protocol_version'],'evaluation_version':VERSION,
        'model_snapshot':manifest['model_snapshot'],
        'label':'Selected 100-question exploratory evaluation; all original responses replayed under explicit Lean -j1 verification.',
        'generation_environment_hash':manifest['environment']['environment_hash'],
        'verification_environment_hash':context['environment']['environment_hash'],
        'verification_policy_hash':digest(context['policy']),
        'reference_policy_hash':context['policy']['reference_policy_hash'],
        'reference_anchor_binding':'uniform_runtime_amendment_reference_index',
        'reference_index_sha256':context['policy']['reference_index_sha256']}
    result['interpretation'].extend(['Generation identities, prompts, model settings and costs remain those of the original frozen runs.',
        'Verification and reference measurements use the separate v2_threads1 runtime amendment. Earlier v1 records are preserved and excluded from these statistics.'])
    return result


def phase_html(phases):
    generation,verification=phases['generation'],phases['verification']
    return ('<section class="notice"><h2>Generation and verification provenance</h2>'
        '<p>The original model responses were replayed uniformly with an explicit Lean thread cap of one. '
        'This report uses the new verification phase and its rechecked references; earlier checks are retained separately.</p>'
        '<table style="table-layout:fixed;overflow-wrap:anywhere"><tr><th>Phase</th><th>Identity</th></tr>'
        '<tr><td>Original generation run</td><td><code>'+html.escape(generation['run_hash'])+'</code></td></tr>'
        '<tr><td>Generation environment</td><td><code>'+html.escape(generation['environment_hash'])+'</code></td></tr>'
        '<tr><td>Verification version</td><td>'+html.escape(verification['version'])+'</td></tr>'
        '<tr><td>Verification environment</td><td><code>'+html.escape(verification['environment_hash'])+'</code></td></tr>'
        '<tr><td>Verification policy</td><td><code>'+html.escape(verification['policy_hash'])+'</code></td></tr></table>'
        '<p><a href="verification-phase.json">Exact phase identities and limits</a> · '
        '<a href="verification-policy.json">Verification policy</a> · '
        '<a href="phase-audit-index.json">Selected proof-audit record index</a></p></section>')


def create_report(verifier,run,amendment,output):
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError('Report destination must be new or empty')
    context=verifier.load_verification_context(run,amendment)
    records=verifier.load_records(run,amendment_path=amendment,graphs=False)
    report=report_from_context(context,records)
    with staged_report(output) as stage:
        write_report(report,stage)
        # Keep the actual two-phase schema. No old-environment relabeling or fake
        # stock records() compatibility exports are produced.
        write_rows(stage/'all_scheduled_attempts.json',records)
        write_json(stage/'verification-phase.json',report['phase_provenance'])
        write_json(stage/'verification-policy.json',context['policy'])
        sampled=set()
        for section in report['models'][context['manifest']['model_snapshot']]['audit_sample'].values():
            for key in ('largest_negative','largest_positive','random_paired'):sampled.update(section.get(key,[]))
        write_json(stage/'phase-audit-index.json',{'kind':'phase_separated_proof_audit_index','version':VERSION,
            'source_run_hash':context['manifest']['run_hash'],'verification_policy_hash':digest(context['policy']),
            'note':'Use the nested verification_result in the linked original record. Hashes bind full files including graphs; this index does not rerun Lean.',
            'records':[{'attempt_id':r['attempt_id'],'generation_identity':r['generation_identity'],
                        'generation_hash':r['generation_hash'],'verification_record':r['verification_record']}
                       for r in records if r['generation_identity']['task_id'] in sampled]})
        page=stage/'report.html';text=page.read_text();marker='<h1>Lean compression evaluation</h1>'
        if text.count(marker)!=1:raise ValueError('Report phase insertion point changed')
        page.write_text(text.replace(marker,marker+phase_html(report['phase_provenance'])))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    work=REPO.parents[1]/'work/full-eval-100'
    parser.add_argument('--verifier',type=Path,default=work/'verify_runtime_v2.py')
    parser.add_argument('--amendment',type=Path,default=work/'runtime-amendment-v2.json')
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('om100_verified_phase_reader',args.verifier)
    verifier=importlib.util.module_from_spec(spec);sys.modules[spec.name]=verifier;spec.loader.exec_module(verifier)
    report=create_report(verifier,args.run,args.amendment,args.output)
    print(json.dumps({'output':str(args.output),'generation_run_hash':report['run_provenance']['run_hash'],
                     'evaluation_version':VERSION,'model':report['run_provenance']['model_snapshot'],'complete':True}))


if __name__=='__main__':main()
