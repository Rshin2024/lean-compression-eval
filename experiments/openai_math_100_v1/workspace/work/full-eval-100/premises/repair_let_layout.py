"""Whitespace/layout repair evidence for two trusted source propositions.

Reference proof bytes are unchanged. The whole source proposition is parenthesized
and its first line restored to the source's four-space column, so both strict
evaluator renderings parse the existing newline-separated let expression.
"""
from pathlib import Path
import copy
import json
import sys

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
sys.path.insert(0, str(WORK))
import repair_materialization as repair
import materialize as m
from lean_compression.runner import evaluate_proof, execute_engine


def request(task, proof, validation):
    out = {k:task[k] for k in ['imports','import_specs','context','binders','proposition','target_name','forbidden_declarations']}
    out.update(proof=proof,max_heartbeats=validation['max_heartbeats'],permitted_axioms=validation['permitted_axioms'])
    return out


def main():
    environment = m.load_environment(WORK/'environment.azure.json')
    config = m.read_json(WORK/'materialized_v1/config.used.json')
    engine = m.REPO/'lean/Evaluator.lean'
    source = WORK/'materialized_v1/repairs_v2/records'
    output = HERE/'let_layout_v1'
    output.mkdir(parents=True,exist_ok=True)
    for ident in ['OM100-041','OM100-094']:
        prior = m.read_json(source/ident/'prepared.json')
        original, reference = prior['task'], prior['reference']
        proof = reference['proof']
        candidate = copy.deepcopy(original)
        if not candidate['proposition'].startswith('let '):raise ValueError('Unexpected source proposition')
        candidate['proposition'] = '(\n    '+original['proposition']+'\n)'
        candidate = m.refresh_hashes(candidate)
        compared = m.comparison_task(original,candidate,proof)
        original_short = 'CompressionEvalMaterialize_'+ident.replace('-','_')+'_Original'
        original_full = repair.source_namespace(original)+'.'+original_short
        compared['context'] = compared['context'].replace('theorem _root_.'+original_short+' ','theorem '+original_short+' ')
        compared['context'] = compared['context'].replace('env.find? `'+original_short,'env.find? `'+original_full)
        validation = config['validation']
        comparison = execute_engine(request(compared,'True.intro',validation),environment,engine,validation,
                                    mode='validate',wall_seconds=validation['wall_seconds'])
        m.write_private(output/(ident+'-comparison.json'),m.json_text(comparison))
        print(json.dumps({'task_id':ident,'stage':'original_vs_wrapped_type','status':comparison.get('validity_status'),
                          'reason':comparison.get('failure_reason','')[:300]}),flush=True)
        if comparison.get('validity_status')!='success' or comparison.get('kernel_replay') is not True:continue
        measured = evaluate_proof(candidate,proof,environment,engine,config)
        record = {'task_id':ident,'task':candidate,'reference':reference,
                  'original_proposition':original['proposition'], 'normalized_proposition':candidate['proposition'],
                  'layout_transform':'Surround unchanged proposition with parentheses/newlines and restore original first-line four-space indentation',
                  'original_proposition_sha256':m.sha256_text(original['proposition']),
                  'normalized_proposition_sha256':m.sha256_text(candidate['proposition']),
                  'reference_proof_unchanged':reference['proof']==proof,'reference_proof_sha256':m.sha256_text(proof),
                  'engine_sha256':m.file_hash(engine),'environment_hash':environment['environment_hash'],
                  'comparison':comparison,'result':measured}
        m.write_private(output/(ident+'-measured.json'),m.json_text(record))
        print(json.dumps({'task_id':ident,'stage':'strict_measurement','status':measured.get('validity_status'),
                          'metric_status':measured.get('metric_status'),'reason':measured.get('metric_unavailable_reason')}),flush=True)


if __name__=='__main__':main()
