#!/usr/bin/env python3
"""Independent final data/metric checks AFTER the existing finalizer passes.

Unactivated until root review. No models, Lean, recovery, build, cloud calls,
input edits or hardlinks. Writes only a new timestamped validation directory.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import cmp_to_key
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent;WORK=HERE.parent;BASE=WORK.parents[1]
REPO=BASE/'outputs/lean-compression-eval';SUITE=REPO/'runs/om100-max-suite-high-20261014'
PACKAGE=HERE/'final-validation-v1'
CORE='958bc7676b5fef5700a71ddde93abcc76e7afd67a4622593b57f7836485504f1'
ENGINE='2ff87da0a2eb9058c6400bcb07bb441e5e1683c684b8571384006be37da299a2'
CONDITIONS=['standard','mathematical_compression','source_concision']
METRICS=['W_S','W_B','U_S_decimal','U_B_decimal','T0','I0','D','log2_T0','log2_I0']
HEX=re.compile('[0-9a-f]{64}')

def require(ok,message):
    if not ok:raise ValueError(message)
def read(path):return json.loads(Path(path).read_bytes())
def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def write(path,value):
    with Path(path).open('x') as f:json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')
def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module

def validate_gate(finalizer,states,audit,models):
    require(len(models)==8 and len(set(models))==8,'Expected exactly8 models')
    require(finalizer.get('state')=='complete','Finalizer has not completed; do not validate or publish a partial comparison')
    require(set(states)==set(models) and all(x.get('state')=='complete_synced_verified' for x in states.values()),'All8 report sync gates must pass')
    require(audit.get('status')=='pass_complete' and audit.get('cohort_complete') is True,'Full streaming audit has not passed')
    require(audit.get('generations_audited')==audit.get('unique_provider_response_ids')==9600,'Streaming audit did not establish9600 unique outcomes')
    require(audit.get('priced_generations')==9600,'All9600 recorded responses must have priced usage before final validation')
    require(len(audit.get('models',[]))==8 and {x['model'] for x in audit['models']}==set(models)
            and all(x['status']=='pass_complete' for x in audit['models']),'Streaming audit model coverage differs')

def validate_viewer_review(amendment, review, expected, actual, manifest_sha):
    require(amendment['baseline']['sha256']==expected and amendment['candidate']['sha256']==actual,'Unbound viewer source delta')
    require(review.get('state')=='root_reviewed_viewer_only_change_for_final_delivery'
            and review.get('manifest_sha256')==manifest_sha
            and review.get('baseline_sha256')==expected and review.get('candidate_sha256')==actual,
            'Viewer amendment has not received the exact separate root review')
    require(all(review.get(k) is False for k in ('source_pins_superseded','report_assets_replaced','cloud_redeployment_authorized')),
            'Viewer review exceeds presentation-only scope')
    require(all(review.get(k)==0 for k in ('new_model_calls','lean_calls','provider_signals')),'Viewer review includes unrelated execution')
    return {'baseline':expected,'candidate':actual,'amendment_sha256':manifest_sha,
            'separate_root_viewer_review_passed':True,'original_source_pins_superseded':False}

def validate_incidents(operations,disclosure,audit):
    a=operations['original_unknown_charge_incidents'];b=disclosure['incidents'];c=audit['authorized_overload_recoveries']
    sets=[{x['attempt_id'] for x in xs} for xs in (a,b,c)]
    require(all(len(xs)==30 for xs in (a,b,c)) and all(len(s)==30 for s in sets) and sets[0]==sets[1]==sets[2], 'Expected exactly30 distinct applied original failures across all disclosures')
    projected={x['attempt_id']:{k:v for k,v in x.items() if k!='model'} for x in a}
    require(projected=={x['attempt_id']:x for x in c},'Operational incident details differ from completed stream audit')
    require(operations['original_unknown_charge_count']==30 and operations['operator_abandonment_count']==0,'Unreviewed incident count or operator abandonment')
    require(operations['original_unknown_charges_by_cause']=={'prestream_disconnect':19,'provider_timeout_error':3,'provider_api_error':6,'provider_overloaded_error':2},'Recovery cause inventory differs from reviewed exact30')
    require(operations['total_original_unknown_charges_usd'] is None and disclosure['all_request_total_usd'] is None
            and disclosure['invoice_reconciled'] is False and all(x['unknown_charge_usd'] is None and x['usage_available'] is False for x in b),'Unknown charges must remain unknown')
    return sorted(sets[0])

def unique_rows(rows,key,expected):
    indexed={r[key]:r for r in rows}
    require(len(rows)==len(indexed)==expected,'Unexpected or duplicate row identities')
    return indexed

def completion_summary(rows):
    accepted=sum(r['status']=='success' for r in rows);completed=empty=truncated=0
    for row in rows:
        s=row.get('sourceLength') or {};require(type(s.get('available')) is bool and type(s.get('truncated')) is bool,'Missing completion provenance')
        done=s.get('responseStatus')=='completed' and s.get('sourceKind')=='submitted_text' and s['truncated'] is False
        cut=s.get('responseStatus')=='truncated_output' and s.get('sourceKind')=='partial_saved_text' and s['truncated'] is True
        require(done or cut,'Unclassified completion provenance')
        valid_complete=done and s['available'];completed+=valid_complete;truncated+=cut;empty+=not s['available']
        require(row['status']!='success' or valid_complete,'Accepted outcome is not a complete nonempty submission')
    n=len(rows)
    return {'scheduled':n,'completed':completed,'accepted':accepted,'truncated':truncated,'empty':empty,'unclassified':0,
        'completionRate':completed/n if n else None,'completedAcceptanceRate':accepted/completed if completed else None,
        'scheduledSolveRate':accepted/n if n else None,'empty_truncated_overlap':sum(r['sourceLength']['truncated'] and not r['sourceLength']['available'] for r in rows)}

def independent_cost(generation,pricing,provider):
    effective=pricing;observations=[];canonical=None
    if provider=='openai':
        observations=[v for v in ((generation.get('provider_response') or {}).get('service_tier'),(generation.get('usage') or {}).get('service_tier'),generation.get('service_tier')) if v is not None]
        require(observations and all(v in ('fast','priority','default') for v in observations),'Missing/unsupported delivered tier cannot be priced')
        tiers={'priority' if v in ('fast','priority') else v for v in observations};require(len(tiers)==1,'Conflicting delivered tiers')
        canonical=next(iter(tiers));extension=pricing['pricing_by_delivered_tier']
        require(extension['version']=='delivered_service_tier_costs_v1' and extension['missing_delivered_tier']=='unavailable','Changed delivered-tier policy')
        rates=extension['rates_per_million'];require(set(rates['priority'])==set(rates['default']) and all(Decimal(rates['priority'][k])==2*Decimal(rates['default'][k]) for k in rates['default']),'Priority rates must be frozen2x default')
        effective={**pricing,'rates_per_million':rates[canonical]}
    usage=generation['usage'];inp,out=usage['input_tokens'],usage['output_tokens']
    if provider=='openai':
        details=usage['input_tokens_details'];cached,written=details['cached_tokens'],details['cache_write_tokens']
        buckets={'input':inp-cached-written,'cache_read':cached,'cache_write':written,'output':out};total_input=inp
    else:
        cached,written=usage['cache_read_input_tokens'],usage['cache_creation_input_tokens'];ttl=usage.get('cache_creation') or {}
        five,hour=ttl.get('ephemeral_5m_input_tokens',0),ttl.get('ephemeral_1h_input_tokens',0)
        require(five+hour==written,'Cache TTL buckets differ')
        buckets={'input':inp,'cache_read':cached,'cache_write':five,'output':out}
        if 'cache_write_1h' in effective['rates_per_million'] or hour:buckets['cache_write_1h']=hour
        total_input=inp+cached+written
    require(all(type(n) is int and n>=0 for n in buckets.values()),'Invalid usage buckets')
    long=effective.get('long_context');premium=bool(long and total_input>long['threshold_input_tokens'])
    with localcontext() as context:
        context.prec=80;parts={}
        for kind,n in buckets.items():
            multiplier=Decimal(long['output_multiplier' if kind=='output' else 'input_multiplier']) if premium else Decimal(1)
            parts[kind]=Decimal(n)*Decimal(effective['rates_per_million'][kind])*multiplier/1000000
        return sum(parts.values(),Decimal(0)),parts,canonical

def exact_cell(rows,metric):
    product=Fraction(1)
    for row in rows:
        m=row['metrics'];r=m[metric] if metric!='W_B' else {'numerator_decimal':str(m['W_B']),'denominator_decimal':'1'}
        product*=Fraction(int(r['numerator_decimal']),int(r['denominator_decimal']))
    require(rows and product>0,'Empty/nonpositive exact cell')
    return product,len(rows)

def rank_oracle(data,metric,condition,gate):
    cells=defaultdict(list)
    for r in data['attempts']:
        if r['condition']==condition and r['status']=='success' and r['metricStatus']=='available':cells[r['model'],r['task']].append(r)
    coverage={m['id']:sum(bool(cells[m['id'],t]) for t in data['tasks']) for m in data['models']}
    cohort=[m['id'] for m in data['models'] if coverage[m['id']]>=gate]
    tasks=[t for t in data['tasks'] if cohort and all(cells[m,t] for m in cohort)]
    if len(cohort)<2 or not tasks:return {'metric':metric,'condition':condition,'gate':gate,'cohort':cohort,'tasks':tasks,'rows':[]}
    totals={m:Fraction(0) for m in cohort};q_ranks={m:{} for m in cohort}
    for task in tasks:
        values={m:exact_cell(cells[m,task],metric) for m in cohort}
        def compare(a,b):
            x,n=values[a];y,k=values[b];left,right=x**k,y**n
            higher=-1 if left>right else 1 if left<right else 0
            return -higher if metric=='W_B' else higher
        ordered=sorted(cohort,key=cmp_to_key(compare));start=0
        while start<len(ordered):
            end=start+1
            while end<len(ordered) and compare(ordered[start],ordered[end])==0:end+=1
            rank=Fraction(start+1+end,2)
            for m in ordered[start:end]:totals[m]+=rank;q_ranks[m][task]=float(rank)
            start=end
    return {'metric':metric,'condition':condition,'gate':gate,'cohort':cohort,'tasks':tasks,
        'rows':[{'model':m,'averageRank':float(totals[m]/len(tasks)),'exact_average_rank':str(totals[m]/len(tasks)),
                 'rankPoints':float((len(cohort)-totals[m]/len(tasks))*100/(len(cohort)-1)),'questions':q_ranks[m]} for m in cohort]}

def validate_cloud_snapshot(ledger,display):
    require(display['sourceSha256']==digest_bytes(ledger),'Cloud display source hash differs')
    source=json.loads(ledger);require(Decimal(str(display['computeEstimatedUsd']))==Decimal(str(source['compute_estimate_usd'])),'Cloud amount differs')
    closed=source.get('compute_allocations_closed') is True;known=source.get('unknown_rate_vm_count')==0
    require(display.get('totalUsd') is None,'A retail VM ledger is not an invoice')
    require(display.get('computeComplete') is not True or closed and known,'Open/unknown allocation shown complete')
    return {'allocations_closed':closed,'all_rates_known':known,'display_compute_complete':display.get('computeComplete'),
        'postcleanup_display_amendment_needed':closed and known and display.get('computeComplete') is not True,
        'note':'Open allocation snapshots are allowed here; final closed-ledger presentation requires a separate reviewed amendment.'}

def digest_bytes(raw):return hashlib.sha256(raw).hexdigest()

def validate(output,plan,finalizer,audit,states):
    watched={}
    def bind(path):
        path=Path(path);value=sha(path);watched[str(path)]=value;return value
    def bundle(directory,expected):
        man=directory/'report-file-manifest.json';require(bind(man)==expected,'Report manifest changed')
        value=read(man);names={'report-file-manifest.json'}
        for row in value['files']:
            p=directory/row['path'];require(not p.is_symlink() and p.resolve().is_relative_to(directory.resolve()),'Unsafe report path')
            require(p.stat().st_size==row['bytes'] and bind(p)==row['sha256'],'Report artifact changed');names.add(row['path'])
        require({str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()}==names,'Unexpected report files')
    preparation=read(PACKAGE/'preparation-manifest.json');bind(PACKAGE/'preparation-manifest.json')
    for relative,expected in preparation['files'].items():
        source=HERE/relative;require(source.resolve().is_relative_to(HERE.resolve()) and bind(source)==expected,'Prepared validator/source snapshot changed')
    require(bind(WORK/'launch_ready.json')==sha(PACKAGE/'launch-plan.snapshot.json'),'Launch plan changed after validator preparation')
    require(plan['source_hash']==CORE and plan['engine_hash']==ENGINE and plan['scheduled_attempts']==9600,'Wrong frozen experiment')
    sys.path[:0]=[str(REPO/'src'),str(REPO/'reporting')]
    from lean_compression.io import evaluator_hash
    from lean_compression.store import schedule
    require(evaluator_hash()==CORE and bind(REPO/'lean/Evaluator.lean')==ENGINE,'Frozen core/Lean changed')
    pins=read(HERE/'source-pins.json');viewer_delta=None
    for name in ['source-pins.json','audit-pins.json']:
        pin=read(HERE/name);bind(HERE/name)
        for key,root in [('work_files',WORK),('repo_files',REPO)]:
            for rel,expected in pin.get(key,{}).items():
                actual=bind(root/rel)
                if actual!=expected:
                    require(rel=='reporting/serve_comparison.py' and key=='repo_files','Non-viewer pinned source changed: '+rel)
                    amendment=read(HERE/'viewer-amendment-v1/manifest.json')
                    review_path=HERE/'viewer-amendment-v1/root-review.json'
                    viewer_delta=validate_viewer_review(amendment,read(review_path),expected,actual,bind(HERE/'viewer-amendment-v1/manifest.json'))
                    viewer_delta['root_review_sha256']=bind(review_path)
    source_lengths=load_module('final_validation_source_lengths',REPO/'reporting/source_lengths.py')
    baseline=read(PACKAGE/'baseline-inputs.json')
    for relative,expected in baseline['files'].items():require(bind(BASE/relative)==expected,'Original8192 artifact changed: '+relative)
    for entry in plan['runs']:
        old_report=REPO/'runs/om100-suite-high-20261013'/('report-'+entry['model'])
        bundle(old_report,baseline['files'][str((old_report/'report-file-manifest.json').relative_to(BASE))])
    data=read(SUITE/'comparison-data.json');bind(SUITE/'comparison-data.json')
    require(data['classification']=='observed' and data['buildStatus']=='complete' and data['totalAttempts']==9600,'Wrong comparison status/count')
    require(data['generation']=={'reasoningEffort':'high','outputTokenCap':128000},'Wrong displayed generation budget')
    require(data['tasks']==[f'OM100-{i:03d}' for i in range(1,101)] and data['conditions']==CONDITIONS and data['samplesPerQuestion']==4,'Changed cohort/questions')
    combined=unique_rows(data['attempts'],'attemptId',9600)
    questions=read(SUITE/'questions-data.json');bind(SUITE/'questions-data.json');bind(SUITE/'questions.html')
    qmap={(q['task_id'],q['condition']):q for q in questions['questions']};require(len(qmap)==len(questions['questions'])==300,'Prompt viewer does not contain300 distinct messages')
    all_ids=set();base_ids=set();summary=[];priced_total=Decimal(0);priced_count=0;prompt_checks=0;phase_cohorts=set()
    for entry in plan['runs']:
        model=entry['model'];require(entry['directory']==f'om100-max-{model}-high-20261014','Wrong run directory')
        run=REPO/'runs'/entry['directory'];old_run=REPO/'runs'/entry['baseline_directory'];manifest=read(run/'manifest.json');old=read(old_run/'manifest.json')
        require(bind(run/'manifest.json')==entry['manifest_sha256'] and bind(old_run/'manifest.json')==entry['baseline_manifest_sha256'],'New/original manifest hash mismatch')
        require(manifest['run_hash']==entry['run_hash']==digest({k:v for k,v in manifest.items() if k!='run_hash'}),'Run identity changed')
        config=manifest['config'];settings=config['generation'];require(settings['max_output_tokens']==128000 and settings['reasoning_setting']=='high' and settings['model_snapshot']==model,'Generation settings changed')
        require(config['samples_per_goal_condition']==4 and config['conditions']==CONDITIONS,'Sampling policy changed')
        require(manifest['slots']==schedule(manifest['tasks'],config,manifest['environment'],model),'Schedule cannot be reconstructed')
        slots={s['attempt_id']:s for s in manifest['slots']};require(len(slots)==1200 and not all_ids.intersection(slots),'Duplicate new slots');all_ids.update(slots)
        original={(s['task_id'],s['condition'],s['sample_index']):s for s in old['slots']};base_ids.update(s['attempt_id'] for s in old['slots'])
        for slot in slots.values():
            previous=original[slot['task_id'],slot['condition'],slot['sample_index']];q=qmap[slot['task_id'],slot['condition']]
            require(slot['messages']==previous['messages']==q['messages'] and slot['prompt_hash']==previous['prompt_hash']==q['prompt_hash']==digest(q['messages']),'Changed original/final viewer prompt')
            require(slot['attempt_id']!=previous['attempt_id'],'Original attempt reused');prompt_checks+=1
        require({p.stem for p in (run/'generations').glob('*.json')}==set(slots),'Generation file coverage differs')
        require({p.stem for p in (run/'evaluations/v2_threads1').glob('*.json') if HEX.fullmatch(p.stem)}==set(slots),'Verification file coverage differs')
        require(not list((run/'inflight').glob('*.json')),'In-flight markers remain')
        report=SUITE/('report-'+model);bundle(report,states[model]['manifest_sha256'])
        analysis=read(report/'analysis.json');phase=analysis['phase_provenance'];phase_cohorts.add(phase['cohort_hash'])
        require(analysis['models'][model]['run_complete'] is True and phase['generation']['run_hash']==manifest['run_hash'] and phase['generation']['manifest_sha256']==entry['manifest_sha256'],'Wrong phase report provenance')
        require(phase['verification']['version']=='v2_threads1' and phase['verification']['limits']=={'cpu_quota':2,'memory_mib':8192,'wall_seconds':180,'extraction_wall_seconds':900,'lean_threads':1},'Changed verification limits')
        policy=read(report/'verification-policy.json');require(digest(policy)==phase['verification']['policy_hash'],'Verification policy hash mismatch')
        rows=read(report/'all_scheduled_attempts.json');require(len(rows)==1200 and {r['attempt_id'] for r in rows}==set(slots),'Phase export coverage mismatch')
        lengths=source_lengths.measure_report(rows,manifest,model)['byAttempt'];cost_report=read(report/'costs.json');costs=unique_rows(cost_report['attempts'],'attempt_id',1200);require(set(costs)==set(slots),'Cost schedule differs')
        subtotal=Decimal(0);priced=0;tiers=Counter();valid=measured=0
        audit_model_path=Path(finalizer['audit_output'])/model/'summary.json';audit_files_path=Path(finalizer['audit_output'])/model/'file-snapshot.json'
        audit_model=read(audit_model_path);audit_files=read(audit_files_path);bind(audit_model_path);bind(audit_files_path)
        require(audit_model['status']=='pass_complete' and audit_model['reports_verified'] is True and audit_model['snapshot_stable'] is True and audit_model['source_run_hash']==entry['run_hash'],'Incomplete/unbound model stream audit')
        for row in rows:
            aid=row['attempt_id'];slot=slots[aid];view=combined[aid];gen=row['generation'];result=row['verification_result']
            require(row['record_kind']=='phase_separated_verified_attempt' and row['generation_identity']=={k:v for k,v in slot.items() if k!='messages'},'Relabeled or pending phase record')
            require(row['verification_policy_hash']==phase['verification']['policy_hash'] and row['generation_hash']==digest(gen),'Phase/generation binding mismatch')
            require(row['verification_record']['sha256']==audit_files[str(run/row['verification_record']['path'])],'Report verification link differs from complete stream-audit file snapshot')
            require(gen['generation_settings']==settings and gen['returned_model']==model,'Recorded model/config differs')
            require(view['model']==model and view['task']==slot['task_id'] and view['condition']==slot['condition'] and view['sample']==slot['sample_index']-manifest.get('sample_index_base',0)+1,'Combined slot relabeled')
            require(view['status']==result['validity_status'] and view['metricStatus']==result['metric_status'],'Combined outcome differs')
            require(view['metrics']=={k:(result.get('metrics') or {}).get(k) for k in METRICS},'Combined measured values differ')
            require(view['sourceLength']==lengths[aid],'Source text lengths/classification differ')
            fresh=gen['cost_estimate'];require(all(fresh.get(k)==v for k,v in costs[aid]['cost'].items()),'Cost export differs from stored generation')
            require(all(costs[aid]['cost'].get(k)==v for k,v in view['cost'].items() if k!='generated'),'Combined costs differ')
            if fresh['status']=='estimated':
                amount,components,tier=independent_cost(gen,config['pricing'],settings['provider']);require(amount==Decimal(fresh['total_usd']),'Delivered-tier cost arithmetic mismatch')
                require(all(amount==Decimal(fresh['components'][kind]['cost_usd']) for kind,amount in components.items()),'Cost component mismatch')
                subtotal+=amount;priced+=1;tiers[tier or 'anthropic_standard']+=1
            else:require(fresh['total_usd'] is None and view['cost']['total_usd'] is None,'Unpriced response received a fabricated total')
            valid+=view['status']=='success';measured+=view['status']=='success' and view['metricStatus']=='available'
        require((Decimal(cost_report['summary']['observed_sum_usd'])==subtotal if priced else cost_report['summary']['observed_sum_usd'] is None) and cost_report['summary']['priced_attempts']==priced,'Per-model cost sum differs')
        require(Decimal(audit_model['recorded_response_token_estimate_usd'])==subtotal and audit_model['priced_generations']==priced,'Final audit cost sum differs')
        if priced<1200:require(cost_report['summary']['total_usd'] is None,'Incomplete costs cannot have a complete total')
        priced_total+=subtotal;priced_count+=priced
        summary.append({'model':model,'attempts':1200,'valid':valid,'measurable':measured,'priced':priced,'recorded_response_token_estimate_usd':str(subtotal),'delivered_tier_counts':dict(tiers)})
    require(len(all_ids)==9600 and not all_ids.intersection(base_ids) and set(combined)==all_ids and len(phase_cohorts)==1,'Cohort identity or phase mismatch')
    require(priced_count==audit['priced_generations'] and priced_total==Decimal(audit['recorded_response_token_estimate_usd']),'Global pricing audit mismatch')
    operations=read(SUITE/'operational-disclosure.json');disclosure=read(SUITE/'transport-cost-disclosure.json')
    incident_ids=validate_incidents(operations,disclosure,audit);require(set(incident_ids)<=all_ids,'Recovery refers to another run')
    require(operations['provider_output_ceiling']==128000 and operations['reference_checks_reused_by_hash']==100 and operations['new_reference_executions']==0 and operations['prompt_text_changed_from_8192_cohort'] is False,'Wrong operational disclosure')
    require(data['operationalDisclosure']==operations and data['transportCostDisclosure']['incidents']==disclosure['incidents'],'Viewer disclosure differs')
    require(data['transportCostDisclosure'].get('allRequestTotalUsd') is None and data['transportCostDisclosure'].get('invoiceReconciled') is False,'Comparison hides unknown request charges')
    for incident in disclosure['incidents']:
        view=combined[incident['attempt_id']]
        require(all(incident[k]==view[v] for k,v in [('model','model'),('task_id','task'),('condition','condition'),('sample','sample')]),'Incident display slot differs')
        entry=next(x for x in plan['runs'] if x['model']==incident['model'])
        require(incident['source_run_hash']==entry['run_hash'] and incident['source_manifest_sha256']==entry['manifest_sha256'],'Incident run binding differs')
    require(data['transportCostDisclosure']['sourceSha256']==bind(SUITE/'transport-cost-disclosure.json'),'Disclosure file hash changed')
    bind(SUITE/'operational-disclosure.json');receipt=read(SUITE/'suite-build-receipt.json');bind(SUITE/'suite-build-receipt.json')
    require(receipt['index_sha256']==bind(SUITE/'index.html') and receipt['data_sha256']==sha(SUITE/'comparison-data.json'),'Original assembly receipt no longer binds page/data; use a separate post-closure amendment validator')
    html=(SUITE/'index.html').read_text();embedded=html.split('<script id="data" type="application/json">',1)[1].split('</script>',1)[0]
    require(json.loads(embedded)==data and '128,000' in html,'Viewer payload/budget differs')
    require(not re.search(r'sk-(?:proj-|ant-)[A-Za-z0-9_-]{12,}',html),'Credential pattern in viewer')
    qhtml=(SUITE/'questions.html').read_text();qembedded=qhtml.split('<script type="application/json" id="data">',1)[1].split('</script>',1)[0]
    require(json.loads(qembedded)==questions,'Question HTML/JSON payload differs')
    baseline_questions=read(REPO/'runs/om100-suite-high-20261013/questions-data.json')
    require(questions['questions']==baseline_questions['questions'],'Question message bytes differ from8192 viewer; only run hashes may change')
    outcomes=completion_summary(data['attempts'])
    cases=[rank_oracle(data,metric,c,gate) for metric in ('T0','I0','W_B') for c in CONDITIONS for gate in (0,1,25,50)]
    write(output/'exact-rank-oracle.json',cases)
    node_script=r"""const fs=require('node:fs'),assert=require('node:assert/strict'),P=require(process.argv[1]),C=require(process.argv[2]);const d=JSON.parse(fs.readFileSync(process.argv[3])),cases=JSON.parse(fs.readFileSync(process.argv[4]));for(const expected of cases){const actual=P.compute(d,{metric:expected.metric,condition:expected.condition,minCoverage:expected.gate});assert.deepEqual(actual.cohort,expected.cohort);assert.deepEqual(actual.tasks,expected.tasks);assert.equal(actual.rows.length,expected.rows.length);for(const e of expected.rows){const r=actual.rows.find(x=>x.model===e.model);assert(Math.abs(r.averageRank-e.averageRank)<1e-12);assert(Math.abs(r.rankPoints-e.rankPoints)<1e-10);assert.deepEqual(Object.fromEntries(r.questions.map(q=>[q.task,q.rank])),e.questions);}}console.log(JSON.stringify({rank_cases:cases.length,outcomes:C.summarizeOutcomes(d.attempts)}));"""
    env={k:os.environ[k] for k in ('PATH','HOME','LANG') if k in os.environ};env['PYTHONDONTWRITEBYTECODE']='1'
    node=subprocess.run(['node','-e',node_script,str(REPO/'reporting/paper_rankings.js'),str(REPO/'reporting/compression_validity.js'),str(SUITE/'comparison-data.json'),str(output/'exact-rank-oracle.json')],env=env,capture_output=True,text=True,check=True)
    js=json.loads(node.stdout)
    for key,value in outcomes.items():
        if key!='empty_truncated_overlap':require(js['outcomes'][key]==value,'Python/JS acceptance population mismatch: '+key)
    cloud_check=None
    if data.get('cloudCosts'):
        expected=data['cloudCosts']['sourceSha256'];candidates=[WORK/'cloud/cloud-costs.json',*(WORK/'cloud/cost-ledger-history').glob('*/before.json'),*(WORK/'cloud/cost-ledger-history').glob('*/after.json')]
        candidates=[p for p in candidates if p.is_file() and sha(p)==expected];require(candidates,'Cloud source snapshot not retained')
        cloud_check=validate_cloud_snapshot(candidates[0].read_bytes(),data['cloudCosts']);bind(candidates[0])
    for path,expected in watched.items():require(sha(path)==expected,'Audited input changed during validation: '+path)
    for name,source in [('launch-plan.json',WORK/'launch_ready.json'),('finalizer-status.json',HERE/'finalization-status.json'),('stream-audit-summary.json',Path(finalizer['audit_output'])/'summary.json'),('original-suite-build-receipt.json',SUITE/'suite-build-receipt.json')]:
        with (output/name).open('xb') as f:f.write(source.read_bytes())
    write(output/'input-file-hashes.json',watched)
    result={'schema_version':1,'status':'pass_data_validation','at_utc':datetime.now(timezone.utc).isoformat(),'script_sha256':sha(Path(__file__)),
      'models':summary,'attempts':9600,'prompt_slot_bindings_checked':prompt_checks,'question_message_sets':300,'old_attempt_overlap':0,
      'outcome_populations':outcomes,'priced_responses':priced_count,'recorded_response_token_estimate_usd':str(priced_total),
      'original_unknown_charge_incidents':len(incident_ids),'unknown_original_charge_usd':None,'exact_rank_oracle_cases':js['rank_cases'],
      'old8192_manifest_and_report_bundle_hashes_verified':True,'old_raw_graphs_deep_rehashed_here':False,
      'cloud_snapshot':cloud_check,'viewer_only_source_delta':viewer_delta,
      'scope':'Independent completed-suite identities, prompt bytes, lengths, exact rankings and costs. Full SSE/raw verification byte checks are bound to the preceding completed streaming audit; cloud/local backups and browser QA remain separate gates.',
      'model_calls':0,'lean_calls':0,'provider_network_calls':0,'input_writes':0,'hardlinks_created':0,
      'cleanup_authorized':False,'postclosure_amendment_activated':False}
    write(output/'summary.json',result);return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path);args=parser.parse_args()
    plan=read(WORK/'launch_ready.json');finalizer=read(HERE/'finalization-status.json')
    # No output directory, heavy reads, imports or snapshots before the complete gate.
    require(finalizer.get('state')=='complete','Finalizer incomplete: validator is not activated')
    audit_path=Path(finalizer['audit_output'])/'summary.json';audit=read(audit_path)
    require(sha(audit_path)==finalizer['audit_summary_sha256'],'Final streaming audit receipt changed')
    states={entry['model']:read(HERE/'jobs'/(entry['model']+'-local.json')) for entry in plan['runs']}
    validate_gate(finalizer,states,audit,[entry['model'] for entry in plan['runs']])
    output=(args.output or PACKAGE/('validation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))).resolve()
    require(output.parent==PACKAGE.resolve(),'Validation output must be a new direct child of final-validation-v1')
    output.mkdir(exist_ok=False)
    try:result=validate(output,plan,finalizer,audit,states)
    except Exception as exc:
        write(output/'failure.json',{'status':'fail','type':type(exc).__name__,'error':str(exc),'input_writes':0});raise
    print(json.dumps({'status':result['status'],'output':str(output),'attempts':result['attempts'],'priced_responses':result['priced_responses']}))

if __name__=='__main__':main()
