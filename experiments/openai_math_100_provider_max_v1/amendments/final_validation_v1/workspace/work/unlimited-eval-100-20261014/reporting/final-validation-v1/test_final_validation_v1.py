"""Offline synthetic checks; never invokes the real experiment validation."""
import copy
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.dont_write_bytecode=True
SOURCE=Path(__file__).resolve().parents[1]/'final_validation_v1.py'
spec=importlib.util.spec_from_file_location('final_validation_v1',SOURCE)
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

class FinalValidationTests(unittest.TestCase):
    def gate(self):
        models=[str(n) for n in range(8)]
        return {'state':'complete'},{m:{'state':'complete_synced_verified'} for m in models},{'status':'pass_complete','cohort_complete':True,'generations_audited':9600,'unique_provider_response_ids':9600,'priced_generations':9600,'models':[{'model':m,'status':'pass_complete'} for m in models]},models
    def test_complete_gate(self):v.validate_gate(*self.gate())
    def test_duplicate_rows_cannot_hide_behind_dictionary_collapse(self):
        rows=[{'id':str(i)} for i in range(3)]
        self.assertEqual(len(v.unique_rows(rows,'id',3)),3)
        with self.assertRaises(ValueError):v.unique_rows(rows+[rows[0]],'id',3)
    def test_partial_and_duplicate_model_gates_rejected(self):
        for where,key,value in [(0,'state','waiting_for_reports'),(2,'status','pass_partial_snapshot'),(2,'cohort_complete',False),(2,'unique_provider_response_ids',9599),(2,'priced_generations',9599)]:
            args=self.gate();args[where][key]=value
            with self.assertRaises(ValueError):v.validate_gate(*args)
        args=self.gate();args[1]['0']['state']='building'
        with self.assertRaises(ValueError):v.validate_gate(*args)
        args=self.gate();args[2]['models'][0]['model']='1'
        with self.assertRaises(ValueError):v.validate_gate(*args)
    def test_incomplete_main_has_no_output_or_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'must-not-exist'
            with patch.object(sys,'argv',['validator','--output',str(target)]),patch.object(v,'read',side_effect=[{}, {'state':'waiting_for_reports'}]),patch.object(v,'validate') as validation:
                with self.assertRaisesRegex(ValueError,'not activated'):v.main()
                validation.assert_not_called();self.assertFalse(target.exists())
    def review(self):
        amendment={'baseline':{'sha256':'old'},'candidate':{'sha256':'new'}}
        review={'state':'root_reviewed_viewer_only_change_for_final_delivery','manifest_sha256':'manifest','baseline_sha256':'old','candidate_sha256':'new','source_pins_superseded':False,'report_assets_replaced':False,'cloud_redeployment_authorized':False,'new_model_calls':0,'lean_calls':0,'provider_signals':0}
        return amendment,review,'old','new','manifest'
    def test_exact_viewer_review_required(self):
        self.assertTrue(v.validate_viewer_review(*self.review())['separate_root_viewer_review_passed'])
        for key,value in [('state','prepared'),('manifest_sha256','other'),('candidate_sha256','other'),('source_pins_superseded',True),('cloud_redeployment_authorized',True),('provider_signals',1)]:
            args=self.review();args[1][key]=value
            with self.assertRaises(ValueError):v.validate_viewer_review(*args)
    @staticmethod
    def outcome(status='success',available=True,truncated=False):
        return {'status':status,'sourceLength':{'available':available,'truncated':truncated,'responseStatus':'truncated_output' if truncated else 'completed','sourceKind':'partial_saved_text' if truncated else 'submitted_text'}}
    def test_completion_populations_are_not_additive(self):
        rows=[self.outcome(),self.outcome('elaboration_failure'),self.outcome('truncated_output',True,True),self.outcome('truncated_output',False,True),self.outcome('parse_failure',False)]
        result=v.completion_summary(rows)
        self.assertEqual({k:result[k] for k in ('scheduled','completed','accepted','truncated','empty','empty_truncated_overlap')},{'scheduled':5,'completed':2,'accepted':1,'truncated':2,'empty':2,'empty_truncated_overlap':1})
        self.assertEqual(result['completedAcceptanceRate'],.5);self.assertEqual(result['scheduledSolveRate'],.2);self.assertEqual(result['completionRate'],.4)
    def test_unknown_or_impossible_completion_rejected(self):
        for row in ({'status':'success'},self.outcome('success',False),self.outcome('success',True,True)):
            with self.assertRaises(ValueError):v.completion_summary([row])
        self.assertIsNone(v.completion_summary([])['completedAcceptanceRate'])
    def incidents(self):
        raw=[{'attempt_id':str(i),'kind':'fixture','evidence_sha256':str(i)} for i in range(30)]
        operations={'original_unknown_charge_incidents':[{'model':'m',**x} for x in raw],'original_unknown_charge_count':30,'operator_abandonment_count':0,'original_unknown_charges_by_cause':{'prestream_disconnect':19,'provider_timeout_error':3,'provider_api_error':6,'provider_overloaded_error':2},'total_original_unknown_charges_usd':None}
        disclosure={'incidents':[{'attempt_id':str(i),'unknown_charge_usd':None,'usage_available':False} for i in range(30)],'all_request_total_usd':None,'invoice_reconciled':False}
        return operations,disclosure,{'authorized_overload_recoveries':raw}
    def test_exact_thirty_incidents_and_unknown_charges(self):
        self.assertEqual(len(v.validate_incidents(*self.incidents())),30)
        for change in ('duplicate','cost','evidence','operator'):
            args=self.incidents()
            if change=='duplicate':args[1]['incidents'][-1]['attempt_id']='0'
            if change=='cost':args[1]['all_request_total_usd']='0'
            if change=='evidence':args[2]['authorized_overload_recoveries'][0]['evidence_sha256']='different'
            if change=='operator':args[0]['operator_abandonment_count']=1
            with self.assertRaises(ValueError):v.validate_incidents(*args)
    def openai(self,tier='fast'):
        rates={'input':'2','output':'10','cache_read':'.1','cache_write':'2.5'}
        pricing={'rates_per_million':rates,'pricing_by_delivered_tier':{'version':'delivered_service_tier_costs_v1','missing_delivered_tier':'unavailable','rates_per_million':{'default':rates,'priority':{k:str(2*Decimal(x)) for k,x in rates.items()}}},'long_context':{'threshold_input_tokens':1000,'input_multiplier':'2','output_multiplier':'1.5'}}
        gen={'usage':{'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':100,'cache_write_tokens':200}},'provider_response':{'service_tier':tier}}
        return gen,pricing,'openai'
    def test_delivered_fast_default_and_reasoning_no_double_count(self):
        args=self.openai();fast,parts,tier=v.independent_cost(*args)
        self.assertEqual(tier,'priority');self.assertEqual(fast,Decimal('.00582'))
        args[0]['usage']['output_tokens_details']={'reasoning_tokens':90}
        self.assertEqual(v.independent_cost(*args)[0],fast)
        self.assertEqual(v.independent_cost(*self.openai('default'))[0],fast/2)
        self.assertEqual(parts['input'],Decimal('.0028'))
    def test_delivered_tier_missing_conflicting_or_bad_cache_rejected(self):
        for case in ('missing','conflict','negative','price'):
            args=self.openai()
            if case=='missing':args[0]['provider_response']={}
            if case=='conflict':args[0]['usage']['service_tier']='default'
            if case=='negative':args[0]['usage']['input_tokens_details']['cached_tokens']=9999
            if case=='price':args[1]['pricing_by_delivered_tier']['rates_per_million']['priority']['input']='3'
            with self.assertRaises(ValueError):v.independent_cost(*args)
    def test_long_context_threshold_strict_and_anthropic_ttls(self):
        args=self.openai('default');args[0]['usage']['input_tokens']=1001
        total,parts,_=v.independent_cost(*args)
        self.assertEqual(parts['input'],Decimal('.002804'));self.assertEqual(parts['output'],Decimal('.0015'))
        gen={'usage':{'input_tokens':80,'output_tokens':10,'cache_read_input_tokens':10,'cache_creation_input_tokens':20,'cache_creation':{'ephemeral_5m_input_tokens':15,'ephemeral_1h_input_tokens':5}}}
        pricing={'rates_per_million':{'input':'1','output':'5','cache_read':'.1','cache_write':'1.25','cache_write_1h':'2'},'long_context':{'threshold_input_tokens':100,'input_multiplier':'5','output_multiplier':'5'}}
        total,parts,tier=v.independent_cost(gen,pricing,'anthropic')
        self.assertIsNone(tier);self.assertEqual(total,Decimal('.00079875'));self.assertEqual(parts['cache_write_1h'],Decimal('.00005'))
        gen['usage']['cache_creation']['ephemeral_1h_input_tokens']=4
        with self.assertRaises(ValueError):v.independent_cost(gen,pricing,'anthropic')
    @staticmethod
    def attempt(model,task,t0,wb=2,status='success'):
        return {'model':model,'task':task,'condition':'mathematical_compression','status':status,'metricStatus':'available','metrics':{'T0':{'numerator_decimal':str(t0),'denominator_decimal':'1'},'I0':{'numerator_decimal':str(wb),'denominator_decimal':'2'},'W_B':wb}}
    def test_exact_rank_survives_float_collapse_and_ties(self):
        huge=2**200
        data={'models':[{'id':'a'},{'id':'b'},{'id':'c'}],'tasks':['q1','q2'],'attempts':[self.attempt('a','q1',huge+1),self.attempt('b','q1',huge),self.attempt('c','q1',huge),self.attempt('a','q2',4),self.attempt('a','q2',9),self.attempt('b','q2',6),self.attempt('c','q2',5)]}
        result=v.rank_oracle(data,'T0','mathematical_compression',0);r={x['model']:x for x in result['rows']}
        self.assertEqual(r['a']['questions'],{'q1':1.,'q2':1.5});self.assertEqual(r['b']['questions'],{'q1':2.5,'q2':1.5});self.assertEqual(r['a']['exact_average_rank'],'5/4')
    def test_lower_proof_length_direction_and_coverage_intersection(self):
        data={'models':[{'id':'a'},{'id':'b'},{'id':'c'}],'tasks':['q1','q2'],'attempts':[self.attempt('a','q1',1,2),self.attempt('b','q1',1,3),self.attempt('a','q2',1,4),self.attempt('b','q2',1,3),self.attempt('c','q1',1,9,'elaboration_failure')]}
        self.assertEqual(v.rank_oracle(data,'W_B','mathematical_compression',0)['rows'],[])
        r=v.rank_oracle(data,'W_B','mathematical_compression',1)
        self.assertEqual(r['cohort'],['a','b']);self.assertEqual(r['tasks'],['q1','q2']);self.assertEqual([x['averageRank'] for x in r['rows']],[1.5,1.5])
    def test_cloud_open_snapshot_and_closed_display_are_distinct(self):
        ledger=json.dumps({'compute_estimate_usd':12.3,'compute_allocations_closed':False,'unknown_rate_vm_count':0}).encode()
        display={'sourceSha256':v.digest_bytes(ledger),'computeEstimatedUsd':12.3,'totalUsd':None,'computeComplete':False}
        self.assertFalse(v.validate_cloud_snapshot(ledger,display)['allocations_closed'])
        display['computeComplete']=True
        with self.assertRaises(ValueError):v.validate_cloud_snapshot(ledger,display)
        closed=json.dumps({'compute_estimate_usd':12.3,'compute_allocations_closed':True,'unknown_rate_vm_count':0}).encode();display.update(sourceSha256=v.digest_bytes(closed),computeComplete=False)
        self.assertTrue(v.validate_cloud_snapshot(closed,display)['postcleanup_display_amendment_needed'])
        display['totalUsd']=12.3
        with self.assertRaises(ValueError):v.validate_cloud_snapshot(closed,display)

if __name__=='__main__':unittest.main()
