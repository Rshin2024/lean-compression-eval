"""Build the complete new cohort and disclose its operational differences.

No old outputs are touched; all eight reports must already be complete. Raw
failed streams/partial text remain private; only hashes and counts are exported.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent;WORK=HERE.parent;REPO=WORK.parents[1]/'outputs/lean-compression-eval'
sys.path[:0]=[str(REPO/'src'),str(REPO/'reporting')]
from build_comparison import build
from lean_compression.io import digest,file_hash
import overload_audit


def supplements(suite):
    fleet=json.loads((WORK/'cloud/verification-fleet-plan.json').read_text())
    hardware=json.loads((WORK/'cloud/host-runtime-evidence.json').read_text())
    hosts={row['region']:row['runtime'] for row in hardware['rows']}
    operations={'schema_version':1,'kind':'provider_max_run_operational_disclosure',
        'provider_output_ceiling':128000,'unlimited_output_available':False,
        'requested_reasoning_effort':'high','prompt_text_changed_from_8192_cohort':False,
        'openai_requested_service_tier':'priority','openai_cost_basis':'actual_delivered_fast_or_priority_2x_or_default_1x',
        'anthropic_requested_processing':'standard','reference_checks_reused_by_hash':100,
        'new_reference_executions':0,'hardware_source_sha256':file_hash(WORK/'cloud/host-runtime-evidence.json'),
        'hardware':[{'model':r['model'],'region':r['region'],'cpu_model':hosts[r['region']]['cpu']['Model name'],
                     'host_vcpus':int(hosts[r['region']]['cpu']['CPU(s)'])} for r in fleet['assignments']],
        'hardware_limitations':'Same image and nominal per-proof limits; mixed physical CPU models. Wall-timeout outcomes may depend on host speed. This is not a controlled runtime-speed comparison.',
        'original_unknown_charge_incidents':[]}
    incidents=[];evidence=[]
    for row in fleet['assignments']:
        run=REPO/'runs'/row['directory'];manifest=json.loads((run/'manifest.json').read_text())
        slots={s['attempt_id']:s for s in manifest['slots']}
        def events(name):
            path=run/name
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
        transport=events('transport.jsonl');streams=events('stream-transport.jsonl');amendments=events('amendments.jsonl')
        _,_,disclosures,_=overload_audit.project(WORK,run,transport,streams,amendments)
        for disclosure in disclosures:
            aid=disclosure['attempt_id'];slot=slots[aid]
            seq=[e for e in transport if e.get('attempt_id')==aid]
            operations['original_unknown_charge_incidents'].append({'model':row['model'],**disclosure})
            incidents.append({'model':row['model'],'attempt_id':aid,'task_id':slot['task_id'],
                'condition':slot['condition'],'sample':slot['sample_index']+1,'kind':disclosure.get('category','terminal_stream_error'),
                'http_status':None,'recorded_at_utc':disclosure.get('original_recorded_at_utc',seq[1]['recorded_at']),'usage_available':False,
                'unknown_charge_usd':None,'source_run_hash':row['run_hash'],
                'source_manifest_sha256':row['manifest_sha256'],'transport_events_sha256':digest(seq)})
            evidence.append({'kind':disclosure.get('category','terminal_stream_error')+'_evidence',
                'sha256':disclosure.get('original_stream_sha256') or disclosure['original_transport_prefix_sha256']})
    operations['original_unknown_charge_count']=len(incidents)
    def cause(row):
        if row.get('category')=='prestream_disconnect':return 'prestream_disconnect'
        if row.get('terminal_error_type')=='timeout_error':return 'provider_timeout_error'
        if row['kind']=='one_use_terminal_partial_api_error_release_v1':return 'provider_api_error'
        if row['kind']=='one_use_terminal_overload_release_v1':return 'provider_overloaded_error'
        raise ValueError('Undisclosed original request failure category')
    operations['original_unknown_charges_by_cause']=dict(Counter(cause(row) for row in operations['original_unknown_charge_incidents']))
    operations['operator_abandonment_count']=0
    operations['total_original_unknown_charges_usd']=None
    incident=WORK/'reporting/incidents/hardlink-fixture-ctime-v1/incident.json'
    if incident.exists():
        operations['supervision_incidents']=[{'kind':'report_equivalence_fixture_changed_live_inode_ctime',
            'source_sha256':file_hash(incident),'file_bytes_changed':False,'inode_metadata_changed':True,
            'effect':'The manifest metadata guard halted new dispatch; existing requests continued. Safe supervision recovery is recorded separately.',
            'fixture_correction':'Use byte copies, not hard links, for live-evidence test fixtures.'}]
    source={'schema_version':1,'kind':'unknown_transport_cost_disclosure',
        'as_of_utc':datetime.now(timezone.utc).isoformat(),'scope':'known_incidents_not_exhaustive',
        'invoice_reconciled':False,'all_request_total_usd':None,'incidents':incidents,'evidence':evidence}
    for name,value in [('operational-disclosure.json',operations),('transport-cost-disclosure.json',source)]:
        (suite/name).write_text(json.dumps(value,indent=2)+'\n')
    transport_view={'schemaVersion':1,'asOf':source['as_of_utc'],'scope':source['scope'],
        'invoiceReconciled':False,'allRequestTotalUsd':None,'incidents':incidents,'evidence':evidence,
        'sourceSha256':file_hash(suite/'transport-cost-disclosure.json')}
    return operations,transport_view


def augment_page(page,data):
    """Only explicitly matched insertion points may change the derived viewer."""
    if page.count('<script id="data" type="application/json">')!=1:raise ValueError('Data target is missing or duplicated')
    payload=json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    page,count=re.subn(r'(<script id="data" type="application/json">).*?(</script>)',lambda m:m[1]+payload+m[2],page,count=1,flags=re.S)
    if count!=1:raise ValueError('Data insertion point changed')
    for old,new in [
        ("events.filter(e=>e.kind==='disconnected_response').length", "events.filter(e=>['disconnected_response','terminal_stream_error','prestream_disconnect'].includes(e.kind)).length"),
        ('Disconnected requests without a captured response','Requests without a final response')]:
        if page.count(old)!=1:raise ValueError('Transport disclosure insertion point changed')
        page=page.replace(old,new,1)
    panel='<p class="note"><b>Provider-maximum rerun.</b> 128,000 tokens is the provider ceiling, not unlimited output. OpenAI Fast/priority processing was requested; reported API estimates use each response’s delivered tier. Anthropic used standard processing. The 100 reference checks were reused unchanged by hash. <a href="operational-disclosure.json">Exact operational disclosure</a> · <a href="transport-cost-disclosure.json">Unknown request charges</a> · <a href="suite-build-receipt.json">Build receipt</a>.</p>'
    if page.count('</header>')!=1:raise ValueError('Header insertion point changed')
    page=page.replace('</header>',panel+'</header>',1)
    note=' The 100 reference checks were reused unchanged by hash. The fleet used AMD EPYC 7763 for Astra/6.1 Sol and Intel Xeon 8573C for the other models; nominal limits and the image match, but physical hardware differs. Wall-timeout outcomes may depend on host speed.'
    marker='<p id="checkerMethods"'
    if marker not in page and 'id="checkerMethods"' not in page:raise ValueError('Checker-method disclosure target is missing')
    if len(re.findall(r'</html>',page,re.I))!=1 or len(re.findall(r'</html>\s*$',page,re.I))!=1:
        raise ValueError('HTML end insertion point changed')
    script='<script id="provider-max-hardware-note">const methodNote=document.querySelector("#checkerMethods");if(!methodNote)throw new Error("Missing checker disclosure target");methodNote.textContent += '+json.dumps(note)+';</script>'
    page=re.sub(r'</html>(\s*)$',lambda m:script+'</html>'+m[1],page,flags=re.I)
    if page.count(script)!=1 or page.count('id="provider-max-hardware-note"')!=1:
        raise ValueError('Hardware disclosure script was not inserted exactly once')
    return page


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=REPO/'runs/om100-max-suite-high-20261014')
    parser.add_argument('--cloud-costs',type=Path,default=WORK/'cloud/cloud-costs.json')
    args=parser.parse_args();suite=args.suite.resolve()
    if suite.name!='om100-max-suite-high-20261014':raise ValueError('New provider-maximum suite only')
    operations,disclosure=supplements(suite)
    data=build(suite,runs_root=REPO/'runs',run_directory_template='om100-max-{model}-high-20261014',cloud_costs=args.cloud_costs)
    if data['totalAttempts']!=9600 or data['generation']['outputTokenCap']!=128000:raise ValueError('Wrong cohort/budget')
    data['operationalDisclosure']=operations;data['transportCostDisclosure']=disclosure
    cloud=json.loads(args.cloud_costs.read_text()) if args.cloud_costs.is_file() else None
    if cloud is not None and 'compute_estimate_usd' in cloud:
        data['cloudCosts']={'asOf':cloud['at_utc'],'currency':'USD','status':cloud.get('status','allocation_snapshot'),
            'computeEstimatedUsd':str(cloud['compute_estimate_usd']),
            'computeComplete':False,'computeBasis':cloud['basis']+' Unknown-rate VM count: '+str(cloud['unknown_rate_vm_count'])+'.',
            'unknownAllocationIntervals':[],'totalUsd':None,'unknownItems':cloud['excluded_unknown_items'],
            'sourceSha256':file_hash(args.cloud_costs)}
    (suite/'comparison-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    page=augment_page((suite/'index.html').read_text(),data)
    (suite/'index.html').write_text(page)
    receipt={'status':'built_complete_cohort','models':8,'attempts':9600,'output_token_ceiling':128000,
        'known_original_unknown_charges':operations['original_unknown_charge_count'],
        'index_sha256':file_hash(suite/'index.html'),'data_sha256':file_hash(suite/'comparison-data.json'),
        'builder_sha256':file_hash(Path(__file__)),'base_builder_sha256':file_hash(REPO/'reporting/build_comparison.py'),
        'new_model_calls':0,'new_lean_calls':0}
    (suite/'suite-build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
