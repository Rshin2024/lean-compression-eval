"""Parallelize the pinned verifier's unchanged per-record read/validation loop.

No model/Lean calls. The generated function is the exact AST loop body, ending
with return(record) instead of appending it to the sequential result list.
"""
from __future__ import annotations
import ast
import copy
from concurrent.futures import ProcessPoolExecutor
import hashlib
import multiprocessing
from pathlib import Path

EXPECTED_VERIFIER='55f5931a9d4a2cfa92338e4079206477abd7d792a94b758b75b61237d33a5ac0'
_CONTEXT=None
_READ_ONE=None


def prepare(verifier):
    path=Path(verifier.__file__);raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=EXPECTED_VERIFIER:raise ValueError('Parallel reader requires exact reviewed verifier source')
    tree=ast.parse(raw)
    original=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='load_records')
    loops=[n for n in original.body if isinstance(n,ast.For)]
    if len(loops)!=1:raise ValueError('Verifier record-loop structure changed')
    loop=loops[0]
    if ast.unparse(loop.target)!='slot' or ast.unparse(loop.iter)!="context['manifest']['slots']":
        raise ValueError('Verifier record-loop target changed')
    if ast.unparse(loop.body[-1])!='records.append(record)':raise ValueError('Verifier return accumulation changed')
    scaffold=ast.parse('def read_one(context, slot, graphs):\n    run=context["run"]\n    directory=context["directory"]\n').body[0]
    scaffold.body.extend(copy.deepcopy(loop.body[:-1]));scaffold.body.append(ast.Return(value=ast.Name(id='record',ctx=ast.Load())))
    module=ast.fix_missing_locations(ast.Module(body=[scaffold],type_ignores=[]))
    namespace=verifier.__dict__.copy();exec(compile(module,str(path)+'::parallel_record_loop','exec'),namespace)
    return namespace['read_one'],hashlib.sha256(ast.unparse(module).encode()).hexdigest()


def chunk(slots):
    return [_READ_ONE(_CONTEXT,slot,False) for slot in slots]


def load_records(verifier,run,*,amendment_path,graphs=False,workers=16):
    global _CONTEXT,_READ_ONE
    if graphs:raise ValueError('Parallel reports export graph-free records only')
    if not 1<=workers<=32:raise ValueError('Parallel report worker count outside approved range')
    context=verifier.load_verification_context(run,amendment_path)
    directory=context['directory']
    if (directory/'policy.json').exists() and verifier.read_json(directory/'policy.json')!=context['policy']:
        raise ValueError('Stored version has a different runtime amendment policy')
    if not (directory/'policy.json').exists() and any(directory.glob('*.json')):
        raise ValueError('Version records lack their policy')
    verifier.assert_code(context)
    _CONTEXT=context;_READ_ONE,function_sha=prepare(verifier)
    slots=context['manifest']['slots'];width=max(1,(len(slots)+workers*4-1)//(workers*4))
    chunks=[slots[i:i+width] for i in range(0,len(slots),width)]
    try:
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('fork')) as pool:
            result=[record for group in pool.map(chunk,chunks) for record in group]
    finally:
        _CONTEXT=None;_READ_ONE=None
    verifier.assert_code(context)
    if [r['attempt_id'] for r in result]!=[s['attempt_id'] for s in slots]:raise ValueError('Parallel reader changed canonical schedule order')
    return result
