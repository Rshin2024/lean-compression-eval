"""Local file/source guards for the inactive recovery proposal."""
from pathlib import Path
import hashlib
import json
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
WORK = HERE.parents[2]
REPO = WORK.parents[1] / 'outputs/lean-compression-eval'
RUN = REPO / 'runs/om100-gpt-6-astra-high'
sys.path.insert(0, str(REPO / 'src'))
from lean_compression.io import evaluator_hash, file_hash, digest
from lean_compression.providers import response_payload
from contract import ensure, authorize


def read(path):
    return json.loads(Path(path).read_bytes())


def events(path):
    return [json.loads(x) for x in Path(path).read_bytes().splitlines() if x.strip()] if Path(path).exists() else []


def load(approval_path=None):
    path = HERE / 'proposal.json'
    raw = path.read_bytes(); proposal = json.loads(raw)
    ensure(evaluator_hash() == proposal['source_pins']['core_source_hash'], 'Frozen core source changed')
    for relative, expected in proposal['source_pins']['files'].items():
        ensure(file_hash(WORK / relative) == expected, f'Pinned operational source changed: {relative}')
    for name, expected in proposal['proposal_source_sha256'].items():
        ensure(file_hash(HERE / name) == expected, f'Proposal source changed: {name}')
    manifest = read(RUN / 'manifest.json')
    ensure(file_hash(RUN / 'manifest.json') == proposal['manifest_sha256'] and manifest['run_hash'] == proposal['run_hash'], 'Frozen manifest changed')
    ensure(manifest['engine_hash'] == file_hash(REPO / 'lean/Evaluator.lean'), 'Lean engine changed')
    slots = {s['attempt_id']: s for s in manifest['slots']}
    for binding in proposal['attempts']:
        slot = slots[binding['attempt_id']]
        ensure(digest(slot) == binding['slot_hash'], 'Pinned slot differs')
        payload = response_payload(slot, manifest['config']['generation'])
        ensure(digest(payload) == binding['request_hash'] and payload['store'] is False, 'Pinned request differs')
    approval = read(approval_path) if approval_path else None
    if approval is not None:
        authorize(proposal, hashlib.sha256(raw).hexdigest(), approval)
    return proposal, hashlib.sha256(raw).hexdigest(), manifest, approval
