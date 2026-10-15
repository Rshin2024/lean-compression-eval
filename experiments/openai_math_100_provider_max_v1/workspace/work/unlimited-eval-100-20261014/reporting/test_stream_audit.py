import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import audit_streams as a


class StreamAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.streaming=a.load_module('test_streaming_evidence_reader',a.WORK/'orchestration/streaming_adapter.py')
    def fixture(self,directory):
        aid='a'*64;body={'id':'response1','status':'completed','output':[]}
        encoded=a.canonical(body)
        raw=b'event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':body}).encode()+b'\n\n'
        path=directory/'stream-captures'/aid/'1.sse';path.parent.mkdir(parents=True);path.write_bytes(raw)
        capture=directory/'captures'/f'{aid}.jsonl';capture.parent.mkdir()
        capture.write_text(json.dumps({'request_id':'request1','body_base64':base64.b64encode(encoded).decode()})+'\n')
        sha=lambda b:hashlib.sha256(b).hexdigest()
        slot={'attempt_id':aid}
        gen={'generation_settings':{'provider':'openai'},'response_capture':{'path':str(capture.relative_to(directory)),'body_sha256':sha(encoded)}}
        descriptor={'path':str(path.relative_to(directory)),'provider':'openai','streaming':True,'complete':True,
            'sha256':sha(raw),'bytes':len(raw),'response_sha256':sha(encoded),'headers':{'X-Request-Id':'request1'},
            'started_at':1,'finished_at':2}
        events=[{**descriptor,'event':'stream_opened'},{**descriptor,'event':'stream_closed'}]
        return slot,gen,events,path
    def test_exact_terminal_chain(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);slot,gen,events,path=self.fixture(run)
            result=a.stream_binding(run,slot,gen,events,self.streaming)
            self.assertEqual(result['stream_sha256'],a.file_hash(path))
    def test_tampered_stream_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);slot,gen,events,path=self.fixture(run);path.write_bytes(path.read_bytes()+b'x')
            with self.assertRaises(ValueError):a.stream_binding(run,slot,gen,events,self.streaming)
    def test_repeated_stream_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);slot,gen,events,path=self.fixture(run);events.append(events[-1])
            with self.assertRaises(ValueError):a.stream_binding(run,slot,gen,events,self.streaming)
    def test_wrong_capture_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d);slot,gen,events,path=self.fixture(run);gen['response_capture']['body_sha256']='0'*64
            with self.assertRaises(ValueError):a.stream_binding(run,slot,gen,events,self.streaming)
    def test_anthropic_usage_is_cumulative(self):
        builder=self.streaming.AnthropicMessage()
        builder.event({'type':'message_start','message':{'id':'m','content':[],'usage':{'input_tokens':8,'output_tokens':1}}})
        builder.event({'type':'message_delta','delta':{'stop_reason':'end_turn'},'usage':{'output_tokens':10}})
        builder.event({'type':'message_delta','delta':{},'usage':{'output_tokens':12}})
        result=builder.event({'type':'message_stop'})
        self.assertEqual(result['usage'],{'input_tokens':8,'output_tokens':12})


if __name__=='__main__':unittest.main()
