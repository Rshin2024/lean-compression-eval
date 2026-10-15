import copy
import fcntl
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("overload_release", Path(__file__).with_name("release_exact_overload.py"))
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def log(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(v)+"\n" for v in values))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name);self.work=self.base/'work';self.repo=self.base/'repo'
        for name,value in [('WORK',self.work),('REPO',self.repo)]:
            p=patch.object(h,name,value);p.start();self.addCleanup(p.stop)
        self.run=self.repo/'runs'/'om100-max-claude-fable-5-1-high-20261014'
        settings={'provider':'anthropic','mode':'single_shot','thinking':{'type':'adaptive'},'reasoning_setting':'high','max_output_tokens':128000}
        slot={'attempt_id':h.ATTEMPT,'model_snapshot':h.MODEL,'messages':[{'role':'system','content':'proof'},{'role':'user','content':'goal'}]}
        m={'model_snapshot':h.MODEL,'slots':[slot],'config':{'generation':settings}};m['run_hash']=h.digest(m)
        save(self.run/'manifest.json',m)
        save(self.work/'launch_ready.json',{'ready':True,'experiment':'new'})
        self.marker=self.run/'inflight'/f'{h.ATTEMPT}.json'
        save(self.marker,{'attempt_id':h.ATTEMPT,'started_at':'2026-10-15T00:00:00+00:00'})
        log(self.run/'transport.jsonl',[{'attempt_id':h.ATTEMPT,'event':'request_started','transport_index':0,'request_hash':h.digest(h.streaming_payload(slot,settings))},
            {'attempt_id':h.ATTEMPT,'event':'outcome_unknown','transport_index':0,'message':'Provider emitted an SSE error; possibly billed outcome retained'}])
        self.messages=[{'type':'message_start','message':{'id':'msg_fixture','model':h.MODEL,'content':[]}},
            {'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':''}},
            {'type':'ping'},{'type':'error','error':{'type':'overloaded_error','message':'Overloaded'}}]
        self.set_stream()
        self.dir=self.work/'proposal';self.dir.mkdir(parents=True)
        evidence=h.inspect(self.run);save(self.dir/'evidence.json',evidence)
        self.proposal={'kind':h.KIND,'attempt_id':h.ATTEMPT,'helper_sha256':h.file_hash(Path(h.__file__)),
            'maximum_release_count':1,'reason':h.REASON,'launch_plan_sha256':h.file_hash(self.work/'launch_ready.json'),
            'evidence_file':'evidence.json','evidence_sha256':h.file_hash(self.dir/'evidence.json')}
        self.path=self.dir/'proposal.json';save(self.path,self.proposal);self.sha=h.file_hash(self.path)

    def set_stream(self):
        data=b''.join(b'event: '+e['type'].encode()+b'\ndata: '+json.dumps(e).encode()+b'\n\n' for e in self.messages)
        path=f'stream-captures/{h.ATTEMPT}/123.sse';p=self.run/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        opened={'attempt_id':h.ATTEMPT,'event':'stream_opened','path':path}
        closed={**opened,'event':'stream_closed','complete':False,'bytes':len(data),'sha256':h.file_hash(p)}
        log(self.run/'stream-transport.jsonl',[opened,closed])

    def test_dry_run_does_not_release_or_write(self):
        before={p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertEqual(h.execute(self.path,self.sha)['state'],'reviewed')
        self.assertEqual(before,{p:p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_one_release_preserves_unknown_charge_and_never_calls_provider(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('No requests')):
            result=h.execute(self.path,self.sha,True)
        self.assertFalse(self.marker.exists());self.assertEqual(result['state'],'released_once')
        self.assertIsNone(result['receipt']['original_charge_usd'])
        self.assertEqual(h.execute(self.path,self.sha,True)['state'],'authorization_already_consumed')
        save(self.marker,{'attempt_id':h.ATTEMPT,'started_at':'replacement'})
        h.execute(self.path,self.sha,True)
        self.assertTrue(self.marker.exists())
        events=h.read_events(self.run/'transport.jsonl')[0]
        self.assertEqual([e['event'] for e in events],['request_started','outcome_unknown','authorized_unknown_release'])

    def test_live_request_lock_blocks_release(self):
        with self.marker.open() as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(ValueError):h.execute(self.path,self.sha,True)
        self.assertTrue(self.marker.exists())

    def test_saved_response_is_never_retried(self):
        save(self.run/'generations'/f'{h.ATTEMPT}.json',{})
        with self.assertRaises(ValueError):h.execute(self.path,self.sha,True)
        self.assertTrue(self.marker.exists())

    def test_output_text_or_other_error_not_eligible(self):
        original=copy.deepcopy(self.messages)
        for change in ['text','other_error','terminal']:
            self.messages=copy.deepcopy(original)
            if change=='text':self.messages[1]['content_block']={'type':'text','text':'some answer'}
            elif change=='other_error':self.messages[-1]['error']['type']='other_error'
            else:self.messages.insert(-1,{'type':'message_stop'})
            self.set_stream()
            with self.assertRaises(ValueError):h.inspect(self.run)

    def test_changed_claim_or_proposal_refuses_release(self):
        save(self.marker,{'attempt_id':h.ATTEMPT,'started_at':'different'})
        with self.assertRaises(ValueError):h.execute(self.path,self.sha,True)
        self.assertTrue(self.marker.exists())
        with self.assertRaises(ValueError):h.execute(self.path,'0'*64,True)


if __name__=='__main__':unittest.main()
