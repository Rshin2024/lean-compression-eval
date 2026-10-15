import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cloud_report_job_v2 as job


class StatusTests(unittest.TestCase):
    def test_waiting_status_updates_and_failure_are_mutable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'work';repo=root/'repo'
            (work/'cloud').mkdir(parents=True)
            (work/'cloud/verification-fleet-plan.json').write_text(json.dumps({'assignments':[{
                'model':'test-model','directory':'new-run','run_hash':'a'*64}]}))
            calls=[]
            def sleep(seconds):
                calls.append(seconds)
                if len(calls)==2:raise RuntimeError('stop test watcher')
            with patch.object(job,'WORK',work),patch.object(job,'REPO',repo),patch.object(job.time,'sleep',sleep),patch('sys.argv',['job','--model','test-model']):
                with self.assertRaisesRegex(RuntimeError,'stop test watcher'):job.main()
            state=json.loads((work/'reporting/jobs/test-model.json').read_text())
            self.assertEqual(state['state'],'failed')
            self.assertEqual(len(calls),2)
            self.assertEqual(state['new_model_calls'],0)
            self.assertFalse((repo/'runs/new-run').exists())


if __name__=='__main__':unittest.main()
