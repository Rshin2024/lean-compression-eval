"""Select saved same-question/model/condition extrema without reading response text."""
from pathlib import Path
from collections import defaultdict
import json, math, hashlib
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
P=ROOT/'outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json'
d=json.loads(P.read_text());g=defaultdict(list)
for r in d['attempts']:
 if r['status']=='success':g[r['model'],r['task'],r['condition']].append(r)
ranked=[]
for key,rr in g.items():
 if len(rr)>1:
  lo=min(rr,key=lambda r:r['metrics']['log2_T0']);hi=max(rr,key=lambda r:r['metrics']['log2_T0'])
  ranked.append((hi['metrics']['log2_T0']-lo['metrics']['log2_T0'],key,lo,hi))
out=[]
for delta,key,lo,hi in sorted(ranked,key=lambda x:x[0],reverse=True)[:5]:
 out.append({'model':key[0],'task':key[1],'condition':key[2],'log2_T0_range':delta,'T0_ratio':2**delta,'W_B_ratio':hi['metrics']['W_B']/lo['metrics']['W_B'],'log2_U_ratio':math.log2(int(hi['metrics']['T0']['numerator_decimal']))-math.log2(int(lo['metrics']['T0']['numerator_decimal'])),'minimum':{k:lo[k] for k in ['sample','attemptId','metrics','sourceLength']},'maximum':{k:hi[k] for k in ['sample','attemptId','metrics','sourceLength']}})
(OUT/'within_cell_extremes.json').write_text(json.dumps({'input_sha256':hashlib.sha256(P.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'examples':out},indent=2)+'\n')
