"""Unknown request charges remain separate from frozen response token estimates."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import build_comparison as comparison
from test_comparison import fixture


class TransportCostDisclosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model = comparison.MODELS[0]
        self.incident = {'model': self.model, 'task_id': 'OM100-001', 'condition': 'standard', 'sample': 1,
            'attempt_id': 'a'*64, 'kind': 'disconnected_response', 'http_status': None,
            'recorded_at_utc': '2026-10-14T09:00:00+00:00', 'usage_available': False,
            'unknown_charge_usd': None, 'source_run_hash': 'b'*64, 'source_manifest_sha256': 'c'*64,
            'transport_events_sha256': 'd'*64}
        self.disclosure = {'schema_version': 1, 'kind': 'unknown_transport_cost_disclosure',
            'as_of_utc': '2026-10-14T10:00:00+00:00', 'scope': 'known_incidents_not_exhaustive',
            'invoice_reconciled': False, 'all_request_total_usd': None, 'incidents': [self.incident],
            'evidence': [{'kind': 'disconnect_diagnostic', 'sha256': 'e'*64}]}
        self.data = {'models': [{'id': self.model, 'phaseProvenance': {'generation': {
            'run_hash': 'b'*64, 'manifest_sha256': 'c'*64}}}],
            'sources': [{'model': self.model}], 'attempts': [{'model': self.model, 'task': 'OM100-001',
            'condition': 'standard', 'sample': 1, 'attemptId': 'a'*64}]}

    def read(self, disclosure=None):
        path = self.root/'disclosure.json'
        path.write_text(json.dumps(self.disclosure if disclosure is None else disclosure))
        return comparison.read_transport_cost_disclosure(path, self.data)

    def test_hashed_allowlist_retains_unknown_charge_not_private_content(self):
        self.disclosure['private_source'] = 'PRIVATE PROOF'
        self.incident['raw_response'] = 'PRIVATE RESPONSE'
        result = self.read()
        self.assertEqual(result['sourceSha256'], hashlib.sha256((self.root/'disclosure.json').read_bytes()).hexdigest())
        self.assertIsNone(result['allRequestTotalUsd'])
        self.assertIsNone(result['incidents'][0]['unknown_charge_usd'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertIsNone(comparison.read_transport_cost_disclosure(None, self.data))

    def test_mismatched_provenance_and_duplicate_incidents_fail_closed(self):
        for key, value in [('attempt_id', 'f'*64), ('source_run_hash', 'f'*64),
                           ('source_manifest_sha256', 'f'*64), ('sample', 0), ('task_id', 'OM100-999')]:
            with self.subTest(key=key):
                changed=copy.deepcopy(self.disclosure);changed['incidents'][0][key]=value
                with self.assertRaises(ValueError):self.read(changed)
        changed=copy.deepcopy(self.disclosure);changed['incidents']*=2
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.read(changed)

    def test_zero_billing_or_invoice_completeness_cannot_be_inferred(self):
        for key,value in [('all_request_total_usd','0'),('invoice_reconciled',True)]:
            changed=copy.deepcopy(self.disclosure);changed[key]=value
            with self.assertRaises(ValueError):self.read(changed)
        for key,value in [('unknown_charge_usd','0'),('usage_available',True)]:
            changed=copy.deepcopy(self.disclosure);changed['incidents'][0][key]=value
            with self.assertRaises(ValueError):self.read(changed)

    def test_transport_counts_only_no_raw_logs_and_boolean_count_rejected(self):
        value=comparison.transport_coverage_view({'outcome_unknown_events':6,'http_error_events':1,
            'invoice_reconciled':False,'raw_log':'PRIVATE'})
        self.assertEqual(value['outcome_unknown_events'],6)
        self.assertNotIn('PRIVATE',json.dumps(value))
        with self.assertRaises(ValueError):comparison.transport_coverage_view({'http_error_events':True})

    def test_complete_response_costs_never_make_all_request_total_known_in_browser(self):
        text=Path(__file__).with_name('comparison.html').read_text()
        money='function sumMoney('+text.split('function sumMoney(',1)[1].split('function dollars(',1)[0]
        summary='function costSummary('+text.split('function costSummary(',1)[1].split('function costs(',1)[0]
        code="const assert=require('node:assert/strict');\n"+money+'\n'+summary+'''
const complete=costSummary([{cost:{generated:true,status:'estimated',total_usd:'0.1'}},
                           {cost:{generated:true,status:'estimated',total_usd:'0.2'}}]);
assert.equal(complete.recordedTotal,'0.3');
assert.equal(complete.allRequestTotal,null);assert.equal(complete.invoiceTotal,null);
const missing=costSummary([{cost:{generated:false,status:'unavailable',total_usd:null}}]);
assert.equal(missing.subtotal,null);assert.equal(missing.recordedTotal,null);
'''
        subprocess.run(['node','-e',code],check=True,capture_output=True,text=True)
        self.assertNotIn('Complete total for this view',text)
        self.assertIn('All-request / invoice total: unavailable.',text)
        self.assertIn('Unknown incident charges',text)

    def test_comparison_consumes_disclosure_without_modifying_model_reports(self):
        suite=self.root/'suite';fixture(suite,count=1)
        report=suite/('report-'+self.model)
        p=report/'analysis.json';value=json.loads(p.read_text())
        value['run_provenance']={'run_hash':'b'*64};p.write_text(json.dumps(value))
        p=report/'all_scheduled_attempts.json';value=json.loads(p.read_text())
        value[0]['attempt_id']='a'*64;p.write_text(json.dumps(value))
        p=report/'costs.json';value=json.loads(p.read_text())
        value['transport_coverage']={'http_error_events':1,'outcome_unknown_events':6,'invoice_reconciled':False}
        p.write_text(json.dumps(value))
        disclosure=self.root/'disclosure.json';disclosure.write_text(json.dumps(self.disclosure))
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in suite.rglob('*') if p.is_file()}
        data=comparison.build(suite,output=self.root/'output',classification='synthetic_test_fixture',
                              transport_cost_disclosure=disclosure)
        self.assertEqual(data['transportCostDisclosure']['incidents'][0]['attempt_id'],'a'*64)
        self.assertEqual(data['models'][0]['costs']['transportCoverage']['outcome_unknown_events'],6)
        self.assertIsNone(data['costScope']['allRequestTotalUsd'])
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in suite.rglob('*') if p.is_file()})

    def test_browser_disclosure_filters_and_safe_missing_disclosure(self):
        text=Path(__file__).with_name('comparison.html').read_text()
        functions='function sumMoney('+text.split('function sumMoney(',1)[1].split('function attempts(',1)[0]
        script="const assert=require('node:assert/strict');\n"+'''
const nodes={},document={querySelector:id=>nodes[id]??={value:'all',innerHTML:''}};
const NAME=x=>x,esc=x=>String(x),finite=Number.isFinite,svgFrame=()=>'',table=(h,r)=>JSON.stringify([h,r]);
let chosen=new Set(['a','b']),costExport;
const DATA={attempts:['a','b'].map(model=>({model,task:'q1',condition:'standard',sample:1,
    cost:{generated:true,status:'estimated',total_usd:'0.1'}})),transportCostDisclosure:{asOf:'2026-10-14',
    sourceSha256:'hash',incidents:[...Array.from({length:6},()=>({model:'a',task_id:'q1',condition:'standard',kind:'disconnected_response'})),
    {model:'b',task_id:'q2',condition:'source_concision',kind:'http_error',http_status:520}]}};
const models=[{id:'a'},{id:'b'}];
'''+functions+r'''
costs(models);assert.match(nodes['#transportCosts'].innerHTML,/<b>7<\/b>/);
assert.match(nodes['#transportCosts'].innerHTML,/HTTP 520/);
chosen=new Set(['a']);costs([models[0]]);assert.match(nodes['#transportCosts'].innerHTML,/<b>6<\/b>/);
nodes['#costTask'].value='q2';costs([models[0]]);
assert.match(nodes['#transportCosts'].innerHTML,/does not establish complete billing/);
DATA.transportCostDisclosure=null;costs([models[0]]);
assert.match(nodes['#transportCosts'].innerHTML,/Missing disclosure does not mean zero/);
assert.match(nodes['#costCoverage'].innerHTML,/invoice total: unavailable/);
'''
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


if __name__=='__main__':unittest.main()
