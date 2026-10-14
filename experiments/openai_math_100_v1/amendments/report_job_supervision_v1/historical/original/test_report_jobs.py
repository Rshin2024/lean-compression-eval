"""Report orchestration checks using local fixtures; no network or real builds."""
import contextlib
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock

p=Path(__file__).with_name('report_jobs.py')
spec=importlib.util.spec_from_file_location('report_jobs_under_test',p);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class ReportJobsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.jobs=self.root/'jobs';self.jobs.mkdir();self.suite=self.root/'suite';self.suite.mkdir()
  for name,value in [('JOBS',self.jobs),('SUITE',self.suite)]:
   context=patch.object(r,name,value);context.start();self.addCleanup(context.stop)

 def report(self,model):
  out=self.suite/('report-'+model);out.mkdir();(out/'analysis.json').write_text('{}')
  manifest={'files':[{'path':'analysis.json','sha256':r.sha(out/'analysis.json'),'bytes':2}]}
  (out/'report-file-manifest.json').write_text(json.dumps(manifest));return out

 def test_completed_report_is_not_rebuilt_or_rewritten(self):
  model=r.MODELS[1];out=self.report(model);before=(out/'report-file-manifest.json').read_bytes()
  r.save(self.jobs/(model+'.json'),{'state':r.COMPLETE_REMOTE,'report_manifest_sha256':r.sha(out/'report-file-manifest.json')})
  with patch.object(r,'backend') as builder:r.worker(model)
  builder.assert_not_called();self.assertEqual(before,(out/'report-file-manifest.json').read_bytes())

 def test_build_requires_exact1200_and_failure_is_checkpointed(self):
  model=r.MODELS[1]
  with patch.object(r,'record_paths',return_value=list(range(1199))),patch.object(r,'backend') as builder:
   with self.assertRaisesRegex(ValueError,'exactly 1200'):r.worker(model)
  builder.assert_not_called();self.assertEqual(r.state(model)['state'],'failed')

 def test_shared_slots_limit_concurrent_builds_to_two(self):
  active=peak=0;gate=threading.Lock();failures=[]
  def main():
   nonlocal active,peak
   with gate:active+=1;peak=max(peak,active)
   time.sleep(.08)
   with gate:active-=1
  def run(model):
   try:r.worker(model)
   except Exception as error:failures.append(error)
  real_sleep=time.sleep
  with patch.object(r,'record_paths',return_value=list(range(1200))),patch.object(r,'backend',return_value=Mock(main=main)),patch.object(r.time,'sleep',side_effect=lambda n:real_sleep(min(n,.02))):
   threads=[threading.Thread(target=run,args=(model,)) for model in r.MODELS[1:5]]
   for thread in threads:thread.start()
   for thread in threads:thread.join()
  self.assertFalse(failures);self.assertEqual(peak,2)

 def test_manifest_rejects_traversal_and_tampering(self):
  out=self.report(r.MODELS[1]);hash=r.sha(out/'report-file-manifest.json');r.verify_files(out,hash)
  (out/'analysis.json').write_text('changed')
  with self.assertRaisesRegex(ValueError,'integrity'):r.verify_files(out,hash)
  (out/'report-file-manifest.json').write_text(json.dumps({'files':[{'path':'../outside','bytes':0,'sha256':'x'}]}))
  with self.assertRaisesRegex(ValueError,'Unsafe'):r.verify_files(out,r.sha(out/'report-file-manifest.json'))

 def test_completed_local_report_skips_sync(self):
  model=r.MODELS[1];out=self.report(model);r.save(self.jobs/(model+'.json'),{'state':r.COMPLETE_LOCAL})
  with patch.object(r,'command') as command:r.sync_model(model,{})
  command.assert_not_called()

if __name__=='__main__':unittest.main()
