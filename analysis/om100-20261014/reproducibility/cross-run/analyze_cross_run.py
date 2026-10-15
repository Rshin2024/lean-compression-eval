#!/usr/bin/env python3
"""Read-only paired-question analysis of completed Lean compression cohorts.

No model, Lean, cloud or network calls. Writes only a new analysis output folder.
Bootstrap units are the 100 questions jointly across runs, models and conditions.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
from decimal import Decimal
from fractions import Fraction
import hashlib,json,csv,math,platform,sys
from pathlib import Path
import numpy as np
sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent;BASE=HERE.parents[2];RUNS=BASE/'outputs/lean-compression-eval/runs'
SPECS={'cap8192':'om100-suite-high-20261013','cap128000':'om100-max-suite-high-20261014'}
CONDITIONS=['standard','mathematical_compression','source_concision']
SEED=20261014;REPLICATES=10000

def require(ok,message):
    if not ok:raise ValueError(message)
def sha_file(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def read(path,inputs):
    raw=Path(path).read_bytes();inputs[str(Path(path).resolve())]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)};return json.loads(raw)
def write_json(path,data):
    with Path(path).open('x') as f:json.dump(data,f,indent=2,ensure_ascii=False,allow_nan=False);f.write('\n')
def write_csv(path,rows):
    rows=list(rows)
    require(rows,'Refusing schema-free empty CSV: '+str(path))
    fields=list(rows[0]);require(all(set(row)==set(fields) for row in rows),'CSV row schema varies')
    with Path(path).open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
def count(value,field,*,optional=False):
    if value is None and optional:return None
    require(type(value) is int and value>=0,'Missing/noninteger token count: '+field);return value

def completion(row):
    s=row.get('sourceLength') or {};require(type(s.get('available')) is bool and type(s.get('truncated')) is bool,'Missing completion provenance')
    done=s.get('responseStatus')=='completed' and s.get('sourceKind')=='submitted_text' and s['truncated'] is False
    truncated=s.get('responseStatus')=='truncated_output' and s.get('sourceKind')=='partial_saved_text' and s['truncated'] is True
    require(done or truncated,'Unclassified response completion')
    complete=done and s['available'];valid=row['status']=='success';require(not valid or complete,'Valid proof is not a completed nonempty submission')
    category='accepted' if valid else 'completed_rejected' if complete else 'truncated' if truncated else 'empty_completed'
    return {'valid':int(valid),'completed':int(complete),'truncated':int(truncated),'empty':int(not s['available']),'outcome_category':category}

def usage_fields(generation):
    u=generation['usage'];provider=generation['generation_settings']['provider'];inp=count(u.get('input_tokens'),'input_tokens');out=count(u.get('output_tokens'),'output_tokens')
    if provider=='openai':
        d=u.get('input_tokens_details') or {};cached=count(d.get('cached_tokens'),'cached_tokens');written=count(d.get('cache_write_tokens'),'cache_write_tokens');require(cached+written<=inp,'Cache buckets exceed total input')
        total_input=inp;uncached=inp-cached-written;five=hour=None
    elif provider=='anthropic':
        cached=count(u.get('cache_read_input_tokens'),'cache_read_input_tokens');written=count(u.get('cache_creation_input_tokens'),'cache_creation_input_tokens');total_input=inp+cached+written;uncached=inp
        ttl=u.get('cache_creation') or {};five=count(ttl.get('ephemeral_5m_input_tokens'),'ephemeral5m',optional=True);hour=count(ttl.get('ephemeral_1h_input_tokens'),'ephemeral1h',optional=True)
        if five is not None and hour is not None:require(five+hour==written,'TTL cache buckets differ')
    else:raise ValueError('Unexpected provider')
    reasoning=count((u.get('output_tokens_details') or {}).get('reasoning_tokens'),'reasoning_tokens',optional=True)
    require(reasoning is None or reasoning<=out,'Reasoning exceeds output count')
    tiers=[x for x in [(generation.get('provider_response') or {}).get('service_tier'),u.get('service_tier'),generation.get('service_tier')] if x is not None]
    canon={'priority' if x in ('fast','priority') else x for x in tiers};require(len(canon)<=1,'Conflicting delivered tiers')
    return {'provider':provider,'delivered_service_tier':next(iter(canon)) if canon else None,
      'requested_service_tier':generation['generation_settings'].get('service_tier'),'input_tokens':inp,'input_total_tokens':total_input,'uncached_input_tokens':uncached,
      'output_tokens':out,'total_billed_tokens':total_input+out,'reasoning_tokens':reasoning,'cache_read_tokens':cached,'cache_write_tokens':written,
      'cache_write_5m_tokens':five,'cache_write_1h_tokens':hour,'generation_elapsed_seconds':generation.get('generation_elapsed_seconds')}

def join_key(row):return row['model'],row['task'],row['condition'],row['sample']
def unique(rows,key):
    result={key(row):row for row in rows};require(len(result)==len(rows),'Duplicate row identity');return result

def normalize_suite(label,spec,inputs):
    suite=RUNS/spec;data=read(suite/'comparison-data.json',inputs)
    require(data['buildStatus']=='complete' and data['classification']=='observed','Unfinished/synthetic cohort')
    cap=8192 if label=='cap8192' else 128000
    require(data['generation']=={'reasoningEffort':'high','outputTokenCap':cap},'Generation policy mismatch')
    models=[x['id'] for x in data['models']];tasks=data['tasks']
    require(len(set(models))==len(models)==8 and tasks==[f'OM100-{i:03d}' for i in range(1,101)] and data['conditions']==CONDITIONS and data['samplesPerQuestion']==4,'Unexpected experiment dimensions')
    expected={(model,task,condition,sample) for model in models for task in tasks for condition in CONDITIONS for sample in range(1,5)}
    view=unique(data['attempts'],join_key);require(set(view)==expected and len(view)==9600,'Comparison schedule is not complete')
    attempts=[]
    for model in models:
        report=suite/('report-'+model);manifest=read(report/'report-file-manifest.json',inputs)
        descriptor=next(x for x in manifest['files'] if x['path']=='all_scheduled_attempts.json');source=report/'all_scheduled_attempts.json'
        require(sha_file(source)==descriptor['sha256'] and source.stat().st_size==descriptor['bytes'],'Scheduled export hash differs from report manifest')
        records=read(source,inputs);require(len(records)==1200,'Wrong phase export length');seen=set()
        for record in records:
            require(record['record_kind']=='phase_separated_verified_attempt','Pending or non-phase record')
            identity=record['generation_identity'];key=(model,identity['task_id'],identity['condition'],identity['sample_index']+1);require(key in view and key not in seen,'Unmatched or duplicate scheduled export');seen.add(key)
            row=view[key];gen=record['generation'];settings=gen['generation_settings'];m=row['metrics'];outcome=completion(row)
            require(row['attemptId']==record['attempt_id']==identity['attempt_id'],'Attempt relabeling')
            require(row['status']==record['verification_result']['validity_status'] and row['metricStatus']==record['verification_result']['metric_status'],'Projected status differs')
            require(gen['returned_model']==settings['model_snapshot']==model and settings['max_output_tokens']==cap and settings['reasoning_setting']=='high','Wrong recorded model/policy')
            require(record['generation_hash']==digest(gen),'Generation hash differs')
            measured=outcome['valid'] and row['metricStatus']=='available'
            if measured:
                for metric in ('T0','I0'):require(int(m[metric]['numerator_decimal'])>0 and int(m[metric]['denominator_decimal'])>0,'Invalid exact ratio')
                require(m['W_B']>0 and m['W_S']>0 and all(math.isfinite(m[x]) for x in ('log2_T0','log2_I0')),'Invalid measured sizes/logs')
            else:require(row['metricStatus']!='available','Invalid proof carries available metrics')
            usage=usage_fields(gen);cost=gen['cost_estimate'];require(cost['status']=='estimated' and Decimal(cost['total_usd'])==Decimal(row['cost']['total_usd']),'Missing or inconsistent recorded-response cost')
            require(usage['input_total_tokens']==cost['input_total_tokens'],'Normalized input-token total differs from cost evidence')
            attempts.append({'run':label,'output_token_cap':cap,'model':model,'task':row['task'],'condition':row['condition'],'sample':row['sample'],
                'attempt_id':row['attemptId'],'prompt_hash':gen['prompt_hash'],'status':row['status'],'metric_status':row['metricStatus'],**outcome,'measured':int(measured),
                'log2_T0':m['log2_T0'] if measured else None,'log2_I0':m['log2_I0'] if measured else None,'log2_W_B':math.log2(m['W_B']) if measured else None,
                'W_S':m['W_S'] if measured else None,'W_B':m['W_B'] if measured else None,'U_S_decimal':m['U_S_decimal'] if measured else None,'U_B_decimal':m['U_B_decimal'] if measured else None,
                'T0_numerator_decimal':m['T0']['numerator_decimal'] if measured else None,'T0_denominator_decimal':m['T0']['denominator_decimal'] if measured else None,
                'I0_numerator_decimal':m['I0']['numerator_decimal'] if measured else None,'I0_denominator_decimal':m['I0']['denominator_decimal'] if measured else None,
                'sourceNonWhitespaceCharacters':row['sourceLength']['sourceNonWhitespaceCharacters'],'sourceCharacters':row['sourceLength']['sourceCharacters'],'sourceUtf8Bytes':row['sourceLength']['sourceUtf8Bytes'],
                'rawCharacters':row['sourceLength']['rawCharacters'],'cost_usd':cost['total_usd'],**usage})
        require(len(seen)==1200,'Unmatched comparison rows')
    return sorted(attempts,key=join_key),data

def bootstrap_weights(n,replicates=REPLICATES,seed=SEED):
    draws=np.random.default_rng(seed).integers(0,n,size=(replicates,n),dtype=np.int16)
    weights=np.zeros((replicates,n),dtype=np.int16)
    for i,draw in enumerate(draws):weights[i]=np.bincount(draw,minlength=n)
    return draws,weights

def ci(values,*,support):
    finite=np.asarray(values,dtype=float);finite=finite[np.isfinite(finite)]
    if support<2 or len(finite)==0:return {'ci_low':None,'ci_high':None,'bootstrap_valid_replicates':len(finite),'ci_reason':'fewer_than_two_eligible_resampling_clusters' if support<2 else 'no_defined_bootstrap_estimates'}
    low,high=np.quantile(finite,[.025,.975],method='linear');return {'ci_low':float(low),'ci_high':float(high),'bootstrap_valid_replicates':len(finite),'ci_reason':None}

def ratio_estimate(numerator,denominator,weights):
    numerator=np.asarray(numerator,dtype=float);denominator=np.asarray(denominator,dtype=float);s=denominator.sum()
    point=float(numerator.sum()/s) if s else None;den=weights@denominator;num=weights@numerator
    boot=np.divide(num,den,out=np.full(num.shape,np.nan),where=den>0)
    return point,boot,int(np.count_nonzero(denominator))

def build_cells(rows):
    grouped=defaultdict(list)
    for row in rows:grouped[(row['run'],row['model'],row['condition'],row['task'])].append(row)
    cells={}
    for key,group in grouped.items():
        require(len(group)==4 and {x['sample'] for x in group}=={1,2,3,4},'Incomplete question cell')
        valid=[x for x in group if x['measured']];cell={'run':key[0],'model':key[1],'condition':key[2],'task':key[3],'scheduled':4,
          **{name:sum(x[name] for x in group) for name in ('valid','completed','truncated','empty','measured')},'pass_at_4':int(any(x['valid'] for x in group)),
          'cost_usd':str(sum((Decimal(x['cost_usd']) for x in group),Decimal(0))),'output_tokens':sum(x['output_tokens'] for x in group),'input_total_tokens':sum(x['input_total_tokens'] for x in group)}
        for metric in ('T0','I0','W_B'):
            product=Fraction(1)
            for x in valid:product*=Fraction(x['W_B']) if metric=='W_B' else Fraction(int(x[metric+'_numerator_decimal']),int(x[metric+'_denominator_decimal']))
            cell[metric+'_log2_geomean']=sum(x['log2_'+metric] for x in valid)/len(valid) if valid else None
            cell[metric+'_numerator_product_decimal']=str(product.numerator) if valid else None;cell[metric+'_denominator_product_decimal']=str(product.denominator) if valid else None
        cells[key]=cell
    return cells

def scope_arrays(cells,run,models,conditions,tasks):
    names=['scheduled','valid','completed','truncated','empty','measured','pass_at_4','cost_usd','output_tokens','input_total_tokens']
    return {name:np.array([sum(float(cells[run,m,c,q][name]) for m in models for c in conditions) for q in tasks]) for name in names}

OUTCOMES={
 'scheduled_success_rate':('valid','scheduled','proportion'),
 'empirical_pass_at_1':('valid','scheduled','proportion'),
 'completion_rate':('completed','scheduled','proportion'),
 'truncation_rate':('truncated','scheduled','proportion'),
 'empty_response_rate':('empty','scheduled','proportion'),
 'completed_acceptance_rate':('valid','completed','proportion'),
 'measured_proof_yield':('measured','scheduled','proportion'),
 'empirical_pass_at_4':('pass_at_4','cells','proportion'),
 'mean_recorded_response_cost_usd':('cost_usd','scheduled','USD_per_scheduled_attempt'),
 'mean_output_tokens':('output_tokens','scheduled','tokens_per_scheduled_attempt'),
 'mean_input_total_tokens':('input_total_tokens','scheduled','tokens_per_scheduled_attempt')}

def outcome_analysis(cells,models,tasks,weights,cluster_ids=None,cluster_type='question'):
    cluster_ids=cluster_ids or tasks
    estimates=[];differences=[]
    for model in [*models,'ALL_MODELS']:
        ms=models if model=='ALL_MODELS' else [model]
        for condition in [*CONDITIONS,'ALL_CONDITIONS']:
            cs=CONDITIONS if condition=='ALL_CONDITIONS' else [condition]
            arrays={run:scope_arrays(cells,run,ms,cs,tasks) for run in SPECS}
            for a in arrays.values():a['cells']=np.full(len(tasks),len(ms)*len(cs))
            for metric,(num,den,unit) in OUTCOMES.items():
                values={}
                for run in SPECS:
                    point,boot,support=ratio_estimate(arrays[run][num],arrays[run][den],weights)
                    independent_support=len({cluster_ids[i] for i,value in enumerate(arrays[run][den]) if value>0});values[run]=(point,boot,independent_support)
                    estimates.append({'run':run,'model':model,'condition':condition,'metric':metric,'unit':unit,'estimate':point,'numerator':float(arrays[run][num].sum()),'denominator':float(arrays[run][den].sum()),'question_clusters':support,'resampling_unit':cluster_type,'eligible_resampling_clusters':independent_support,**ci(boot,support=independent_support)})
                a,ab,sa=values['cap8192'];b,bb,sb=values['cap128000'];diff=bb-ab
                differences.append({'model':model,'condition':condition,'metric':metric,'unit':'proportion_difference' if unit=='proportion' else unit+'_difference',
                    'baseline_estimate':a,'rerun_estimate':b,'difference':b-a if a is not None and b is not None else None,'question_clusters':len(tasks),'resampling_unit':cluster_type,'eligible_resampling_clusters':min(sa,sb),**ci(diff,support=min(sa,sb))})
    return estimates,differences

def exact_compare_cell(a,b,metric):
    # Compare rerun/baseline geometric means without floating-point tie collapse.
    fa=Fraction(int(a[metric+'_numerator_product_decimal']),int(a[metric+'_denominator_product_decimal']))
    fb=Fraction(int(b[metric+'_numerator_product_decimal']),int(b[metric+'_denominator_product_decimal']))
    left=fb**a['measured'];right=fa**b['measured'];return (left>right)-(left<right)

def compression_analysis(cells,models,tasks,weights,cluster_ids=None,cluster_type='question'):
    cluster_ids=cluster_ids or tasks
    result=[];question_rows=[]
    for model in models:
        for condition in CONDITIONS:
            old=[cells['cap8192',model,condition,q] for q in tasks];new=[cells['cap128000',model,condition,q] for q in tasks]
            a_mask=np.array([x['measured']>0 for x in old]);b_mask=np.array([x['measured']>0 for x in new]);both=a_mask&b_mask
            for metric in ('T0','I0','W_B'):
                diffs=np.array([(b[metric+'_log2_geomean']-a[metric+'_log2_geomean']) if keep else 0 for a,b,keep in zip(old,new,both)])
                point,boot,support=ratio_estimate(diffs,both,weights);independent_support=len({cluster_ids[i] for i,keep in enumerate(both) if keep});interval=ci(boot,support=independent_support)
                comparison=Counter(exact_compare_cell(a,b,metric) for a,b,keep in zip(old,new,both) if keep)
                result.append({'model':model,'condition':condition,'metric':metric,'direction_preferred':'lower' if metric=='W_B' else 'higher',
                    'baseline_measurable_questions':int(a_mask.sum()),'rerun_measurable_questions':int(b_mask.sum()),'shared_measurable_questions':int(both.sum()),'resampling_unit':cluster_type,'eligible_resampling_clusters':independent_support,
                    'baseline_only_questions':int((a_mask&~b_mask).sum()),'rerun_only_questions':int((~a_mask&b_mask).sum()),'neither_questions':int((~a_mask&~b_mask).sum()),
                    'paired_mean_log2_change':point,'rerun_over_baseline_geometric_ratio':2**point if point is not None else None,
                    'log2_ci_low':interval['ci_low'],'log2_ci_high':interval['ci_high'],'ratio_ci_low':2**interval['ci_low'] if interval['ci_low'] is not None else None,'ratio_ci_high':2**interval['ci_high'] if interval['ci_high'] is not None else None,
                    'bootstrap_valid_replicates':interval['bootstrap_valid_replicates'],'ci_reason':interval['ci_reason'],
                    'rerun_metric_greater_questions':comparison[1],'equal_questions_exact':comparison[0],'rerun_metric_lower_questions':comparison[-1],
                    'selection_note':'Conditioned on >=1 successful measured proof in both runs for the same question; failures and newly solved questions are not assigned compression zero.'})
                for q,a,b,keep in zip(tasks,old,new,both):
                    question_rows.append({'model':model,'condition':condition,'task':q,'metric':metric,'baseline_measured_samples':a['measured'],'rerun_measured_samples':b['measured'],'paired_eligible':int(keep),
                        'baseline_log2_geomean':a[metric+'_log2_geomean'],'rerun_log2_geomean':b[metric+'_log2_geomean'],
                        'log2_change':b[metric+'_log2_geomean']-a[metric+'_log2_geomean'] if keep else None,'exact_comparison':exact_compare_cell(a,b,metric) if keep else None})
    return result,question_rows

def distributions(rows):
    fields=['input_total_tokens','output_tokens','reasoning_tokens','cache_read_tokens','cache_write_tokens','cost_usd','generation_elapsed_seconds','sourceNonWhitespaceCharacters']
    result=[]
    for run in SPECS:
        subset=[x for x in rows if x['run']==run]
        for model in [*dict.fromkeys(x['model'] for x in subset),'ALL_MODELS']:
            for condition in [*CONDITIONS,'ALL_CONDITIONS']:
                group=[x for x in subset if (model=='ALL_MODELS' or x['model']==model) and (condition=='ALL_CONDITIONS' or x['condition']==condition)]
                for outcome in ['ALL_OUTCOMES','accepted','completed_rejected','truncated','empty_completed']:
                    selected=group if outcome=='ALL_OUTCOMES' else [x for x in group if x['outcome_category']==outcome]
                    for field in fields:
                        values=np.array([float(x[field]) for x in selected if x[field] is not None]);quantiles=np.quantile(values,[0,.25,.5,.75,.9,.95,1],method='linear') if len(values) else [None]*7
                        result.append({'run':run,'model':model,'condition':condition,'outcome':outcome,'variable':field,'selected_attempts':len(selected),'observed':len(values),'missing':len(selected)-len(values),
                            'mean':float(values.mean()) if len(values) else None,**{k:float(v) if v is not None else None for k,v in zip(['min','q25','median','q75','q90','q95','max'],quantiles)}})
    return result

def smoke_descriptive(inputs):
    data=read(RUNS/'smoke-suite-high-20261012/comparison-data.json',inputs);rows=data['attempts'];models=[x['id'] for x in data['models']];tasks=sorted({x['task'] for x in rows});conditions=data['conditions']
    require(len(rows)==960 and len(models)==8 and len(tasks)==10 and len(conditions)==3,'Unexpected smoke dimensions')
    indexed=unique(rows,join_key);expected={(m,t,c,s) for m in models for t in tasks for c in conditions for s in range(1,5)};require(set(indexed)==expected,'Incomplete smoke schedule')
    results=[]
    for m in models:
        for c in conditions:
            subset=[x for x in rows if x['model']==m and x['condition']==c];valid=[x for x in subset if x['status']=='success'];measured=[x for x in valid if x['metricStatus']=='available']
            results.append({'model':m,'condition':c,'questions':10,'scheduled':40,'valid':len(valid),'measured':len(measured),'empirical_pass_at_1':len(valid)/40,'empirical_pass_at_4':sum(any(x['status']=='success' for x in subset if x['task']==t) for t in tasks)/10,
              'median_log2_T0_among_measured_attempts':float(np.median([x['metrics']['log2_T0'] for x in measured])) if measured else None,
              'status_counts_json':json.dumps(dict(Counter(x['status'] for x in subset)),sort_keys=True),'comparable_to_main_questions':False,
              'completion_rate':None,'token_cost_distribution':None,'missing_reason':'Legacy smoke comparison DTO lacks completion provenance and usage/cost fields; not imputed.'})
    return results,{'scheduled':960,'questions':tasks,'models':models,'conditions':conditions,'scope':'Separate descriptive smoke test on ten much simpler questions; no pooled inference with main cohorts.'}

def findings_text(summary,differences,compression):
    rows=[x for x in differences if x['condition']=='ALL_CONDITIONS' and x['model']!='ALL_MODELS' and x['metric']=='scheduled_success_rate']
    lines=['# Cross-run findings','', 'These are paired-question descriptions of two separate generation cohorts, not a causal estimate of increasing the token cap. Both use high effort; the rerun also changes OpenAI requested processing from default to priority/Fast and uses streaming transport. The same 100 questions, three prompts and four sample positions are matched, but sample positions do not identify a shared random seed or a shared generated response.','', '## Successful-proof yield across all three prompts','', '| Model | 8,192 yield | 128,000 yield | Difference, percentage points [95% family-block interval] |','|---|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['model']} | {100*r['baseline_estimate']:.2f}% | {100*r['rerun_estimate']:.2f}% | {100*r['difference']:+.2f} [{100*r['ci_low']:+.2f}, {100*r['ci_high']:+.2f}] |")
    lines+=['','## Completion and measurement','']
    for run,value in summary['cohorts'].items():
        lines.append(f"- {run}: {value['valid']:,}/{value['scheduled']:,} accepted ({100*value['valid']/value['scheduled']:.2f}% scheduled yield); {value['completed']:,} completed nonempty submissions; {value['truncated']:,} truncated responses; {value['empty']:,} empty responses. Empty and truncated counts overlap. Recorded-response token estimate: ${value['cost_usd']}; {value['unknown_original_charge_incidents']} separately disclosed original requests have unknown charges.")
    lines+=['','## Conditional compression comparison','','The compression CSV compares each model and prompt only on questions with at least one successful measured proof in both runs. It averages log2 metrics within question/run, then gives each overlapping question equal weight. The exponentiated change is the geometric ratio of those per-question geometric means. The different valid-sample counts and exclusion of failures make this a selected-outcome comparison. Exact rational comparisons determine ties; logs only quantify magnitude. Larger T0 is preferred; larger I0 means a larger proof-relative-to-statement ratio, not an unconditional quality improvement; smaller W_B means a shorter measured proof.','', '| Model, compression prompt | Shared measurable questions | T0 rerun/baseline geometric ratio [95% interval] |','|---|---:|---:|']
    for r in compression:
        if r['condition']=='mathematical_compression' and r['metric']=='T0':
            ratio=f"{r['rerun_over_baseline_geometric_ratio']:.3g}" if r['rerun_over_baseline_geometric_ratio'] is not None else 'unavailable';interval=f"[{r['ratio_ci_low']:.3g}, {r['ratio_ci_high']:.3g}]" if r['ratio_ci_low'] is not None else '[unavailable]';lines.append(f"| {r['model']} | {r['shared_measurable_questions']}/100 | {ratio} {interval} |")
    lines+=['','## Uncertainty and scope','','The table intervals use 10,000 percentile bootstrap replicates of the 19 problem-family blocks, sampled jointly across models, conditions and both runs with the same index matrix (seed 20261014). Each selected family carries all its questions; ratio-of-sums preserves the question-weighted estimand despite unequal family sizes. Companion files give the 100-question bootstrap as a sensitivity analysis, not a claim that related lemmas are independent. Neither approach treats 9,600 attempts as independent. The curated 100-question set is fixed and not a probability sample of mathematics; intervals describe sensitivity to the empirical question mix and do not establish population-wide performance. Conditional metrics can have an empty selected subset in a bootstrap replicate; their valid-replicate counts are exported and no zero is substituted. No multiplicity-adjusted confirmatory significance claims are made.','','Empirical pass@1 is the mean success indicator over the four samples and equals scheduled-proof yield. Empirical pass@4 is the fraction of questions with at least one accepted proof among those four samples. Pooled pass@4 across prompts averages question-by-model-by-prompt cells; it does not mean success across twelve attempts.','','Token and cost distributions are descriptive distributions over recorded attempts, with unknown fields left blank. Anthropic has no separately reported reasoning-token counter here; missing reasoning counts are not zero. OpenAI input counts include cached tokens, while Anthropic total input is normalized by adding separate cache-read/write counters. Reasoning is already included in billed output. Generation wall time is observational and affected by processing tier, concurrency and transport. All costs are recorded-response estimates, not all-request totals or invoices.','','The 960-attempt smoke test is reported separately on its ten simpler questions. Its legacy combined DTO lacks completion and usage/cost provenance; those fields are explicitly unavailable in the smoke CSV. It is not pooled with the main benchmark.','']
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=HERE/'results-v1');args=p.parse_args();output=args.output.resolve();require(output.parent==HERE.resolve() and not output.exists(),'Use a fresh direct child output directory')
    inputs={};all_rows=[];datasets={}
    for label,spec in SPECS.items():rows,data=normalize_suite(label,spec,inputs);all_rows+=rows;datasets[label]=data
    by_run={label:unique([x for x in all_rows if x['run']==label],join_key) for label in SPECS};require(set(by_run['cap8192'])==set(by_run['cap128000']),'Unmatched cross-run keys')
    require(not {x['attempt_id'] for x in by_run['cap8192'].values()}&{x['attempt_id'] for x in by_run['cap128000'].values()},'Generation attempt ID reused across runs')
    require(all(a['prompt_hash']==by_run['cap128000'][key]['prompt_hash'] for key,a in by_run['cap8192'].items()),'Prompt differs across paired slots')
    models=[x['id'] for x in datasets['cap8192']['models']];tasks=datasets['cap8192']['tasks'];require(models==[x['id'] for x in datasets['cap128000']['models']],'Model order/cohort differs')
    family_path=Path('/Users/ryanshin/Downloads/openai_math_100_candidates.jsonl');family_raw=family_path.read_bytes();inputs[str(family_path)]={'sha256':hashlib.sha256(family_raw).hexdigest(),'bytes':len(family_raw)}
    catalogue=[json.loads(line) for line in family_raw.decode().split('\n') if line.strip()];family=unique(catalogue,lambda x:x['id'])
    require(set(family)==set(tasks) and len({x['family'] for x in family.values()})==19,'Family metadata mismatch')
    require(Counter(x['selection_group'] for x in family.values())=={'algebra':25,'analysis':25,'combinatorics':25,'cs_probability':25},'Unexpected selection groups')
    for row in all_rows:row.update(family=family[row['task']]['family'],selection_group=family[row['task']]['selection_group'])
    output.mkdir();write_csv(output/'attempts_normalized.csv',all_rows)
    write_json(output/'normalization-ready.json',{'state':'normalized_inputs_validated','rows':19200,'paired_keys':9600,'prompt_hash_matches':9600,'attempt_ids_disjoint':True,'csv_sha256':sha_file(output/'attempts_normalized.csv'),'no_raw_proof_or_response_text_exported':True})
    print(json.dumps({'state':'normalized_inputs_ready','path':str(output/'attempts_normalized.csv')}),flush=True)
    draws,weights=bootstrap_weights(len(tasks));np.save(output/'bootstrap_question_indices.npy',draws,allow_pickle=False)
    families=sorted({x['family'] for x in family.values()});family_ids=[family[q]['family'] for q in tasks];family_draws,family_counts=bootstrap_weights(len(families));family_weights=family_counts[:,[families.index(f) for f in family_ids]]
    np.save(output/'bootstrap_family_indices.npy',family_draws,allow_pickle=False)
    write_json(output/'question_family_mapping.json',{q:{'family':family[q]['family'],'selection_group':family[q]['selection_group']} for q in tasks})
    cells=build_cells(all_rows);write_csv(output/'question_cells.csv',[cells[k] for k in sorted(cells)])
    estimates,differences=outcome_analysis(cells,models,tasks,weights);write_csv(output/'outcome_estimates.csv',estimates);write_csv(output/'cross_run_differences.csv',differences)
    family_estimates,family_differences=outcome_analysis(cells,models,tasks,family_weights,family_ids,'problem_family');write_csv(output/'outcome_estimates_family.csv',family_estimates);write_csv(output/'cross_run_differences_family.csv',family_differences)
    compression,paired_questions=compression_analysis(cells,models,tasks,weights);write_csv(output/'conditional_compression.csv',compression);write_csv(output/'conditional_compression_question_pairs.csv',paired_questions)
    family_compression,_=compression_analysis(cells,models,tasks,family_weights,family_ids,'problem_family');write_csv(output/'conditional_compression_family.csv',family_compression)
    write_csv(output/'token_cost_length_time_distributions.csv',distributions(all_rows))
    transitions=Counter((key[0],key[2],a['outcome_category'],by_run['cap128000'][key]['outcome_category']) for key,a in by_run['cap8192'].items())
    write_csv(output/'matched_attempt_transitions.csv',[{'model':m,'condition':c,'baseline_outcome':a,'rerun_outcome':b,'matched_slots':n} for (m,c,a,b),n in sorted(transitions.items())])
    smoke,smoke_scope=smoke_descriptive(inputs);write_csv(output/'smoke_descriptive.csv',smoke)
    cohorts={}
    for label in SPECS:
        rows=[x for x in all_rows if x['run']==label];cohorts[label]={'scheduled':len(rows),**{k:sum(x[k] for x in rows) for k in ('valid','completed','truncated','empty','measured')},'cost_usd':str(sum((Decimal(x['cost_usd']) for x in rows),Decimal(0))),
          'input_total_tokens':sum(x['input_total_tokens'] for x in rows),'output_tokens':sum(x['output_tokens'] for x in rows),'status_counts':dict(Counter(x['status'] for x in rows)),
          'delivered_tier_counts':dict(Counter(str(x['delivered_service_tier']) for x in rows)),'unknown_original_charge_incidents':len(datasets[label].get('transportCostDisclosure',{}).get('incidents',[]))}
    for path,description in inputs.items():require(sha_file(path)==description['sha256'],'Input mutated during analysis: '+path)
    methods={'bootstrap_replicates':REPLICATES,'seed':SEED,'unit':'19 problem-family blocks primary; 100 question clusters sensitivity; same resampling indices across runs/models/conditions','family_block_estimand':'Question-weighted ratio-of-sums with all questions retained in each selected family; uneven block sizes do not become equal-family weights','family_count':19,'question_count':100,'interval':'95% percentile; numpy.quantile linear','paired_join':['model','task','condition','sample'],'pairing_is_same_question_design_not_same_random_seed':True,'unmatched_keys':0,'attempt_ids_reused':0,'causal_token_cap_effect_claimed':False,'smoke_pooled_with_main':False,'unknown_fields_imputed_as_zero':False,
      'pass_at_1':'Average of four observed success indicators per question','pass_at_4':'Indicator of >=1 accepted proof among four samples for each question/model/condition; averages across selected cells',
      'conditional_compression':'Equal-question average log2 change over shared measurable questions; selected-outcome comparison; exact rational products for tie/win comparisons',
      'confidence_scope':'Problem-family and question-mix bootstrap on curated fixed dataset; not population-wide inference or independent-attempt confidence' ,'multiplicity':'Exploratory intervals, no confirmatory multiple-testing claims','python':platform.python_version(),'numpy':np.__version__,'script_sha256':sha_file(Path(__file__))}
    summary={'state':'analysis_complete','at_utc':datetime.now(timezone.utc).isoformat(),'cohorts':cohorts,'matched_slots':9600,'methods':methods,'smoke':smoke_scope,'model_calls':0,'lean_calls':0,'cloud_calls':0}
    write_json(output/'summary.json',summary);write_json(output/'input_hashes.json',inputs);write_json(output/'methods.json',methods);(output/'findings.md').write_text(findings_text(summary,family_differences,family_compression))
    files={p.name:sha_file(p) for p in output.iterdir() if p.is_file()};write_json(output/'output_manifest.json',{'files':files,'script_sha256':methods['script_sha256'],'input_count':len(inputs)})
    print(json.dumps({'state':'analysis_complete','output':str(output),'cohorts':cohorts}),flush=True)

if __name__=='__main__':main()
