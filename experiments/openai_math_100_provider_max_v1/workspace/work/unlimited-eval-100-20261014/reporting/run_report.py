"""Report one complete new model cohort using frozen core and honest phase links."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
WORK=HERE.parent
REPO=WORK.parents[1]/'outputs/lean-compression-eval'
sys.path[:0]=[str(REPO/'src'),str(WORK/'preparation')]
import priority_costs
priority_costs.install()  # Before analysis/cost_reporting bind their imported functions.
from lean_compression.io import file_hash,read_json


def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def load_reporter():
    pins=read_json(HERE/'source-pins.json')
    for root,key in ((WORK,'work_files'),(REPO,'repo_files')):
        for rel,expected in pins[key].items():
            if file_hash(root/rel)!=expected:raise ValueError('Reporting source pin changed: '+rel)
    verifier=load_module('provider_max_verifier',WORK/'verify_runtime_v2.py')
    phase=load_module('provider_max_phase_report',REPO/'reporting/report_verification_v2.py')
    original=phase.report_from_context
    def report_from_context(context,records):
        report=original(context,records)
        report['run_provenance']['label']='Separate provider-maximum rerun of the selected100 questions;128000-token ceiling, high effort, uniform Lean -j1 verification.'
        report['costs']['pricing_schedule']=context['config']['pricing']
        report['interpretation']=[s for s in report['interpretation'] if not s.startswith(('Generation identities,','Verification and reference'))]
        report['interpretation'].extend([
            'This separate cohort uses fresh model responses at a 128000-token provider ceiling. The previous 8192-token cohort is retained separately.',
            'Question prompts and requested high effort match the earlier cohort exactly. OpenAI priority/Fast processing was requested; Anthropic uses standard processing.',
            'Reported token cost estimates use the delivered service tier, including any OpenAI standard fallback. Unknown tier evidence remains unpriced.',
            'Verification uses the existing v2_threads1 image and 100 previously checked reference proofs. Reference checks were reused by hash; no new reference execution is claimed.'])
        return report
    phase.report_from_context=report_from_context
    original_html=phase.phase_html
    def phase_html(phases):
        return original_html(phases).replace('The original model responses were replayed uniformly with an explicit Lean thread cap of one. This report uses the new verification phase and its rechecked references; earlier checks are retained separately.',
          'Fresh responses from this provider-maximum cohort were verified with the existing explicit Lean thread cap of one. The 100 reference checks are reused unchanged by hash. The earlier 8192-token cohort remains separate.')
    phase.phase_html=phase_html
    return verifier,phase


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    verifier,phase=load_reporter()
    report=phase.create_report(verifier,args.run,WORK/'runtime-amendment-v2.json',args.output)
    print(json.dumps({'model':report['run_provenance']['model_snapshot'],'output':str(args.output),
       'complete':True,'source_run_hash':report['run_provenance']['run_hash']}))


if __name__=='__main__':main()
