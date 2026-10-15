"""Independent exact pairwise preference-score calculation; all questions/slots retained."""
from collections import defaultdict
from fractions import Fraction
from itertools import combinations
from pathlib import Path
import hashlib,json,math
BASE=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
P=BASE/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json'
d=json.loads(P.read_text());models=[m['id'] for m in d['models']];tasks=d['tasks'];condition='mathematical_compression'
g=defaultdict(list)
for r in d['attempts']:
 if r['condition']==condition:g[r['model'],r['task']].append(r)
def accepted(r):return r['status']=='success' and r['metricStatus']=='available'
def value(r):return Fraction(int(r['metrics']['T0']['numerator_decimal']),int(r['metrics']['T0']['denominator_decimal']))
def pref(a,b):
 va,vb=accepted(a),accepted(b)
 if va!=vb:return Fraction(int(va))
 if not va:return Fraction(1,2)
 aa,bb=value(a),value(b)
 return Fraction(1 if aa>bb else 0 if aa<bb else 1,1 if aa!=bb else 2)
per_task=defaultdict(list);pairrows=[];masses=defaultdict(lambda:defaultdict(int));t0_wb_concordance=defaultdict(int)
for a,b in combinations(models,2):
 for t in tasks:
  aa,bb=g[a,t],g[b,t];assert len(aa)==len(bb)==4
  p=sum((pref(x,y) for x in aa for y in bb),Fraction())/16
  pa=Fraction(sum(accepted(x) for x in aa),4);pb=Fraction(sum(accepted(x) for x in bb),4)
  both=[(x,y) for x in aa for y in bb if accepted(x) and accepted(y)]
  conditional=sum((pref(x,y) for x,y in both),Fraction())/len(both) if both else None
  reliability=Fraction(1,2)*(pa-pb)
  compression=pa*pb*(conditional-Fraction(1,2)) if both else Fraction()
  assert p==Fraction(1,2)+reliability+compression
  for model,op,score,rel,comp in [(a,b,p,reliability,compression),(b,a,1-p,-reliability,-compression)]:
   per_task[model,t].append({'opponent':op,'score':score,'reliability':rel,'compression':comp})
  for x in aa:
   for y in bb:
    category='both_valid' if accepted(x) and accepted(y) else 'both_failed' if not accepted(x) and not accepted(y) else 'one_valid'
    for m in [a,b]:masses[m][category]+=1
    if category=='both_valid':
     tv=(value(x)>value(y))-(value(x)<value(y));wx=x['metrics']['W_B'];wy=y['metrics']['W_B'];wv=(wy>wx)-(wy<wx)
     cat='both_tie' if tv==wv==0 else 'T0_tie_only' if tv==0 else 'WB_tie_only' if wv==0 else 'concordant' if tv==wv else 'discordant'
     t0_wb_concordance[cat]+=1
  pairrows.append({'a':a,'b':b,'task':t,'score_a':float(p),'reliability_term':float(reliability),'conditional_T0_term':float(compression),'both_valid_pairs':len(both),'both_valid_win_fraction_a':float(conditional) if conditional is not None else None})
results=[]
for m in models:
 rows=[z for t in tasks for z in per_task[m,t]]
 avg=lambda k:sum((r[k] for r in rows),Fraction())/len(rows)
 s,rel,comp=[avg(k) for k in ['score','reliability','compression']]
 results.append({'model':m,'points_percent':float(s*100),'exact_score':str(s),'baseline_percent':50,'reliability_contribution_pp':float(rel*100),'conditional_T0_contribution_pp':float(comp*100),'pair_mass_counts':dict(masses[m]),'per_model_denominator_pairs':11200})
assert sum(Fraction(r['exact_score']) for r in results)/8==Fraction(1,2)
result={'kind':'posthoc_joint_reliability_T0_preference_score_independent_check','condition':condition,'formula':'0.5 + 0.5*(pA-pB) + pA*pB*(conditional valid T0 preference -0.5), per task/opponent then equal mean','comparison_data_sha256':hashlib.sha256(P.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'models':results,'undirected_pair_task_rows':len(pairrows),'undirected_response_pairs':44800,'all_questions_neutral_from_universal_failure':[t for t in tasks if not any(accepted(r) for m in models for r in g[m,t])],'T0_vs_shorter_WB_on_both_valid_pairs':dict(t0_wb_concordance),'per_question_opponent_scores':pairrows,'interpretation':'All failed-versus-failed pairs earn0.5; 50 is cohort-neutral, not an absolute quality threshold. These16 pairings reuse4 samples and are not independent observations.'}
(OUT/'contest-audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='per_question_opponent_scores'},indent=2))
