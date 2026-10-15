#!/usr/bin/env python3
"""Prepare/apply a separately recorded cloud-cost display amendment. No network.

Only the two derived suite files may be replaced. Existing measurement, source,
report, original assembly and finalizer receipts are read-only. Prepare never
activates the change; apply uses a retained intent and can safely resume a crash.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
WORK=HERE.parents[1]
BASE=WORK.parents[1]
SUITE=BASE/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014'
VALIDATION=WORK/'reporting/final-validation-v1/validation-20261015T044217.374393Z'
VALIDATION_PINS={
    'summary.json':'f3b6c77570d05bb3a945394af4beb74f7433629fa9ef75c2adf829518c41756c',
    'input-file-hashes.json':'6d2aeb425335508c896c43237c194c222a5336fb22bfc7ab0948743d7a6cd46a',
    'finalizer-status.json':'937af0d031f078c3893f7fe8338784fa5e7d006b518e51c85e73421a29b09613',
    'original-suite-build-receipt.json':'8e36dd1f43bf7cd311bfc0dcc3eed8e597097648d011514d20e0ad6e2bca14b5'}
REGIONS={'westus2','eastus2','centralus','southcentralus','northcentralus','westus'}
OPEN='<script id="data" type="application/json">'
OLD_LINK='<a href="suite-build-receipt.json">Build receipt</a>'
NEW_LINK='<a href="suite-build-receipt.json">Original assembly receipt</a>'
NOTE='<p class="note" id="cloud-cost-closure-note"><b>Cloud compute allocation estimate closed.</b> All six VMs were observed deallocated. Storage, public-IP, network, tax and credit charges remain outside this compute estimate; the full invoice total is unknown. The original assembly receipt binds the pre-closure page. <a href="comparison-data.json">Closed-cost evidence</a> is recorded separately in the report data.</p>'

def require(ok,message):
    if not ok:raise ValueError(message)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def file_sha(path):
    path=Path(path);require(path.is_file() and not path.is_symlink(),'Missing, linked or nonregular input: '+str(path))
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def parsed(raw):return json.loads(raw)
def raw_json(value):return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
def exclusive(path,raw):
    with Path(path).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
def publish_new(path,raw):
    path=Path(path);require(not path.exists(),'Immutable receipt already exists')
    temporary=path.with_name('.'+path.name+'.publishing')
    if temporary.exists():require(file_sha(temporary)==sha(raw),'Unexpected interrupted receipt bytes')
    else:exclusive(temporary,raw)
    require(not path.exists(),'Receipt appeared during publication')
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def timestamp(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'));require(d.tzinfo is not None,'Naive allocation timestamp');return d

def validate_ledgers(before,closed,lifecycle):
    require(before['resource_group']==closed['resource_group']=='lce-om100-unlimited-20261014','Wrong resource group')
    require(closed.get('compute_allocations_closed') is True and closed.get('unknown_rate_vm_count')==0,'Allocation intervals or rates remain unknown')
    require(closed['excluded_unknown_items']==before['excluded_unknown_items'],'Unmeasured invoice items changed')
    old={x['region']:x for x in before['vms']};new={x['region']:x for x in closed['vms']}
    require(len(before['vms'])==len(closed['vms'])==6 and set(old)==set(new)==set(lifecycle)==REGIONS,'Expected exactly the six original VM allocations/receipts')
    require(len({x['resource_id'] for x in new.values()})==6,'Duplicate VM identity')
    total=Decimal(0)
    for region,row in new.items():
        original=old[region];receipt=lifecycle[region]
        for key in ('resource_id','size','allocation_started_at_utc','currency','rate_source','rate_effective_start'):
            require(row.get(key)==original.get(key),'Changed allocation/rate identity: '+region+'/'+key)
        require(row['currency']=='USD' and Decimal(str(row['compute_hourly_usd']))==Decimal(str(original['compute_hourly_usd']))>0,'Frozen hourly rate changed')
        require(receipt.get('resource_id')==row['resource_id'] and receipt.get('power_state')=='PowerState/deallocated','Wrong deallocation receipt')
        end=timestamp(row['allocation_end_at_utc']);start=timestamp(row['allocation_started_at_utc'])
        require(end==timestamp(receipt['deallocated_observed_at_utc']) and row['allocation_state']=='closed_deallocated','Closure timestamp/state differs from lifecycle receipt')
        require(end>=start and end>=timestamp(original['observation_at_utc']) and timestamp(closed['at_utc'])>=end,'Allocation time reversed or predates original observation')
        seconds=Decimal(str((end-start).total_seconds()));hours=seconds/Decimal(3600);amount=hours*Decimal(str(row['compute_hourly_usd']))
        require(abs(Decimal(str(row['allocation_hours']))-hours)<=Decimal('1e-10'),'Allocation hours do not reproduce')
        require(abs(Decimal(str(row['compute_estimate_usd']))-amount)<=Decimal('1e-8'),'Allocation compute estimate does not reproduce')
        total+=Decimal(str(row['compute_estimate_usd']))
    require(abs(Decimal(str(closed['compute_estimate_usd']))-total)<=Decimal('1e-8'),'Cloud sum does not reproduce')
    return {'intervals':6,'hourly_rates_unchanged':True,'independent_compute_sum_usd':str(total),'currency':'USD','arithmetic_tolerance_usd':'0.00000001'}

def html_parts(raw):
    text=raw.decode();require(text.count(OPEN)==1,'Expected exactly one report data payload')
    prefix,tail=text.split(OPEN,1);payload,suffix=tail.split('</script>',1)
    return prefix,payload,suffix

def build_outputs(data_raw,index_raw,closed_raw,evidence):
    data=parsed(data_raw);closed=parsed(closed_raw);prefix,payload,suffix=html_parts(index_raw)
    require(parsed(payload)==data,'Original HTML/JSON data differ')
    require('closureEvidence' not in data['cloudCosts'] and 'cloud-cost-closure-note' not in prefix+suffix,'Page already amended')
    updated=copy.deepcopy(data);updated['cloudCosts']={**data['cloudCosts'],
        'asOf':closed['at_utc'],'status':'allocation_intervals_closed','computeEstimatedUsd':str(closed['compute_estimate_usd']),
        'computeComplete':True,'computeBasis':'Frozen official Azure retail hourly rates multiplied by allocation wall time through observed VM deallocation; six allocation intervals closed. This is a compute estimate, not an invoice.',
        'unknownAllocationIntervals':[],'totalUsd':None,'unknownItems':closed['excluded_unknown_items'],
        'sourceSha256':sha(closed_raw),'closureEvidence':evidence}
    require({k:v for k,v in updated.items() if k!='cloudCosts'}=={k:v for k,v in data.items() if k!='cloudCosts'},'Non-cloud data changed')
    outside=prefix+OPEN+'__PAYLOAD__</script>'+suffix
    require(outside.count(OLD_LINK)==1 and outside.count('</header>')==1,'Expected original assembly link/header')
    outside_after=outside.replace(OLD_LINK,NEW_LINK,1).replace('</header>',NOTE+'</header>',1)
    payload_after=json.dumps(updated,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    html_after=outside_after.replace('__PAYLOAD__',payload_after,1).encode()
    a,b,c=html_parts(html_after);require(parsed(b)==updated,'Amended HTML payload differs')
    require((a+OPEN+'__PAYLOAD__</script>'+c).replace(NOTE,'',1).replace(NEW_LINK,OLD_LINK,1)==outside,'Unexpected non-payload HTML edit')
    return raw_json(updated),html_after

def check_guards(guards,allowed=None):
    allowed=allowed or {}
    for path,expected in guards.items():
        actual=file_sha(path);require(actual in allowed.get(path,{expected}),'Guarded input changed: '+path)

def prepare_transaction(transaction,*,suite,validation,finalizer_path,baseline_ledger,closed_ledger,lifecycle_dir,validation_pins=VALIDATION_PINS,script_path=Path(__file__)):
    transaction=Path(transaction);require(not transaction.exists(),'Transaction already exists; inspect or apply its immutable intent')
    validation=Path(validation);suite=Path(suite).resolve();script_path=Path(script_path).resolve()
    validation_raw={name:(validation/name).read_bytes() for name in validation_pins}
    require(all(sha(validation_raw[name])==expected for name,expected in validation_pins.items()),'Completed validation receipt changed')
    summary=parsed(validation_raw['summary.json']);require(summary.get('status')=='pass_data_validation' and summary.get('attempts')==summary.get('priced_responses')==9600,'Completed priced-cohort validation required')
    guards=parsed(validation_raw['input-file-hashes.json']);guards=dict(guards)
    data_path=suite/'comparison-data.json';index_path=suite/'index.html'
    data_raw=data_path.read_bytes();index_raw=index_path.read_bytes();data=parsed(data_raw)
    require(guards[str(data_path)]==sha(data_raw) and guards[str(index_path)]==sha(index_raw),'Original derived report differs from completed validation')
    assembly=suite/'suite-build-receipt.json';assembly_raw=assembly.read_bytes();finalizer_path=Path(finalizer_path).resolve();finalizer_raw=finalizer_path.read_bytes()
    require(assembly_raw==validation_raw['original-suite-build-receipt.json'] and finalizer_raw==validation_raw['finalizer-status.json'],'Original assembly/finalizer receipt changed')
    for receipt in (parsed(assembly_raw),parsed(finalizer_raw)):
        require(receipt['index_sha256']==sha(index_raw) and receipt['data_sha256']==sha(data_raw),'Original receipt does not bind pre-closure files')
    require(parsed(finalizer_raw)['state']=='complete','Finalizer not complete')
    baseline_raw=Path(baseline_ledger).read_bytes();closed_raw=Path(closed_ledger).read_bytes()
    require(sha(baseline_raw)==data['cloudCosts']['sourceSha256'],'Baseline allocation snapshot differs')
    before=parsed(baseline_raw);closed=parsed(closed_raw)
    lifecycle_raw={region:(Path(lifecycle_dir)/(region+'-deallocated.json')).read_bytes() for region in REGIONS}
    arithmetic=validate_ledgers(before,closed,{k:parsed(v) for k,v in lifecycle_raw.items()})
    # Only the original cloud ledger may legitimately have moved since validation.
    cloud_input=str(WORK/'cloud/cloud-costs.json')
    if cloud_input in guards:
        require(guards[cloud_input]==sha(baseline_raw),'Original ledger exception is not bound');guards.pop(cloud_input)
    for path in (script_path,assembly,finalizer_path,*[validation/name for name in validation_pins],Path(baseline_ledger),Path(closed_ledger),*[Path(lifecycle_dir)/(region+'-deallocated.json') for region in REGIONS]):guards[str(path.resolve())]=file_sha(path)
    check_guards(guards)
    evidence={'schemaVersion':1,'kind':'closed_cloud_cost_display_amendment_v1','helperSha256':file_sha(script_path),
        'completedValidationSha256':sha(validation_raw['summary.json']),'originalAssemblyReceiptSha256':sha(assembly_raw),'originalFinalizerReceiptSha256':sha(finalizer_raw),
        'beforeDataSha256':sha(data_raw),'beforeIndexSha256':sha(index_raw),'baselineLedgerSha256':sha(baseline_raw),'closedLedgerSha256':sha(closed_raw),
        'lifecycleReceipts':[{'region':r,'sha256':sha(lifecycle_raw[r])} for r in sorted(REGIONS)],
        'closedAllocationIntervals':6,'frozenHourlyRatesUnchanged':True,'invoiceTotalKnown':False,
        'scope':'Cloud cost display only. Original assembly/finalizer receipts describe the pre-closure page. An external immutable amendment receipt binds the final page and data.'}
    after_data,after_index=build_outputs(data_raw,index_raw,closed_raw,evidence)
    transaction.mkdir(parents=True,exist_ok=False)
    snapshots={'before-comparison-data.json':data_raw,'before-index.html':index_raw,'after-comparison-data.json':after_data,'after-index.html':after_index,
        'baseline-ledger.json':baseline_raw,'closed-ledger.json':closed_raw,'original-suite-build-receipt.json':assembly_raw,'original-finalizer-receipt.json':finalizer_raw,
        'completed-validation.json':validation_raw['summary.json'],'completed-validation-input-hashes.json':validation_raw['input-file-hashes.json'],'helper-source.py':script_path.read_bytes(),
        **{r+'-deallocated.json':raw for r,raw in lifecycle_raw.items()}}
    for name,raw in snapshots.items():exclusive(transaction/name,raw)
    check_guards(guards)
    intent={'schema_version':1,'kind':'closed_cloud_cost_display_amendment_v1','state':'prepared_not_applied','prepared_at_utc':datetime.now(timezone.utc).isoformat(),
        'helper_path':str(script_path),'helper_sha256':file_sha(script_path),'suite':str(suite),'guard_file_hashes':guards,'snapshots':{name:sha(raw) for name,raw in snapshots.items()},
        'targets':[{'path':str(data_path),'before_sha256':sha(data_raw),'after_sha256':sha(after_data),'after_snapshot':'after-comparison-data.json'},
                   {'path':str(index_path),'before_sha256':sha(index_raw),'after_sha256':sha(after_index),'after_snapshot':'after-index.html'}],
        'arithmetic':arithmetic,'scope':'Only cloudCosts and explicit HTML closure note/assembly-receipt label; all other data and original receipts are unchanged.',
        'model_calls':0,'lean_calls':0,'cloud_resource_calls':0,'source_pin_modifications':0,'hardlinks_created':0}
    publish_new(transaction/'intent.json',raw_json(intent));return intent

def atomic_replace(path,raw,*,before_sha,after_sha,intent_sha):
    path=Path(path);current=file_sha(path)
    if current==after_sha:return False
    require(current==before_sha,'Derived target is neither recorded before nor after bytes: '+str(path))
    temporary=path.parent/('.closed-cost-'+intent_sha[:16]+'-'+path.name+'.tmp')
    if temporary.exists():require(file_sha(temporary)==after_sha,'Unexpected interrupted-write bytes')
    else:exclusive(temporary,raw)
    os.chmod(temporary,stat.S_IMODE(path.stat().st_mode))
    require(file_sha(path)==before_sha and file_sha(temporary)==after_sha,'Target changed before atomic replacement')
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return True

def validate_intent(transaction,intent,*,validation_pins=VALIDATION_PINS,expected_suite=SUITE):
    suite=Path(intent['suite']);require(suite==Path(expected_suite).resolve(),'Intent points at another suite')
    snapshot=lambda name:(transaction/name).read_bytes()
    pin_names={'summary.json':'completed-validation.json','input-file-hashes.json':'completed-validation-input-hashes.json',
               'finalizer-status.json':'original-finalizer-receipt.json','original-suite-build-receipt.json':'original-suite-build-receipt.json'}
    for original,copied in pin_names.items():require(sha(snapshot(copied))==validation_pins[original],'Intent lost completed validation/receipt binding')
    original_guards=parsed(snapshot('completed-validation-input-hashes.json'))
    before_data=snapshot('before-comparison-data.json');before_index=snapshot('before-index.html')
    baseline=snapshot('baseline-ledger.json');closed=snapshot('closed-ledger.json')
    require(parsed(before_data)['cloudCosts']['sourceSha256']==sha(baseline),'Intent baseline ledger differs')
    lifecycle={r:parsed(snapshot(r+'-deallocated.json')) for r in REGIONS}
    require(validate_ledgers(parsed(baseline),parsed(closed),lifecycle)==intent['arithmetic'],'Intent arithmetic evidence differs')
    for path,expected in original_guards.items():
        if path==str(WORK/'cloud/cloud-costs.json'):
            require(expected==sha(baseline),'Intent old-ledger exception differs')
        else:require(intent['guard_file_hashes'].get(path)==expected,'Intent dropped or changed an original guard')
    for name,raw in [('comparison-data.json',before_data),('index.html',before_index)]:
        require(original_guards[str(suite/name)]==sha(raw),'Intent baseline report differs')
    updated=parsed(snapshot('after-comparison-data.json'));evidence=updated['cloudCosts']['closureEvidence']
    require(evidence['helperSha256']==intent['helper_sha256']==sha(snapshot('helper-source.py')),'Intent helper evidence differs')
    require(evidence['completedValidationSha256']==validation_pins['summary.json']
            and evidence['originalAssemblyReceiptSha256']==validation_pins['original-suite-build-receipt.json']
            and evidence['originalFinalizerReceiptSha256']==validation_pins['finalizer-status.json'],'Intent closure receipt bindings differ')
    require(evidence['beforeDataSha256']==sha(before_data) and evidence['beforeIndexSha256']==sha(before_index)
            and evidence['baselineLedgerSha256']==sha(baseline) and evidence['closedLedgerSha256']==sha(closed),'Intent closure input bindings differ')
    require(evidence['lifecycleReceipts']==[{'region':r,'sha256':sha(snapshot(r+'-deallocated.json'))} for r in sorted(REGIONS)],'Intent lifecycle evidence differs')
    require(evidence['closedAllocationIntervals']==6 and evidence['frozenHourlyRatesUnchanged'] is True and evidence['invoiceTotalKnown'] is False,'Intent closure scope changed')
    expected_data,expected_index=build_outputs(before_data,before_index,closed,evidence)
    require(expected_data==snapshot('after-comparison-data.json') and expected_index==snapshot('after-index.html'),'Intent contains unapproved derived changes')
    by_name={Path(x['path']).name:x for x in intent['targets']}
    for name,before,after in [('comparison-data.json',before_data,expected_data),('index.html',before_index,expected_index)]:
        target=by_name[name];require(target['before_sha256']==sha(before) and target['after_sha256']==sha(after)
                and target['after_snapshot']=='after-'+name,'Intent target hashes/snapshot differ')

def apply_transaction(transaction,*,script_path=Path(__file__),after_write=None,validation_pins=VALIDATION_PINS,expected_suite=SUITE):
    transaction=Path(transaction).resolve();intent_path=transaction/'intent.json';intent_raw=intent_path.read_bytes();intent=parsed(intent_raw);intent_sha=sha(intent_raw)
    require(intent.get('kind')=='closed_cloud_cost_display_amendment_v1' and len(intent['targets'])==2,'Invalid transaction intent')
    require(file_sha(script_path)==intent['helper_sha256'],'Reviewed helper changed since preparation')
    suite=Path(intent['suite']);require({x['path'] for x in intent['targets']}=={str(suite/'index.html'),str(suite/'comparison-data.json')},'Unexpected target route')
    lock_path=transaction/'.apply.lock';require(not lock_path.is_symlink(),'Linked transaction lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for name,expected in intent['snapshots'].items():
            require(Path(name).name==name and file_sha(transaction/name)==expected,'Transaction snapshot changed')
        validate_intent(transaction,intent,validation_pins=validation_pins,expected_suite=expected_suite)
        allowed={x['path']:{x['before_sha256'],x['after_sha256']} for x in intent['targets']}
        check_guards(intent['guard_file_hashes'],allowed)
        receipt_path=transaction/'receipt.json'
        if receipt_path.exists():
            file_sha(receipt_path);receipt=parsed(receipt_path.read_bytes());require(receipt['intent_sha256']==intent_sha,'Existing receipt belongs to another intent')
            require(receipt.get('state')=='applied_verified' and receipt.get('helper_sha256')==intent['helper_sha256']
                    and receipt.get('data_sha256')==file_sha(suite/'comparison-data.json')
                    and receipt.get('index_sha256')==file_sha(suite/'index.html')
                    and receipt.get('original_assembly_receipt_sha256')==intent['snapshots']['original-suite-build-receipt.json']
                    and receipt.get('original_finalizer_receipt_sha256')==intent['snapshots']['original-finalizer-receipt.json'],'Existing receipt facts differ')
            require(all(file_sha(x['path'])==x['after_sha256'] for x in intent['targets']),'Completed transaction output changed')
            require(file_sha(intent_path)==intent_sha,'Intent changed during validation')
            return receipt
        try:
            require(file_sha(intent_path)==intent_sha,'Intent changed before application')
            for index,target in enumerate(intent['targets']):
                changed=atomic_replace(target['path'],(transaction/target['after_snapshot']).read_bytes(),before_sha=target['before_sha256'],after_sha=target['after_sha256'],intent_sha=intent_sha)
                if changed and after_write:after_write(index)
            require(all(file_sha(x['path'])==x['after_sha256'] for x in intent['targets']),'Derived output replacement incomplete')
            check_guards(intent['guard_file_hashes'],{x['path']:{x['after_sha256']} for x in intent['targets']})
            require(file_sha(intent_path)==intent_sha and all(file_sha(transaction/name)==expected for name,expected in intent['snapshots'].items()),'Intent/snapshot changed during application')
            receipt={'schema_version':1,'kind':intent['kind'],'state':'applied_verified','at_utc':datetime.now(timezone.utc).isoformat(),'intent_sha256':intent_sha,
                'helper_sha256':intent['helper_sha256'],'data_sha256':file_sha(suite/'comparison-data.json'),'index_sha256':file_sha(suite/'index.html'),
                'original_assembly_receipt_sha256':intent['snapshots']['original-suite-build-receipt.json'],'original_finalizer_receipt_sha256':intent['snapshots']['original-finalizer-receipt.json'],
                'all_other_bound_inputs_unchanged':True,'invoice_total_known':False,'model_calls':0,'lean_calls':0,'cloud_resource_calls':0,'source_pin_modifications':0,'hardlinks_created':0}
            receipt_raw=raw_json(receipt)
            staged_receipt=receipt_path.with_name('.'+receipt_path.name+'.publishing')
            if staged_receipt.exists():
                file_sha(staged_receipt);staged_raw=staged_receipt.read_bytes();staged=parsed(staged_raw)
                require(set(staged)==set(receipt) and all(staged[k]==value for k,value in receipt.items() if k!='at_utc'),'Interrupted receipt facts differ')
                require(timestamp(staged['at_utc'])>=timestamp(intent['prepared_at_utc']),'Interrupted receipt predates its intent')
                receipt=staged;receipt_raw=staged_raw
            publish_new(receipt_path,receipt_raw);return receipt
        except Exception as exc:
            exclusive(transaction/('failure-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.json'),raw_json({'type':type(exc).__name__,'error':str(exc),'intent_sha256':intent_sha,'state':'intent_retained_for_reviewed_resume'}));raise

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--baseline-ledger',required=True,type=Path);prep.add_argument('--closed-ledger',required=True,type=Path);prep.add_argument('--lifecycle-dir',required=True,type=Path);prep.add_argument('--transaction',type=Path,default=HERE/'application-v1')
    apply=sub.add_parser('apply');apply.add_argument('--transaction',type=Path,default=HERE/'application-v1')
    args=parser.parse_args();require(args.transaction.resolve().parent==HERE.resolve(),'Transaction must be a direct child of this amendment directory')
    if args.command=='prepare':result=prepare_transaction(args.transaction,suite=SUITE,validation=VALIDATION,finalizer_path=WORK/'reporting/finalization-status.json',baseline_ledger=args.baseline_ledger,closed_ledger=args.closed_ledger,lifecycle_dir=args.lifecycle_dir)
    else:result=apply_transaction(args.transaction)
    print(json.dumps({'state':result['state'],'transaction':str(args.transaction.resolve()),'model_calls':0,'lean_calls':0,'cloud_resource_calls':0}))

if __name__=='__main__':main()
