"""Stopped-by-default operational watchdog for one captured text-only stop case.

No model requests, provider tools, shell commands, or Lean checks are made here.
After evidence-only reconciliation, an exclusive atomic handoff may resume only
the named blocked shard. The existing frozen controller performs later scheduled
work. Unknown outcomes, live claims, stale status, and ambiguous logs stay held.
Apply mode requires explicit reviewed helper, watchdog and launch-plan hashes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

import recover_text_tool_stops as recovery
from lean_compression.io import append_event, digest, evaluator_hash, file_hash
from lean_compression.store import load_run, generation_path

WORK = Path(__file__).resolve().parent
REPO = recovery.REPO
KIND = "text_only_tool_stop_watchdog_resume_v1"


def read(path):
    data = Path(path).read_bytes()
    return json.loads(data, object_pairs_hook=recovery.unique_object), hashlib.sha256(data).hexdigest()


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def pinned_runs(work, repo, plan_sha256, helper_sha256, watchdog_sha256):
    if file_hash(Path(recovery.__file__)) != helper_sha256 or file_hash(Path(__file__)) != watchdog_sha256:
        raise ValueError("Reviewed operational helper hashes differ")
    plan, actual = read(work/'launch_ready.json')
    if not isinstance(plan,dict) or actual != plan_sha256 or plan.get('ready') is not True or plan.get('experiment') != 'openai_math_100_v1':
        raise ValueError("Pinned launch plan differs or is not ready")
    if plan.get('source_hash') != evaluator_hash() or plan.get('engine_hash') != file_hash(repo/'lean/Evaluator.lean'):
        raise ValueError("Frozen core or Lean engine hash differs")
    count = plan.get('generation_workers_per_model')
    if type(count) is not int or not 1 <= count <= 8:
        raise ValueError("Invalid frozen shard count")
    runs = {}
    for entry in plan.get('runs', []):
        name, model = entry.get('directory'), entry.get('model')
        if not isinstance(name, str) or re.fullmatch(r'om100-[a-z0-9.-]+-high',name) is None or model in runs:
            raise ValueError("Invalid or duplicate frozen run")
        run = (repo/'runs'/name).resolve()
        if run.parent != (repo/'runs').resolve() or file_hash(run/'manifest.json') != entry.get('manifest_sha256'):
            raise ValueError("Frozen run path or manifest hash differs")
        manifest = load_run(run)
        if (manifest.get('run_hash') != entry.get('run_hash') or manifest.get('model_snapshot') != model or
                manifest.get('evaluator_source_hash') != plan['source_hash'] or
                manifest.get('engine_hash') != plan['engine_hash']):
            raise ValueError("Frozen run identity differs")
        ids = [s.get('attempt_id') for s in manifest['slots']]
        if len(ids) != len(set(ids)) or any(not isinstance(x,str) or re.fullmatch(r'[0-9a-f]{64}',x) is None for x in ids):
            raise ValueError("Invalid frozen attempt IDs")
        runs[model] = run, manifest
    if not runs or sum(len(m['slots']) for _,m in runs.values()) != plan.get('scheduled_attempts'):
        raise ValueError("Frozen schedule count differs")
    return plan, runs


def fresh_status(work, now, max_age):
    status, _ = read(work/'status.json')
    if not isinstance(status,dict):raise ValueError("Controller status is not an object")
    updated = status.get('updated_at')
    if not finite(updated) or not -5 <= now-updated <= max_age:
        raise ValueError("Controller status is stale or has an invalid clock")
    if status.get('state') not in {'running','needs_attention','checked_complete'} or not isinstance(status.get('blocked_shards'),dict):
        raise ValueError("Invalid controller status")
    return status


def expected_error(attempt):
    return (f"Provider outcome is unknown ({recovery.KNOWN_DIAGNOSTIC}); the in-flight marker and transport "
            f"evidence were retained for reconcile, with the response in captures/{attempt}.jsonl")


def inspect_shard(work, model, index, count, run, manifest, blocked):
    at = blocked.get('at')
    if not finite(at) or at>time.time()+5 or type(blocked.get('exit_code')) is not int or blocked['exit_code'] == 0:
        raise ValueError("Not a failed generation child")
    expected_log = f'generation-logs/{model}-{index}.log'
    if blocked.get('log') != expected_log:
        raise ValueError("Blocked shard log identity differs")
    owned = {slot['attempt_id'] for slot in manifest['slots'][index::count]}
    events = [e for e in recovery.read_events(run/'transport.jsonl')[0] if e.get('attempt_id') in owned]
    if not events:
        raise ValueError("No matching shard transport evidence")
    tail = events[-1]; attempt = tail['attempt_id']
    if tail.get('event') != 'outcome_unknown' or tail.get('reason') != recovery.KNOWN_DIAGNOSTIC:
        raise ValueError("Blocked shard has another unresolved outcome")
    unknown_at = recovery.timestamp(tail.get('recorded_at')).timestamp()
    if not 0 <= at-unknown_at <= 120:
        raise ValueError("Block is not contemporaneous with the recognized outcome")
    with (work/expected_log).open('rb') as stream:
        stream.seek(0,os.SEEK_END);size=stream.tell();stream.seek(max(0,size-65536))
        lines = stream.read().decode('utf-8',errors='replace').splitlines()
    final_line = next((line for line in reversed(lines) if line.strip()),'')
    if not final_line.endswith(expected_error(attempt)):
        raise ValueError("Latest child error is not the recognized captured response")
    markers = {p.stem for p in (run/'inflight').glob('*.json') if p.stem in owned}
    latest = {e['attempt_id']:e for e in events}
    unknowns = {a for a,e in latest.items() if e.get('event')=='outcome_unknown' and not generation_path(run,a).exists()}
    if markers-{attempt} or unknowns-{attempt}:
        raise ValueError("Other unresolved calls remain in this shard")
    return attempt, digest(tail)


def publish_exclusive(path, value):
    """A same-directory hard-link publishes complete bytes only if absent."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.text-stop-resume-',delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(value,stream,sort_keys=True,allow_nan=False);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        os.link(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return True
    except FileExistsError:
        return False
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def dispatched(work, operation_id):
    for path in [work/'resume_shards.json',*work.glob('resume_shards.applied-*.json')]:
        if path.exists():
            value,_=read(path)
            if isinstance(value,dict) and value.get('operation_id')==operation_id:
                return True
    return False


def scan(work, repo, *, plan_sha256, helper_sha256, watchdog_sha256, apply=False, max_status_age=90):
    work,repo=Path(work).resolve(),Path(repo).resolve()
    plan,runs=pinned_runs(work,repo,plan_sha256,helper_sha256,watchdog_sha256)
    status=fresh_status(work,time.time(),max_status_age)
    report={'kind':'text_only_tool_stop_watchdog_scan_v1','apply':apply,'actions':[],'held':[],
            'resume_requested_shards':[],'model_calls':0,'tools_executed':0}
    prepared=[];count=plan['generation_workers_per_model']
    for shard,blocked in sorted(status['blocked_shards'].items()):
        try:
            if not isinstance(shard,str) or not isinstance(blocked,dict):raise ValueError('Invalid blocked-shard entry')
            model,raw_index=shard.rsplit('/',1);index=int(raw_index)
            if str(index)!=raw_index or not 0<=index<count or model not in runs:
                raise ValueError('Unknown frozen shard')
            run,manifest=runs[model];settings=manifest['config']['generation']
            if settings.get('provider')!='anthropic' or settings.get('mode')!='single_shot':
                raise ValueError('Shard is outside the narrow provider/mode policy')
            attempt,tail_hash=inspect_shard(work,model,index,count,run,manifest,blocked)
            result=recovery.recover(run,attempt,apply)
            item={'shard':shard,'blocked_at':blocked['at'],'attempt_id':attempt,
                  'generation_sha256':result['generation_sha256'],'capture':result['capture'],
                  'request_hash':result['request_hash'],'transport_tail_hash':tail_hash}
            report['actions'].append({'shard':shard,**result})
            prepared.append(item)
        except (ValueError,KeyError,TypeError,OSError) as error:
            report['held'].append({'shard':shard,'reason':str(error)})
    if not apply or not prepared:return report
    # Re-read immediately before handoff; a concurrent root intervention or new
    # block must not inherit an older recovery's authorization.
    current=fresh_status(work,time.time(),max_status_age)
    ready=[]
    for item in prepared:
        shard=item['shard'];block=current['blocked_shards'].get(shard)
        if not isinstance(block,dict) or block.get('at')!=item['blocked_at']:continue
        model,raw_index=shard.rsplit('/',1);run,manifest=runs[model]
        try:
            attempt,tail_hash=inspect_shard(work,model,int(raw_index),count,run,manifest,block)
            if attempt!=item['attempt_id'] or tail_hash!=item['transport_tail_hash']:
                raise ValueError('Transport changed before resume')
            if any((run/'inflight'/f"{s['attempt_id']}.json").exists() for s in manifest['slots'][int(raw_index)::count]):
                raise ValueError('An unresolved marker remains before resume')
            if file_hash(generation_path(run,attempt))!=item['generation_sha256']:
                raise ValueError('Recovered immutable generation changed')
            ready.append(item)
        except (ValueError,KeyError,TypeError,OSError) as error:
            report['held'].append({'shard':shard,'reason':str(error)})
    if not ready:return report
    operation_id=digest({'kind':KIND,'plan_sha256':plan_sha256,'recoveries':ready})
    if dispatched(work,operation_id):
        report['handoff']='already_dispatched';return report
    payload={'kind':KIND,'operation_id':operation_id,'issued_at':time.time(),
             'shards':[x['shard'] for x in ready],'recoveries':ready,'plan_sha256':plan_sha256,
             'helper_sha256':helper_sha256,'watchdog_sha256':watchdog_sha256,'new_model_calls':0,'tools_executed':0}
    if not publish_exclusive(work/'resume_shards.json',payload):
        report['handoff']='existing_resume_request_preserved';return report
    report['resume_requested_shards']=payload['shards'];report['handoff']='published';report['operation_id']=operation_id
    append_event(work/'operational-recovery/watchdog-events.jsonl',payload,once=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,default=WORK);parser.add_argument('--repo',type=Path,default=REPO)
    parser.add_argument('--plan-sha256',required=True);parser.add_argument('--helper-sha256',required=True)
    parser.add_argument('--watchdog-sha256',required=True)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--watch',action='store_true')
    parser.add_argument('--interval-seconds',type=int,default=60)
    args=parser.parse_args()
    if not 5<=args.interval_seconds<=60:raise ValueError('Poll interval must be between5 and60 seconds')
    # The watchdog has no use for credentials even if its launcher has them.
    for name in ('OPENAI_API_KEY','ANTHROPIC_API_KEY'):os.environ.pop(name,None)
    directory=args.work/'operational-recovery';directory.mkdir(parents=True,exist_ok=True)
    with (directory/'watchdog.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                result=scan(args.work,args.repo,plan_sha256=args.plan_sha256,helper_sha256=args.helper_sha256,
                            watchdog_sha256=args.watchdog_sha256,apply=args.apply)
            except (ValueError,KeyError,TypeError,OSError) as error:
                result={'kind':'watchdog_held','reason':str(error),'model_calls':0,'tools_executed':0}
            print(json.dumps(result,sort_keys=True),flush=True)
            if not args.watch:return 0
            time.sleep(args.interval_seconds)


if __name__=='__main__':raise SystemExit(main())
