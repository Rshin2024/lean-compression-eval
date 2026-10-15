"""Read-only derived diagnostics; no evaluator/provider calls."""
from pathlib import Path
from collections import defaultdict
from statistics import mean,median
from fractions import Fraction
import math,json,hashlib,itertools
ROOT=Path('/Users/ryanshin/Documents/Codex/2026-10-11/bas')
OUT=ROOT/'work/paper-analysis-20261014/methods'
def corr(x,y):
 mx,my=mean(x),mean(y);a=[v-mx for v in x];b=[v-my for v in y];den=math.sqrt(sum(v*v for v in a)*sum(v*v for v in b));return sum(u*v for u,v in zip(a,b))/den if den else None
def q(x,p):
 x=sorted(x);v=(len(x)-1)*p;lo=int(v);hi=min(lo+1,len(x)-1);return x[lo]*(1-v+lo)+x[hi]*(v-lo)
def stats(x):return {'min':min(x),'q25':q(x,.25),'median':median(x),'q75':q(x,.75),'max':max(x)}
def audit(name):
 path=ROOT/'outputs/lean-compression-eval/runs'/name/'comparison-data.json';d=json.loads(path.read_text());rows=[x for x in d['attempts'] if x['status']=='success' and x['metricStatus']=='available'];groups=defaultdict(list)
 for r in rows:groups[r['task']].append(r)
 taskrows=[];sx=[];sy=[];lu=[];groupmeans=[];fractions=[];concordant=discordant=ties=0
 for task,rr in groups.items():
  ws={r['metrics']['W_S'] for r in rr};us={r['metrics']['U_S_decimal'] for r in rr};u=[int(r['metrics']['U_S_decimal'])+int(r['metrics']['U_B_decimal']) for r in rr];lw=[math.log2(r['metrics']['W_S']+r['metrics']['W_B']) for r in rr];lt=[math.log2(z)-v for z,v in zip(u,lw)];logs=[math.log2(z) for z in u];shares=[float(Fraction(int(r['metrics']['U_S_decimal']),z)) for r,z in zip(rr,u)];fractions+=shares
  sx.extend(v-mean(lw) for v in lw);sy.extend(v-mean(lt) for v in lt);lu.extend(logs);groupmeans.extend([mean(logs)]*len(logs))
  for a,b in itertools.combinations(rr,2):
   am,bm=a['metrics'],b['metrics'];au=int(am['U_S_decimal'])+int(am['U_B_decimal']);bu=int(bm['U_S_decimal'])+int(bm['U_B_decimal']);aw=am['W_S']+am['W_B'];bw=bm['W_S']+bm['W_B'];delta=au*bw-bu*aw;short=bw-aw
   if delta==0 or short==0:ties+=1
   elif delta*short>0:concordant+=1
   else:discordant+=1
  taskrows.append({'task':task,'valid_measurable':len(rr),'W_S_distinct':len(ws),'U_S_distinct':len(us),'U_total_distinct':len(set(u)),'U_range_at_most_1pct':100*max(u)<=101*min(u),'U_range_at_most_10pct':10*max(u)<=11*min(u),'U_range_at_most_2x':max(u)<=2*min(u),'log2_U_range':max(logs)-min(logs),'statement_fraction_median':median(shares),'log2_U_geomean':mean(logs)})
 denom=sum((x-mean(lu))**2 for x in lu);between=sum((x-mean(lu))**2 for x in groupmeans)
 return {'source':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'scheduled':len(d['attempts']),'measurable':len(rows),'tasks_with_measurement':len(groups),'W_S_invariant_all_tasks':all(x['W_S_distinct']==1 for x in taskrows),'U_S_invariant_all_tasks':all(x['U_S_distinct']==1 for x in taskrows),'tasks_at_least2_valid':sum(x['valid_measurable']>=2 for x in taskrows),'tasks_total_numerator_invariant_at_least2':sum(x['valid_measurable']>=2 and x['U_total_distinct']==1 for x in taskrows),'statement_fraction_US_over_total':stats(fractions),'count_statement_more_than_half':sum(x>.5 for x in fractions),'count_statement_more_than_10pct':sum(x>.1 for x in fractions),'tasks_total_U_range_at_most_1pct':sum(x['valid_measurable']>=2 and x['U_range_at_most_1pct'] for x in taskrows),'tasks_total_U_range_at_most_10pct':sum(x['valid_measurable']>=2 and x['U_range_at_most_10pct'] for x in taskrows),'tasks_total_U_range_at_most_2x':sum(x['valid_measurable']>=2 and x['U_range_at_most_2x'] for x in taskrows),'within_question_log2_U_ranges':stats([x['log2_U_range'] for x in taskrows if x['valid_measurable']>=2]),'between_question_fraction_of_observed_log2_U_variation':between/denom,'within_question_pearson_log2_T0_vs_log2_total_source':corr(sx,sy),'within_question_all_valid_pair_T0_order_vs_shorter_source':{'concordant':concordant,'discordant':discordant,'ties_in_either':ties,'concordant_fraction_excluding_ties':concordant/(concordant+discordant)},'per_question':sorted(taskrows,key=lambda x:x['task'])}
x={'numerical_note':'Integer invariance and range threshold comparisons are exact. log2 ranges use floating display arithmetic and may round an extremely small nonzero difference to zero. Variance and correlations are descriptive, not significance tests.','scope':'Exploratory metric-mechanism audit of all accepted measurable records; equal attempt weighting except per-question diagnostics. No causal interpretation. Numeric metrics are read from final DTOs; exact integer products determine pair-order signs. No raw proof source exported.','cohorts':{n:audit(n) for n in ['smoke-suite-high-20261012','om100-suite-high-20261013','om100-max-suite-high-20261014']}}
(OUT/'metric-mechanism-audit.json').write_text(json.dumps(x,indent=2)+'\n')
print(json.dumps({k:{a:b for a,b in v.items() if a!='per_question'} for k,v in x['cohorts'].items()},indent=2))
