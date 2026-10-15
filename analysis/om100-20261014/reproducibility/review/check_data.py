"""Independent read-only arithmetic/coverage audit. Writes only this review directory.
No provider, Lean, cloud, response-body, or chain-of-thought access.
"""
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from functools import cmp_to_key
from itertools import combinations
from math import comb, log2, prod
from pathlib import Path
import hashlib, json, statistics, sys

BASE = Path(__file__).resolve().parents[3]
REPO = BASE / 'outputs/lean-compression-eval'
SUITE = REPO / 'runs/om100-max-suite-high-20261014'
OUT = Path(__file__).resolve().parent
DATA = SUITE / 'comparison-data.json'
INDEX = REPO / 'data/prepared/openai_math_100_v1/index.json'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
d = json.loads(DATA.read_text()); qs=json.loads(INDEX.read_text())['questions']
models=[m['id'] for m in d['models']]; conditions=d['conditions']; tasks=d['tasks']
rows=d['attempts']; mm={m['id']:m for m in d['models']}; qm={q['task_id']:q for q in qs}
errors=[]
def check(ok, context):
    if not ok: errors.append(context)
def mean(x): return statistics.mean(x) if x else None
def quantiles(xs):
    x=sorted(xs)
    def q(f):
        if not x:return None
        i=(len(x)-1)*f;lo=int(i);hi=min(lo+1,len(x)-1)
        return x[lo]+(x[hi]-x[lo])*(i-lo)
    return {'n':len(x),'min':q(0),'p25':q(.25),'median':q(.5),'p75':q(.75),'p95':q(.95),'max':q(1)}
def integer(x):
    assert type(x) is int or isinstance(x,str) and x.isascii() and x.isdigit()
    y=int(x);assert y>=0;return y
def good(r):return r['status']=='success' and r['metricStatus']=='available'
def approx(x,y):return x is None and y is None or x is not None and y is not None and abs(x-y)<=1e-9

groups=defaultdict(list); identities=set();ids=set();sourcegroups=defaultdict(list)
for r in rows:
    key=(r['model'],r['task'],r['condition'],r['sample'])
    check(key not in identities, ['duplicate_schedule',key]);identities.add(key)
    check(r['attemptId'] not in ids,['duplicate_attempt_id',r['attemptId']]);ids.add(r['attemptId'])
    groups[key[:3]].append(r)
    check(r['model'] in models and r['task'] in tasks and r['condition'] in conditions and r['sample'] in range(1,5),['unknown_schedule',key])
    check((r['metricStatus']=='available')==good(r),['metric_on_invalid',key])
    s=r['sourceLength']
    check(s['status']==r['status'],['source_status_mismatch',key])
    check(not good(r) or s['available'] and not s['truncated'],['valid_source_conflict',key])
    if s['available']:sourcegroups[r['task'],s['sourceTextSha256']].append(r)
check(len(identities)==len(models)*len(tasks)*len(conditions)*4==9600,'schedule_count')
check(len(set(models))==8 and len(set(tasks))==100 and len(qm)==100,'cohort_uniqueness')
valid=[r for r in rows if good(r)];taskmetrics=defaultdict(list);cell={};maxlogerror=0
for r in valid:
    m=r['metrics'];ws=integer(m['W_S']);wb=integer(m['W_B']);us=integer(m['U_S_decimal']);ub=integer(m['U_B_decimal'])
    n=integer(m['T0']['numerator_decimal']);de=integer(m['T0']['denominator_decimal'])
    check(n==us+ub and de==ws+wb and ws>0 and wb>0,['T0_components',r['attemptId']])
    check(Fraction(integer(m['I0']['numerator_decimal']),integer(m['I0']['denominator_decimal']))==Fraction(wb,ws),['I0_components',r['attemptId']])
    exactlog=log2(n)-log2(de);e=abs(exactlog-m['log2_T0']);maxlogerror=max(maxlogerror,e)
    check(e<=1e-9 and approx(m['T0']['log2'],m['log2_T0']),['T0_log',r['attemptId']])
    taskmetrics[r['task']].append((r,ws,wb,us,ub,n,de))
for model in models:
 for task in tasks:
  for condition in conditions:
    rr=groups[model,task,condition];vv=[r for r in rr if good(r)]
    check(len(rr)==4 and {r['sample'] for r in rr}==set(range(1,5)),['cell_schedule',model,task,condition])
    values={k:[] for k in ['logT','logU','logW','logWB']};ratio=Fraction(1)
    for r in vv:
      m=r['metrics'];n=int(m['T0']['numerator_decimal']);den=int(m['T0']['denominator_decimal']);ratio*=Fraction(n,den)
      for k,v in [('logT',log2(n)-log2(den)),('logU',log2(n)),('logW',log2(den)),('logWB',log2(m['W_B']))]:values[k].append(v)
    z={'valid':len(vv),'first':int(next(r for r in rr if r['sample']==1)['status']=='success'),'pass4':int(bool(vv)),**{k:mean(v) for k,v in values.items()},'exact_product':ratio}
    cell[model,task,condition]=z;reported=mm[model]['perTask'][task][condition]
    for key,v in [('valid_count',z['valid']),('measurable_count',z['valid']),('first_attempt_success',z['first']),('any_sample_success',z['pass4'])]:check(reported[key]==v,['cell_count',model,task,condition,key])
    for key,k in [('mean_log2_T0','logT'),('mean_log2_U','logU'),('mean_log2_W','logW'),('mean_log2_W_B','logWB')]:check(approx(reported[key],z[k]),['cell_mean',model,task,condition,key])
    exact=reported.get('exact_geometric_T0')
    if vv:check(exact and Fraction(int(exact['numerator_product_decimal']),int(exact['denominator_product_decimal']))==ratio and exact['sample_count']==len(vv),['exact_geomean',model,task,condition])

per_model=[];paired=[]
for model in models:
 for c in conditions:
    cells=[cell[model,t,c] for t in tasks];rr=[r for r in rows if r['model']==model and r['condition']==c];vv=[r for r in rr if good(r)]
    z={'model':model,'condition':c,'scheduled':400,'accepted':len(vv),'scheduled_solve_rate':len(vv)/400,'first_attempt_rate':mean([x['first'] for x in cells]),'pass_at_k':{str(k):mean([1-(comb(4-x['valid'],k) if 4-x['valid']>=k else 0)/comb(4,k) for x in cells]) for k in range(1,5)},'covered_questions':sum(x['pass4'] for x in cells),'completed':sum(not r['sourceLength']['truncated'] for r in rr),'native_limit_truncated':sum(r['sourceLength']['truncated'] for r in rr),'statuses':dict(Counter(r['status'] for r in rr)),'successful_W_B':quantiles([r['metrics']['W_B'] for r in vv]),'successful_log2_T0':quantiles([r['metrics']['log2_T0'] for r in vv])}
    z['completed_acceptance_rate']=z['accepted']/z['completed']
    per_model.append(z)
    summary=mm[model]['conditions'][c]
    check(summary['valid_attempts']==len(vv) and summary['measurable_task_count']==z['covered_questions'],['summary_count',model,c])
 for control in ['standard','source_concision']:
    b='mathematical_compression';tt=[t for t in tasks if cell[model,t,b]['valid'] and cell[model,t,control]['valid']]
    def delta(k):return mean([cell[model,t,b][k]-cell[model,t,control][k] for t in tt])
    dt,du,dw,dwb=[delta(k) for k in ['logT','logU','logW','logWB']]
    check(approx(dt,du-dw),['decomposition',model,control])
    reported_contrast=mm[model]['contrasts']['compression_vs_'+('standard' if control=='standard' else 'concision')]
    check(reported_contrast['tasks']==len(tt) and approx(reported_contrast['T0']['estimate'],dt) and approx(reported_contrast['WB']['estimate'],dwb),['reported_contrast',model,control])
    paired.append({'model':model,'contrast':'compression_vs_'+('standard' if control=='standard' else 'concision'),'tasks':len(tt),'task_ids':tt,'families':len({qm[t]['family_id'] for t in tt}),'domains':dict(Counter(qm[t]['domain'] for t in tt)),'scheduled_solve_rate_difference':mean([(cell[model,t,b]['valid']-cell[model,t,control]['valid'])/4 for t in tasks]),'log2_T0_difference':dt,'T0_ratio':2**dt,'log2_U_difference':du,'log2_W_difference':dw,'W_B_ratio':2**dwb,'compression_only_tasks':sum(bool(cell[model,t,b]['valid']) and not cell[model,t,control]['valid'] for t in tasks),'control_only_tasks':sum(bool(cell[model,t,control]['valid']) and not cell[model,t,b]['valid'] for t in tasks)})

signature=[];taskdetail=[]
for t,rr in taskmetrics.items():
 sig={(a[1],a[3]) for a in rr}
 if len(sig)!=1:signature.append({'task':t,'distinct_signatures':len(sig)})
 values=[Fraction(x[5],x[6]) for x in rr];usfrac=[x[3]/x[5] for x in rr];wfrac=[x[1]/x[6] for x in rr]
 taskdetail.append({'task':t,'family':qm[t]['family_id'],'domain':qm[t]['domain'],'valid_attempts':len(rr),'models':len({x[0]['model'] for x in rr}),'distinct_T0':len(set(values)),'distinct_total_U':len({x[5] for x in rr}),'distinct_W_B':len({x[2] for x in rr}),'minimum_W_B':min(x[2] for x in rr),'log2_T0_range':max(x[0]['metrics']['log2_T0'] for x in rr)-min(x[0]['metrics']['log2_T0'] for x in rr),'statement_expansion_fraction':quantiles(usfrac),'statement_source_fraction':quantiles(wfrac)})

exact_duplicate_groups=[];discordant=[]
for (task,h),rr in sourcegroups.items():
 vv=[r for r in rr if good(r)]
 if len({r['model'] for r in vv})>=2:
  exact_duplicate_groups.append({'task':task,'source_sha256':h,'attempts':len(vv),'models':sorted({r['model'] for r in vv}),'conditions':sorted({r['condition'] for r in vv}),'distinct_metrics':len({json.dumps(r['metrics'],sort_keys=True) for r in vv})})
  check(exact_duplicate_groups[-1]['distinct_metrics']==1,['identical_source_metric_difference',task,h])
 if vv and any(not good(r) for r in rr):discordant.append({'task':task,'source_sha256':h,'counts':dict(Counter(r['status'] for r in rr)),'attempt_ids':[r['attemptId'] for r in rr]})

coverage=[];pairwise=[]
for c in conditions:
 for gate in [0,25,50,75,100]:
    cohort=[m for m in models if sum(cell[m,t,c]['valid']>0 for t in tasks)>=gate]
    shared=[t for t in tasks if cohort and all(cell[m,t,c]['valid'] for m in cohort)]
    coverage.append({'condition':c,'coverage_gate':gate,'cohort':cohort,'shared_tasks':shared,'shared_count':len(shared),'shared_families':len({qm[t]['family_id'] for t in shared})})
 for a,b in combinations(models,2):
    tt=[t for t in tasks if cell[a,t,c]['valid'] and cell[b,t,c]['valid']]
    pairwise.append({'condition':c,'a':a,'b':b,'shared_count':len(tt),'families':len({qm[t]['family_id'] for t in tt})})

extreme=lambda r:{'model':r['model'],'task':r['task'],'condition':r['condition'],'sample':r['sample'],'attempt_id':r['attemptId'],'source_sha256':r['sourceLength']['sourceTextSha256'],'metrics':r['metrics']}
sourcehashes={str(p.relative_to(REPO)):sha(p) for p in [DATA,INDEX,REPO/'docs/evaluation-plan.md',REPO/'src/lean_compression/metrics.py',REPO/'src/lean_compression/analysis.py',REPO/'reporting/source_lengths.py',REPO/'reporting/comparison.html']}
for s in d['sources']:
 p=SUITE/s['path'];sourcehashes[str(p.relative_to(REPO))]=sha(p);check(sha(p)==s['sha256'],['source_analysis_hash',s['model']])
summary={'schema_version':1,'input_hashes':sourcehashes,'audit_script_sha256':sha(Path(__file__)),'scope':'All comparison rows and aggregate cells; exact stored metric arithmetic. No graph re-extraction, raw response bodies or private reasoning read.','errors':errors,'row_count':len(rows),'unique_schedule_count':len(identities),'unique_attempt_ids':len(ids),'accepted':len(valid),'metric_missing_valid':sum(r['status']=='success' and not good(r) for r in rows),'max_log2_rounding_difference':maxlogerror,'statuses':dict(Counter(r['status'] for r in rows)),'families':dict(Counter(q['family_id'] for q in qs)),'domain_families':{c:len({q['family_id'] for q in qs if q['domain']==c}) for c in sorted({q['domain'] for q in qs})},'fixed_statement_mismatches':signature,'statement_expansion_fraction':quantiles([a[3]/a[5] for rr in taskmetrics.values() for a in rr]),'statement_source_fraction':quantiles([a[1]/a[6] for rr in taskmetrics.values() for a in rr]),'statement_expansion_gt_90pct':sum(a[3]/a[5]>.9 for rr in taskmetrics.values() for a in rr),'zero_U_B':sum(int(r['metrics']['U_B_decimal'])==0 for r in valid),'W_B_at_most_5':sum(r['metrics']['W_B']<=5 for r in valid),'all_models_all_arms_unsolved_tasks':[t for t in tasks if not taskmetrics[t]],'constant_T0_tasks_at_least_2_valid':[x['task'] for x in taskdetail if x['valid_attempts']>=2 and x['distinct_T0']==1],'constant_total_U_tasks_at_least_2_valid':[x['task'] for x in taskdetail if x['valid_attempts']>=2 and x['distinct_total_U']==1],'cross_model_identical_successful_source_groups':len(exact_duplicate_groups),'cross_model_identical_successful_source_attempts':sum(x['attempts'] for x in exact_duplicate_groups),'same_source_success_failure_discordances':discordant,'recorded_api_estimate_usd':str(sum(Decimal(r['cost']['total_usd']) for r in rows)),'all_rows_priced':all(r['cost']['status']=='estimated' for r in rows),'top_T0_examples':[extreme(r) for r in sorted(valid,key=lambda r:r['metrics']['log2_T0'],reverse=True)[:5]],'shortest_W_B_examples':[extreme(r) for r in sorted(valid,key=lambda r:r['metrics']['W_B'])[:5]],'per_model_condition':per_model,'prompt_contrasts':paired,'coverage_sensitivity':coverage,'pairwise_coverage':pairwise,'task_diagnostics':taskdetail,'duplicate_source_groups':exact_duplicate_groups}
(OUT/'data-audit.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:summary[k] for k in ['errors','row_count','accepted','metric_missing_valid','max_log2_rounding_difference','statuses','fixed_statement_mismatches','statement_expansion_fraction','statement_source_fraction','statement_expansion_gt_90pct','zero_U_B','W_B_at_most_5','all_models_all_arms_unsolved_tasks','constant_T0_tasks_at_least_2_valid','constant_total_U_tasks_at_least_2_valid','cross_model_identical_successful_source_groups','cross_model_identical_successful_source_attempts','recorded_api_estimate_usd']},indent=2))

if errors: raise SystemExit("Audit failed; see data-audit.json")
