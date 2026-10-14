"""Diagnose trusted-reference whitespace parsing with the unchanged evaluator."""
from pathlib import Path
import copy
import json
import sys

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
REPO = WORK.parents[1] / 'outputs/lean-compression-eval'
sys.path.insert(0, str(REPO/'src'))
from lean_compression.environment import load_environment
from lean_compression.io import digest, file_hash
from lean_compression.runner import execute_engine


def main():
    environment = load_environment(WORK/'environment.azure.json')
    config = json.loads((WORK/'materialized_v1/config.used.json').read_text())
    engine = REPO/'lean/Evaluator.lean'
    root = WORK/'materialized_v1/repairs_v2/records'
    output = HERE/'layout_probe_v1'
    output.mkdir(parents=True, exist_ok=True)
    for ident in ['OM100-041', 'OM100-094']:
        prepared = json.loads((root/ident/'prepared.json').read_text())
        task, original = prepared['task'], prepared['reference']['proof']
        variants = {'trailing_newline':original+'\n', 'parenthesized':'(\n'+original+'\n)'}
        if ident == 'OM100-041':
            lines = original.splitlines()
            lines[18:20] = [lines[18]+' '+lines[19].lstrip()]
            variants['join_hb_type'] = '\n'.join(lines)+'\n'
        else:
            variants['semicolon_haves'] = original.replace(
                "(hm.1.trans (hupper N C)) ha)\n", "(hm.1.trans (hupper N C)) ha);\n").replace(
                "(hm.2.trans (hupper N C)) ha)\n", "(hm.2.trans (hupper N C)) ha);\n")+'\n'
        for label, proof in variants.items():
            path = output/(ident+'-'+label+'.json')
            if path.exists():
                result=json.loads(path.read_text())['result']
            else:
                request = {k:task[k] for k in ['imports','import_specs','context','binders','proposition','target_name','forbidden_declarations']}
                request.update(proof=proof,max_heartbeats=config['validation']['max_heartbeats'],
                               permitted_axioms=config['validation']['permitted_axioms'])
                result=execute_engine(request,environment,engine,config['validation'],mode='validate',
                                      wall_seconds=config['validation']['wall_seconds'])
                record={'task_id':ident,'variant':label,'original_proof_sha256':digest(original),
                        'normalized_proof_sha256':digest(proof),'normalized_proof':proof,
                        'engine_sha256':file_hash(engine),'environment_hash':environment['environment_hash'],
                        'result':result}
                path.write_text(json.dumps(record,ensure_ascii=False,indent=2))
                path.chmod(0o600)
            print(json.dumps({'task_id':ident,'variant':label,'status':result.get('validity_status'),
                              'reason':result.get('failure_reason',result.get('error',''))[:350]}),flush=True)


if __name__=='__main__':
    main()
