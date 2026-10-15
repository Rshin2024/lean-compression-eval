"""Fork eight immutable schedules, preserving prompts/reference anchors and core.

Never performs API calls or activates launch_ready. The original generation
environment remains historical prompt provenance; verification is uniformly
bound to the existing v2_threads1 image and reused verified reference evidence.
"""
from __future__ import annotations
import copy
from decimal import Decimal
import os
from pathlib import Path
import shutil
import sys

HERE=Path(__file__).resolve().parent
WORK=HERE.parent
BASE=WORK.parents[1]
REPO=BASE/'outputs/lean-compression-eval'
OLD=BASE/'work/full-eval-100'
sys.path.insert(0,str(REPO/'src'))
from lean_compression.io import digest,file_hash,evaluator_hash,measurement_hash,now,read_json,write_json
from lean_compression.integrity import require_implemented_policy,frozen_portfolio
from lean_compression.store import load_run,schedule,require_reference_anchors
from lean_compression.costs import validate_pricing
from lean_compression.prompts import adapt_response,prompt_policy_hash
from priority_costs import VERSION

EXPERIMENT='openai_math_100_provider_max_v2_20261014'
MODELS=['gpt-6.1-sol','gpt-6-sol','gpt-6-luna','gpt-6-astra',
        'claude-haiku-5-5','claude-sonnet-5-5','claude-opus-5-5','claude-fable-5-1']


def unchanged_copy(src,dst):
    dst.parent.mkdir(parents=True,exist_ok=True)
    if dst.exists():
        if file_hash(src)!=file_hash(dst):raise ValueError('Different existing artifact: '+str(dst))
    else:
        # Immutable reference records may be large; retain same immutable inode.
        os.link(src,dst)
    return {'path':dst.relative_to(WORK).as_posix(),'sha256':file_hash(dst)}


def main():
    old_amend=read_json(OLD/'runtime-amendment-v2.json')
    assert evaluator_hash()==old_amend['source_core_hash']
    assert file_hash(REPO/'lean/Evaluator.lean')==old_amend['engine_hash']
    assert not (WORK/'launch_ready.json').exists()
    old_run_bindings={x['model']:x for x in old_amend['generation_runs']}
    records=read_json(OLD/'verification_v2/reference-index.json')
    rows=[];comparisons=[];old_ids=set();new_ids=set()
    for model in MODELS:
        source=REPO/'runs'/('om100-'+model+'-high')
        old=load_run(source)
        assert old['run_hash']==old_run_bindings[model]['run_hash']
        assert file_hash(source/'manifest.json')==old_run_bindings[model]['manifest_sha256']
        old_ids.update(s['attempt_id'] for s in old['slots'])
        new=copy.deepcopy(old)
        config=new['config']
        config['experiment']=EXPERIMENT
        config['generation']['max_output_tokens']=128000
        config['generation']['timeout_seconds']=600
        config['generation']['notes']='Provider-maximum rerun: 128000 reasoning-plus-answer tokens, frozen high effort and identical original prompts; streaming transport read-idle timeout is 600 seconds, not a total generation deadline.'
        config['pricing']['as_of']='2026-10-14'
        if config['generation']['provider']=='openai':
            config['generation']['service_tier']='priority'
            rates=copy.deepcopy(config['pricing']['rates_per_million'])
            config['pricing']['pricing_by_delivered_tier']={
                'version':VERSION,'region':'global','missing_delivered_tier':'unavailable',
                'requested_service_tier':'priority','equivalent_response_tiers':{'fast':'priority'},
                'rates_per_million':{'default':rates,'priority':{k:str(Decimal(v)*2) for k,v in rates.items()}},
                'sources':[f'https://developers.openai.com/api/docs/models/{model}',
                           'https://developers.openai.com/api/docs/guides/fast-mode',
                           'https://developers.openai.com/api/docs/pricing'],
                'core_default_price_is_not_used_for_priority_outputs':True}
        config['runtime_extensions']={
            'transport':'provider_max_streaming_v1',
            'cost_extension':{'path':'preparation/priority_costs.py','sha256':file_hash(HERE/'priority_costs.py')},
            'verification_version':'v2_threads1',
            'historical_generation_environment_is_not_execution_preflight':True,
            'source_run_hash':old['run_hash']}
        require_implemented_policy(config);frozen_portfolio(config)
        validate_pricing(config['pricing'],config['generation']['provider'],model)
        adapt_response('',config['generation']['response_adapter'])
        assert measurement_hash(config)==old['measurement_hash']
        assert prompt_policy_hash()==old['prompt_policy_hash']
        new['created_at']=now();new['config_hash']=digest(config)
        new['slots']=schedule(new['tasks'],config,new['environment'],model)
        assert len(new['slots'])==1200 and len(new['tasks'])==100
        for a,b in zip(old['slots'],new['slots']):
            assert (a['task_id'],a['condition'],a['sample_index'],a['schedule_index'],a['messages'],a['prompt_hash']) == (b['task_id'],b['condition'],b['sample_index'],b['schedule_index'],b['messages'],b['prompt_hash'])
            assert a['attempt_id']!=b['attempt_id']
        assert new['task_manifest_hash']==old['task_manifest_hash']
        assert new['reference_anchors']==old['reference_anchors']
        for task in new['tasks']:
            reference=records['reference_records'][task['task_id']]
            assert digest(reference['proof'])==new['reference_proof_hashes'][task['task_id']]
        new_ids.update(s['attempt_id'] for s in new['slots'])
        new.pop('run_hash');new['run_hash']=digest(new)
        directory='om100-max-'+model+'-high-20261014'
        target=REPO/'runs'/directory
        if (target/'manifest.json').exists():
            existing=load_run(target)
            existing_compare={k:v for k,v in existing.items() if k not in ('created_at','run_hash')}
            new_compare={k:v for k,v in new.items() if k not in ('created_at','run_hash')}
            if existing_compare!=new_compare:raise ValueError('Different existing frozen rerun: '+model)
            new=existing
        else:write_json(target/'manifest.json',new)
        load_run(target)
        write_json(HERE/'frozen-configs'/f'{model}.json',config)
        row={'model':model,'directory':directory,'run_hash':new['run_hash'],
             'manifest_sha256':file_hash(target/'manifest.json'),'slots_hash':digest(new['slots']),
             'generation_config_hash':new['config_hash'],
             'generation_environment_hash':new['environment']['environment_hash']}
        rows.append(row)
        comparisons.append({'model':model,'old_run_hash':old['run_hash'],
            'old_manifest_sha256':file_hash(source/'manifest.json'),'new_run_hash':new['run_hash'],
            'all_1200_slot_prompts_byte_identical':True,'all_1200_attempt_ids_fresh':True,
            'reference_anchors_unchanged':True,'task_manifest_hash':new['task_manifest_hash'],
            'measurement_hash_unchanged':True})
        print('Frozen '+model,flush=True)
    assert len(new_ids)==9600 and not new_ids.intersection(old_ids)
    # Copy immutable runtime and source reference evidence, without a new Lean execution.
    descriptors={}
    for name in ('environment.json','runtime-build.json','reference-index.json'):
        descriptors[name]=unchanged_copy(OLD/'verification_v2'/name,WORK/'verification_v2'/name)
    bindings=records['record_bindings']
    for tid,binding in bindings.items():
        src=OLD/binding['path'];dst=WORK/binding['path']
        desc=unchanged_copy(src,dst)
        assert desc['sha256']==binding['sha256']
    unchanged_copy(OLD/'verification_v2/reference-checks/policy.json',WORK/'verification_v2/reference-checks/policy.json')
    unchanged_copy(OLD/'verify_runtime_v2.py',WORK/'verify_runtime_v2.py')
    amendment=copy.deepcopy(old_amend)
    amendment.update(generation_runs=rows,reason='Fresh provider-maximum generation cohort; reuse the exact previously verified v2_threads1 runtime and 100 reference records. Verification never calls providers.',
        generation_settings_and_responses_unchanged=True,original_manifests_and_attempt_ids_unchanged=True,
        reference_reuse={'source_workspace':str(OLD),'source_amendment_sha256':file_hash(OLD/'runtime-amendment-v2.json'),
            'new_reference_executions':False,'hash_verified_reference_records':100},
        verification_change='Apply existing explicit -j1 runtime uniformly to the separately frozen provider-maximum cohort',
        generation_scope='new separate cohort; unchanged means relative to this cohort freeze, not previous8192 cohort')
    amendment['verification_environment']=descriptors['environment.json']
    amendment['runtime_build']=descriptors['runtime-build.json']
    amendment['reference_index']=descriptors['reference-index.json']
    write_json(WORK/'runtime-amendment-v2.json',amendment)
    plan={'ready':False,'experiment':EXPERIMENT,'source_hash':evaluator_hash(),
       'engine_hash':old_amend['engine_hash'],'runs':rows,'conditions':['standard','mathematical_compression','source_concision'],
       'scheduled_attempts':9600,'samples_per_question_condition':4,'questions':100,
       'max_output_tokens':128000,'reasoning_effort':'high',
       'cost_extension':{'path':'preparation/priority_costs.py','sha256':file_hash(HERE/'priority_costs.py')},
       'freezer':{'path':'preparation/freeze_provider_max.py','sha256':file_hash(Path(__file__))},
       'amendment':{'path':'runtime-amendment-v2.json','sha256':file_hash(WORK/'runtime-amendment-v2.json')},
       'transport_approval_and_cloud_preflight':'root_validation_required_before_ready_true',
       'old_run_immutability_and_prompt_comparison':comparisons,
       'data_scope':'same exploratory100 supplied questions; no formal250questionstudy',
       'created_at':now()}
    write_json(HERE/'launch_entries.json',plan)
    sys.path.insert(0,str(WORK))
    import verify_runtime_v2 as v2
    for row in rows:
        c=v2.load_verification_context(REPO/'runs'/row['directory'],WORK/'runtime-amendment-v2.json')
        assert len(c['references'])==100
    write_json(HERE/'freeze-validation.json',{'status':'pass','scheduled_attempts':9600,
       'models':8,'references':100,'all_prompts_identical':True,'new_attempt_ids':9600,
       'old_attempt_overlap':0,'core_unchanged':True,'v2_contexts_validated':8,
       'launch_ready_created':False,'launch_entries_sha256':file_hash(HERE/'launch_entries.json'),
       'cost_extension_sha256':file_hash(HERE/'priority_costs.py')})
    print('Validated all8frozenruncontexts; NOT RELEASED',flush=True)


if __name__=='__main__':main()
