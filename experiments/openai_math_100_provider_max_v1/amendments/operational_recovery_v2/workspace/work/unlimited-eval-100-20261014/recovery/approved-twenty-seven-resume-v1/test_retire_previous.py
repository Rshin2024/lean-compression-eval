import importlib.util
import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import watch_resume as watcher

SPEC=importlib.util.spec_from_file_location('retire_previous',Path(__file__).with_name('retire_previous_watcher.py'))
r=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(r)


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


class RetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        for key in ['HERE','WORK']:
            p=patch.object(r,key,self.root);p.start();self.addCleanup(p.stop)
        save(self.root/'old-activation.json',{'pid':68390});save(self.root/'old-plan.json',{})
        (self.root/'old-source.py').write_text('source')
        self.identity='68390 original /work/approved-eight-resume-v1/watch_resume.py --watch'
        old={'pid':68390,'identity':self.identity,'activation_path':'old-activation.json','activation_sha256':watcher.file_hash(self.root/'old-activation.json'),
             'plan_path':'old-plan.json','plan_sha256':watcher.file_hash(self.root/'old-plan.json'),'source_path':'old-source.py','source_sha256':watcher.file_hash(self.root/'old-source.py')}
        self.plan={'retirement_helper_sha256':watcher.file_hash(Path(r.__file__)),'superseded_watcher':old};save(self.root/'watch-plan.json',self.plan)
        self.sha=watcher.file_hash(self.root/'watch-plan.json')
        p=patch.object(watcher,'process_identity',return_value=self.identity);self.ps=p.start();self.addCleanup(p.stop)
        p=patch.object(r.os,'kill');self.kill=p.start();self.addCleanup(p.stop)

    def test_dry_run_never_signals(self):
        self.assertEqual(r.execute(self.sha,False)['state'],'reviewed_no_signals');self.kill.assert_not_called()

    def test_only_credential_free_watcher_is_signalled_once(self):
        self.ps.side_effect=[self.identity,self.identity,None]
        result=r.execute(self.sha,True)
        self.kill.assert_called_once_with(68390,signal.SIGTERM)
        self.assertEqual(result['provider_signals_sent'],0)
        self.assertEqual(r.execute(self.sha,True)['state'],'already_retired')

    def test_reused_pid_or_provider_pid_cannot_be_signalled(self):
        self.ps.return_value='different process'
        with self.assertRaises(ValueError):r.execute(self.sha,True)
        self.kill.assert_not_called()
        self.plan['superseded_watcher']['pid']=47090;save(self.root/'watch-plan.json',self.plan)
        with self.assertRaises(ValueError):r.execute(watcher.file_hash(self.root/'watch-plan.json'),True)
        self.kill.assert_not_called()

    def test_prior_resume_prevents_retirement(self):
        save(self.root/'resume_controller.json',{'ready':True})
        with self.assertRaises(ValueError):r.execute(self.sha,True)
        self.kill.assert_not_called()

    def test_already_exited_watcher_needs_no_signal(self):
        self.ps.return_value=None
        result=r.execute(self.sha,True)
        self.assertFalse(result['signal_sent']);self.kill.assert_not_called()

    def test_resume_publication_during_retirement_blocks_success_receipt(self):
        self.ps.side_effect=[self.identity,self.identity,None]
        self.kill.side_effect=lambda *args:save(self.root/'resume_controller.json',{'ready':True})
        with self.assertRaises(ValueError):r.execute(self.sha,True)
        self.assertFalse((self.root/'retirement-receipt.json').exists())
        race=json.loads((self.root/'retirement-race.json').read_text())
        self.assertTrue(race['pending_control_exists']);self.assertEqual(race['provider_signals_sent'],0)


class ProcessInspection(unittest.TestCase):
    def test_absence_is_distinct_from_ps_error(self):
        with patch.object(watcher.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='')):
            self.assertIsNone(watcher.process_identity(123))
        for result in [SimpleNamespace(returncode=1,stdout='',stderr='permission denied'),SimpleNamespace(returncode=0,stdout='',stderr='')]:
            with patch.object(watcher.subprocess,'run',return_value=result):
                with self.assertRaises(ValueError):watcher.process_identity(123)


if __name__=='__main__':unittest.main()
