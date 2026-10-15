"""Small offline fixtures test the operational gates, not the frozen verifier."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("resume_watcher", Path(__file__).with_name("watch_resume.py"))
w = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(w)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'work';self.here=self.work/'recovery/watcher';self.here.mkdir(parents=True)
        for key,value in [('WORK',self.work),('HERE',self.here)]:
            p=patch.object(w,key,value);p.start();self.addCleanup(p.stop)
        p=patch.object(w,'source_and_receipt_checks',return_value={});p.start();self.addCleanup(p.stop)
        p=patch.object(w,'verified_generation',side_effect=lambda record,slot:record);p.start();self.addCleanup(p.stop)
        p=patch.object(w.os,'kill',return_value=None);p.start();self.addCleanup(p.stop)
        self.run=self.root/'run';self.run.mkdir()
        ids=sorted(w.APPROVED)+['a'*64,'b'*64];slots={a:(self.run,{'attempt_id':a}) for a in ids}
        manifest={'slots':[s for _,s in slots.values()]};save(self.run/'manifest.json',manifest)
        for a in ids[-2:]:save(self.run/'generations'/f'{a}.json',{'attempt_id':a})
        self.plan={'credential_parent_pid':10,'original_generation_child_pid':11,'launch_plan_sha256':'launch',
                   'releases':[{'attempt_id':a,'proposal_sha256':'proposal'} for a in sorted(w.APPROVED)]}
        self.context={'watch_plan':self.plan,'plan_sha256':'watchplan','slots':slots,'runs':{self.run:(w.stamp(self.run/'manifest.json'),manifest)},'known':{},
                      'baseline':{a:w.file_hash(self.run/'generations'/f'{a}.json') for a in ids[-2:]}}
        self.parent={'parent_pid':10,'child_pid':11,'state':'holding_credentials_for_explicit_resume','failure_at':time.time()-10}
        save(self.work/'generation-parent-status.json',self.parent)
        save(self.work/'generation-status.json',{'pid':11,'active_workers':0})
        rows=[]
        for a in sorted(w.APPROVED):rows += [{'attempt_id':a,'event':'request_started'},{'attempt_id':a,'event':'outcome_unknown'},
            {'attempt_id':a,'event':'authorized_unknown_release','reconciliation':{'proposal_sha256':'proposal'}}]
        (self.run/'transport.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def test_waits_for_natural_child_exit(self):
        save(self.work/'generation-parent-status.json',{**self.parent,'state':'child_running'})
        self.assertFalse(w.readiness(self.context)['ready'])
        self.assertFalse((self.work/'resume_controller.json').exists())

    def test_exact_missing_set_and_no_claims_gate(self):
        self.assertTrue(w.readiness(self.context)['ready'])
        marker=self.run/'inflight'/('a'*64+'.json');save(marker,{})
        self.assertFalse(w.readiness(self.context)['ready']);marker.unlink()
        (self.run/'generations'/('a'*64+'.json')).unlink()
        self.assertFalse(w.readiness(self.context)['ready'])

    def test_changed_prior_generation_refuses(self):
        save(self.run/'generations'/('a'*64+'.json'),{'changed':True})
        with self.assertRaises(ValueError):w.readiness(self.context)

    def test_one_publication_only_and_fresh_nonce(self):
        ready=w.readiness(self.context)
        self.assertEqual(w.publish_once(self.context,ready,False)['state'],'ready_for_one_resume')
        self.assertFalse((self.here/'one-use-intent.json').exists())
        with patch('urllib.request.urlopen',side_effect=AssertionError('No API calls')):
            result=w.publish_once(self.context,ready,True)
        self.assertEqual(result['state'],'published_once')
        control=json.loads((self.work/'resume_controller.json').read_text())
        self.assertGreater(control['issued_at'],self.parent['failure_at']);self.assertGreaterEqual(len(control['nonce']),16)
        self.assertEqual(w.file_hash(self.work/'resume_controller.json'),result['control_sha256'])
        self.assertEqual(w.publish_once(self.context,ready,True)['state'],'one_use_authorization_already_consumed')

    def test_existing_control_and_previously_consumed_control_block(self):
        save(self.work/'resume_controller.json',{'someone_else':'control'})
        self.assertFalse(w.readiness(self.context)['ready'])
        (self.work/'resume_controller.json').unlink();save(self.work/'resume_controller.applied-1.json',{})
        with self.assertRaises(ValueError):w.readiness(self.context)

    def test_replacement_child_or_extra_attempt_request_block(self):
        save(self.work/'generation-parent-status.json',{**self.parent,'child_pid':12})
        with self.assertRaises(ValueError):w.readiness(self.context)
        save(self.work/'generation-parent-status.json',self.parent)
        with (self.run/'transport.jsonl').open('a') as out:out.write(json.dumps({'attempt_id':next(iter(w.APPROVED)),'event':'request_started'})+'\n')
        with self.assertRaises(ValueError):w.readiness(self.context)

    def test_exclusive_publication_preserves_existing_control(self):
        target=self.work/'control.json';save(target,{'original':True});before=target.read_bytes()
        with self.assertRaises(FileExistsError):w.exclusive_json(target,{'replacement':True})
        self.assertEqual(target.read_bytes(),before)

    def test_changed_bytes_during_final_full_rehash_prevent_publication(self):
        ready=w.readiness(self.context)
        save(self.run/'generations'/('a'*64+'.json'),{'changed':True})
        with patch.object(w,'readiness',return_value=ready):
            with self.assertRaises(ValueError):w.publish_once(self.context,ready,True)
        self.assertFalse((self.here/'one-use-intent.json').exists())


if __name__=='__main__':unittest.main()
