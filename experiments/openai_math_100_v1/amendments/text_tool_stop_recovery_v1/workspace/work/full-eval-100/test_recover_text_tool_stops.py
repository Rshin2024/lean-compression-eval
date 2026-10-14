"""Offline, temporary-directory tests. Never reads or mutates a live run."""
import base64
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import recover_text_tool_stops as recovery
from lean_compression.io import digest, file_hash
from lean_compression.providers import response_payload


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def log(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows))


def fixture(root, count=4):
    run = root/'run'
    settings = {'provider':'anthropic', 'mode':'single_shot', 'thinking':{'type':'adaptive'},
                'reasoning_setting':'high', 'max_output_tokens':8192}
    slots = []
    for index in range(count):
        messages = [{'role':'system','content':'Proof only.'},{'role':'user','content':f'Task {index}'}]
        slots.append({'attempt_id':hashlib.sha256(f'fixture-{index}'.encode()).hexdigest(),
                      'model_snapshot':'claude-fixture', 'messages':messages,'prompt_hash':digest(messages)})
    manifest = {'config':{'generation':settings},'slots':slots,'model_snapshot':'claude-fixture'}
    manifest['run_hash'] = digest(manifest)
    save(run/'manifest.json',manifest)
    return run, manifest


def capture(run, manifest, index=0):
    slot = manifest['slots'][index]; attempt = slot['attempt_id']; clock = time.time()-30
    stamp = lambda offset:datetime.fromtimestamp(clock+offset,timezone.utc).isoformat()
    response = {'id':'msg_fixture','type':'message','role':'assistant','model':slot['model_snapshot'],
        'stop_reason':'tool_use','stop_sequence':None,
        'content':[{'type':'thinking','thinking':'fixture reasoning','signature':'fixture'},
                   {'type':'text','text':'  Leading prose\n<invoke name="never_execute">'},
                   {'type':'text','text':'\n  λ exact h  \n</invoke>\nTrailing prose  '}],
        'usage':{'input_tokens':5,'output_tokens':8,'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}
    body = json.dumps(response,ensure_ascii=False).encode();sha = hashlib.sha256(body).hexdigest()
    cap = {'attempt_id':attempt,'transport_index':0,'request_id':'request_fixture',
           'body_base64':base64.b64encode(body).decode(),'body_sha256':sha,'recorded_at':stamp(2)}
    where = {'path':f'captures/{attempt}.jsonl','body_sha256':sha}
    events = [{'attempt_id':attempt,'transport_index':0,'event':'request_started',
               'request_hash':digest(response_payload(slot,manifest['config']['generation'])),'recorded_at':stamp(1)},
              {'attempt_id':attempt,'transport_index':0,'event':'response_received','request_id':'request_fixture',
               'capture':where,'elapsed_seconds':1.25,'recorded_at':stamp(3)},
              {'attempt_id':attempt,'event':'outcome_unknown','capture':where,
               'reason':recovery.KNOWN_DIAGNOSTIC,'recorded_at':stamp(4)}]
    owner = {'attempt_id':attempt,'started_at':stamp(0)}
    save(run/'inflight'/f'{attempt}.json',owner)
    log(run/'captures'/f'{attempt}.jsonl',[cap])
    existing = recovery.read_events(run/'transport.jsonl')[0] if (run/'transport.jsonl').exists() else []
    log(run/'transport.jsonl',existing+events)
    return slot,response,cap,events,owner


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.run,self.manifest = fixture(Path(self.tmp.name))
        self.slot,self.response,self.cap,self.events,self.owner = capture(self.run,self.manifest)
        self.attempt = self.slot['attempt_id'];self.marker=self.run/'inflight'/f'{self.attempt}.json'

    def test_all_text_usage_and_provider_response_preserved_without_calls(self):
        before = file_hash(self.run/'captures'/f'{self.attempt}.jsonl')
        with mock.patch('urllib.request.urlopen',side_effect=AssertionError('No API calls')):
            result = recovery.recover(self.run,self.attempt,True)
        record=json.loads(recovery.generation_path(self.run,self.attempt).read_text())
        expected='\n'.join(x['text'] for x in self.response['content'] if x['type']=='text')
        self.assertEqual(record['raw_response'],expected)
        self.assertEqual(record['provider_response'],self.response)
        self.assertEqual(record['usage'],self.response['usage'])
        self.assertEqual(record['raw_response_hash'],digest(expected))
        self.assertEqual(record['capture_reconciliation']['transport_evidence_hash'],digest(self.events))
        self.assertEqual(record['model_tool_calls'],0)
        self.assertEqual(before,file_hash(self.run/'captures'/f'{self.attempt}.jsonl'))
        self.assertFalse(self.marker.exists());self.assertEqual(result['state'],'recorded')

    def test_dry_run_never_writes_or_releases(self):
        before={p.relative_to(self.run):p.read_bytes() for p in self.run.rglob('*') if p.is_file()}
        self.assertEqual(recovery.recover(self.run,self.attempt,False)['state'],'reviewed')
        self.assertEqual(before,{p.relative_to(self.run):p.read_bytes() for p in self.run.rglob('*') if p.is_file()})

    def test_immutable_idempotence_and_stale_marker_after_recording(self):
        recovery.recover(self.run,self.attempt,True)
        generation=recovery.generation_path(self.run,self.attempt);before=generation.read_bytes()
        amendments=(self.run/'amendments.jsonl').read_bytes()
        self.assertEqual(recovery.recover(self.run,self.attempt,True)['state'],'already_recorded')
        save(self.marker,self.owner)
        self.assertEqual(recovery.recover(self.run,self.attempt,True)['state'],'recorded_marker_released')
        self.assertEqual(generation.read_bytes(),before)
        self.assertEqual((self.run/'amendments.jsonl').read_bytes(),amendments)

    def test_corrupt_existing_record_is_not_silently_accepted(self):
        recovery.recover(self.run,self.attempt,True)
        p=recovery.generation_path(self.run,self.attempt);row=json.loads(p.read_text());row['raw_response']='changed'
        save(p,row);before=p.read_bytes()
        with self.assertRaises(ValueError):recovery.recover(self.run,self.attempt,True)
        self.assertEqual(p.read_bytes(),before)

    def test_live_lock_is_never_released(self):
        with self.marker.open('r') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'live worker'):recovery.recover(self.run,self.attempt,True)
        self.assertTrue(self.marker.exists())

    def test_structured_blocks_and_other_stops_are_rejected(self):
        variants=[]
        for stop in ['end_turn','max_tokens','pause_turn',None]:
            r=copy.deepcopy(self.response);r['stop_reason']=stop;variants.append(r)
        for kind in ['tool_use','server_tool_use','tool_result','unknown']:
            r=copy.deepcopy(self.response);r['content'].append({'type':kind,'name':'not_executed'});variants.append(r)
        for variant in variants:
            with self.subTest(variant=variant.get('stop_reason')),self.assertRaises(ValueError):
                recovery.recoverable_text(variant,self.slot['model_snapshot'])

    def test_capture_and_transport_binding_fail_closed(self):
        for field in ['hash','attempt','capture_index','request_index','response_index','request_id','request_hash',
                      'capture_path','unknown_reason','marker_attempt','marker_time','capture_time','duplicate_response']:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                run,m=fixture(Path(tmp));s,response,cap,events,owner=capture(run,m);attempt=s['attempt_id']
                if field=='hash':cap['body_sha256']='0'*64
                elif field=='attempt':cap['attempt_id']='1'*64
                elif field=='capture_index':cap['transport_index']=1
                elif field=='request_index':events[0]['transport_index']=True
                elif field=='response_index':events[1]['transport_index']=1
                elif field=='request_id':events[1]['request_id']='different'
                elif field=='request_hash':events[0]['request_hash']='0'*64
                elif field=='capture_path':events[1]['capture']['path']='captures/different.jsonl'
                elif field=='unknown_reason':events[2]['reason']='Other error'
                elif field=='marker_attempt':owner['attempt_id']='0'*64
                elif field=='marker_time':owner['started_at']='2099-01-01T00:00:00+00:00'
                elif field=='capture_time':cap['recorded_at']='2000-01-01T00:00:00+00:00'
                else:events.insert(2,copy.deepcopy(events[1]))
                log(run/'captures'/f'{attempt}.jsonl',[cap]);log(run/'transport.jsonl',events)
                save(run/'inflight'/f'{attempt}.json',owner)
                with self.assertRaises(ValueError):recovery.recover(run,attempt,True)
                self.assertTrue((run/'inflight'/f'{attempt}.json').exists())
                self.assertFalse(recovery.generation_path(run,attempt).exists())
                self.assertFalse((run/'amendments.jsonl').exists())


if __name__=='__main__':unittest.main()
