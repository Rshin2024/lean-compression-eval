import copy
import json
from pathlib import Path
import tempfile
import unittest
from lean_compression.io import digest,file_hash
import overload_audit as a


class OverloadAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.work=Path(self.temp.name);self.run=self.work/'run';self.run.mkdir()
        self.root=self.work/'proposal';self.root.mkdir();self.aid='a'*64
        def save(path,value):path.write_text(json.dumps(value))
        self.save=save;save(self.run/'manifest.json',{});save(self.work/'launch_ready.json',{})
        helper=self.root/'helper.py';helper.write_text('"""fixture"""\n')
        self.oldstream=self.run/'old.sse';self.oldstream.write_bytes(b'original raw stream')
        (self.root/'original.sse').write_bytes(self.oldstream.read_bytes())
        prefix=[{'event':'request_started','attempt_id':self.aid},{'event':'outcome_unknown','attempt_id':self.aid}]
        streams=[{'event':'stream_opened','attempt_id':self.aid},{'event':'stream_closed','attempt_id':self.aid}]
        evidence={'transport_prefix':prefix,'attempt_id':self.aid,'run_directory':self.run.name,
            'manifest_sha256':file_hash(self.run/'manifest.json'),'marker':{},'marker_sha256':'b'*64,
            'stream_sha256':file_hash(self.oldstream),'stream_path':'old.sse','stream_events':streams,
            'stream_events_hash':digest(streams),'no_answer_text':True,'final_message_stop_present':False,
            'terminal_error':{'error':{'type':'overloaded_error'}},'provider_response_id':'original-id'}
        save(self.root/'evidence.json',evidence)
        proposal={'attempt_id':self.aid,'helper_sha256':file_hash(helper),
            'launch_plan_sha256':file_hash(self.work/'launch_ready.json'),'evidence_file':'evidence.json',
            'evidence_sha256':file_hash(self.root/'evidence.json'),'maximum_release_count':1,
            'original_charge_usd':None,'original_outcome':'unknown','kind':'one_use_terminal_overload_release_v1',
            'preserved_original_stream_file':'original.sse','authorization_source':'root operator scope'}
        save(self.root/'proposal.json',proposal)
        intent={'proposal_sha256':file_hash(self.root/'proposal.json'),'helper_sha256':file_hash(helper),
            'attempt_id':self.aid,'maximum_release_count':1,'original_charge_usd':None,'original_outcome':'unknown',
            'marker':{},'marker_sha256':'b'*64,'original_stream_sha256':file_hash(self.oldstream),
            'new_model_calls':0,'tools_executed':0}
        save(self.root/'release-intent.json',intent)
        save(self.root/'release-receipt.json',{**intent,'state':'released_once','original_marker_removed':True})
        self.events=prefix+[{'event':'authorized_unknown_release','attempt_id':self.aid,'reconciliation':intent},
                            {'event':'request_started','attempt_id':self.aid}]
        self.streams=streams+[{'event':'stream_opened','attempt_id':self.aid}]
        self.amendments=[{**intent,'resolution':'released_for_one_authorized_replacement_request'}]
        self.scope={'attempt':self.aid,'proposal':'proposal/proposal.json',
            'proposal_sha':file_hash(self.root/'proposal.json'),'helper':'proposal/helper.py','helper_sha':file_hash(helper)}
    def tearDown(self):self.temp.cleanup()
    def project(self):return a.project_one(self.work,self.run,self.events,self.streams,self.amendments,self.scope)
    def test_preserves_original_unknown_billing(self):
        events,streams,disclosure,files=self.project()
        self.assertEqual([e['event'] for e in events],['request_started'])
        self.assertIsNone(disclosure[0]['original_charge_usd'])
        self.assertEqual(disclosure[0]['authorization_source'],'root operator scope')
        self.assertIn(str(self.oldstream),files)
    def test_second_replacement_refused(self):
        self.events.append({'event':'request_started','attempt_id':self.aid})
        with self.assertRaisesRegex(ValueError,'More than one replacement'):self.project()
    def test_changed_failed_bytes_refused(self):
        self.oldstream.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'stream file changed'):self.project()
    def test_changed_proposal_refused(self):
        self.root.joinpath('proposal.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'proposal changed'):self.project()
    def test_missing_receipt_refused(self):
        self.root.joinpath('release-receipt.json').unlink()
        with self.assertRaises(FileNotFoundError):self.project()
    def test_twice_consumed_release_refused(self):
        self.events.append(copy.deepcopy(self.events[2]))
        with self.assertRaisesRegex(ValueError,'repeated unknown-call'):self.project()


if __name__=='__main__':unittest.main()
