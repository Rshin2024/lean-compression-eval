"""Pure, exact-six audit exception contract. No files, locks or model calls."""
from datetime import datetime
import hashlib
import json

EXACT_IDS = {
    '0b58cb738a4aa21aae8fbad025cf76ea8d3821e283d8c42d8fcd9040ed2f9ce8',
    '1774d2bc68bde86887cee647fdc2276d942f47f634a4270d24295926393c867e',
    '7043bcf6d4cb30983278d6e6b01151bfa1965a87774475c4af725ab4a9afcdcd',
    '7c0b014005a586b88f03c1c910167e615a5debbae1f9fe376160ad504e045fb6',
    '8d3fdcb8d19b2b0b5ec5880109e178cff4887c0ca1a24aa523184538ef9812d6',
    'f2ceab0958f9538c025dcb02748d8a4b2dea82264e3b1091413d6ab647407210',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def ensure(value, message):
    if not value:
        raise ValueError(message)


def time(value):
    t = datetime.fromisoformat(value)
    ensure(t.tzinfo is not None, 'Timestamp needs a timezone')
    return t


def authorize(proposal, proposal_sha, approval):
    ensure(approval.get('status') == 'approved_by_user', 'Actual user approval has not been recorded')
    ensure(approval.get('proposal_sha256') == proposal_sha, 'Approval is for a different proposal')
    ensure(approval.get('scope') == 'release_exact_six_unknown_astra_outcomes_once_and_resume_original_shards', 'Approval scope differs')
    ids = sorted(x['attempt_id'] for x in proposal['attempts'])
    ensure(len(ids) == 6 and set(ids) == EXACT_IDS and sorted(approval.get('attempt_ids', [])) == ids, 'Approval must name exactly the six IDs')
    ensure(approval.get('acknowledges_possible_original_charges') is True, 'Unknown original billing must be acknowledged')
    ensure(approval.get('unknown_outcome_release_limit_per_attempt') == 1, 'Only one explicit unknown-outcome release is permitted')
    ensure(time(approval['approved_at_utc']) >= time(proposal['authorization_not_before_utc']),
           'Approval precedes disclosure of these unknown calls')
    evidence = approval.get('trusted_user_evidence') or {}
    ensure(evidence.get('source') == 'human_user_message' and isinstance(evidence.get('message_reference'), str)
           and evidence['message_reference'] and isinstance(evidence.get('exact_answer'), str) and evidence['exact_answer'],
           'Trusted human message reference and answer are required; a pending question or timer is not approval')


def reason_for(binding):
    e = binding['original_transport_events'][-1]
    return f"no response body was captured; the call logged outcome_unknown ({e['type']}: {e['message']})"


def inspect_sequence(binding, sequence, amendments, approval):
    """Return held/amendment_only/released while rejecting any widened release."""
    aid = binding['attempt_id']
    original = binding['original_transport_events']
    ensure(len(original) == 2 and [x['event'] for x in original] == ['request_started', 'outcome_unknown'], 'Original evidence is not the pinned reset')
    ensure(original[0].get('request_hash') == binding['request_hash'] and
           original[1].get('type') == 'ConnectionResetError' and
           original[1].get('message') == '[Errno 54] Connection reset by peer' and
           all(x.get('transport_index') == 0 for x in original), 'Original reset/request details differ')
    ensure(digest(original) == binding['original_transport_events_hash'], 'Original evidence hash differs')
    ensure(sequence[:2] == original, f'Original unknown-call prefix changed: {aid}')
    ensure(all(x.get('attempt_id') == aid for x in sequence), 'Mixed attempt transport')
    ensure([time(x['recorded_at']) for x in sequence] == sorted(time(x['recorded_at']) for x in sequence), 'Nonmonotonic recovery evidence')
    releases = [x for x in amendments if x.get('attempt_id') == aid and x.get('resolution') == 'released_without_model_outcome']
    ensure(len(releases) <= 1, f'Multiple explicit releases are forbidden: {aid}')
    for amend in releases:
        expected = {'kind': 'in_flight_reconciliation', 'attempt_id': aid,
                    'resolution': 'released_without_model_outcome', 'marker': binding['marker'],
                    'reason': reason_for(binding), 'capture': None}
        ensure({k: v for k, v in amend.items() if k != 'recorded_at'} == expected, 'Stock release amendment differs')
        ensure(time(amend['recorded_at']) >= time(approval['approved_at_utc']), 'Release precedes user approval')
    tagged = [x for x in sequence if x.get('reconciliation') is not None]
    ensure(len(tagged) <= 1, 'A second reconciliation-tagged release is forbidden')
    if len(sequence) == 2:
        return 'amendment_only' if releases else 'held'
    event = sequence[2]
    expected = {'attempt_id': aid, 'event': 'no_model_outcome', 'capture': None, 'last_error': None,
                'provider_response': None, 'prompt_rejected': False,
                'reconciliation': {'marker': binding['marker'], 'reason': reason_for(binding)}}
    ensure({k: v for k, v in event.items() if k != 'recorded_at'} == expected, 'Retry lacks the exact stock release event')
    ensure(len(releases) == len(tagged) == 1, 'Release event and amendment must correspond one-to-one')
    ensure(time(releases[0]['recorded_at']) <= time(event['recorded_at']), 'Release event precedes its amendment')
    ensure(not any(x.get('event') == 'outcome_unknown' for x in sequence[3:]), 'Replacement outcome is unknown: hold it; a second release is not authorized')
    return 'released'


def exception_projection(events, slots, proposal, approval, amendments, original_audit_events):
    """Validate pinned originals, then remove only those two events in memory.

    The stock state machine still checks every replacement event. Return the
    ORIGINAL full event mapping to the remaining stock capture/request checks.
    Nothing is removed from logs, record hashes, cost accounting or disclosures.
    """
    from collections import defaultdict
    by_id = defaultdict(list)
    for e in events:
        by_id[e['attempt_id']].append(e)
    bindings = {x['attempt_id']: x for x in proposal['attempts']}
    exceptional = set(bindings) & set(slots)
    if not exceptional:
        return original_audit_events(events, slots), []
    ensure(exceptional == set(bindings), 'Unexpected partial Astra cohort')
    omitted, evidence = set(), []
    for aid in sorted(exceptional):
        binding = bindings[aid]
        ensure(slots[aid]['model_snapshot'] == 'gpt-6-astra' and digest(slots[aid]) == binding['slot_hash'], 'Recovery slot identity differs')
        state = inspect_sequence(binding, by_id[aid], amendments, approval)
        if state == 'released':
            omitted.update((aid, i) for i in range(2))
        evidence.append({'attempt_id': aid, 'state': state, 'original_unknown_call_cost_usd': None,
                         'original_evidence_hash': binding['original_transport_events_hash'],
                         'full_transport_events_hash': digest(by_id[aid])})
    indexes = defaultdict(int)
    projected = []
    for event in events:
        aid = event['attempt_id']; i = indexes[aid]; indexes[aid] += 1
        if (aid, i) not in omitted:
            projected.append(event)
    _, pending = original_audit_events(projected, slots)
    return (by_id, pending), evidence
