import importlib.util
from pathlib import Path
import unittest
import numpy as np
spec=importlib.util.spec_from_file_location('analysis',Path(__file__).with_name('analyze_cross_run.py'));a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class CrossRunTests(unittest.TestCase):
    def test_completion_denominators_and_overlap(self):
        row={'status':'elaboration_failure','sourceLength':{'available':True,'truncated':False,'responseStatus':'completed','sourceKind':'submitted_text'}}
        self.assertEqual(a.completion(row),{'valid':0,'completed':1,'truncated':0,'empty':0,'outcome_category':'completed_rejected'})
        row['sourceLength'].update(available=False,truncated=True,responseStatus='truncated_output',sourceKind='partial_saved_text')
        self.assertEqual(a.completion(row)['empty'],1);self.assertEqual(a.completion(row)['truncated'],1)
        row['status']='success'
        with self.assertRaises(ValueError):a.completion(row)
    def test_missing_completion_is_not_zero(self):
        with self.assertRaises(ValueError):a.completion({'status':'elaboration_failure'})
    def test_duplicate_join_rejected(self):
        with self.assertRaises(ValueError):a.unique([{'id':1},{'id':1}],lambda x:x['id'])
    def test_provider_input_accounting_and_missing_reasoning(self):
        g={'generation_settings':{'provider':'openai','service_tier':'priority'},'provider_response':{'service_tier':'fast'},'usage':{'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':20,'cache_write_tokens':30},'output_tokens_details':{'reasoning_tokens':8}}}
        u=a.usage_fields(g);self.assertEqual(u['input_total_tokens'],100);self.assertEqual(u['total_billed_tokens'],110);self.assertEqual(u['uncached_input_tokens'],50);self.assertEqual(u['delivered_service_tier'],'priority')
        g={'generation_settings':{'provider':'anthropic','service_tier':'standard'},'usage':{'input_tokens':100,'output_tokens':10,'cache_read_input_tokens':20,'cache_creation_input_tokens':30,'cache_creation':{'ephemeral_5m_input_tokens':5,'ephemeral_1h_input_tokens':25}}}
        u=a.usage_fields(g);self.assertEqual(u['input_total_tokens'],150);self.assertEqual(u['total_billed_tokens'],160);self.assertIsNone(u['reasoning_tokens'])
    def test_missing_usage_counter_is_not_zero(self):
        g={'generation_settings':{'provider':'openai'},'usage':{'input_tokens':10,'output_tokens':3,'input_tokens_details':{'cached_tokens':0}}}
        with self.assertRaises(ValueError):a.usage_fields(g)
    def test_reproducible_joint_bootstrap(self):
        x,w=a.bootstrap_weights(100,17,42);y,z=a.bootstrap_weights(100,17,42)
        np.testing.assert_array_equal(x,y);np.testing.assert_array_equal(w,z);np.testing.assert_array_equal(w.sum(axis=1),np.repeat(100,17))
        for row,draw in zip(w,x):np.testing.assert_array_equal(row,np.bincount(draw,minlength=100))
    def test_unequal_family_blocks_preserve_question_weighting(self):
        weights=np.array([[1,1,1,1],[2,0,0,0],[0,2,2,2]])
        point,rep,support=a.ratio_estimate([1,0,0,0],[1,1,1,1],weights)
        self.assertEqual(point,.25);np.testing.assert_allclose(rep,[.25,1,0]);self.assertEqual(support,4)
    def test_joint_pairing_preserves_identical_run_difference(self):
        _,w=a.bootstrap_weights(4,100,7);before,bb,_=a.ratio_estimate([1,2,0,4],[4,4,4,4],w);after,ab,_=a.ratio_estimate([1,2,0,4],[4,4,4,4],w)
        self.assertEqual(before,after);np.testing.assert_array_equal(ab-bb,np.zeros(100))
    def test_undefined_bootstrap_denominator_not_zero(self):
        p,b,n=a.ratio_estimate([2,0],[4,0],np.array([[1,1],[0,2]]));self.assertEqual(p,.5);self.assertTrue(np.isnan(b[1]));self.assertEqual(a.ci(b,support=2)['bootstrap_valid_replicates'],1)
        self.assertIsNone(a.ci(b,support=1)['ci_low'])
    def test_exact_geometric_comparison_and_ties(self):
        old={'measured':2,'T0_numerator_product_decimal':'36','T0_denominator_product_decimal':'1'};new={'measured':1,'T0_numerator_product_decimal':'6','T0_denominator_product_decimal':'1'}
        self.assertEqual(a.exact_compare_cell(old,new,'T0'),0)
        old.update(measured=1,T0_numerator_product_decimal=str(2**200));new['T0_numerator_product_decimal']=str(2**200+1);self.assertEqual(a.exact_compare_cell(old,new,'T0'),1)

if __name__=='__main__':unittest.main()
