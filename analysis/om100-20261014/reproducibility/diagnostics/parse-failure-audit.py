"""Read-only status/diagnostic inventory and deterministic visible-answer sample."""
from pathlib import Path
from collections import Counter,defaultdict
import json,re,hashlib
ROOT=Path('/Users/ryanshin/Documents/Codex/2026-10-11/bas');REPO=ROOT/'outputs/lean-compression-eval';OUT=ROOT/'work/paper-analysis-20261014/methods';result={}
for model in ['claude-haiku-5-5','claude-sonnet-5-5','claude-opus-5-5']:
 p=REPO/'runs/om100-max-suite-high-20261014'/('report-'+model)/'all_scheduled_attempts.json';rows=json.loads(p.read_text());fails=[r for r in rows if r['verification_result']['validity_status']=='parse_failure'];counts=Counter();first=Counter();conditions=defaultdict(Counter);samples=[]
 for r in rows:conditions[r['generation_identity']['condition']][r['verification_result']['validity_status']]+=1
 for r in fails:
  v=r['verification_result'];reason=v['validation'].get('failure_reason','');text=v['adapted_response'];prefix=text.lstrip();match=re.match(r'candidate\.lean:(\d+):(\d+): (.*)',reason,re.S);line=int(match.group(1)) if match else None
  counts['at_first_line' if line==1 else 'later_line_or_other']+=1
  normalized=re.sub(r'candidate\.lean:\d+:\d+: ', '', reason).split('\n')[0];first[normalized]+=1
  if re.match(r'(?i)^(?:I(?:\s|[’\'])|Here\s|This\s|The\s|A\s|Below\s|We\s|Unfortunately\s|Sorry\s|Let\s|To\s|For\s|It\s|You\s)',prefix):counts['explicit_english_prefix_regex']+=1
  if prefix.startswith('#'):counts['markdown_heading_prefix']+=1
  if prefix.startswith('```'):counts['code_fence_prefix_not_removed']+=1
  if prefix.startswith('by') and (len(prefix)==2 or prefix[2].isspace()):counts['by_prefix']+=1
  if '```' in text:counts['contains_fence_anywhere']+=1
  if '<PROOF>' in text:counts['contains_proof_tag_anywhere']+=1
  if v['validation'].get('process_returncode')==0:counts['engine_process_returncode_zero']+=1
  r['_diag']={'reason':reason,'error_line':line,'source_prefix':text[:1100],'source_suffix':text[-400:] if len(text)>1500 else None,'characters':len(text)}
 # Six first-line and four later-line examples; deterministic hash ordering, not chosen for anecdotes.
 for label,selected in [('first_line',[r for r in fails if r['_diag']['error_line']==1]),('other',[r for r in fails if r['_diag']['error_line']!=1])]:
  for r in sorted(selected,key=lambda x:hashlib.sha256(('parse-audit-v1:'+x['generation_identity']['attempt_id']).encode()).hexdigest())[:6 if label=='first_line' else 4]:
   samples.append({'stratum':label,'attempt_id':r['generation_identity']['attempt_id'],'task_id':r['generation_identity']['task_id'],'condition':r['generation_identity']['condition'],'sample_index':r['generation_identity']['sample_index'],**r['_diag']})
 result[model]={'source':str(p),'source_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'scheduled':len(rows),'parse_failures':len(fails),'counts':dict(counts),'diagnostic_histogram':dict(first.most_common()),'condition_statuses':{c:dict(z) for c,z in conditions.items()},'deterministic_visible_submission_samples':samples}
(OUT/'parse-failure-audit.json').write_text(json.dumps({'scope':'Full saved diagnostic counts for three models; lexical prefix flags are descriptive patterns, not regraded proof verdicts. Samples use submitted visible adapted_response only, never provider hidden thinking. No Lean calls or proof repairs.','sample_rule':'SHA256(parse-audit-v1:attempt_id), six first-line and four other-location failures per model when present.','models':result},indent=2)+'\n')
print(json.dumps({k:{a:b for a,b in v.items() if a not in ['deterministic_visible_submission_samples','diagnostic_histogram']}|{'top_diagnostics':dict(list(v['diagnostic_histogram'].items())[:6])} for k,v in result.items()},indent=2))
