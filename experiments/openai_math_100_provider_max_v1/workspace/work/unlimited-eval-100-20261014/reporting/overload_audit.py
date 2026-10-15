"""Read-only exact-one recovery audit; never releases or retries a model call."""
from __future__ import annotations
import json
from pathlib import Path
from lean_compression.io import digest,file_hash
import prestream_audit

ATTEMPT='fb6e512ee5aea4245f7391677c7f6f9b2621c31a1c6fc7a2f740b859e208cb84'
PROPOSAL_SHA='167b9490628b04573cf686c8d1c23f2e7a6bb1bd0ee6e28b0761ca41c1adce1a'
HELPER_SHA='356ee575f446c09d7bad7c691c85ebff98edf32f4d13833215e3ff4767509567'

def require(ok,message):
    if not ok:raise ValueError(message)


def project_one(work,run,events,stream_events,amendments,scope):
    ATTEMPT,PROPOSAL_SHA,HELPER_SHA=scope['attempt'],scope['proposal_sha'],scope['helper_sha']
    releases=[e for e in events if e.get('event')=='authorized_unknown_release' and e.get('attempt_id')==ATTEMPT]
    if not releases:return events,stream_events,[],{}
    require(len(releases)==1 and releases[0].get('attempt_id')==ATTEMPT,'Unapproved or repeated unknown-call release')
    proposal_path=work/scope['proposal']
    require(file_hash(proposal_path)==PROPOSAL_SHA,'Exact overload proposal changed')
    root=proposal_path.parent;proposal=json.loads(proposal_path.read_text())
    require(file_hash(work/scope['helper'])==HELPER_SHA==proposal['helper_sha256'],'Release helper changed')
    require(file_hash(work/'launch_ready.json')==proposal['launch_plan_sha256'],'Overload authorization binds another launch')
    evidence_path=root/proposal['evidence_file']
    require(file_hash(evidence_path)==proposal['evidence_sha256'],'Overload evidence snapshot changed')
    evidence=json.loads(evidence_path.read_text());sequence=[e for e in events if e.get('attempt_id')==ATTEMPT]
    require(sequence[:2]==evidence['transport_prefix'] and sequence[2]==releases[0],'Original request/error/release prefix changed')
    require(evidence['attempt_id']==proposal['attempt_id']==ATTEMPT and evidence['run_directory']==run.name,'Overload attempt/run mismatch')
    require(file_hash(run/'manifest.json')==evidence['manifest_sha256'],'Overload source manifest changed')
    require(proposal['maximum_release_count']==1 and proposal['original_charge_usd'] is None
            and proposal.get('original_outcome',evidence.get('original_outcome'))=='unknown','Overload scope or unknown charge was changed')
    intent=json.loads((root/'release-intent.json').read_text());receipt=json.loads((root/'release-receipt.json').read_text())
    require(intent==releases[0]['reconciliation'],'Logged release differs from saved intent')
    require(intent['proposal_sha256']==PROPOSAL_SHA and intent['helper_sha256']==HELPER_SHA
            and intent['attempt_id']==ATTEMPT and intent['maximum_release_count']==1
            and intent['original_charge_usd'] is None and intent['original_outcome']=='unknown'
            and intent['marker']==evidence['marker'] and intent['marker_sha256']==evidence['marker_sha256']
            and intent['original_stream_sha256']==evidence['stream_sha256']
            and intent['new_model_calls']==0 and intent['tools_executed']==0,'Release intent scope mismatch')
    require(all(receipt.get(k)==v for k,v in intent.items()) and receipt['state']=='released_once'
            and receipt['original_marker_removed'] is True,'Release receipt mismatch')
    bound_amendment={**intent,'resolution':'released_for_one_authorized_replacement_request'}
    require(sum({k:v for k,v in e.items() if k!='recorded_at'}==bound_amendment for e in amendments)==1,
            'Release amendment absent or duplicated')
    require(sum(e['event']=='request_started' for e in sequence[3:])<=1,'More than one replacement request under one-use authorization')
    originals=[e for e in stream_events if e.get('attempt_id')==ATTEMPT][:2]
    require(originals==evidence['stream_events'] and digest(originals)==evidence['stream_events_hash'],
            'Original failed stream evidence changed')
    old_stream=run/evidence['stream_path']
    require(old_stream.resolve().is_relative_to(run.resolve()) and file_hash(old_stream)==evidence['stream_sha256'],
            'Original failed stream file changed')
    require(file_hash(root/proposal['preserved_original_stream_file'])==evidence['stream_sha256'],
            'Preserved overload stream copy changed')
    require(evidence['final_message_stop_present'] is False,'Authorized original outcome has a final response')
    if scope.get('partial'):
        partial=root/proposal['preserved_partial_answer_file']
        expected_error=scope.get('error_type','api_error')
        expected_kind='one_use_terminal_partial_'+expected_error+'_release_v1'
        require(proposal['kind']==expected_kind
                and evidence['terminal_error']['error']['type']==expected_error
                and file_hash(partial)==evidence['partial_answer_sha256']==intent['partial_answer_sha256']
                and len(partial.read_bytes().decode('utf-8'))==evidence['partial_answer_chars']==intent['partial_answer_chars']
                and evidence['partial_answer_chars']>0,'Original partial answer or api_error scope differs')
        if expected_error=='timeout_error':
            require(proposal['operator_abandonment'] is False and intent['operator_abandonment'] is False
                    and intent['terminal_error_type']=='timeout_error','Natural provider timeout relabeled as operator abandonment')
    else:
        require(proposal['kind']=='one_use_terminal_overload_release_v1' and evidence['no_answer_text'] is True
                and evidence['terminal_error']['error']['type']=='overloaded_error','Authorized original overload differs')
    # Projection only teaches the existing event checker the reviewed boundary.
    # Every removed byte remains hash-bound above and is disclosed below.
    original_ids={id(e) for e in sequence[:3]};original_stream_ids={id(e) for e in originals}
    projected=[e for e in events if id(e) not in original_ids]
    streams=[e for e in stream_events if id(e) not in original_stream_ids]
    disclosure={'attempt_id':ATTEMPT,'kind':proposal['kind'],'proposal_sha256':PROPOSAL_SHA,
       'authorization_source':proposal.get('authorization_source') or proposal['authorization_basis'],'maximum_replacements':1,
       'original_outcome':'unknown','original_charge_usd':None,'original_stream_sha256':evidence['stream_sha256'],
       'original_provider_response_id_hash':digest(evidence['provider_response_id']),
       'replacement_request_count':sum(e['event']=='request_started' for e in sequence[3:])}
    files={str(p):file_hash(p) for p in (proposal_path,evidence_path,root/'release-intent.json',
           root/'release-receipt.json',old_stream,root/proposal['preserved_original_stream_file'])}
    if scope.get('partial'):
        files[str(partial)]=file_hash(partial)
        disclosure.update(original_partial_answer_sha256=evidence['partial_answer_sha256'],
                          original_partial_answer_chars=evidence['partial_answer_chars'])
    if scope.get('error_type')=='timeout_error':
        disclosure.update(terminal_error_type='timeout_error',operator_abandonment=False)
    return projected,streams,[disclosure],files


def terminal_project(work,run,events,stream_events,amendments):
    releases=[e for e in events if e.get('event')=='authorized_unknown_release']
    if not releases:return events,stream_events,[],{}
    registry=[{'attempt':ATTEMPT,'proposal':'recovery/opus-overload/proposal-v1/proposal.json',
        'proposal_sha':PROPOSAL_SHA,'helper':'recovery/opus-overload/proposal-v1/release_exact_overload.py',
        'helper_sha':HELPER_SHA},
        {'attempt':'3932f38e255ba6ef806b0110719bacbb05c0bbba4ae9a5b2e3e75ff5417045ea',
        'proposal':'recovery/fable-overload/proposal-v1/proposal.json',
        'proposal_sha':'46a992f01a78d00141add2168ac2310cd1a22f0a6247c0559023066911009e02',
        'helper':'recovery/fable-overload/proposal-v1/release_exact_overload.py',
        'helper_sha':'4c7d41d82371b2366d565ada97e75cc92d137542ca9e1ecc5cd69b5c3ae9d296'}]
    batch_relative=Path('recovery/sonnet-api-errors/proposal-v1')
    batch_path=work/batch_relative/'batch-proposal.json'
    batch_sha='07fe27dddd62f310e2d8abd74afd322c368d0103cf9e62c3e85ac5e63a008788'
    require(file_hash(batch_path)==batch_sha,'Exact-six Sonnet scope changed')
    batch=json.loads(batch_path.read_text())
    require(len(batch['attempts'])==6 and len({r['attempt_id'] for r in batch['attempts']})==6
            and batch['maximum_release_count_per_attempt']==1 and batch['original_total_charge_usd'] is None,
            'Exact-six Sonnet release count or original billing changed')
    require(file_hash(work/batch_relative/'release_batch.py')==batch['batch_helper_sha256']
            =='c13167a12ef34ab8bd721be5005f010c9e0dbe64c2b87f24f3b6614c85512328','Sonnet batch helper changed')
    for item in batch['attempts']:
        registry.append({'attempt':item['attempt_id'],'proposal':str(batch_relative/item['proposal_path']),
            'proposal_sha':item['proposal_sha256'],'helper':str(batch_relative/'release_exact_api_error.py'),
            'helper_sha':'42a1036166f0bc586650098cfa84ee2849228773fc9bd430dfd1e8610ab20fac','partial':True})
    timeout_relative=Path('recovery/sonnet-terminal-timeouts/proposal-v1')
    timeout_path=work/timeout_relative/'proposal.json'
    timeout_sha='246ecd642b4eaba2a5476016f036bf2dcddbb598bdaa858de073ea452ee6ea33'
    require(file_hash(timeout_path)==timeout_sha,'Exact-three provider timeout scope changed')
    require(file_hash(work/timeout_relative/'root-review.json')=='f840d40f283e18ca2a168bce4a954f3729a36127fc26a75f8afcb53a6bd24cf1',
            'Exact-three provider timeout operator review changed')
    timeout=json.loads(timeout_path.read_text())
    require(len(timeout['attempts'])==3 and len({r['attempt_id'] for r in timeout['attempts']})==3
            and timeout['operator_abandonment'] is False and timeout['provider_signals']==0,'Timeout scope changed')
    for item in timeout['attempts']:
        registry.append({'attempt':item['attempt_id'],'proposal':item['proposal_path'],
            'proposal_sha':item['proposal_sha256'],'helper':str(timeout_relative/'release_exact_timeout.py'),
            'helper_sha':'b8c176ec823bf3c072e806fd5b4a15c002c30a3ec6b4cd295853c08800be0899',
            'partial':True,'error_type':'timeout_error'})
    allowed={s['attempt']:s for s in registry}
    require(all(e.get('attempt_id') in allowed for e in releases),'Release outside the eleven exact reviewed terminal-error attempts')
    disclosures=[];files={str(batch_path):batch_sha,str(timeout_path):timeout_sha,
        str(work/timeout_relative/'root-review.json'):file_hash(work/timeout_relative/'root-review.json')}
    for attempt in sorted({e['attempt_id'] for e in releases}):
        events,stream_events,extra,bound=project_one(work,run,events,stream_events,amendments,allowed[attempt])
        disclosures.extend(extra);files.update(bound)
    return events,stream_events,disclosures,files


def project(work,run,events,stream_events,amendments):
    events,stream_events,first,first_files=prestream_audit.project(work,run,events,stream_events,amendments)
    events,stream_events,second,second_files=terminal_project(work,run,events,stream_events,amendments)
    return events,stream_events,first+second,{**first_files,**second_files}
