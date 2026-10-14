"""Publish successful strict layout-repair evidence in materializer record shape."""
from pathlib import Path
import copy
import json
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import materialize as m


def main():
    output=HERE/'let_layout_v1'
    for ident in ['OM100-041','OM100-094']:
        path=output/(ident+'-measured.json')
        record=m.read_json(path)
        result,comparison=record['result'],record['comparison']
        if (result.get('validity_status')!='success' or result.get('metric_status')!='available'
            or result.get('validation',{}).get('kernel_replay') is not True
            or comparison.get('validity_status')!='success' or comparison.get('kernel_replay') is not True
            or record['reference_proof_unchanged'] is not True):
            raise ValueError('Layout repair is not independently checked and measurable: '+ident)
        task,ref=copy.deepcopy(record['task']),copy.deepcopy(record['reference'])
        if m.sha256_text(ref['proof'])!=record['reference_proof_sha256']:
            raise ValueError('Reference proof bytes changed')
        transform={'policy':'parenthesized_source_let_layout_v1',
                   'original_proposition':record['original_proposition'],
                   'original_proposition_sha256':record['original_proposition_sha256'],
                   'normalized_proposition_sha256':record['normalized_proposition_sha256'],
                   'description':record['layout_transform'],
                   'original_vs_normalized_kernel_type_equal':True,
                   'original_proof_bytes_preserved':True,
                   'source_proposition_bytes_preserved':False,
                   'added_source_tokens':'two parentheses; original proposition and proof tokens unchanged'}
        policy={'policy':'om100_let_layout_repair_v1','engine_sha256':record['engine_sha256'],
                'environment_hash':record['environment_hash'],'source_provenance':task['provenance'],
                'reference_proof_sha256':record['reference_proof_sha256'],'transform':transform,
                'repair_script_sha256':m.file_hash(HERE/'repair_let_layout.py'),
                'evidence_sha256':m.file_hash(path)}
        policy_hash=m.digest(policy)
        task['audit'].update({key:True for key in m.MECHANICAL_AUDITS})
        task['audit'].update(answer_access_audited=False,family_reviewed=False)
        task['model_generation_enabled']=False
        task['preparation_status']='materialized_measurable'
        task['provenance'].update(signature_materialization_policy_hash=policy_hash,
            signature_materialization='Source proposition let layout parenthesized; unchanged proof independently replayed; original versus normalized whole types kernel-compared',
            proposition_layout_transform=transform)
        task=m.refresh_hashes(task)
        ref.update(verification_status='isolated_explicit_signature_pass',metric_status='available',
                   verification_evidence=str(path),signature_materialization_policy_hash=policy_hash)
        evidence={'task_id':ident,'status':'materialized_measurable','model_generation_enabled':False,
                  'policy_hash':policy_hash,'comparison_status':'success','explicit_status':'success',
                  'metric_status':'available','source_proposition_layout_transform':transform,
                  'reference_proof_unchanged':True,'evidence_path':str(path),
                  'independent_answer_access_review':'pending','prompt_interface_review':'pending'}
        m.write_private(output/(ident+'-prepared.json'),m.json_text({'task':task,'reference':ref,'evidence':evidence}))
        m.write_private(output/(ident+'-policy.json'),m.json_text(policy))
        print(json.dumps({'task_id':ident,'status':'materialized_measurable','prepared':str(output/(ident+'-prepared.json'))}),flush=True)


if __name__=='__main__':main()
