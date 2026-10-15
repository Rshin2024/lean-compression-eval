"""Offline byte-copy fixtures only: no real report amendment is activated."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.dont_write_bytecode=True
SOURCE=Path(__file__).with_name('close_cost_display.py')
spec=importlib.util.spec_from_file_location('close_cost_display',SOURCE);c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class ClosedCostTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.root=Path(self.temporary.name).resolve();self.suite=self.root/'suite';self.suite.mkdir();self.validation=self.root/'validation';self.validation.mkdir();self.lifecycle=self.root/'lifecycle';self.lifecycle.mkdir();self.tx=self.root/'application';self.script=self.root/'helper.py';self.script.write_bytes(SOURCE.read_bytes())
        self.old={'at_utc':'2026-10-15T03:00:00Z','resource_group':'lce-om100-unlimited-20261014','compute_estimate_usd':12,'compute_allocations_closed':False,'unknown_rate_vm_count':0,'excluded_unknown_items':['disks','taxes'],'vms':[]}
        for region in sorted(c.REGIONS):
            self.old['vms'].append({'region':region,'resource_id':'/resource/'+region,'size':'same-sku','allocation_started_at_utc':'2026-10-15T02:00:00Z','observation_at_utc':'2026-10-15T03:00:00Z','allocation_end_at_utc':None,'allocation_state':'running_at_observation','currency':'USD','rate_source':'https://official.example/'+region,'rate_effective_start':'2026-01-01T00:00:00Z','compute_hourly_usd':2,'compute_estimate_usd':2,'allocation_hours':1})
        self.closed=copy.deepcopy(self.old);self.closed.update(at_utc='2026-10-15T04:01:00Z',compute_estimate_usd=24,compute_allocations_closed=True)
        for row in self.closed['vms']:
            row.update(allocation_end_at_utc='2026-10-15T04:00:00Z',allocation_state='closed_deallocated',allocation_hours=2,compute_estimate_usd=4)
            (self.lifecycle/(row['region']+'-deallocated.json')).write_bytes(c.raw_json({'resource_id':row['resource_id'],'power_state':'PowerState/deallocated','deallocated_observed_at_utc':row['allocation_end_at_utc']}))
        self.baseline=self.root/'baseline.json';self.baseline.write_bytes(c.raw_json(self.old));self.closed_path=self.root/'closed.json';self.closed_path.write_bytes(c.raw_json(self.closed))
        self.data={'classification':'observed','attempts':[{'id':'frozen','metric':17}],'cloudCosts':{'sourceSha256':c.sha(self.baseline.read_bytes()),'computeEstimatedUsd':'12','computeComplete':False,'totalUsd':None,'currency':'USD'}}
        (self.suite/'comparison-data.json').write_bytes(c.raw_json(self.data));self.before_index=('<html><header>'+c.OLD_LINK+'</header>'+c.OPEN+json.dumps(self.data)+'</script><footer>original</footer></html>').encode();(self.suite/'index.html').write_bytes(self.before_index)
        (self.suite/'questions.html').write_bytes(b'actual prompts unchanged');(self.suite/'report.json').write_bytes(b'raw immutable report')
        base={'index_sha256':c.file_sha(self.suite/'index.html'),'data_sha256':c.file_sha(self.suite/'comparison-data.json')}
        (self.suite/'suite-build-receipt.json').write_bytes(c.raw_json(base));self.finalizer=self.root/'finalizer.json';self.finalizer.write_bytes(c.raw_json({'state':'complete',**base}))
        guards={str(p):c.file_sha(p) for p in self.suite.iterdir()}
        (self.validation/'summary.json').write_bytes(c.raw_json({'status':'pass_data_validation','attempts':9600,'priced_responses':9600}));(self.validation/'input-file-hashes.json').write_bytes(c.raw_json(guards));(self.validation/'finalizer-status.json').write_bytes(self.finalizer.read_bytes());(self.validation/'original-suite-build-receipt.json').write_bytes((self.suite/'suite-build-receipt.json').read_bytes())
        self.pins={p.name:c.file_sha(p) for p in self.validation.iterdir()}
        self.original={str(p):p.read_bytes() for p in [*self.suite.iterdir(),self.finalizer]}
    def tearDown(self):self.temporary.cleanup()
    def prepare(self):return c.prepare_transaction(self.tx,suite=self.suite,validation=self.validation,finalizer_path=self.finalizer,baseline_ledger=self.baseline,closed_ledger=self.closed_path,lifecycle_dir=self.lifecycle,validation_pins=self.pins,script_path=self.script)
    def apply(self,**extra):return c.apply_transaction(self.tx,script_path=self.script,validation_pins=self.pins,expected_suite=self.suite,**extra)
    def write_closed(self):self.closed_path.write_bytes(c.raw_json(self.closed))
    def test_prepare_is_nonactivating_and_snapshots_are_byte_copies(self):
        intent=self.prepare();self.assertEqual(intent['state'],'prepared_not_applied')
        for path,raw in self.original.items():self.assertEqual(Path(path).read_bytes(),raw)
        self.assertEqual((self.tx/'before-index.html').read_bytes(),self.before_index)
        self.assertTrue(all(p.stat().st_nlink==1 for p in self.tx.iterdir()))
        self.assertFalse((self.tx/'receipt.json').exists())
    def test_apply_changes_cloud_only_and_preserves_original_receipts(self):
        self.prepare();receipt=self.apply();self.assertEqual(receipt['state'],'applied_verified')
        data=c.parsed((self.suite/'comparison-data.json').read_bytes());self.assertEqual(data['attempts'],self.data['attempts']);self.assertEqual(data['classification'],'observed')
        self.assertTrue(data['cloudCosts']['computeComplete']);self.assertEqual(data['cloudCosts']['computeEstimatedUsd'],'24');self.assertIsNone(data['cloudCosts']['totalUsd']);self.assertFalse(data['cloudCosts']['closureEvidence']['invoiceTotalKnown'])
        text=(self.suite/'index.html').read_text();self.assertIn(c.NEW_LINK,text);self.assertIn(c.NOTE,text)
        for path,raw in self.original.items():
            if Path(path).name not in ('comparison-data.json','index.html'):self.assertEqual(Path(path).read_bytes(),raw)
    def test_reapply_is_idempotent_and_does_not_rewrite_outputs(self):
        self.prepare();first=self.apply();paths=[self.suite/'comparison-data.json',self.suite/'index.html',self.tx/'receipt.json'];states=[(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_ino) for p in paths]
        self.assertEqual(self.apply(),first);self.assertEqual(states,[(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_ino) for p in paths])
    def test_crash_after_first_atomic_file_keeps_intent_and_resumes(self):
        self.prepare()
        def crash(index):raise RuntimeError('injected interruption')
        with self.assertRaisesRegex(RuntimeError,'injected'):self.apply(after_write=crash)
        self.assertTrue((self.tx/'intent.json').exists());self.assertFalse((self.tx/'receipt.json').exists());self.assertEqual((self.suite/'index.html').read_bytes(),self.before_index);self.assertTrue(list(self.tx.glob('failure-*.json')))
        self.assertEqual(self.apply()['state'],'applied_verified')
    def test_missing_lifecycle_receipt_refuses_before_preparation(self):
        next(self.lifecycle.iterdir()).unlink()
        with self.assertRaises(FileNotFoundError):self.prepare()
        self.assertFalse(self.tx.exists())
    def test_open_or_unknown_ledger_refuses(self):
        for key,value in [('compute_allocations_closed',False),('unknown_rate_vm_count',1)]:
            bad=copy.deepcopy(self.closed);bad[key]=value
            with self.assertRaises(ValueError):c.validate_ledgers(self.old,bad,{r:c.parsed((self.lifecycle/(r+'-deallocated.json')).read_bytes()) for r in c.REGIONS})
    def test_changed_rate_wrong_vm_and_arithmetic_refuse(self):
        for key,value in [('compute_hourly_usd',3),('resource_id','another-vm'),('allocation_hours',5),('compute_estimate_usd',1),('allocation_end_at_utc','2026-10-15T02:59:00Z')]:
            bad=copy.deepcopy(self.closed);bad['vms'][0][key]=value
            with self.assertRaises(ValueError):c.validate_ledgers(self.old,bad,{r:c.parsed((self.lifecycle/(r+'-deallocated.json')).read_bytes()) for r in c.REGIONS})
    def test_unknown_invoice_items_must_not_disappear(self):
        self.closed['excluded_unknown_items']=[];self.write_closed()
        with self.assertRaisesRegex(ValueError,'Unmeasured'):self.prepare()
    def test_target_tampering_never_gets_overwritten(self):
        self.prepare();target=self.suite/'comparison-data.json';target.write_bytes(b'unexpected bytes')
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(target.read_bytes(),b'unexpected bytes');self.assertFalse((self.tx/'receipt.json').exists())
    def test_noncloud_input_change_stops_application(self):
        self.prepare();p=self.suite/'questions.html';p.write_bytes(b'modified prompt')
        with self.assertRaisesRegex(ValueError,'Guarded input changed'):self.apply()
        self.assertEqual((self.suite/'index.html').read_bytes(),self.before_index)
    def test_intent_cannot_drop_original_report_guard(self):
        self.prepare();p=self.tx/'intent.json';intent=c.parsed(p.read_bytes());intent['guard_file_hashes'].pop(str(self.suite/'report.json'));p.write_bytes(c.raw_json(intent))
        with self.assertRaisesRegex(ValueError,'dropped or changed'):self.apply()
    def test_rehashed_after_snapshot_cannot_change_measurements(self):
        self.prepare();p=self.tx/'after-comparison-data.json';data=c.parsed(p.read_bytes());data['attempts'][0]['metric']=999;p.write_bytes(c.raw_json(data));i=self.tx/'intent.json';intent=c.parsed(i.read_bytes());intent['snapshots'][p.name]=c.file_sha(p);intent['targets'][0]['after_sha256']=c.file_sha(p);i.write_bytes(c.raw_json(intent))
        with self.assertRaisesRegex(ValueError,'unapproved derived'):self.apply()
    def test_crash_after_receipt_fsync_resumes_exact_staged_receipt(self):
        self.prepare();real_replace=c.os.replace
        def crash(source,destination):
            if Path(destination).name=='receipt.json':raise RuntimeError('receipt rename interrupted')
            return real_replace(source,destination)
        with patch.object(c.os,'replace',side_effect=crash):
            with self.assertRaisesRegex(RuntimeError,'receipt rename'):self.apply()
        staged=self.tx/'.receipt.json.publishing';saved=staged.read_bytes();self.assertFalse((self.tx/'receipt.json').exists())
        receipt=self.apply();self.assertEqual((self.tx/'receipt.json').read_bytes(),saved);self.assertEqual(receipt,c.parsed(saved));self.assertFalse(staged.exists())
    def test_existing_receipt_tampering_is_not_silently_accepted(self):
        self.prepare();self.apply();p=self.tx/'receipt.json';value=c.parsed(p.read_bytes());value['data_sha256']='invented';p.write_bytes(c.raw_json(value))
        with self.assertRaisesRegex(ValueError,'receipt facts'):self.apply()
    def test_intent_change_during_apply_fails_without_a_success_receipt(self):
        self.prepare()
        def change(index):
            if index==0:
                p=self.tx/'intent.json';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'Intent/snapshot changed'):self.apply(after_write=change)
        self.assertFalse((self.tx/'receipt.json').exists())
    def test_helper_source_change_refuses(self):
        self.prepare();self.script.write_bytes(self.script.read_bytes()+b'\n# changed\n')
        with self.assertRaisesRegex(ValueError,'helper changed'):self.apply()
    def test_duplicate_vm_or_receipt_identity_refuses(self):
        lifecycle={r:c.parsed((self.lifecycle/(r+'-deallocated.json')).read_bytes()) for r in c.REGIONS};region=next(iter(lifecycle));lifecycle[region]['resource_id']='other'
        with self.assertRaisesRegex(ValueError,'deallocation receipt'):c.validate_ledgers(self.old,self.closed,lifecycle)
        bad=copy.deepcopy(self.closed);bad['vms'][-1]['region']=bad['vms'][0]['region']
        with self.assertRaisesRegex(ValueError,'six original'):c.validate_ledgers(self.old,bad,{r:c.parsed((self.lifecycle/(r+'-deallocated.json')).read_bytes()) for r in c.REGIONS})

if __name__=='__main__':unittest.main()
