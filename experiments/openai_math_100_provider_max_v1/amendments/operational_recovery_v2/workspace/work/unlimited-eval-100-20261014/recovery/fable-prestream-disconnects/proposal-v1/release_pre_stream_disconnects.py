"""Exact nineteen original pre-stream failures: release once, never call a model.

Preparation/dry validation is the default. Applying requires a separately bound
root-review receipt. Original outcomes and billing remain unknown; this is not
an attestation of direct, exact-ID human approval. No controller is resumed.
"""
import argparse,fcntl,hashlib,json,os,pathlib,sys
HERE=pathlib.Path(__file__).resolve().parent;WORK=HERE.parents[2];REPO=WORK.parents[1]/'outputs/lean-compression-eval'
sys.path[:0]=[str(REPO/'src'),str(WORK/'orchestration')]
from lean_compression.io import _abandoned,_release,append_event,digest,evaluator_hash,file_hash,read_json,write_json
from lean_compression.store import load_run
from streaming_adapter import streaming_payload
MODEL='claude-fable-5-1';RUN='om100-max-claude-fable-5-1-high-20261014';KIND='one_use_original_prestream_disconnect_release_v1'
IDS=[
'a3a356a147ff38718133d2157d004305de082bd928a6ddd02f33635793425462',
'de7e827971497f0ed4043c8f56202e2bd36f65323fe2e897567154fae877a8cb',
'2d2f4d82623d8cfff2f5c871669f255a18a330a453328b92c5a56e8f96c3a7c4',
'4bc3a52d0f90c478eab18cd17e688202157051a7257940d8e685c37bd2b259fb',
'd89075ddac40da0dd307a4de6fb7d67f79dfe3db62dfc155c1ac626bef544658',
'99f069c0e034ba9e432e3229b26b7b5a1ba2dee6da1054c80bd207c68eae5a30',
'23120aa50142c5b0c11f1608b39cd73108e960fae86ff8a86c6699fa0120adb0',
'21f2cd2c3d1be0d84d0913bf1da10f4e1a8ec46966b4bb4385586ed32142085c',
'99c441c0b8105d452922e93075079a9acc480e9111a92a92e2b32ffa1e92cdba',
'b1b3b08a3df5102946f29a0e2843223923f22c24a8e596532eb11312e3d30089',
'9efbbef86d42aed807031ada7caac9615cc8dc9dcef41b50c5fc7bafe38551b9',
'ab30b73be0ef9e2c2608a31dcf36951da28f78f9d724f2b7241def49a31c7657',
'fc38f54ca242035bb42206cfce032ee0fd4d53a6a09028affd4ec5dcd8587644',
'd31dd77f98bd93c942265a0e856a35d58e434ff3f234d197c48f6a1bf1a14c1e',
'7db8bf980fdfb4fa928bf146fa57e2dc98684d4ca59f68d0ec882c6f1f8b99c7',
'b4138a57391e7c85a841764f56f09c17fc9e5a03aa1ecfb9873b7affb2124378',
'bad8dda80fa7794933725f2d65a8a66f0b6c99b533c2584cedca229272863d3e',
'2e8c19a46b60a9cbe0bb4a4499504319a4fe2c80cfc6190b2c3f3ea99bf719d9',
'2169cd729a3d79f275870deddb76018001ae7f8b985abeeb02c812dfd6c37fda']
REASON='Original pre-stream OSError disconnect; no saved stream or response; outcome and billing unknown'

def selected_lines(path,attempt):
 if not path.exists():return b'',[]
 data=path.read_bytes()
 if data and not data.endswith(b'\n'):raise ValueError('Append log has an incomplete final line; inspect again without mutation')
 lines=[];events=[]
 for line in data.split(b'\n'):
  if not line:continue
  event=json.loads(line)
  if event.get('attempt_id')==attempt:lines.append(line+b'\n');events.append(event)
 return b''.join(lines),events

def validate_history(events,request_hash):
 if len(events)<2 or [e.get('event') for e in events[-2:]]!=['request_started','outcome_unknown']:
  raise ValueError('Latest original is not the exact unknown pre-stream failure')
 if events[-1].get('type')!='OSError' or events[-1].get('message')!='Streaming connection failed; request outcome is uncertain':
  raise ValueError('Unknown outcome classification differs')
 if events[-2].get('transport_index')!=0 or events[-1].get('transport_index')!=0:raise ValueError('Original failed transport index differs')
 prefix=events[:-2];awaiting=False;seen_http=0
 for e in prefix:
  kind=e.get('event')
  if kind=='request_started':
   if awaiting or e.get('request_hash')!=request_hash:raise ValueError('Prior request sequence or payload differs')
   awaiting=True
  elif kind=='http_error':
   if not awaiting or e.get('http_status')!=429:raise ValueError('Earlier outcome is not a confirmed HTTP429 rejection')
   awaiting=False;seen_http+=1
  elif kind=='no_model_outcome':
   if awaiting or (e.get('last_error') or {}).get('http_status')!=429 or e.get('capture') is not None or e.get('provider_response') is not None:raise ValueError('Earlier transport summary differs')
  else:raise ValueError('Unexpected original event, release, or replacement outcome')
 if awaiting or seen_http<1 or events[-2].get('request_hash')!=request_hash:raise ValueError('Original prefix or frozen request differs')

def inspect(run,attempt):
 run=pathlib.Path(run)
 if run.resolve()!=(REPO/'runs'/RUN).resolve() or attempt not in IDS:raise ValueError('Outside exact nineteen original attempts')
 manifest=load_run(run);index=822+IDS.index(attempt);slot=manifest['slots'][index]
 if slot['attempt_id']!=attempt or manifest['model_snapshot']!=MODEL:raise ValueError('Frozen model, schedule position, or identity differs')
 if (run/'generations'/(attempt+'.json')).exists() or (run/'captures'/(attempt+'.jsonl')).exists():raise ValueError('Saved or captured responses must never be retried')
 if (run/'stream-captures'/attempt).exists():raise ValueError('Any saved stream path blocks this pre-stream release')
 raw,events=selected_lines(run/'transport.jsonl',attempt);stream_raw,streams=selected_lines(run/'stream-transport.jsonl',attempt)
 if streams or stream_raw:raise ValueError('Stream opening/closing evidence exists')
 request_hash=digest(streaming_payload(slot,manifest['config']['generation']));validate_history(events,request_hash)
 marker=run/'inflight'/(attempt+'.json');marker_bytes=marker.read_bytes();marker_data=json.loads(marker_bytes)
 if marker_data.get('attempt_id')!=attempt:raise ValueError('In-flight marker names another attempt')
 generation_claim=run/'generations'/(attempt+'.json.claim')
 return {'attempt_id':attempt,'model':MODEL,'slot_index_zero_based':index,'run_directory':RUN,'manifest_sha256':file_hash(run/'manifest.json'),'run_hash':manifest['run_hash'],'slot_identity':{k:v for k,v in slot.items() if k!='messages'},'request_hash':request_hash,'marker':marker_data,'marker_sha256':hashlib.sha256(marker_bytes).hexdigest(),'transport_prefix':events,'transport_prefix_hash':digest(events),'transport_prefix_bytes_sha256':hashlib.sha256(raw).hexdigest(),'transport_prefix_bytes':len(raw),'stream_events':[],'saved_stream_present':False,'saved_capture_present':False,'saved_generation_present':False,'generation_claim_sha256':file_hash(generation_claim) if generation_claim.exists() else None,'original_outcome':'unknown','original_charge_usd':None}

def review_gate(path,sha,batch,helper):
 if path is None or sha is None or file_hash(path)!=sha:raise ValueError('A separately hash-bound root review is required for apply')
 r=read_json(path)
 if r.get('state')!='approved_for_one_release' or r.get('batch_proposal_sha256')!=batch or r.get('helper_sha256')!=helper or len(r.get('attempt_ids',[]))!=19 or set(r['attempt_ids'])!=set(IDS):raise ValueError('Root review does not authorize this exact one-use batch')
 return r

def execute(batch_path,batch_sha,apply=False,review_path=None,review_sha=None):
 if batch_path.resolve()!=HERE/'proposal.json' or file_hash(batch_path)!=batch_sha:raise ValueError('Batch proposal differs from reviewed bytes/path')
 batch=read_json(batch_path);helper=file_hash(pathlib.Path(__file__))
 if batch.get('kind')!='exact_nineteen_prestream_disconnect_batch_v1' or batch.get('helper_sha256')!=helper or batch.get('maximum_release_per_attempt')!=1 or batch.get('attempt_ids')!=IDS:raise ValueError('Batch source or exact scope differs')
 if file_hash(WORK/'launch_ready.json')!=batch['launch_plan_sha256']:raise ValueError('Frozen launch changed')
 launch=read_json(WORK/'launch_ready.json')
 if evaluator_hash()!=launch['source_hash'] or file_hash(REPO/'lean/Evaluator.lean')!=launch['engine_hash']:raise ValueError('Frozen evaluator changed')
 for relative,expected in launch['operational_source_hashes'].items():
  source=(WORK/relative).resolve()
  if not source.is_relative_to(WORK) or file_hash(source)!=expected:raise ValueError('Frozen generation source changed')
 bindings=[r for r in launch['runs'] if r['model']==MODEL and r['directory']==RUN]
 if len(bindings)!=1:raise ValueError('Frozen model must have exactly one launch binding')
 binding=bindings[0]
 review=review_gate(review_path,review_sha,batch_sha,helper) if apply else None
 if (HERE/'batch-release-intent.json').exists():return {'state':'batch_authorization_already_consumed_manual_review_required','new_model_calls':0,'claims_released':0}
 descriptors=batch['attempt_proposals']
 if len(descriptors)!=19 or [d['attempt_id'] for d in descriptors]!=IDS:raise ValueError('Per-attempt proposal set differs')
 run=REPO/'runs'/RUN;locks=[];actuals=[]
 try:
  for d in descriptors:
   a=d['attempt_id'];directory=HERE/'attempts'/a;path=directory/'proposal.json'
   if file_hash(path)!=d['proposal_sha256']:raise ValueError('Per-attempt proposal changed')
   proposal=read_json(path)
   if proposal.get('attempt_id')!=a or proposal.get('kind')!=KIND or proposal.get('helper_sha256')!=helper or proposal.get('maximum_release_count')!=1:raise ValueError('Per-attempt scope differs')
   if (directory/'release-intent.json').exists() or (directory/'release-receipt.json').exists():raise ValueError('Attempt authorization already consumed; never release a replacement')
   for name,expected in proposal['preserved_evidence_hashes'].items():
    if pathlib.Path(name).name!=name or file_hash(directory/name)!=expected:raise ValueError('Preserved original evidence changed')
   expected=read_json(directory/'evidence.json')
   marker=run/'inflight'/(a+'.json');fd=_abandoned(marker,protocol=False)
   if fd is None:raise ValueError('Original marker is absent or actively owned')
   locks.append((fd,marker))
   claim=run/'generations'/(a+'.json.claim')
   if claim.exists():
    cf=_abandoned(claim,protocol=True)
    if cf is None:raise ValueError('Generation claim is live or unrecognized')
    locks.append((cf,None))
   actual=inspect(run,a)
   if actual['manifest_sha256']!=binding['manifest_sha256'] or actual['run_hash']!=binding['run_hash']:raise ValueError('Original run is not bound to frozen launch')
   if actual!=expected:raise ValueError('Original history/claim differs; refuse any later request')
   actuals.append((d,directory,actual,fd,marker))
  if not apply:return {'state':'reviewed_dry_only','attempts':19,'all_original_locks_abandoned':True,'new_model_calls':0,'claims_released':0,'controller_resume_requested':False}
  # Consume the batch before any marker is released. A crash requires manual
  # review of remaining original IDs; it never repeats a consumed authorization.
  write_json(HERE/'batch-release-intent.json',{'batch_proposal_sha256':batch_sha,'helper_sha256':helper,'review_sha256':review_sha,'attempt_ids':IDS,'maximum_release_per_attempt':1,'new_model_calls':0,'controller_resume_requested':False})
  receipts=[]
  for d,directory,actual,fd,marker in actuals:
   if inspect(run,actual['attempt_id'])!=actual:raise ValueError('Evidence changed before release; leave remaining attempts held')
   a=actual['attempt_id'];intent={'kind':KIND,'attempt_id':a,'proposal_sha256':d['proposal_sha256'],'batch_proposal_sha256':batch_sha,'helper_sha256':helper,'marker':actual['marker'],'marker_sha256':actual['marker_sha256'],'transport_prefix_hash':actual['transport_prefix_hash'],'transport_prefix_bytes_sha256':actual['transport_prefix_bytes_sha256'],'reason':REASON,'authorization_basis':batch['authorization_basis'],'direct_exact_id_human_approval':False,'original_outcome':'unknown','original_charge_usd':None,'maximum_release_count':1,'new_model_calls':0,'tools_executed':0,'controller_resume_requested':False}
   write_json(directory/'release-intent.json',intent)
   append_event(run/'amendments.jsonl',{**intent,'resolution':'released_for_one_authorized_replacement_request'},once=True)
   append_event(run/'transport.jsonl',{'attempt_id':a,'event':'authorized_unknown_release','reconciliation':intent,'reason':REASON},once=True)
   _release(fd,marker);locks.remove((fd,marker))
   receipt={**intent,'state':'released_once','original_marker_removed':True};write_json(directory/'release-receipt.json',receipt);receipts.append({'attempt_id':a,'receipt_sha256':file_hash(directory/'release-receipt.json')})
  result={'state':'released_exact_nineteen_once','receipts':receipts,'new_model_calls':0,'controller_resume_requested':False};write_json(HERE/'batch-release-receipt.json',result);return result
 finally:
  for fd,_ in locks:os.close(fd)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--proposal-sha256',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--root-review',type=pathlib.Path);p.add_argument('--root-review-sha256');a=p.parse_args()
 for key in ['OPENAI_API_KEY','ANTHROPIC_API_KEY']:os.environ.pop(key,None)
 with (HERE/'release.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  print(json.dumps(execute(HERE/'proposal.json',a.proposal_sha256,a.apply,a.root_review,a.root_review_sha256),indent=2))
if __name__=='__main__':main()
