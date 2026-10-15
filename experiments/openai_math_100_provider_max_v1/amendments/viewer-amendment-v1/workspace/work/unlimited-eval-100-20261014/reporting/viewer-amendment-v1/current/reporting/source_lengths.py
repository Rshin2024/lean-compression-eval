"""Reporting-only lengths of frozen submitted text, including failed submissions.

These character/byte counts are not the evaluator's Lean parser-token W_B.
Only counts, classifications and provenance hashes leave this module.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unicodedata

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
from lean_compression import prompts
from lean_compression.io import digest, json_text
from lean_compression.outcomes import outcome_class

VERSION = 'submitted_text_lengths_v1'


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _utf8(text):
    _require(isinstance(text, str), 'Source-length input must be a string')
    try:
        return text.encode('utf-8', errors='strict')
    except UnicodeEncodeError as error:
        raise ValueError('Source-length input is not valid Unicode UTF-8 text') from error


def _serialized_sha(value):
    # The immutable generation/manifest writer uses this exact serialization.
    # This verifies its stored file binding when only the parsed report is given.
    return _sha(json_text(value).encode('utf-8'))


def _measure(raw, source, adapter, status, response_status):
    raw_bytes, source_bytes = _utf8(raw), _utf8(source)
    available = any(not char.isspace() for char in source)
    return {
        'rawCharacters': len(raw), 'rawUtf8Bytes': len(raw_bytes),
        'sourceCharacters': len(source) if available else None,
        'sourceNonWhitespaceCharacters': sum(not char.isspace() for char in source) if available else None,
        'sourceUtf8Bytes': len(source_bytes) if available else None,
        'sourceLines': len(source.splitlines()) if available else None,
        'available': available,
        'sourceKind': 'submitted_text' if response_status == 'completed' else 'partial_saved_text',
        'truncated': response_status == 'truncated_output',
        'status': status, 'responseStatus': response_status,
        'sourceTextSha256': _sha(source_bytes), 'rawResponseHash': digest(raw),
        'adapter': adapter, 'wrapperRemoved': source != raw,
        'unavailableReason': None if available else 'empty_or_whitespace_only_text',
    }


def measure_report(source_rows, manifest, model):
    """Measure one complete phase report against its original frozen manifest.

    The supported observed cohort has completed or truncated generations.
    Refusals and malformed/unknown provider outcomes fail closed if introduced;
    they must not silently become short proof candidates. Completed prose is
    retained verbatim because it was also sent to the frozen checker.
    """
    _require(isinstance(manifest, dict), 'Source lengths require a frozen manifest')
    run_hash = manifest.get('run_hash')
    _require(isinstance(run_hash, str) and run_hash == digest({k: v for k, v in manifest.items() if k != 'run_hash'}),
             'Frozen manifest run_hash mismatch')
    config = manifest.get('config')
    _require(isinstance(config, dict), 'Frozen manifest configuration missing')
    settings = config.get('generation')
    _require(isinstance(settings, dict), 'Frozen generation settings missing')
    _require(isinstance(model, str) and bool(model) and manifest.get('model_snapshot') == model
             and settings.get('model_snapshot') == model, 'Frozen model identity mismatch')
    _require(manifest.get('config_hash') == digest(config), 'Frozen configuration hash mismatch')
    adapter = settings.get('response_adapter')
    _require(isinstance(adapter, str), 'Frozen response adapter missing')
    prompts.adapt_response('', adapter)  # Reject an unsupported policy before measuring anything.
    slot_rows = manifest.get('slots')
    _require(isinstance(slot_rows, list) and bool(slot_rows), 'Frozen slots missing')
    _require(all(isinstance(s, dict) and isinstance(s.get('attempt_id'), str) for s in slot_rows), 'Invalid frozen slot')
    slots = {s['attempt_id']: s for s in slot_rows}
    _require(len(slots) == len(slot_rows) == manifest.get('scheduled_attempts'), 'Frozen slot identity count mismatch')
    for slot in slots.values():
        _require(slot.get('model_snapshot') == model and slot.get('config_hash') == manifest['config_hash'],
                 'Frozen slot model/config identity mismatch')
        _require(slot.get('prompt_hash') == digest(slot.get('messages')), 'Frozen slot prompt hash mismatch')

    rows = list(source_rows)
    _require(len(rows) == len(slots), 'Source-length report must contain every scheduled slot')
    result, seen = {}, set()
    manifest_file_sha = _serialized_sha(manifest)
    for row in rows:
        _require(isinstance(row, dict), 'Invalid source-length report row')
        aid = row.get('attempt_id')
        _require(isinstance(aid, str) and aid in slots and aid not in seen, 'Duplicate or unscheduled source-length attempt')
        seen.add(aid)
        slot = slots[aid]
        identity = {k: v for k, v in slot.items() if k != 'messages'}
        _require(row.get('record_kind') == 'phase_separated_verified_attempt', 'Source lengths require final phase records')
        _require(row.get('source_run_hash') == run_hash, 'Source-length source run identity mismatch')
        _require(row.get('generation_identity') == identity, 'Source-length frozen generation identity mismatch')
        if 'generation_identity_hash' in row:
            _require(row['generation_identity_hash'] == digest(identity), 'Generation identity hash mismatch')
        if 'source_manifest_sha256' in row:
            _require(row['source_manifest_sha256'] == manifest_file_sha, 'Manifest file binding mismatch')
        generation = row.get('generation')
        _require(isinstance(generation, dict), 'Saved generation missing')
        _require(row.get('generation_hash') == digest(generation), 'Saved generation hash mismatch')
        _require(generation.get('attempt_id') == aid and generation.get('prompt_hash') == slot['prompt_hash'],
                 'Saved generation attempt/prompt mismatch')
        _require(generation.get('generation_settings') == settings and generation.get('returned_model') == model,
                 'Saved generation settings/model mismatch')
        if 'generation_file_sha256' in row:
            _require(row['generation_file_sha256'] == _serialized_sha(generation), 'Generation file binding mismatch')
        raw = generation.get('raw_response')
        _require(isinstance(raw, str), 'Saved raw_response must be text')
        _require(generation.get('raw_response_hash') == digest(raw), 'Saved raw_response hash mismatch')
        response_status = generation.get('generation_status')
        _require(response_status in {'completed', 'truncated_output'}, 'Unsupported response status for submitted-text lengths')
        verification = row.get('verification_result')
        _require(isinstance(verification, dict), 'Saved verification result missing')
        status = row.get('validity_status')
        _require(status == verification.get('validity_status') and outcome_class(status) != 'incomplete',
                 'Verification outcome is inconsistent or incomplete')
        _require(row.get('metric_status') == verification.get('metric_status'), 'Verification metric status mismatch')
        adapted = prompts.adapt_response(raw, adapter)
        if response_status == 'completed':
            _require(row.get('checker_executed') is True, 'Completed generation lacks submitted checker evidence')
            _require(isinstance(verification.get('adapted_response'), str) and verification['adapted_response'] == adapted,
                     'Saved adapted response differs from frozen response adapter')
            _require(verification.get('source_hash') == digest(adapted), 'Saved submitted source hash mismatch')
            _require(status not in {'truncated_output', 'refusal', 'malformed_response'},
                     'Completed response was relabeled as a provider outcome')
        else:
            _require(row.get('checker_executed') is False and status == 'truncated_output',
                     'Truncated response was relabeled as a checked proof')
            if 'adapted_response' in verification:
                _require(isinstance(verification['adapted_response'], str) and verification['adapted_response'] == adapted,
                         'Unexpected saved partial-text adapter mismatch')
        result[aid] = _measure(raw, adapted, adapter, status, response_status)
    _require(seen == set(slots), 'Missing scheduled source-length attempts')
    policy = {
        'version': VERSION,
        'measurement': 'Literal submitted-text lengths, separate from frozen Lean parser-token W_B',
        'characterPolicy': 'Python str length: Unicode code points, not UTF-16 code units or grapheme clusters; no Unicode normalization.',
        'whitespacePolicy': 'sourceNonWhitespaceCharacters counts code points for which Python str.isspace() is false.',
        'unicodeVersion': unicodedata.unidata_version,
        'bytePolicy': 'Length of strict UTF-8 encoding of the exact stored/adapted text.',
        'linePolicy': 'len(text.splitlines()); Unicode line boundaries, no extra line for a trailing line terminator; unavailable when blank.',
        'contentPolicy': 'Retain comments, prose, invalid syntax and every other character; no heuristic extraction, repair or comment removal.',
        'emptyPolicy': 'Empty/whitespace-only submitted text has null source lengths; raw text counts may be zero. No zero-length proof is inferred.',
        'partialPolicy': 'Truncated text is adapted mechanically for length only, never evaluated here; explicitly partial and excluded by default downstream.',
        'supportedResponseStatuses': ['completed', 'truncated_output'],
        'unsupportedResponsePolicy': 'Refusal, malformed and unknown statuses fail closed; their text is not silently treated as a proof candidate.',
        'adapter': adapter,
        'adapterSourceSha256': _sha(Path(prompts.__file__).read_bytes()),
        'measurementSourceSha256': _sha(Path(__file__).read_bytes()),
        'hashPolicy': 'rawResponseHash uses frozen canonical JSON digest; sourceTextSha256 hashes exact UTF-8 bytes, including blank text.',
        'fileBindingPolicy': 'When present, generation/manifest file SHA must equal exact frozen io.json_text serialization of the supplied object.',
        'noRawTextExported': True,
    }
    return {'byAttempt': result, 'policy': policy}
