"""Offline finality and provenance tests; no real requests or Lean execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import report_verification_v2 as adapter
import build_comparison as combined
from lean_compression.io import digest
from lean_compression.metrics import score_graph


def fixture():
    model='gpt-6.1-sol'
    config={'generation':{'provider':'openai','model_snapshot':model,'reasoning_setting':'high','max_output_tokens':8192},
        'validation':{'cpu_quota':2,'memory_mib':8192,'wall_seconds':180,'docker_image':'sha256:'+'b'*64},
        'extraction':{'wall_seconds':900},'environment':{'lean_toolchain':'leanprover/lean4:v4.34.1','mathlib_commit':'m',
            'boundary_policy':'expand_mathlib_and_evaluator_v1'},
        'analysis':{'bootstrap_replicates':10},'samples_per_goal_condition':4,
        'experiment':'openai_math_100_v1','protocol_version':'test'}
    tasks=[{'task_id':f'OM100-{i:03d}','family_id':str(i%19),'domain':'algebra','split':'main'} for i in range(1,101)]
    slots=[{'attempt_id':digest([model,t['task_id'],c,s]),'task_id':t['task_id'],'model_snapshot':model,
        'condition':c,'sample_index':s,'environment_hash':'generation-env','config_hash':'generation-config','prompt_hash':digest([t,c]),'messages':[]}
        for t in tasks for c in combined.CONDS for s in range(4)]
    policy={'source_run_hash':'source-run','source_manifest_sha256':'source-manifest','version':adapter.VERSION,
        'generation_environment_hash':'generation-env','verification_environment_hash':'verification-env',
        'verification_docker_image':'sha256:'+'b'*64,'generation_config_hash':'generation-config',
        'verification_config_hash':digest(config),'amendment_hash':'amendment','measurement_hash':'measurement',
        'engine_hash':'engine','evaluator_source_hash':'core','reference_policy_hash':'reference-policy',
        'reference_index_sha256':'reference-index'}
    manifest={'run_hash':'source-run','config_hash':'generation-config','model_snapshot':model,'tasks':tasks,'slots':slots,
        'environment':{'environment_hash':'generation-env','docker_image':'sha256:'+'a'*64},
        'config':config,'development':True,'split':'main'}
    metrics=score_graph([{'name':'root','primitive':False,'signature':{'atom':1},'body':{'atom':2}},
                         {'name':'atom','primitive':True}],'root',3,2)
    refs={t['task_id']:{'environment_hash':'verification-env','validity_status':'success','metric_status':'available','metrics':metrics} for t in tasks}
    context={'manifest':manifest,'policy':policy,'config':config,'environment':{'environment_hash':'verification-env'},'references':refs}
    records=[]
    for slot in slots:
        generation={'attempt_id':slot['attempt_id'],'prompt_hash':slot['prompt_hash'],'generation_status':'truncated_output',
                    'raw_response':'unfinished','raw_response_hash':digest('unfinished'),'generation_settings':config['generation']}
        records.append({'record_kind':'phase_separated_verified_attempt','evaluation_version':adapter.VERSION,
            'attempt_id':slot['attempt_id'],'source_run_hash':'source-run','source_manifest_sha256':'source-manifest',
            'generation_identity':{k:v for k,v in slot.items() if k!='messages'},'generation':generation,'generation_hash':digest(generation),
            'verification_policy_hash':digest(policy),'verification_environment_hash':'verification-env',
            'verification_result':{'validity_status':'truncated_output','metric_status':'unavailable','metrics':None},
            'validity_status':'truncated_output','metric_status':'unavailable',
            'verification_record':{'path':f'evaluations/{adapter.VERSION}/{slot["attempt_id"]}.json','sha256':'full-file-hash'}})
    return context,records


class PhaseReportingTests(unittest.TestCase):
    def setUp(self):self.context,self.records=fixture()

    def test_terminal_looking_pending_truncation_is_incomplete(self):
        self.records[0]['record_kind']='phase_separated_pending_attempt'
        with self.assertRaisesRegex(ValueError,'stored terminal'):
            adapter.report_from_context(self.context,self.records)

    def test_missing_duplicate_and_wrong_phase_records_rejected(self):
        with self.assertRaisesRegex(ValueError,'schedule coverage'):
            adapter.require_complete_phase(self.context,self.records[:-1])
        duplicate=self.records[:-1]+[self.records[0]]
        with self.assertRaisesRegex(ValueError,'schedule coverage'):
            adapter.require_complete_phase(self.context,duplicate)
        self.records[0]['verification_environment_hash']='generation-env'
        with self.assertRaisesRegex(ValueError,'phase record identity|Phase record identity'):
            adapter.require_complete_phase(self.context,self.records)

    def test_projection_has_verification_env_and_preserves_original_phase(self):
        original=copy.deepcopy(self.records[0])
        row=adapter.analysis_projection(self.context,self.records[0])
        self.assertEqual(row['environment_hash'],'verification-env')
        self.assertEqual(row['generation_identity']['environment_hash'],'generation-env')
        self.assertEqual(self.records[0],original)
        self.assertEqual(row['generation'],original['generation'])

    def test_complete_report_counts_all_truncations_and_keeps_cost_unknown(self):
        report=adapter.report_from_context(self.context,self.records)
        self.assertTrue(report['models']['gpt-6.1-sol']['run_complete'])
        self.assertEqual(report['run_provenance']['run_hash'],'source-run')
        self.assertEqual(report['provenance']['environment_hash'],'verification-env')
        self.assertIsNone(report['costs']['summary']['observed_sum_usd'])
        self.assertEqual(report['costs']['summary']['generated_attempts'],1200)
        self.assertEqual(report['phase_provenance']['generation']['environment_hash'],'generation-env')

    def test_combined_view_reads_nested_result_and_rejects_policy_mismatch(self):
        phase=adapter.phase_provenance(self.context)
        row=combined.phase_attempt_view(self.records[0],phase)
        self.assertEqual(row['validity_status'],'truncated_output')
        self.assertNotIn('environment_hash',row)
        self.records[0]['verification_policy_hash']='other'
        with self.assertRaisesRegex(ValueError,'different generation or verification'):
            combined.phase_attempt_view(self.records[0],phase)

    def test_combined_phase_validation_and_legacy_om100_rejection(self):
        phase=adapter.phase_provenance(self.context)
        analysis={'phase_provenance':phase,'run_provenance':{'run_hash':'source-run','evaluation_version':adapter.VERSION}}
        with tempfile.TemporaryDirectory() as tmp:
            report=Path(tmp);(report/'verification-policy.json').write_text(json.dumps(self.context['policy']))
            self.assertEqual(combined.reviewed_phase(analysis,report,100,'observed'),phase)
            with self.assertRaisesRegex(ValueError,'requires uniform'):
                combined.reviewed_phase({},report,100,'observed')
            changed=copy.deepcopy(analysis);changed['phase_provenance']['verification']['environment_hash']='other'
            with self.assertRaisesRegex(ValueError,'cohort hash'):
                combined.reviewed_phase(changed,report,100,'observed')

    def test_complete_eight_model_report_export_and_mixed_phase_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);suite=root/'suite';suite.mkdir()
            for model in combined.MODELS:
                ctx,records=fixture();manifest=ctx['manifest'];policy=ctx['policy']
                manifest['model_snapshot']=model;manifest['run_hash']=digest(model)
                ctx['config']['generation']['model_snapshot']=model
                ctx['config']['generation']['provider']='anthropic' if model.startswith('claude') else 'openai'
                policy['source_run_hash']=manifest['run_hash'];policy['source_manifest_sha256']=digest([model,'manifest'])
                policy['verification_config_hash']=digest(ctx['config'])
                for slot,row in zip(manifest['slots'],records):
                    aid=digest([model,slot['task_id'],slot['condition'],slot['sample_index']])
                    slot.update(model_snapshot=model,attempt_id=aid)
                    row.update(attempt_id=aid,source_run_hash=manifest['run_hash'],
                        generation_identity={k:v for k,v in slot.items() if k!='messages'},
                        verification_policy_hash=digest(policy),source_manifest_sha256=policy['source_manifest_sha256'])
                    row['generation']['attempt_id']=aid;row['generation_hash']=digest(row['generation'])
                    row['verification_record']['path']=f'evaluations/{adapter.VERSION}/{aid}.json'
                fake=SimpleNamespace(load_verification_context=lambda *args,c=ctx:c,
                                     load_records=lambda *args,r=records,**kwargs:r)
                adapter.create_report(fake,root/'unused-run',root/'unused-amendment',suite/('report-'+model))
            result=combined.build(suite,output=root/'page',classification='synthetic_test_fixture')
            self.assertEqual(result['totalAttempts'],9600)
            self.assertEqual(result['verification']['version'],adapter.VERSION)
            self.assertEqual(result['verification']['limits']['lean_threads'],1)
            self.assertTrue(all(m['phaseProvenance']['generation']['environment_hash']=='generation-env' for m in result['models']))
            export=json.loads((suite/('report-'+combined.MODELS[0])/'all_scheduled_attempts.json').read_text())
            self.assertNotIn('metrics',export[0]);self.assertIn('verification_result',export[0])
            # A self-consistent different amendment still cannot join this cohort.
            reportdir=suite/('report-'+combined.MODELS[-1]);p=reportdir/'analysis.json';analysis=json.loads(p.read_text())
            phase=analysis['phase_provenance'];phase['verification']['amendment_hash']='different-amendment'
            policy=json.loads((reportdir/'verification-policy.json').read_text());policy['amendment_hash']='different-amendment'
            phase['verification']['policy_hash']=digest(policy)
            phase['cohort_hash']=digest({k:phase['verification'][k] for k in adapter.COMMON_KEYS})
            p.write_text(json.dumps(analysis));(reportdir/'verification-policy.json').write_text(json.dumps(policy))
            with self.assertRaisesRegex(ValueError,'mixed verification'):
                combined.build(suite,output=root/'mixed',classification='synthetic_test_fixture')


if __name__=='__main__':unittest.main()
