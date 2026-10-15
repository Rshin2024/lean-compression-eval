"""Offline audit of provider-maximum request/SSE/capture/generation/cost evidence.

Reuses unchanged, hash-pinned original auditor helpers. Reads existing evidence,
never sends requests, executes Lean, releases a marker, or edits a run record.
"""
from __future__ import annotations
import argparse
import base64
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import sys
import tempfile

HERE=Path(__file__).resolve().parent
WORK=HERE.parent
REPO=WORK.parents[1]/'outputs/lean-compression-eval'
SUITE=REPO/'runs/om100-max-suite-high-20261014'
sys.path[:0]=[str(REPO/'src'),str(WORK/'preparation'),str(WORK/'orchestration')]
import priority_costs
priority_costs.install()
from lean_compression.io import digest,file_hash,canonical,evaluator_hash,write_json
from lean_compression.store import load_run,schedule
from lean_compression.integrity import verified_generation
from lean_compression.providers import response_payload
from run_report import load_module
import overload_audit

HEX=re.compile('[0-9a-f]{64}')
RECOVERY_SHA='99c9024323e07e59dec26015cca349aa0add793fd2494e0fe50e6d231d14c354'


def require(ok,message):
    if not ok:raise ValueError(message)


def load_pinned_dependencies():
    plan=json.loads((WORK/'launch_ready.json').read_text())
    for name in ('cost_adapter','generation_adapter'):
        descriptor=plan[name]
        require(file_hash(WORK/descriptor['path'])==descriptor['sha256'],name+' changed')
    pins=json.loads((HERE/'audit-pins.json').read_text())
    for rel,sha in pins['work_files'].items():require(file_hash(WORK/rel)==sha,'Audit source changed: '+rel)
    legacy=load_module('provider_max_legacy_audit',HERE/'legacy_final_auditor.py')
    streaming=load_module('provider_max_stream_reader',WORK/plan['generation_adapter']['path'])
    recovery_path=WORK/'recovery/recover_text_tool_stops.py'
    require(file_hash(recovery_path)==RECOVERY_SHA,'Reviewed text-only recovery helper changed')
    recovery=load_module('provider_max_readonly_recovery_audit',recovery_path)
    recovery._PLAN_SHA=file_hash(WORK/'launch_ready.json')
    return plan,legacy,streaming,recovery


def stream_binding(run,slot,generation,events,streaming):
    aid=slot['attempt_id']
    closed=[e for e in events if e.get('event')=='stream_closed']
    complete=[e for e in closed if e.get('complete') is True]
    require(len(complete)==1,'Must have exactly one terminal stream: '+aid)
    terminal=complete[0]
    require(len(closed)==1,'Prior uncertain stream cannot be silently retried: '+aid)
    opened=[e for e in events if e.get('event')=='stream_opened']
    require(len(opened)==1 and opened[0]['path']==terminal['path'],'Stream open/close mismatch: '+aid)
    path=run/terminal['path']
    require(path.resolve().is_relative_to(run.resolve()) and not path.is_symlink(),'Unsafe stream path')
    require(file_hash(path)==terminal['sha256'] and path.stat().st_size==terminal['bytes'],'Raw SSE hash/size changed: '+aid)
    provider=generation['generation_settings']['provider']
    require(terminal['provider']==provider and terminal['streaming'] is True,'Stream provider mismatch')
    with path.open('rb') as response,tempfile.TemporaryFile() as sink:
        reconstructed=streaming.read_sse(response,sink,provider)
        require(response.tell()==path.stat().st_size,'Trailing unaudited SSE bytes: '+aid)
    body=canonical(reconstructed)
    import hashlib
    response_sha=hashlib.sha256(body).hexdigest()
    require(response_sha==terminal['response_sha256']==generation['response_capture']['body_sha256'],
            'SSE terminal/capture/generation response hash mismatch: '+aid)
    capture_path=run/generation['response_capture']['path']
    captures=[json.loads(line) for line in capture_path.read_bytes().splitlines() if line.strip()]
    require(len(captures)==1 and base64.b64decode(captures[0]['body_base64'],validate=True)==body,
            'Stock capture is not the reconstructed stream result: '+aid)
    headers={k.lower():v for k,v in terminal.get('headers',{}).items()}
    request_id=headers.get('request-id' if provider=='anthropic' else 'x-request-id')
    require(request_id is not None and request_id==captures[0]['request_id'],'SSE/capture request identity mismatch: '+aid)
    require(terminal['started_at']<=terminal['finished_at'],'Stream time order invalid')
    return {'stream_path':terminal['path'],'stream_sha256':terminal['sha256'],
            'stream_bytes':terminal['bytes'],'reconstructed_response_sha256':response_sha}


def effective_price(record,pricing):
    import copy
    result=copy.deepcopy(pricing)
    if pricing['provider']=='openai':
        tier=record['provider_response'].get('service_tier')
        key='priority' if tier in ('fast','priority') else tier
        require(key in ('priority','default'),'Missing/unsupported delivered tier')
        result['rates_per_million']=pricing['pricing_by_delivered_tier']['rates_per_million'][key]
    return result


def audit_model(entry,plan,legacy,streaming,recovery,output,require_complete=False):
    run=REPO/'runs'/entry['directory'];manifest=load_run(run)
    require(manifest['run_hash']==entry['run_hash'] and file_hash(run/'manifest.json')==entry['manifest_sha256'],'Manifest identity changed')
    require(manifest['slots']==schedule(manifest['tasks'],manifest['config'],manifest['environment'],entry['model']),'Schedule reconstruction mismatch')
    settings=manifest['config']['generation'];pricing=manifest['config']['pricing']
    require(settings['max_output_tokens']==128000 and settings['reasoning_setting']=='high','Wrong generation budget/effort')
    slots={s['attempt_id']:s for s in manifest['slots']}
    require(len(slots)==1200 and len(manifest['tasks'])==100,'Wrong population')
    generations={p.stem:p for p in (run/'generations').glob('*.json')}
    checks={p.stem:p for p in (run/'evaluations/v2_threads1').glob('*.json') if HEX.fullmatch(p.stem)}
    require(set(generations)<=set(slots) and set(checks)<=set(slots),'Unscheduled record')
    transport_path=run/'transport.jsonl';stream_path=run/'stream-transport.jsonl'
    transport_raw=transport_path.read_bytes() if transport_path.exists() else b''
    stream_raw=stream_path.read_bytes() if stream_path.exists() else b''
    transport=[json.loads(line) for line in transport_raw.splitlines() if line.strip()]
    stream_events=[json.loads(line) for line in stream_raw.splitlines() if line.strip()]
    amendment_path=run/'amendments.jsonl'
    amendment_raw=amendment_path.read_bytes() if amendment_path.exists() else b''
    amendments=[json.loads(line) for line in amendment_raw.splitlines() if line.strip()]
    request_hashes={}
    for event in transport:
        aid=event.get('attempt_id');require(aid in slots,'Unscheduled transport event')
        if event.get('event')=='request_started':
            if aid not in request_hashes:request_hashes[aid]=digest({**response_payload(slots[aid],settings),'stream':True})
            require(event['request_hash']==request_hashes[aid],'Original or replacement request differs from frozen streaming payload')
    projected_transport,projected_streams,overload_disclosures,overload_files=overload_audit.project(WORK,run,transport,stream_events,amendments)
    by_id,pending=legacy.audit_events(projected_transport,slots)
    by_stream=defaultdict(list)
    for event in projected_streams:
        require(event['attempt_id'] in slots,'Unscheduled stream event')
        by_stream[event['attempt_id']].append(event)
    snapshot=dict(overload_files);response_ids=[];statuses=Counter();tiers=Counter();total=Decimal(0);priced=0;cost_estimates={}
    output.mkdir(parents=True,exist_ok=False)
    evidence_path=output/'attempt-evidence.jsonl'
    with evidence_path.open('x') as evidence:
        for aid,path in sorted(generations.items()):
            record=verified_generation(json.loads(path.read_text()),slots[aid])
            require(record['generation_settings']==settings and record['returned_model']==entry['model'],'Generation policy/model mismatch')
            actual_payload={**response_payload(slots[aid],settings),'stream':True}
            requests=[e for e in by_id[aid] if e['event']=='request_started']
            require(requests and all(e['request_hash']==digest(actual_payload) for e in requests),'Request is not the frozen streaming request')
            reconciliation=record.get('capture_reconciliation')
            if reconciliation:
                require(reconciliation.get('kind')==recovery.KIND,'Unsupported generation recovery')
                marker=reconciliation['marker']
                recovered=recovery.bound_evidence(run,slots[aid],settings,marker)
                recovery.check_existing(record,slots[aid],settings,recovered,marker)
                matching=[event for event in amendments if {k:v for k,v in event.items() if k!='recorded_at'}==reconciliation]
                require(len(matching)==1,'Recovery amendment missing or duplicated')
                require(legacy.timestamp(record['created_at'])>=legacy.timestamp(recovered['unknown_recorded_at']),'Recovered generation predates its evidence')
                binding={'capture_file_sha256':recovered['capture_file_sha256'],
                     'response_body_sha256':recovered['capture']['body_sha256'],
                     'request_id_hash':digest(recovered['request_id']),'received_at':recovered['captured_at'],
                     'capture_reconciliation_kind':recovery.KIND}
            else:
                binding=legacy.audit_capture(run,slots[aid],record,by_id[aid],None,[])
            stream=stream_binding(run,slots[aid],record,by_stream[aid],streaming)
            fresh=priority_costs.estimate_cost(record,pricing,provider=settings['provider'],model=entry['model'])
            require(record['cost_estimate']==fresh,'Stored cost does not reproduce')
            cost_estimates[aid]=fresh
            if fresh['status']=='estimated':
                amount,components=legacy.independent_amount(record,effective_price(record,pricing))
                require(amount==Decimal(fresh['total_usd']),'Independent delivered-tier cost mismatch')
                for kind,component in components.items():
                    actual=fresh['components'][kind]
                    require(component['tokens']==actual['tokens'] and component['amount']==Decimal(actual['cost_usd']),'Cost component mismatch')
                total+=amount;priced+=1
            rid=record['provider_response_id'];require(rid not in response_ids,'Provider response reused')
            for disclosure in overload_disclosures:
                if disclosure['attempt_id']==aid and disclosure.get('original_provider_response_id_hash') is not None:
                    require(digest(rid)!=disclosure['original_provider_response_id_hash'],'Replacement reused original uncertain provider response')
            response_ids.append(rid);statuses[record['generation_status']]+=1
            tiers[fresh.get('service_tier','unavailable')]+=1
            snapshot[str(path)]=file_hash(path)
            snapshot[str(run/record['response_capture']['path'])]=binding['capture_file_sha256']
            snapshot[str(run/stream['stream_path'])]=stream['stream_sha256']
            evidence.write(json.dumps({'attempt_id':aid,'provider_response_id_hash':digest(rid),
                'generation_sha256':snapshot[str(path)],'request_hash':digest(actual_payload),
                'cost_status':fresh['status'],'cost_usd':fresh['total_usd'],**binding,**stream})+'\n')
    markers=[p.stem for p in (run/'inflight').glob('*.json')]
    complete=(len(generations)==1200 and len(checks)==1200 and not pending and not markers)
    if require_complete:require(complete,'Model has not completed all1200generations/checks')
    report_directory=SUITE/('report-'+entry['model']);reports_verified=False
    if complete and (report_directory/'costs.json').exists():
        costs=json.loads((report_directory/'costs.json').read_text())
        analysis=json.loads((report_directory/'analysis.json').read_text())
        require(analysis['run_provenance']['run_hash']==manifest['run_hash'],'Report belongs to a different run')
        require(costs['pricing_schedule']==pricing and costs['pricing_hash']==digest(pricing),'Report price evidence differs')
        rows=costs['attempts'];require(len(rows)==1200 and {r['attempt_id'] for r in rows}==set(slots),'Report cost identities differ')
        for row in rows:
            fresh=cost_estimates[row['attempt_id']]
            require(all(fresh.get(k)==v for k,v in row['cost'].items()),'Report attempt cost mismatch')
        require(costs['summary']['priced_attempts']==priced,'Report priced count mismatch')
        require(Decimal(costs['summary']['observed_sum_usd'])==total,'Report cost total mismatch')
        if priced!=1200:require(costs['summary']['total_usd'] is None,'Unpriced calls cannot have a complete total')
        exports=json.loads((report_directory/'all_scheduled_attempts.json').read_text())
        require(len(exports)==1200 and {r['attempt_id'] for r in exports}==set(slots),'Report phase identities differ')
        for row in exports:
            link=row['verification_record'];path=run/link['path']
            require(path.resolve().is_relative_to(run.resolve()) and file_hash(path)==link['sha256'],'Full verification record changed after report')
            snapshot[str(path)]=link['sha256']
        reports_verified=True
    if require_complete:require(reports_verified,'Completed model report is missing')
    log_sha={str(p):legacy.sha(raw) for p,raw in ((transport_path,transport_raw),(stream_path,stream_raw),
             (amendment_path,amendment_raw)) if p.exists()}
    stable=all(file_hash(Path(p))==sha for p,sha in log_sha.items()) and set(generations)=={p.stem for p in (run/'generations').glob('*.json')}
    if require_complete:
        require(stable,'Final transport/generation snapshot changed during audit')
        snapshot.update(log_sha)
    legacy.require_file_snapshot(snapshot)
    write_json(output/'file-snapshot.json',snapshot)
    report={'status':'pass_complete' if complete and reports_verified and stable else 'pass_partial_snapshot','model':entry['model'],
        'source_run_hash':manifest['run_hash'],'scheduled':1200,'generated':len(generations),
        'verification_record_files':len(checks),'unique_provider_response_ids':len(response_ids),
        'provider_response_id_hashes':[digest(x) for x in response_ids],'priced_generations':priced,
        'recorded_response_token_estimate_usd':str(total),'generation_status_counts':dict(statuses),
        'reports_verified':reports_verified,
        'transport_logs_sha256':log_sha,'snapshot_stable':stable,
        'delivered_tier_counts':dict(tiers),'pending_requests':pending,'inflight_markers':markers,
        'request_and_stream_counts':{'transport':dict(Counter(e['event'] for e in transport)),
                                   'stream':dict(Counter(e['event'] for e in stream_events))},
        'scope':'Request/SSE/capture/generation and costs; Lean graph/provenance validation is performed by the separate report adapter.',
        'unknown_retry_charges':'excluded_not_zero','model_calls':0,'lean_calls':0,'network_calls':0}
    report['authorized_overload_recovery']=overload_disclosures
    write_json(output/'summary.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--model',action='append')
    parser.add_argument('--require-complete',action='store_true');parser.add_argument('--output',type=Path)
    args=parser.parse_args();plan,legacy,streaming,recovery=load_pinned_dependencies()
    require(evaluator_hash()==plan['source_hash'],'Core source changed')
    require(file_hash(REPO/'lean/Evaluator.lean')==plan['engine_hash'],'Lean evaluator changed')
    entries=[e for e in plan['runs'] if not args.model or e['model'] in args.model]
    require(entries and (not args.model or {e['model'] for e in entries}==set(args.model)),'Unknown model')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output=args.output or HERE/'audit-results'/stamp;output.mkdir(parents=True,exist_ok=False)
    reports=[audit_model(e,plan,legacy,streaming,recovery,output/e['model'],args.require_complete) for e in entries]
    response_ids=[x for r in reports for x in r['provider_response_id_hashes']]
    require(len(response_ids)==len(set(response_ids)),'Provider response reused across models')
    complete=len(reports)==8 and all(r['status']=='pass_complete' for r in reports)
    result={'status':'pass_complete' if complete else 'pass_partial_snapshot','cohort_complete':complete,
       'generations_audited':sum(r['generated'] for r in reports),'unique_provider_response_ids':len(response_ids),
       'priced_generations':sum(r['priced_generations'] for r in reports),
       'recorded_response_token_estimate_usd':str(sum((Decimal(r['recorded_response_token_estimate_usd']) for r in reports),Decimal(0))),
       'models':[{'model':r['model'],'status':r['status']} for r in reports],
       'authorized_overload_recoveries':[d for r in reports for d in r['authorized_overload_recovery']],
       'launch_sha256':file_hash(WORK/'launch_ready.json'),'audit_source_sha256':file_hash(Path(__file__)),
       'legacy_auditor_sha256':file_hash(HERE/'legacy_final_auditor.py'),'model_calls':0,'lean_calls':0,'network_calls':0}
    write_json(output/'summary.json',result);print(json.dumps({**result,'output':str(output)}))


if __name__=='__main__':main()
