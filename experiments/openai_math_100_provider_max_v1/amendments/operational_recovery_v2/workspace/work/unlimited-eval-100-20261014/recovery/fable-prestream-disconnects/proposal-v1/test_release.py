"""Offline fixtures only: no signals, API requests, resume controls, or live releases."""
import importlib.util,json,pathlib,tempfile,unittest
from unittest import mock
spec=importlib.util.spec_from_file_location('release19',pathlib.Path(__file__).with_name('release_pre_stream_disconnects.py'));h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
class History(unittest.TestCase):
 def setUp(self):
  self.r='request';self.events=[{'event':'request_started','request_hash':self.r,'transport_index':0},{'event':'http_error','http_status':429,'transport_index':0},{'event':'no_model_outcome','last_error':{'http_status':429},'capture':None,'provider_response':None},{'event':'request_started','request_hash':self.r,'transport_index':0},{'event':'outcome_unknown','type':'OSError','message':'Streaming connection failed; request outcome is uncertain','transport_index':0}]
 def test_original_429_then_unknown_allowed(self):h.validate_history(self.events,self.r)
 def test_replacement_or_release_is_never_an_original(self):
  for e in [{'event':'request_started'},{'event':'authorized_unknown_release'},{'event':'response_recorded'}]:
   with self.assertRaises(ValueError):h.validate_history(self.events+[e],self.r)
 def test_any_prior_unknown_or_saved_outcome_blocks(self):
  for kind in ['outcome_unknown','authorized_unknown_release','response_recorded']:
   with self.assertRaises(ValueError):h.validate_history([{'event':kind}]+self.events,self.r)
 def test_different_request_or_error_blocks(self):
  for key,value in [('type','TimeoutError'),('message','different'),('transport_index',1)]:
   rows=[dict(x) for x in self.events];rows[-1][key]=value
   with self.assertRaises(ValueError):h.validate_history(rows,self.r)
  with self.assertRaises(ValueError):h.validate_history(self.events,'another payload')
 def test_non429_prior_error_blocks(self):
  rows=[dict(x) for x in self.events];rows[1]['http_status']=500
  with self.assertRaises(ValueError):h.validate_history(rows,self.r)
 def test_root_review_required_for_apply(self):
  with self.assertRaises(ValueError):h.review_gate(None,None,'batch','helper')
 def test_exact_nineteen_unique_ids(self):self.assertEqual(19,len(h.IDS));self.assertEqual(19,len(set(h.IDS)))
class Files(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.repo=pathlib.Path(self.temp.name);self.run=self.repo/'runs'/h.RUN;self.run.mkdir(parents=True);self.a=h.IDS[0];self.payload={'m':'fixed'};request=h.digest(self.payload)
  for folder in ['generations','captures','inflight','stream-captures']:(self.run/folder).mkdir()
  (self.run/'manifest.json').write_text('{}');(self.run/'inflight'/(self.a+'.json')).write_text(json.dumps({'attempt_id':self.a,'started_at':'original'}))
  self.events=[{'attempt_id':self.a,'event':'request_started','request_hash':request,'transport_index':0},{'attempt_id':self.a,'event':'http_error','http_status':429,'transport_index':0},{'attempt_id':self.a,'event':'no_model_outcome','last_error':{'http_status':429}},{'attempt_id':self.a,'event':'request_started','request_hash':request,'transport_index':0},{'attempt_id':self.a,'event':'outcome_unknown','type':'OSError','message':'Streaming connection failed; request outcome is uncertain','transport_index':0}]
  (self.run/'transport.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in self.events));self.m={'slots':[{}]*822+[{'attempt_id':self.a,'messages':[]}],'model_snapshot':h.MODEL,'config':{'generation':{}},'run_hash':'frozen'}
  self.patches=[mock.patch.object(h,'REPO',self.repo),mock.patch.object(h,'load_run',return_value=self.m),mock.patch.object(h,'streaming_payload',return_value=self.payload)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def test_absent_stream_capture_generation_fixture_passes(self):self.assertEqual(h.inspect(self.run,self.a)['original_charge_usd'],None)
 def test_any_capture_or_generation_blocks(self):
  for folder,suffix in [('captures','.jsonl'),('generations','.json')]:
   p=self.run/folder/(self.a+suffix);p.write_text('{}')
   with self.assertRaises(ValueError):h.inspect(self.run,self.a)
   p.unlink()
 def test_even_empty_stream_directory_blocks(self):
  (self.run/'stream-captures'/self.a).mkdir()
  with self.assertRaises(ValueError):h.inspect(self.run,self.a)
 def test_stream_opened_metadata_blocks_without_a_file(self):
  (self.run/'stream-transport.jsonl').write_text(json.dumps({'attempt_id':self.a,'event':'stream_opened'})+'\n')
  with self.assertRaises(ValueError):h.inspect(self.run,self.a)
 def test_other_attempt_or_model_blocks(self):
  with self.assertRaises(ValueError):h.inspect(self.run,'0'*64)
  self.m['model_snapshot']='another-model'
  with self.assertRaises(ValueError):h.inspect(self.run,self.a)
 def test_live_inflight_lock_is_not_abandoned(self):
  import fcntl,os
  p=self.run/'inflight'/(self.a+'.json');fd=os.open(p,os.O_RDONLY)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);self.assertIsNone(h._abandoned(p,protocol=False))
  finally:os.close(fd)
if __name__=='__main__':unittest.main()
