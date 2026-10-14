"""Offline watchdog/restart tests with synthetic logs; no live paths or calls."""
import copy
import fcntl
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest import mock

import watch_text_tool_stops as watchdog
import recover_text_tool_stops as recovery
from lean_compression.io import digest, evaluator_hash, file_hash
from test_recover_text_tool_stops import fixture, capture, save, log


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.work=root/'work';self.repo=root/'repo'
        self.work.mkdir();(self.repo/'lean').mkdir(parents=True)
        (self.repo/'lean/Evaluator.lean').write_text('-- synthetic test engine only\n')
        original,self.manifest=fixture(root/'fixture')
        self.run=self.repo/'runs/om100-claude-fixture-high';self.run.parent.mkdir(parents=True)
        shutil.move(original,self.run)
        self.manifest.pop('run_hash')
        self.manifest.update(evaluator_source_hash=evaluator_hash(),engine_hash=file_hash(self.repo/'lean/Evaluator.lean'))
        self.manifest['run_hash']=digest(self.manifest);save(self.run/'manifest.json',self.manifest)
        self.slot,self.response,self.cap,self.events,self.owner=capture(self.run,self.manifest)
        self.attempt=self.slot['attempt_id'];self.model='claude-fixture';self.shard=self.model+'/0'
        self.plan={'ready':True,'experiment':'openai_math_100_v1','source_hash':evaluator_hash(),
                   'engine_hash':self.manifest['engine_hash'],'generation_workers_per_model':2,'scheduled_attempts':4,
                   'runs':[{'model':self.model,'directory':self.run.name,'run_hash':self.manifest['run_hash'],
                            'manifest_sha256':file_hash(self.run/'manifest.json')}]}
        save(self.work/'launch_ready.json',self.plan)
        self.status={'state':'needs_attention','updated_at':time.time(),'blocked_shards':{}}
        self.block(self.shard,self.attempt,self.events)
        self.pins={'plan_sha256':file_hash(self.work/'launch_ready.json'),
                   'helper_sha256':file_hash(Path(recovery.__file__)),
                   'watchdog_sha256':file_hash(Path(watchdog.__file__))}

    def block(self,shard,attempt,events):
        index=shard.rsplit('/',1)[1];name=f'generation-logs/{self.model}-{index}.log'
        self.status['blocked_shards'][shard]={'at':recovery.timestamp(events[-1]['recorded_at']).timestamp()+1,
                                            'exit_code':1,'log':name}
        path=self.work/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('RuntimeError: '+watchdog.expected_error(attempt)+'\n')
        save(self.work/'status.json',self.status)

    def scan(self,apply=True,**changes):
        return watchdog.scan(self.work,self.repo,**{**self.pins,**changes},apply=apply)

    def test_narrow_recovery_and_atomic_handoff_with_no_calls(self):
        original={p:p.read_bytes() for p in [self.run/'manifest.json',self.run/'transport.jsonl',self.run/'captures'/f'{self.attempt}.jsonl']}
        with mock.patch('urllib.request.urlopen',side_effect=AssertionError('No API calls')):
            report=self.scan()
        self.assertEqual(report['resume_requested_shards'],[self.shard])
        handoff=json.loads((self.work/'resume_shards.json').read_text())
        self.assertGreater(handoff['issued_at'],self.status['blocked_shards'][self.shard]['at'])
        self.assertEqual(handoff['shards'],[self.shard]);self.assertEqual(handoff['new_model_calls'],0)
        self.assertEqual(handoff['helper_sha256'],self.pins['helper_sha256'])
        self.assertTrue(recovery.generation_path(self.run,self.attempt).exists())
        self.assertTrue(all(p.read_bytes()==data for p,data in original.items()))

    def test_dry_run_has_no_record_or_resume_effect(self):
        report=self.scan(False)
        self.assertEqual(report['actions'][0]['state'],'reviewed')
        self.assertFalse(recovery.generation_path(self.run,self.attempt).exists())
        self.assertTrue((self.run/'inflight'/f'{self.attempt}.json').exists())
        self.assertFalse((self.work/'resume_shards.json').exists())

    def test_idempotence_before_and_after_controller_consumes_handoff(self):
        self.scan();path=self.work/'resume_shards.json';before=path.read_bytes()
        gen=recovery.generation_path(self.run,self.attempt);gen_before=gen.read_bytes()
        self.assertEqual(self.scan()['handoff'],'already_dispatched');self.assertEqual(path.read_bytes(),before)
        path.rename(self.work/'resume_shards.applied-fixture.json')
        self.assertEqual(self.scan()['handoff'],'already_dispatched');self.assertFalse(path.exists())
        self.assertEqual(gen.read_bytes(),gen_before)

    def test_existing_root_resume_request_is_never_clobbered(self):
        path=self.work/'resume_shards.json';save(path,{'issued_at':time.time(),'shards':['other/0']});before=path.read_bytes()
        self.assertEqual(self.scan()['handoff'],'existing_resume_request_preserved')
        self.assertEqual(path.read_bytes(),before)

    def test_other_unknown_in_same_shard_holds_everything(self):
        capture(self.run,self.manifest,2)
        report=self.scan();self.assertTrue(report['held']);self.assertFalse(report['actions'])
        self.assertFalse((self.work/'resume_shards.json').exists())
        self.assertFalse(recovery.generation_path(self.run,self.attempt).exists())

    def test_other_unknown_shard_is_not_resumed(self):
        slot,response,cap,events,owner=capture(self.run,self.manifest,1)
        self.block(self.model+'/1',slot['attempt_id'],events)
        all_events=recovery.read_events(self.run/'transport.jsonl')[0];all_events[-1]['reason']='Different unknown'
        log(self.run/'transport.jsonl',all_events)
        report=self.scan();self.assertEqual(report['resume_requested_shards'],[self.shard])
        self.assertTrue((self.run/'inflight'/f"{slot['attempt_id']}.json").exists())
        self.assertFalse(recovery.generation_path(self.run,slot['attempt_id']).exists())

    def test_stale_status_and_pin_changes_hold_before_mutation(self):
        with self.assertRaisesRegex(ValueError,'hashes differ'):self.scan(helper_sha256='0'*64)
        self.status['updated_at']=time.time()-1000;save(self.work/'status.json',self.status)
        with self.assertRaisesRegex(ValueError,'stale'):self.scan()
        self.assertFalse(recovery.generation_path(self.run,self.attempt).exists())

    def test_partial_transport_read_and_live_locks_stay_held(self):
        path=self.run/'transport.jsonl';before=path.read_bytes();path.write_bytes(before+b'{partial')
        self.assertTrue(self.scan()['held']);self.assertFalse((self.work/'resume_shards.json').exists())
        path.write_bytes(before)
        with (self.run/'inflight'/f'{self.attempt}.json').open('r') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertTrue(self.scan()['held'])
        self.assertFalse(recovery.generation_path(self.run,self.attempt).exists())

    def test_changed_block_after_recovery_does_not_get_old_resume(self):
        updated=copy.deepcopy(self.status);updated['blocked_shards'][self.shard]['at']+=10
        with mock.patch.object(watchdog,'fresh_status',side_effect=[self.status,updated]):
            report=self.scan()
        self.assertFalse(report['resume_requested_shards']);self.assertFalse((self.work/'resume_shards.json').exists())
        self.assertTrue(recovery.generation_path(self.run,self.attempt).exists())

    def test_crash_between_recovery_and_handoff_resumes_without_rewriting(self):
        recovery.recover(self.run,self.attempt,True)
        path=recovery.generation_path(self.run,self.attempt);before=path.read_bytes()
        self.assertEqual(self.scan()['resume_requested_shards'],[self.shard]);self.assertEqual(path.read_bytes(),before)

    def test_crash_after_atomic_publication_does_not_duplicate_handoff(self):
        with mock.patch.object(watchdog,'append_event',side_effect=OSError('Synthetic journal interruption')):
            with self.assertRaises(OSError):self.scan()
        before=(self.work/'resume_shards.json').read_bytes()
        self.assertEqual(self.scan()['handoff'],'already_dispatched')
        self.assertEqual((self.work/'resume_shards.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
