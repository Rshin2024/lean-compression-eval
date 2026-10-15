"""Independent verification of primary results, without importing its analysis code."""
from pathlib import Path
from collections import defaultdict,Counter
from fractions import Fraction
from functools import cmp_to_key
import hashlib,json,math,statistics
import numpy as np
from scipy.stats import t as student
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
RESULTS=OUT.parent/'main/results';P=ROOT/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json'
D=json.loads(P.read_text());rows=D['attempts'];tasks=sorted(D['tasks']);models=[m['id'] for m in D['models']]
index=json.loads((ROOT/'outputs/lean-compression-eval/data/prepared/openai_math_100_v1/index.json').read_text());family={q['task_id']:q['family_id'] for q in index['questions']}
meta={r['id']:r for r in map(json.loads,Path('/Users/ryanshin/Downloads/openai_math_100_candidates.jsonl').read_text().splitlines())}
assert all(str(meta[t]['family'])==family[t] for t in tasks)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
inputs={};errors=[];counts=Counter()
def load(name):
 p=RESULTS/(name+'.json');inputs[str(p)]=sha(p);return json.loads(p.read_text())
def close(x,y,key,tol=1e-9):
 counts[key]+=1
 if not (x is None and y is None) and (x is None or y is None or abs(x-y)>tol):errors.append({'kind':key,'expected':x,'actual':y})
g=defaultdict(list)
for r in rows:g[r['model'],r['condition'],r['task']].append(r)
def good(r):return r['status']=='success'
def logt(r):
 m=r['metrics'];return math.log2(int(m['U_S_decimal'])+int(m['U_B_decimal']))-math.log2(m['W_S']+m['W_B'])
def avg(xs):return statistics.mean(xs) if xs else None
# Reproduce family resampling with family sums/counts, not the parent's task-weight matrix.
rng=np.random.default_rng(20261014)
rng.multinomial(100,np.full(100,.01),size=10000)
family_levels=sorted(set(family.values()))
family_counts=rng.multinomial(len(family_levels),np.full(len(family_levels),1/len(family_levels)),size=10000)
def check_boot(y,row):
    y=np.asarray(y,dtype=float)
    sums=np.array([sum(y[i] for i,t in enumerate(tasks) if family[t]==f and np.isfinite(y[i])) for f in family_levels])
    ns=np.array([sum(family[t]==f and np.isfinite(y[i]) for i,t in enumerate(tasks)) for f in family_levels])
    den=family_counts@ns;vv=(family_counts@sums)[den>0]/den[den>0]
    lo,hi=np.quantile(vv,[.025,.975]);close(float(lo),row['ci_family_low'],'family_boot_CI');close(float(hi),row['ci_family_high'],'family_boot_CI');close(int((den==0).sum()),row['empty_family_replicates'],'family_boot_empty')
for r in load('rates'):
 if r['condition']=='all':rr=[x for x in rows if x['model']==r['model']]
 else:rr=[x for x in rows if x['model']==r['model'] and x['condition']==r['condition']]
 by=defaultdict(list)
 for x in rr:by[x['task']].append(x)
 def calc(xs):
  if r['measure']=='valid':return sum(good(x) for x in xs)/len(xs)
  if r['measure']=='completed':return sum(x['sourceLength']['available'] and not x['sourceLength']['truncated'] for x in xs)/len(xs)
  if r['measure']=='truncated':return sum(x['sourceLength']['truncated'] for x in xs)/len(xs)
  if r['measure']=='pass4':return int(any(good(x) for x in xs))
  if r['measure']=='accepted_given_complete':
   n=sum(x['sourceLength']['available'] and not x['sourceLength']['truncated'] for x in xs)
   return sum(good(x) for x in xs)/n if n else None
 vv=[calc(xs) for xs in by.values()];vv=[x for x in vv if x is not None];close(avg(vv),r['mean'],'rate_mean')
 if r['condition']=='standard' and r['measure']=='valid':check_boot([calc(by[t]) for t in tasks],r)
 close(None,r.get('p_family'),'no_zero_null_rate_test')
for r in load('prompt_effects'):
 yy=[];bytask={}
 for t in tasks:
  a=g[r['model'],r['condition_a'],t];b=g[r['model'],r['condition_b'],t]
  if r['outcome']=='valid_yield':
   bytask[t]=(sum(map(good,a))-sum(map(good,b)))/4;yy.append(bytask[t]);continue
  a=[x for x in a if good(x)];b=[x for x in b if good(x)]
  if not a or not b:continue
  def measure(x):
   m=x['metrics'];k=r['outcome']
   return logt(x) if k=='log2_T0' else math.log2(m['W_B']/m['W_S']) if k=='log2_I0' else math.log2(m['W_B']) if k=='log2_W_B' else math.log2(int(m['U_S_decimal'])+int(m['U_B_decimal'])) if k=='log2_total_U' else math.log2(m['W_S']+m['W_B'])
  bytask[t]=avg(list(map(measure,a)))-avg(list(map(measure,b)));yy.append(bytask[t])
 close(avg(yy),r['mean'],'prompt_mean');close(len(yy),r['n_questions'],'prompt_support')
 if r['outcome'] in ['valid_yield','log2_T0']:check_boot([bytask.get(t,float('nan')) for t in tasks],r)
 represented=sorted({family[t] for t in bytask});mu=avg(yy);n=len(yy);G=len(represented)
 sums=[sum(value-mu for task,value in bytask.items() if family[task]==f) for f in represented]
 se=math.sqrt(G/(G-1)*sum(z*z for z in sums)/(n*n)) if G>1 else None
 pvalue=float(2*student.sf(abs(mu/se),G-1)) if G>=5 and se and se>0 else 1. if G>=5 and se==0 and mu==0 else None
 close(pvalue,r['p_family'],'prompt_CR1_p')

contest={r['model']:r for r in json.loads((OUT/'contest-audit.json').read_text())['models']}
for r in load('joint_contest'):
 if r['condition']=='mathematical_compression' and r['metric']=='T0':
  expected=contest[r['model']]
  close(expected['points_percent']/100,r['mean'],'contest')
  close(expected['reliability_contribution_pp']/100,r['reliability'],'contest_reliability')
  close(expected['conditional_T0_contribution_pp']/100,r['compression'],'contest_compression')
# Exact ordering independently: rank = 1 + strictly-better count + half(other exact ties).
rank_input=load('conditional_ranks_by_question');cache={}
for r in rank_input:
 k=(r['condition'],r['coverage_gate'],r['metric'],r['task'])
 if k not in cache:
  chosen=[m for m in models if sum(any(good(x) for x in g[m,r['condition'],t]) for t in tasks)>=r['coverage_gate']];vv={}
  for m in chosen:
   rr=[x for x in g[m,r['condition'],r['task']] if good(x)];p=Fraction(1)
   for x in rr:
    z=x['metrics'];p*=Fraction(int(z['U_S_decimal'])+int(z['U_B_decimal']),z['W_S']+z['W_B']) if r['metric']=='T0' else Fraction(z['W_B'],z['W_S'] if r['metric']=='I0' else 1)
   vv[m]=(p,len(rr))
  cache[k]=vv
 vv=cache[k];p,n=vv[r['model']];better=ties=0
 for m,(q,nn) in vv.items():
  if m==r['model']:continue
  a,b=p**nn,q**n;ties+=int(a==b);better+=int(b<a if r['metric']=='W_B' else b>a)
 close(1+better+ties/2,r['rank'],'exact_rank')
# Within-cell slope: compute centered products directly, no dataframe implementation.
for r in load('source_length_associations'):
 gg=defaultdict(list)
 for x in rows:
  s=x['sourceLength']
  if not s['available'] or not s['sourceNonWhitespaceCharacters'] or r['condition']!='all' and x['condition']!=r['condition']:continue
  if r['population']!='include_partial' and s['truncated']:continue
  if r['population']=='completed_excluding_format' and x['status']=='parse_failure':continue
  gg[x['model'],x['condition'],x['task']].append((math.log2(s['sourceNonWhitespaceCharacters']),int(good(x))))
 numer=denom=0
 for pairs in gg.values():
  xm=avg([x for x,y in pairs]);ym=avg([y for x,y in pairs]);numer+=sum((x-xm)*(y-ym) for x,y in pairs);denom+=sum((x-xm)**2 for x,y in pairs)
 close(numer/denom,r['slope_probability_per_doubling'],'fixed_cell_slope')
# Split regression: explicit dummy design and hand-computed CR1 sandwich, no statsmodels/patsy.
split_rows=load('split_sample_associations');split_details=[]
for r in split_rows:
 first=[1,2] if r['split']=='forward' else [3,4];last=[3,4] if r['split']=='forward' else [1,2]
 ss=[]
 for (m,c,t),rr in sorted(g.items()):
  early=[x for x in rr if x['sample'] in first and good(x)]
  if not early:continue
  value=avg([logt(x) if r['metric']=='log2_T0' else math.log2(x['metrics']['W_B']) for x in early]);y=sum(good(x) for x in rr if x['sample'] in last)/2
  ss.append((m,c,t,value,y))
 levels=sorted({(m,c) for m,c,t,x,y in ss});tl=sorted({t for m,c,t,x,y in ss})
 X=np.array([[1.,x]+[float(t==tt) for tt in tl[1:]]+[float((m,c)==mc) for mc in levels[1:]] for m,c,t,x,y in ss]);Y=np.array([y for m,c,t,x,y in ss]);F=np.array([family[t] for m,c,t,x,y in ss]);pinv=np.linalg.pinv(X);beta=pinv@Y;resid=Y-X@beta;bread=pinv@pinv.T
 meat=np.zeros((X.shape[1],X.shape[1]));fs=sorted(set(F))
 for f in fs:
  score=X[F==f].T@resid[F==f];meat+=np.outer(score,score)
 cov=bread@meat@bread*len(fs)/(len(fs)-1)*(len(Y)-1)/(len(Y)-X.shape[1]);se=math.sqrt(cov[1,1]);delta=student.ppf(.975,len(fs)-1)*se
 close(float(beta[1]),r['coefficient_probability_per_bit'],'split_coef',1e-8);close(float(beta[1]-delta),r['ci_family_low'],'split_CI',1e-8);close(float(beta[1]+delta),r['ci_family_high'],'split_CI',1e-8)
 close(len(ss),r['groups'],'split_sample_count');split_details.append({'split':r['split'],'metric':r['metric'],'coefficient':float(beta[1]),'se':se,'design_rank':int(np.linalg.matrix_rank(X))})
# Holm adjustment retains missing/uncomputable tests as planned comparisons with p=1.
for name,groupkey in [('prompt_effects','outcome'),('model_yield_differences','condition'),('joint_contest_differences',None),('split_sample_associations',None)]:
    rr=load(name);groups=defaultdict(list)
    for i,row in enumerate(rr):groups[row.get(groupkey,'all')].append(i)
    for indices in groups.values():
        ordered=sorted(indices,key=lambda i:rr[i]['p_family'] if rr[i]['p_family'] is not None else 1.)
        scaled=[min(1.,(len(indices)-j)*(rr[i]['p_family'] if rr[i]['p_family'] is not None else 1.)) for j,i in enumerate(ordered)]
        for j,i in enumerate(ordered):close(None if rr[i]['p_family'] is None else max(scaled[:j+1]),rr[i]['p_holm'],'holm_adjustment')
assert all(sha(Path(p))==s for p,s in inputs.items()),'Results changed during review'
result={'status':'pass' if not errors else 'fail','comparison_data_sha256':sha(P),'analysis_source_sha256':sha(OUT.parent/'main/analyze_primary.py'),'review_source_sha256':sha(Path(__file__)),'input_result_hashes':inputs,'checks':dict(counts),'errors':errors,'split_independent_results':split_details,'scope':'Point estimates, all exact per-question ranks, raw-vs-adapted units, manual CR1 split regression. Family-bootstrap CIs independently checked for standard yield and all yield/T0 prompt contrasts using family-level sums/counts. Question-bootstrap CIs and other bootstrap endpoints reviewed by source, not independently replayed.'}
(OUT/'primary-results-review.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ['input_result_hashes','split_independent_results']},indent=2))
if errors:raise SystemExit(1)
