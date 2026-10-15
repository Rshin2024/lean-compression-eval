from pathlib import Path
from functools import lru_cache
import json,hashlib,math
ROOT=Path('/Users/ryanshin/Documents/Codex/2026-10-11/bas');REPO=ROOT/'outputs/lean-compression-eval';OUT=ROOT/'work/paper-analysis-20261014/methods';dto=json.loads((REPO/'runs/om100-max-suite-high-20261014/comparison-data.json').read_text());result=[]
for tid in ['OM100-065','OM100-100']:
 rows=[r for r in dto['attempts'] if r['task']==tid and r['metricStatus']=='available'];pair=[]
 for direction,sel in [('min',min),('max',max)]:
  r=sel(rows,key=lambda x:x['metrics']['log2_T0']);p=REPO/'runs'/('om100-max-'+r['model']+'-high-20261014')/'evaluations/v2_threads1'/(r['attemptId']+'.json');rec=json.loads(p.read_text());v=rec['verification_result'];ext=v['extraction'];nodes={n['name']:n for n in ext['nodes']};root=nodes[ext['root']]
  @lru_cache(None)
  def total(n):
   x=nodes[n]
   if x['primitive']:return 1
   return sum(int(c)*total(dep) for h in [x['signature'],x['body'] or {}] for dep,c in h.items())
  us=sum(int(c)*total(dep) for dep,c in root['signature'].items());ub=sum(int(c)*total(dep) for dep,c in root['body'].items());assert str(us)==r['metrics']['U_S_decimal'] and str(ub)==r['metrics']['U_B_decimal']
  contributions=[]
  for dep,c in root['body'].items():
   n=int(c)*total(dep);contributions.append({'name':dep,'owner_module':nodes[dep]['owner_module'],'multiplicity':c,'expanded_per_reference':str(total(dep)),'body_contribution':str(n),'body_fraction':n/ub})
  contributions.sort(key=lambda x:int(x['body_contribution']),reverse=True)
  proof=v['adapted_response'];proofpath=OUT/(tid+'-'+direction+'-submitted.lean');proofpath.write_text(proof)
  item={k:r[k] for k in ['task','model','condition','sample','attemptId','metrics']};item.update(direction=direction,record_path=str(p),record_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),submitted_proof_path=str(proofpath),submitted_proof_sha256=hashlib.sha256(proof.encode()).hexdigest(),validation_status=v['validity_status'],kernel_replay=v['validation'].get('kernel_replay'),node_count=len(nodes),top_direct_body_dependencies=contributions[:12],fresh_python_weighted_recomputation_matches=True)
  result.append(item);pair.append(item)
  print(json.dumps({'task':tid,'direction':direction,'attempt':r['attemptId'],'top_body_dependencies':contributions[:5]},indent=2))
 result[-1]['vs_min_same_task']={'delta_log2_T0':pair[1]['metrics']['log2_T0']-pair[0]['metrics']['log2_T0'],'delta_log2_U':math.log2(int(pair[1]['metrics']['T0']['numerator_decimal']))-math.log2(int(pair[0]['metrics']['T0']['numerator_decimal'])),'minus_delta_log2_W':math.log2(int(pair[0]['metrics']['T0']['denominator_decimal']))-math.log2(int(pair[1]['metrics']['T0']['denominator_decimal']))}
(OUT/'outlier-audit.json').write_text(json.dumps({'scope':'Post hoc all-condition all-model min/max T0 in provider-maximum cohort for two named questions; only saved submitted text and recorded dependency graphs examined. Not blinded; no Lean reruns; no outputs removed.','records':result},indent=2)+'\n')
