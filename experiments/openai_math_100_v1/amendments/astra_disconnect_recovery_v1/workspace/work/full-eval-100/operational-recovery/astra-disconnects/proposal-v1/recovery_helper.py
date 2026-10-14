#!/usr/bin/env python3
"""INACTIVE PROPOSAL. Preflight is read-only; apply requires actual user approval.

After approval only: release the six original abandoned calls through unchanged
stock reconciliation under its file locks, then publish the existing resume
control. No direct provider call or frozen record edit is implemented here.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.dont_write_bytecode = True
from common import HERE, WORK, RUN, load, events, read, digest, file_hash
from contract import EXACT_IDS, ensure, inspect_sequence
from lean_compression.io import _abandoned, append_event
from lean_compression.providers import _reconcile, _transport_evidence


def stamp():
    return datetime.now(timezone.utc).isoformat()


def inventory_check(proposal):
    path = HERE / 'recorded-generations-baseline.json'
    ensure(file_hash(path) == proposal['baseline_inventory_sha256'], 'Recorded-generation baseline changed')
    for relative, expected in read(path).items():
        ensure(file_hash(WORK.parents[1] / relative) == expected, f'Previously recorded output changed: {relative}')


def state(binding, approval=None):
    aid = binding['attempt_id']; marker = RUN / 'inflight' / (aid + '.json')
    seq = [e for e in events(RUN / 'transport.jsonl') if e.get('attempt_id') == aid]
    amendments = events(RUN / 'amendments.jsonl')
    if approval is None:
        ensure(seq == binding['original_transport_events'], 'Unapproved attempt evidence changed')
        ensure(not any(e.get('attempt_id') == aid and e.get('resolution') == 'released_without_model_outcome'
                       for e in amendments), 'Release occurred before this proposal approval')
        status = 'held'
    else:
        status = inspect_sequence(binding, seq, amendments, approval)
    if status in ('held', 'amendment_only'):
        ensure(marker.exists() and file_hash(marker) == binding['marker_sha256'], 'Original marker is missing or changed')
        ensure(not (RUN / 'captures' / (aid + '.jsonl')).exists(), 'Saved capture appeared; use ordinary recovery, never release it')
        ensure(not (RUN / 'generations' / (aid + '.json')).exists(), 'A recorded output must never be retried')
    return status, seq


def preflight(approval_path=None):
    proposal, proposal_sha, manifest, approval = load(approval_path)
    ensure({x['attempt_id'] for x in proposal['attempts']} == EXACT_IDS, 'Recovery allowlist differs')
    inventory_check(proposal)
    states = {x['attempt_id']: state(x, approval)[0] for x in proposal['attempts']}
    return {'proposal_sha256': proposal_sha, 'states': states, 'approved': approval is not None,
            'activation_performed': False, 'api_calls': 0, 'markers_released': 0}


def release_locked(binding, manifest, approval):
    """One original marker; stock lock + recheck + stock reconciliation only."""
    aid = binding['attempt_id']; marker = RUN / 'inflight' / (aid + '.json')
    current, seq = state(binding, approval)
    if current == 'released' and not marker.exists():
        return {'attempt_id': aid, 'action': 'already_released'}
    # If a replacement already owns the same filename, never touch its marker.
    ensure(file_hash(marker) == binding['marker_sha256'], 'Marker now belongs to a new call; do not release again')
    lock = _abandoned(marker, protocol=False)
    ensure(lock is not None, 'A live worker holds the marker; leave it untouched')
    try:
        ensure(file_hash(marker) == binding['marker_sha256'], 'Marker changed while acquiring lock')
        current, seq = state(binding, approval)
        slots = {s['attempt_id']: s for s in manifest['slots']}
        evidence = _transport_evidence(RUN, {aid})[aid]
        result = _reconcile(RUN, marker, slots[aid], manifest['config']['generation'], evidence,
                            apply=True, release_unreadable=True)
        ensure(result['action'] in {'release', 'remove_marker'}, 'Unexpected stock reconciliation result; stop')
        final, _ = state(binding, approval)
        ensure(final == 'released' and not marker.exists(), 'Stock reconciliation did not finish the pinned release')
        return result
    finally:
        os.close(lock)


def existing_resume(proposal_sha, approval_sha):
    paths = list(WORK.glob('resume_shards.applied-*.json'))
    pending = WORK / 'resume_shards.json'
    if pending.exists():
        paths.append(pending)
    for path in paths:
        row = read(path)
        if row.get('recovery_proposal_sha256') == proposal_sha:
            ensure(row.get('approval_sha256') == approval_sha, 'Different approval already resumed this proposal')
            return str(path)
    return None


def publish_resume(proposal, proposal_sha, approval_sha):
    prior = existing_resume(proposal_sha, approval_sha)
    if prior:
        return {'already_published': prior}
    status = read(WORK / 'status.json')
    shards = [f"gpt-6-astra/{b['shard']}" for b in proposal['attempts']]
    ensure(all(s in status.get('blocked_shards', {}) for s in shards), 'Not all original shards are blocked; inspect before resuming')
    for b in proposal['attempts']:
        name = f"gpt-6-astra/{b['shard']}"
        ensure(status['blocked_shards'][name] == proposal['original_blocked_shards'][name], 'Shard was reblocked after another attempt; do not resume twice')
    issued = time.time()
    ensure(all(issued > status['blocked_shards'][s]['at'] for s in shards), 'Resume timestamp is not newer than holds')
    payload = {'issued_at': issued, 'shards': shards, 'attempt_ids': sorted(EXACT_IDS),
               'recovery_proposal_sha256': proposal_sha, 'approval_sha256': approval_sha,
               'note': 'Explicit user-approved release of six original unknown outcomes; no other release is authorized.'}
    destination = WORK / 'resume_shards.json'
    # Atomic publication without replacing any competing control file.
    fd, tmp = tempfile.mkstemp(prefix='.astra-resume-', dir=WORK)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream, sort_keys=True); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.link(tmp, destination)
    finally:
        os.unlink(tmp)
    return {'published': str(destination), 'payload': payload}


def apply(approval_path):
    ensure(approval_path is not None, 'An actual approval file is required')
    proposal, proposal_sha, manifest, approval = load(approval_path)
    ensure(approval is not None, 'Pending human question is not authorization')
    approval_sha = file_hash(approval_path)
    activation = HERE / 'activation'
    activation.mkdir(exist_ok=True)
    with (activation / 'helper.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if prior := existing_resume(proposal_sha, approval_sha):
            return {'status': 'already_resumed', 'evidence': prior, 'new_releases': 0}
        # All six must be safe before the first mutation. After a crash, an exact
        # amendment-only or release-event-only state can be completed once.
        preflight(approval_path)
        append_event(activation / 'events.jsonl', {'kind': 'approved_recovery_started', 'proposal_sha256': proposal_sha,
                    'approval_sha256': approval_sha}, once=True)
        results = []
        for binding in proposal['attempts']:
            ensure(file_hash(approval_path) == approval_sha, 'Approval record changed during activation')
            result = release_locked(binding, manifest, approval)
            results.append(result)
            append_event(activation / 'events.jsonl', {'kind': 'stock_reconciliation_checked',
                         'attempt_id': binding['attempt_id'], 'action': result['action']}, once=True)
        ensure(all(state(b, approval)[0] == 'released' for b in proposal['attempts']), 'Not all six releases finished')
        inventory_check(proposal)
        ensure(file_hash(approval_path) == approval_sha, 'Approval record changed before resume publication')
        # Receipt is written before publication, so a crash leaves reviewable
        # release evidence. The existing-control payload itself is the resume receipt.
        append_event(activation / 'events.jsonl', {'kind': 'all_six_released_resume_ready',
                    'proposal_sha256': proposal_sha, 'approval_sha256': approval_sha}, once=True)
        resumed = publish_resume(proposal, proposal_sha, approval_sha)
        append_event(activation / 'events.jsonl', {'kind': 'resume_control_published', **resumed}, once=True)
        return {'status': 'authorized_recovery_published', 'reconciliations': results, 'resume': resumed,
                'direct_api_calls': 0, 'generation_triggered_by_existing_controller': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preflight', 'apply'])
    parser.add_argument('--approval', type=Path)
    args = parser.parse_args()
    print(json.dumps(preflight(args.approval) if args.action == 'preflight' else apply(args.approval), indent=2))


if __name__ == '__main__':
    main()
