#!/usr/bin/env python3
"""Inspect or explicitly archive one completed task VM; no cleanup/model calls.

Preparation alone starts nothing. `archive` additionally requires the reviewed
plan SHA. Every assigned model must satisfy all local and remote gates.
"""
from datetime import datetime, timezone
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
CLOUD = HERE.parent
WORK = CLOUD.parent
ROOT = WORK.parents[1]
REPO = ROOT / 'outputs/lean-compression-eval'
from remote_archive import (SOURCE_ROOT, OUTPUT_ROOT, GROUP, SUBSCRIPTION, TASK,
                            require, read, sha_file, verify_archive, write_new)


def corroborate_backup_file(path, expected, verified_epoch):
    info = path.lstat()
    require(not path.is_symlink() and info.st_nlink == 1 and info.st_size == expected['bytes'],
            'Current graph backup metadata differs')
    require(info.st_mtime <= verified_epoch, 'Graph file modification time is newer than full-hash receipt')


def load_plan(path=None):
    path = Path(path) if path else HERE / 'plan.json'
    plan = read(path); plan_sha = sha_file(path)
    require(plan['resource_group'] == GROUP and plan['subscription_id'] == SUBSCRIPTION and plan['task_tag'] == TASK,
            'Archive plan ownership scope differs')
    require(plan['source_root'] == str(SOURCE_ROOT) and plan['output_root'] == str(OUTPUT_ROOT), 'Archive plan path scope differs')
    for name, expected in plan['helper_source_sha256'].items():
        require(name in {'archive_host.py', 'remote_archive.py', 'test_operational_archive.py'}, 'Unrecognized helper source pin')
        require(sha_file(HERE / name) == expected, 'Archive helper changed after review: ' + name)
    require(sha_file(CLOUD / 'verification-fleet-plan.json') == plan['fleet_plan_sha256'], 'Fleet source plan changed')
    require(sha_file(CLOUD / 'ssh-config') == plan['ssh_config_sha256'], 'Pinned SSH configuration changed')
    require(sha_file(CLOUD / 'cleanup-plan/resource-inventory.json') == plan['ownership_inventory_sha256'], 'Reviewed ownership inventory changed')
    inventory = read(CLOUD / 'cleanup-plan/resource-inventory.json')
    vms = {x['name']: x for x in inventory if x['type'].lower() == 'microsoft.compute/virtualmachines'}
    require(set(vms) == set(plan['hosts']), 'Exact six-host ownership set differs')
    fleet = read(CLOUD / 'verification-fleet-plan.json')
    for alias, host in plan['hosts'].items():
        vm = vms[alias]
        require(vm['id'].lower() == host['resource_id'].lower() and vm.get('tags', {}).get('task') == TASK,
                'Planned host ownership differs')
        assigned = [x for x in fleet['assignments'] if x['ssh_alias'] == alias]
        require(host['models'] == [{k: x[k] for k in ['model', 'directory', 'manifest_sha256', 'run_hash']} for x in assigned],
                'Model-to-host assignments changed')
    return plan, plan_sha


def local_gates(host):
    state = read(WORK / 'generation-status.json')
    result, enriched = [], copy.deepcopy(host)
    for entry in enriched['models']:
        model = entry['model']; run = REPO / 'runs' / entry['directory']
        require(sha_file(run / 'manifest.json') == entry['manifest_sha256'], 'Local frozen manifest differs')
        manifest = read(run / 'manifest.json'); ids = {x['attempt_id'] for x in manifest['slots']}
        require(len(ids) == 1200 and manifest['run_hash'] == entry['run_hash'], 'Local model schedule differs')
        require(state['generated'][model] == 1200 and state['active_per_model'][model] == 0,
                'Provider model is not fully drained')
        require(not list((run / 'inflight').glob('*.json')), 'Provider in-flight marker remains')
        for relative in ['generations', 'evaluations/v2_threads1']:
            paths = {p.stem for p in (run / relative).glob('*.json') if re.fullmatch('[0-9a-f]{64}', p.stem)}
            require(paths == ids, 'Local generation/verification model is incomplete')
        revision = run / 'evaluations/v2_threads1'
        require(not list(revision.rglob('*.claim')) and not list(revision.rglob('*.retained')), 'Local verification claim remains')
        backup_path = WORK / 'final-backups' / (model + '-verification.json'); backup = read(backup_path)
        require(backup['status'] == 'pass' and not any(backup[x] for x in ['missing', 'extra', 'different']), 'Graph backup did not pass')
        require(backup['run_manifest_sha256'] == entry['manifest_sha256'] and backup['source_run_hash'] == entry['run_hash'], 'Graph backup binds another run')
        maps = []
        for label in ['local', 'cloud']:
            path = WORK / 'final-backups' / (model + '-' + label + '-inventory.json')
            require(sha_file(path) == backup[label + '_inventory_sha256'], 'Graph inventory receipt hash differs')
            maps.append(read(path))
        require(maps[0] == maps[1], 'Cloud/local graph inventories differ')
        expected = maps[0]
        verified_epoch = datetime.fromisoformat(backup['verified_at_utc'].replace('Z', '+00:00')).timestamp()
        current = {p.relative_to(run).as_posix(): p for sub in ['generations', 'evaluations/v2_threads1']
                   for p in (run / sub).rglob('*') if p.is_file() and not p.name.startswith('.') and p.suffix in {'.json', '.jsonl'}}
        require(set(current) == set(expected), 'Current graph backup file set changed')
        for name, path in current.items():
            corroborate_backup_file(path, expected[name], verified_epoch)
        local_evidence = WORK / 'final-backups' / (model + '-local-generation-evidence.json')
        evidence = read(local_evidence)
        require(evidence['generation_count'] == 1200 and evidence['manifest_sha256'] == entry['manifest_sha256']
                and evidence['run_hash'] == entry['run_hash'], 'Local stream/capture evidence receipt absent or foreign')
        job_path = WORK / 'reporting/jobs' / (model + '-local.json'); job = read(job_path)
        require(job['state'] == 'complete_synced_verified', 'Local report is not complete and verified')
        report = REPO / 'runs/om100-max-suite-high-20261014' / ('report-' + model)
        report_manifest = report / 'report-file-manifest.json'
        require(sha_file(report_manifest) == job['manifest_sha256'], 'Local report manifest binding differs')
        bundle = read(report_manifest)
        require(bundle['model'] == model and bundle['run_hash'] == entry['run_hash'], 'Report bundle identity differs')
        for item in bundle['files']:
            relative = Path(item['path'])
            require(not relative.is_absolute() and '..' not in relative.parts, 'Unsafe report member')
            path = report / relative
            require(not path.is_symlink() and path.resolve().is_relative_to(report.resolve())
                    and sha_file(path) == item['sha256'], 'Completed report content changed')
        entry['report_manifest_sha256'] = sha_file(report_manifest)
        result.append({'model': model, 'graph_backup_receipt_sha256': sha_file(backup_path),
                       'local_generation_receipt_sha256': sha_file(local_evidence),
                       'local_report_job_sha256': sha_file(job_path), 'report_manifest_sha256': entry['report_manifest_sha256'],
                       'graph_scope': 'Existing full-hash receipt plus current file-set/size/link and mtime-not-newer corroboration; not a new graph rehash.'})
    return enriched, result


def remote_call(request):
    source = (HERE / 'remote_archive.py').read_text()
    command = ['ssh', '-F', str(CLOUD / 'ssh-config'), request['host']['ssh_alias'],
               'python3 -B -c ' + shlex.quote(source)]
    result = subprocess.run(command, input=json.dumps(request), text=True, capture_output=True, timeout=300)
    require(result.returncode == 0, 'Remote operational archive check failed: ' + result.stderr[-1500:])
    return json.loads(result.stdout)


def run(action, alias, authorization_sha=None):
    plan, plan_sha = load_plan()
    require(alias in plan['hosts'], 'Host is not in the exact archive plan')
    if action == 'archive':
        require(authorization_sha == plan_sha, 'Archive creation requires the reviewed plan SHA and root execution authorization')
    host, gates = local_gates(plan['hosts'][alias])
    attempt = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    request = {'action': action, 'host': host, 'source_root': plan['source_root'], 'output_root': plan['output_root'],
               'plan_sha256': plan_sha, 'worker_sha256': plan['helper_source_sha256']['remote_archive.py'], 'attempt': attempt}
    if action == 'inspect':
        return {'state': 'read_only_ready', 'plan_sha256': plan_sha, 'local_gates': gates, 'remote': remote_call(request)}
    output = HERE / 'receipts' / alias / attempt
    require(not any(p.is_symlink() for p in [output, *output.parents] if p.exists()), 'Linked local archive output ancestor')
    output.mkdir(parents=True, exist_ok=False)
    lock_path = output.parent / 'archive.lock'
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        require(os.fstat(lock.fileno()).st_nlink == 1, 'Hardlinked local archive lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Recheck after obtaining the local per-host lock. No old evidence or
        # remote partial attempts are overwritten on a failed invocation.
        host2, gates2 = local_gates(plan['hosts'][alias])
        require(host == host2 and gates == gates2, 'Completion gates changed while acquiring archive lock')
        remote = remote_call(request)
        require(remote['status'] == 'verified_remote_archive' and remote['plan_sha256'] == plan_sha,
                'Remote archive receipt differs')
        expected_parent = OUTPUT_ROOT / alias / plan_sha / attempt
        require(remote['archive'] == str(expected_parent / 'operational.tar.gz') and
                remote['inventory'] == str(expected_parent / 'inventory.json'), 'Remote archive paths differ')
        archive = output / 'operational.tar.gz'; inventory = output / 'inventory.json'
        for remote_path, local_path in [(remote['archive'], archive), (remote['inventory'], inventory)]:
            require(not local_path.exists(), 'Local archive output already exists')
            subprocess.run(['scp', '-q', '-F', str(CLOUD / 'ssh-config'), alias + ':' + remote_path, str(local_path)],
                           check=True, timeout=300)
            require(not local_path.is_symlink() and local_path.stat().st_nlink == 1, 'Transferred archive is linked')
        require(sha_file(archive) == remote['archive_sha256'] and archive.stat().st_size == remote['archive_bytes'],
                'Transferred archive SHA/size differs')
        require(sha_file(inventory) == remote['inventory_sha256'], 'Transferred inventory SHA differs')
        inventory_data = read(inventory)
        require(set(inventory_data['files']) == set(host['files']), 'Archive inventory differs from reviewed allowlist')
        verified = verify_archive(archive, inventory_data)
        require(verified['archive_sha256'] == remote['archive_sha256'] and verified['files'] == remote['files']
                and verified['bytes'] == remote['bytes'], 'Local archive contents differ')
        require(load_plan()[1] == plan_sha, 'Archive plan or helper pins changed during transfer')
        final_host, final_gates = local_gates(plan['hosts'][alias])
        require(final_host == host and final_gates == gates, 'Local completion gates changed during transfer')
        receipt = {'schema_version': 1, 'state': 'verified_local_operational_archive', 'host': alias,
                   'at_utc': datetime.now(timezone.utc).isoformat(), 'plan_sha256': plan_sha,
                   'archive': str(archive), 'inventory': str(inventory), 'archive_sha256': sha_file(archive),
                   'inventory_sha256': sha_file(inventory), 'archive_bytes': archive.stat().st_size,
                   'files': verified['files'], 'uncompressed_bytes': verified['bytes'], 'remote_receipt': remote,
                   'local_gates': gates, 'model_calls': 0, 'resource_changes': False,
                   'source_or_status_files_modified': False, 'byte_copies_only': True}
        write_new(output / 'receipt.json', receipt)
        return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'archive'])
    parser.add_argument('--host', required=True)
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    print(json.dumps(run(args.action, args.host, args.plan_sha256), indent=2))
