#!/usr/bin/env python3
"""Portable statistical replay; never requests generations or executes Lean.

Original analysis sources remain byte-identical. Primary uses copied DTO/metadata.
Cross-run uses a documented normalization adapter because private provider records
are intentionally excluded; its statistical functions and bootstrap are unchanged.
"""
from pathlib import Path
import argparse,csv,hashlib,importlib.util,json,sys
ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True

def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def verify_bundle():
 manifest=json.loads((ROOT/'reproducibility/bundled-input-manifest.json').read_bytes())
 for relative,record in manifest['files'].items():
  p=ROOT/relative;assert p.is_file() and not p.is_symlink(),relative
  with p.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==record['sha256'],relative

INTEGER={'output_token_cap','sample','valid','completed','truncated','empty','measured','W_S','W_B','sourceNonWhitespaceCharacters','sourceCharacters','sourceUtf8Bytes','rawCharacters','input_tokens','input_total_tokens','uncached_input_tokens','output_tokens','total_billed_tokens','reasoning_tokens','cache_read_tokens','cache_write_tokens','cache_write_5m_tokens','cache_write_1h_tokens'}
FLOAT={'log2_T0','log2_I0','log2_W_B','generation_elapsed_seconds'}
def normalized():
 with (ROOT/'data/attempts_normalized.csv').open() as f:rows=list(csv.DictReader(f))
 for row in rows:
  for k,v in row.items():
   row[k]=None if v=='' else int(v) if k in INTEGER else float(v) if k in FLOAT else v
 return rows

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('analysis',choices=['primary','cross-run']);p.add_argument('--output',type=Path);args=p.parse_args();verify_bundle()
 target=(args.output or ROOT/'reproduced'/args.analysis).resolve();assert not target.exists(),'Output already exists; choose a new directory';target.parent.mkdir(parents=True,exist_ok=True)
 if args.analysis=='primary':
  m=module('frozen_primary',ROOT/'reproducibility/main/analyze_primary.py');m.INPUT=ROOT/'inputs/om100-max-suite-high-20261014/comparison-data.json';m.META=ROOT/'inputs/question-candidates.jsonl';m.OUT=target;m.main()
 else:
  m=module('frozen_cross',ROOT/'reproducibility/cross-run/analyze_cross_run.py');m.RUNS=ROOT/'inputs';m.HERE=target.parent
  rows=normalized();real_path=Path
  # Route only the original hard-coded candidate location to its exact byte copy.
  m.Path=lambda value,*parts: ROOT/'inputs/question-candidates.jsonl' if str(value)=='/Users/ryanshin/Downloads/openai_math_100_candidates.jsonl' and not parts else real_path(value,*parts)
  def from_normalized(label,spec,inputs):
   data=m.read(m.RUNS/spec/'comparison-data.json',inputs);selected=[r.copy() for r in rows if r['run']==label]
   lookup=m.unique(data['attempts'],m.join_key);assert len(selected)==len(lookup)==9600
   for row in selected:
    view=lookup[m.join_key(row)];assert row['attempt_id']==view['attemptId'] and row['status']==view['status']
    assert all(row[k]==v for k,v in m.completion(view).items())
    assert row['cost_usd']==view['cost']['total_usd'] or m.Decimal(row['cost_usd'])==m.Decimal(view['cost']['total_usd'])
    if row['measured']:
     for metric in ['T0','I0']:
      assert row[metric+'_numerator_decimal']==view['metrics'][metric]['numerator_decimal']
      assert row[metric+'_denominator_decimal']==view['metrics'][metric]['denominator_decimal']
   path=ROOT/'data/attempts_normalized.csv';inputs[str(path)]={'sha256':m.sha_file(path),'bytes':path.stat().st_size}
   return selected,data
  m.normalize_suite=from_normalized
  sys.argv=[str(ROOT/'reproducibility/cross-run/analyze_cross_run.py'),'--output',str(target)];m.main()
 print(json.dumps({'status':'statistical_replay_complete','output':str(target),'provider_record_reauditing':False,'model_calls':0,'lean_calls':0}))

if __name__=='__main__':main()
