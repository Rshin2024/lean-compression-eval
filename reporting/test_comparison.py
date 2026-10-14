"""Offline comparison-contract tests; fixtures are never evaluation observations."""
import copy
import json
import subprocess
from pathlib import Path
import tempfile
import unittest

from build_comparison import build, canonical_hash, CONDS, MODELS, exact_geometric_t0, write_question_viewer


def fixture(suite, count=100):
    suite.mkdir(parents=True, exist_ok=True)
    tasks = [f'OM100-{i:03d}' for i in range(1, count + 1)]
    for mi, model in enumerate(MODELS):
        rows, cost_rows, per_task, conditions = [], [], {}, {}
        for task in tasks:
            per_task[task] = {}
            # The eighth model has no measurable output: the all-eight common cohort is empty.
            good = mi < 7
            for condition in CONDS:
                per_task[task][condition] = {'measurable_count': 4 if good else 0,
                    'mean_log2_T0': float(mi + 1) if good else None,
                    'mean_log2_W_B': 2.0 if good else None}
                for sample in range(4):
                    key = {'task_id': task, 'condition': condition, 'sample_index': sample,
                           'model_snapshot': model}
                    rows.append({**key, 'validity_status': 'success' if good else 'parse_failure',
                        'metric_status': 'available' if good else 'unavailable',
                        'metrics': {'W_B': 4, 'W_S': 4, 'U_B_decimal': str(2**(mi+4)),
                            'U_S_decimal': '0', 'D': 3, 'log2_T0': float(mi+1),
                            'T0': {'numerator_decimal': str(2**(mi+1)), 'denominator_decimal': '1'},
                            'I0': {'numerator_decimal': '1', 'denominator_decimal': '1'}} if good else {},
                        'generation': {'generation_settings': {'reasoning_setting':'high',
                            'max_output_tokens': 16384}, 'raw_response': 'PRIVATE RESPONSE MUST NOT BE EXPORTED'}})
                    priced = sample != 3
                    cost_rows.append({**key, 'generated': True, 'cost': {'status': 'estimated' if priced else 'unavailable',
                        'total_usd': '0.125' if priced else None, 'reasons': [] if priced else ['usage_missing'],
                        'currency': 'USD', 'pricing_hash': 'fixture', 'usage_hash': 'fixture'}})
        for condition in CONDS:
            n = count * 4
            conditions[condition] = {'scheduled_attempts': n, 'valid_attempts': n if mi < 7 else 0,
                'measurable_valid_attempts': n if mi < 7 else 0, 'measurable_task_count': count if mi < 7 else 0,
                'failure_statuses': {} if mi < 7 else {'parse_failure': n},
                'attainment': {str(k): {'attaining_valid_measurable_attempts': 0} for k in range(3)}}
        contrast = {'mean_log2_T0_difference': {'geometric_ratio':1.0,'geometric_ratio_interval':None},
                    'mean_log2_W_B_difference': {'geometric_ratio':1.0,'geometric_ratio_interval':None},
                    'paired_population': {'task_count': count if mi<7 else 0}}
        analysis = {'population': {'task_count': count}, 'samples_per_goal_condition': 4,
            'models': {model: {'run_complete':True, 'conditions': conditions, 'per_task':per_task,
                'contrasts': {k:contrast for k in ['compression_vs_standard','compression_vs_concision']}}}}
        report = suite / ('report-' + model); report.mkdir(exist_ok=True)
        (report/'analysis.json').write_text(json.dumps(analysis))
        (report/'all_scheduled_attempts.json').write_text(json.dumps(rows))
        (report/'costs.json').write_text(json.dumps({'attempts':cost_rows, 'pricing_hash':'fixture',
            'pricing_as_of':'2026-10-13', 'summary': {'scheduled_attempts':count*12,
                'priced_attempts':count*9, 'observed_sum_usd':str(count*9*.125)}}))
    return tasks


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.suite = self.root/'suite'
        fixture(self.suite)

    def build(self):
        return build(self.suite, output=self.root/'output', classification='synthetic_test_fixture')

    def test_full_population_cost_unknowns_and_answer_separation(self):
        result = self.build()
        self.assertEqual(result['totalAttempts'],9600)
        self.assertEqual(result['rankCoverageDefault'],25)
        self.assertEqual(result['magnitudePopulationDefault'],'shared')
        self.assertEqual(sum(r['cost']['status']=='estimated' for r in result['attempts']),7200)
        self.assertTrue(all(r['cost']['total_usd'] is None for r in result['attempts'] if r['cost']['status']=='unavailable'))
        self.assertNotIn('PRIVATE RESPONSE', (self.root/'output'/'index.html').read_text())
        self.assertFalse(result['questionViewer']['available'])

    def test_duplicate_attempt_rejected(self):
        p=self.suite/('report-'+MODELS[0])/'all_scheduled_attempts.json'
        rows=json.loads(p.read_text());rows[-1]=rows[0];p.write_text(json.dumps(rows))
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.build()

    def test_cost_subtotal_tampering_rejected(self):
        p=self.suite/('report-'+MODELS[0])/'costs.json'
        value=json.loads(p.read_text());value['summary']['observed_sum_usd']='0';p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'reconcile'):self.build()

    def test_question_ranking_tampering_rejected(self):
        p=self.suite/('report-'+MODELS[0])/'analysis.json'
        value=json.loads(p.read_text());value['models'][MODELS[0]]['per_task']['OM100-001']['standard']['mean_log2_T0']=999
        p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'ranking metric'):self.build()

    def test_questions_export_only_verified_frozen_messages(self):
        entries=[]
        for model in MODELS:
            messages=[{'role':'system','content':'Exact system'}, {'role':'user','content':'Prove </script><script>forbidden()</script>.'}]
            slots=[{'task_id':'test','condition':c,'sample_index':s,'messages':messages,'prompt_hash':canonical_hash(messages)} for c in CONDS for s in range(4)]
            value={'model_snapshot':model,'slots':slots,'config':{'validation':{},'extraction':{}},'private':'DO NOT EXPORT'}
            value['run_hash']=canonical_hash(value)
            p=self.root/(model+'.json');p.write_text(json.dumps(value));entries.append((model,p,value['run_hash']))
        result=write_question_viewer(entries,self.root/'questions',['test'],4)
        self.assertEqual(result['messageSets'],3)
        html=(self.root/'questions'/'questions.html').read_text()
        self.assertNotIn('DO NOT EXPORT',html);self.assertNotIn('</script><script>forbidden()',html)
        value['slots'][0]['messages']=copy.deepcopy(messages);value['slots'][0]['messages'][1]['content']='tampered'
        p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'manifest hash mismatch'):
            write_question_viewer(entries,self.root/'questions',['test'],4)

    def test_browser_math_exact_costs_and_equal_question_tie_ranks(self):
        html=Path(__file__).with_name('comparison.html').read_text()
        ranks=html.split('function exactMeanCompare(',1)[1].split('function questionRankView(',1)[0]
        money=html.split('function sumMoney(',1)[1].split('function dollars(',1)[0]
        script="const assert=require('node:assert/strict'),NAME=x=>x,mean=a=>a.reduce((x,y)=>x+y,0)/a.length;\n"
        script+='function exactMeanCompare('+ranks+'\nfunction sumMoney('+money
        script+='''
assert.equal(sumMoney(['0.1','0.2','0.000000001']),'0.300000001');
assert.equal(sumMoney([]),null);assert.equal(sumMoney(['0.000','0']), '0');
const cohort=[['a',[3,1]],['b',[3,2]],['c',[1,3]]].map(([id,values])=>({id,perTask:Object.fromEntries(values.map((v,i)=>['q'+i,{test:{mean_log2_T0:v}}]))}));
const result=summarizeQuestionRanks(cohort,['q0','q1'],'test');
assert.deepEqual(Object.fromEntries(result.map(r=>[r.model,r.average])),{b:1.75,c:2,a:2.25});
assert.equal(result.find(r=>r.model==='a').ranks.q0,1.5);
assert.deepEqual(summarizeQuestionRanks(cohort,[],'test'),[]);
// These values collapse to the same float but have distinct exact ranks.
const huge=10n**95n;
const stats=(num,den='1',count=1)=>({mean_log2_T0:315,exact_geometric_T0:{numerator_product_decimal:String(num),denominator_product_decimal:den,sample_count:count}});
const precise=[['a',stats(huge)],['b',stats(huge+1n)],['c',stats(huge**2n,'1',2)]].map(([id,s])=>({id,perTask:{q:{test:s}}}));
const exact=summarizeQuestionRanks(precise,['q'],'test');
assert.deepEqual(exact.map(r=>[r.model,r.average]),[['b',1],['a',2.5],['c',2.5]]);
assert.equal(exact[0].rankingMethods.q,'exact_geometric_mean_rational');
assert.equal(exactMeanCompare(stats(9n,'4',2),stats(3n,'2',1)),0);
'''
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)

    def test_exact_product_reduces_and_preserves_sub_float_difference(self):
        huge=10**95
        rows=[{'metrics':{'T0':{'numerator_decimal':str(huge+1),'denominator_decimal':'2'}}},
              {'metrics':{'T0':{'numerator_decimal':'2','denominator_decimal':'1'}}}]
        self.assertEqual(exact_geometric_t0(rows),{'numerator_product_decimal':str(huge+1),
            'denominator_product_decimal':'1','sample_count':2})
        self.assertIsNone(exact_geometric_t0([{'metrics':{}}]))


if __name__ == '__main__':unittest.main()
