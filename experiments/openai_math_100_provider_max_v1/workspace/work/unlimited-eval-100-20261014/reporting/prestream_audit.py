"""Read-only exact19 pre-header disconnect release validation."""
import json
from lean_compression.io import digest,file_hash

BATCH_SHA='18c0043980422d40552c3b6b0048cbebab747dfa45c9a4db1debb3d415de2f61'
ACTIVE_SHA='c882a948dfe983ec45bcf417326b82af8fe54808980688e37fbab92cf0055e76'
HELPER_SHA='d5745fd1bbae676fb1b6b9018a0bd94021d2c68ea20dae977247c7dca9a385df'
REVIEW_SHA='fd269035f1a02928c59585ffdf7d9585b12a62a40f765204254a12928ccfd215'
KIND='one_use_original_prestream_disconnect_release_v1'

def require(ok,message):
    if not ok:raise ValueError(message)


def project(work,run,events,streams,amendments):
    releases=[e for e in events if e.get('event')=='authorized_unknown_release'
              and e.get('reconciliation',{}).get('kind')==KIND]
    if not releases:return events,streams,[],{}
    root=work/'recovery/fable-prestream-disconnects/proposal-v1'
    require(file_hash(root/'proposal.json')==BATCH_SHA,'Exact19 batch changed')
    require(file_hash(root/'active-release-bindings.json')==ACTIVE_SHA,'Exact19 active receipts changed')
    require(file_hash(root/'release_pre_stream_disconnects.py')==HELPER_SHA,'Exact19 helper changed')
    require(file_hash(root/'root-review.json')==REVIEW_SHA,'Exact19 operator review changed')
    batch=json.loads((root/'proposal.json').read_text());active=json.loads((root/'active-release-bindings.json').read_text())
    require(file_hash(work/'launch_ready.json')==batch['launch_plan_sha256'],'Prestream release binds another launch')
    require(len(batch['attempt_ids'])==19 and len(set(batch['attempt_ids']))==19
            and batch['maximum_release_per_attempt']==1 and batch['unknown_replacement_retry_authorized'] is False,
            'Exact19 scope changed')
    require(active['batch_proposal_sha256']==BATCH_SHA and file_hash(root/'batch-release-receipt.json')==active['batch_receipt_sha256'],
            'Exact19 batch receipt mismatch')
    bindings={b['attempt_id']:b for b in active['additional_release_bindings']}
    require(set(bindings)==set(batch['attempt_ids']),'Exact19 receipt identities differ')
    files={str(p):file_hash(p) for p in [root/'proposal.json',root/'active-release-bindings.json',
            root/'root-review.json',root/'batch-release-intent.json',root/'batch-release-receipt.json']}
    removed=set();disclosures=[]
    for release in releases:
        aid=release['attempt_id'];require(aid in bindings,'Prestream release outside exact19')
        sequence=[e for e in events if e.get('attempt_id')==aid]
        require(sum(e['event']=='authorized_unknown_release' for e in sequence)==1,'Prestream release repeated')
        bound=bindings[aid];proposal_path=work/bound['proposal_path'];receipt_path=work/bound['receipt_path']
        require(file_hash(proposal_path)==bound['proposal_sha256'] and file_hash(receipt_path)==bound['receipt_sha256'],'Prestream proposal/receipt changed')
        proposal=json.loads(proposal_path.read_text());receipt=json.loads(receipt_path.read_text());directory=proposal_path.parent
        for name,sha in proposal['preserved_evidence_hashes'].items():
            path=directory/name;require(file_hash(path)==sha,'Original prestream evidence changed');files[str(path)]=sha
        evidence=json.loads((directory/'evidence.json').read_text());prefix=evidence['transport_prefix'];n=len(prefix)
        require(sequence[:n]==prefix and sequence[n]==release,'Original prestream request prefix changed')
        require(prefix[-1]['event']=='outcome_unknown' and prefix[-1]['type']=='OSError'
                and prefix[-2]['event']=='request_started','Original prestream outcome differs')
        require(not evidence['stream_events'] and evidence['saved_capture_present'] is False
                and evidence['saved_generation_present'] is False and evidence['saved_stream_present'] is False,
                'Prestream case had a saved response or stream')
        require(file_hash(run/'manifest.json')==proposal['manifest_sha256']==evidence['manifest_sha256']
                and evidence['run_directory']==run.name and evidence['attempt_id']==aid,'Prestream source identity differs')
        require(digest(prefix)==receipt['transport_prefix_hash']==evidence['transport_prefix_hash']
                and file_hash(directory/'original-transport-prefix.jsonl')==receipt['transport_prefix_bytes_sha256'],
                'Original prestream event bytes/hash differ')
        intent=json.loads((directory/'release-intent.json').read_text())
        require(intent==release['reconciliation'] and all(receipt.get(k)==v for k,v in intent.items()),'Prestream release intent mismatch')
        require(receipt['state']=='released_once' and receipt['original_marker_removed'] is True
                and receipt['maximum_release_count']==1 and receipt['original_charge_usd'] is None
                and receipt['original_outcome']=='unknown' and receipt['new_model_calls']==0
                and receipt['batch_proposal_sha256']==BATCH_SHA and receipt['helper_sha256']==HELPER_SHA,
                'Prestream release scope/billing changed')
        require(sum({k:v for k,v in e.items() if k!='recorded_at'}=={**intent,'resolution':'released_for_one_authorized_replacement_request'}
                    for e in amendments)==1,'Prestream amendment missing/duplicated')
        count=sum(e['event']=='request_started' for e in sequence[n+1:])
        require(count<=1,'Prestream authorization consumed by more than one replacement')
        removed.update(id(e) for e in sequence[:n+1])
        disclosures.append({'attempt_id':aid,'kind':KIND,'category':'prestream_disconnect',
            'proposal_sha256':bound['proposal_sha256'],'batch_proposal_sha256':BATCH_SHA,
            'authorization_source':receipt['authorization_basis'],'maximum_replacements':1,
            'original_outcome':'unknown','original_charge_usd':None,'original_provider_response_id_hash':None,
            'original_transport_prefix_sha256':evidence['transport_prefix_bytes_sha256'],
            'original_recorded_at_utc':prefix[-1]['recorded_at'],'replacement_request_count':count})
        for p in [proposal_path,receipt_path,directory/'release-intent.json']:files[str(p)]=file_hash(p)
    return [e for e in events if id(e) not in removed],streams,disclosures,files
