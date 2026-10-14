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
  model=r.MODELS[1];out=self.report(model);r.save(self.jobs/(model+'.json'),{'state':r.COMPLETE_LOCAL,'report_manifest_sha256':r.sha(out/'report-file-manifest.json')})
  with patch.object(r,'command') as command:r.sync_model(model,{})
  command.assert_not_called()

 def test_completed_local_corruption_is_held_without_overwriting_and_exact_restore_clears(self):
  model=r.MODELS[1];out=self.report(model);expected=r.sha(out/'report-file-manifest.json')
  r.save(self.jobs/(model+'.json'),{'state':r.COMPLETE_LOCAL,'report_manifest_sha256':expected})
  (out/'analysis.json').write_text('changed by fixture')
  before={p.name:p.read_bytes() for p in out.iterdir()}
  with patch.object(r,'command') as command:
   with self.assertRaisesRegex(ValueError,'integrity mismatch'):r.sync_model(model,{})
   self.assertEqual(r.state(model)['state'],r.COMPLETE_LOCAL_HOLD)
   self.assertEqual(r.state(model)['report_manifest_sha256'],expected)
   self.assertEqual(before,{p.name:p.read_bytes() for p in out.iterdir()})
   with self.assertRaisesRegex(ValueError,'integrity mismatch'):r.sync_model(model,{})
   (out/'analysis.json').write_text('{}')
   r.sync_model(model,{})
  command.assert_not_called();self.assertEqual(r.state(model)['state'],r.COMPLETE_LOCAL)
  self.assertEqual(r.sha(out/'report-file-manifest.json'),expected)

 def test_missing_completed_local_bundle_stays_held_and_is_not_downloaded(self):
  model=r.MODELS[1];out=self.report(model);expected=r.sha(out/'report-file-manifest.json')
  r.save(self.jobs/(model+'.json'),{'state':r.COMPLETE_LOCAL,'report_manifest_sha256':expected})
  (out/'analysis.json').unlink()
  with patch.object(r,'command') as command:
   with self.assertRaisesRegex(ValueError,'missing; root repair'):r.sync_model(model,{})
  command.assert_not_called();self.assertFalse((out/'analysis.json').exists())
  self.assertEqual(r.state(model)['state'],r.COMPLETE_LOCAL_HOLD)

 def test_local_controller_rechecks_completed_checkpoint_before_claiming_complete(self):
  model=r.MODELS[1];out=self.report(model);expected=r.sha(out/'report-file-manifest.json')
  r.save(self.jobs/(model+'.json'),{'state':r.COMPLETE_LOCAL,'report_manifest_sha256':expected})
  (out/'analysis.json').unlink()
  remote={model:{'state':r.COMPLETE_REMOTE,'report_manifest_sha256':expected}}
  with patch.object(r,'MODELS',(model,)),patch.object(r,'fetch_remote',return_value=remote),patch.object(r.time,'sleep',side_effect=RuntimeError('fixture loop exit')):
   with self.assertRaisesRegex(RuntimeError,'fixture loop exit'):r.local_main()
  controller=r.read(self.jobs/'controller-local.json')
  self.assertEqual(controller['state'],'needs_attention')
  self.assertIn(model,controller['sync_error']['per_model'])
  self.assertEqual(controller['reports'][model],r.COMPLETE_LOCAL_HOLD)

 def test_queued_duplicate_rechecks_completion_after_build_slot(self):
  model=r.MODELS[1];out=self.report(model);before=(out/'report-file-manifest.json').read_bytes()
  completed={'state':r.COMPLETE_REMOTE,'report_manifest_sha256':r.sha(out/'report-file-manifest.json')}
  # The first checkpoint read precedes queuing; the second observes the other
  # worker's completion while this worker was waiting for a global build slot.
  with patch.object(r,'state',side_effect=[{},completed]),patch.object(r,'backend') as builder,patch.object(r,'record_paths') as paths:
   r.worker(model)
  builder.assert_not_called();paths.assert_not_called()
  self.assertEqual((out/'report-file-manifest.json').read_bytes(),before)

 def test_backend_model_lock_guard_keeps_completed_manifest_immutable(self):
  model=r.MODELS[1];out=self.report(model);before=(out/'report-file-manifest.json').read_bytes()
  job=self.jobs/(model+'.json');r.save(job,{'state':r.COMPLETE_REMOTE,'report_manifest_sha256':r.sha(out/'report-file-manifest.json')})
  checkpoint=job.read_bytes()
  spec=importlib.util.spec_from_file_location('backend_lock_guard_test',Path(__file__).with_name('haiku_report_job.py'))
  backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
  with patch.object(backend,'WORK',self.root),patch.object(backend,'JOB',job),patch.object(backend,'OUT',out),patch.object(backend,'MODEL',model),patch.object(backend,'validate_output') as validate,patch.object(backend.subprocess,'run') as build:
   backend.main()
  validate.assert_not_called();build.assert_not_called()
  self.assertEqual((out/'report-file-manifest.json').read_bytes(),before)
  self.assertEqual(job.read_bytes(),checkpoint)

if __name__=='__main__':unittest.main()
