"""Narrow task-only operational archive primitives; no model or resource actions."""
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import urllib.request

sys.dont_write_bytecode = True
SOURCE_ROOT = Path('/home/leanrunner/om100-unlimited/work/unlimited-eval-100-20261014')
REPO = Path('/home/leanrunner/om100-unlimited/outputs/lean-compression-eval')
OUTPUT_ROOT = Path('/home/leanrunner/om100-unlimited/operational-archives-v1')
GROUP = 'lce-om100-unlimited-20261014'
SUBSCRIPTION = 'bea88ce4-de82-43a3-8d6b-a082075a8ee4'
TASK = 'om100-unlimited-20261014'
REGIONS = {'westus2', 'eastus2', 'centralus', 'southcentralus', 'northcentralus', 'westus'}
SECRET_NAMES = {'id_ed25519', 'id_rsa', 'id_ecdsa', 'ssh-config', '.env', 'credentials', 'credentials.json'}
SECRET_MARKERS = (b'-----BEGIN OPENSSH PRIVATE KEY-----', b'-----BEGIN RSA PRIVATE KEY-----',
                  b'-----BEGIN PRIVATE KEY-----', b'sk-proj-', b'sk-ant-')
MAX_BYTES = 512 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, value):
    with Path(path).open('x') as f:
        os.chmod(path, 0o600)
        json.dump(value, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


def safe_relative(name):
    p = PurePosixPath(name)
    require(isinstance(name, str) and name and not p.is_absolute() and p.as_posix() == name
            and all(x not in {'', '.', '..'} for x in p.parts), 'Unsafe archive member path')
    return p


def metadata(info):
    return {'device': info.st_dev, 'inode': info.st_ino, 'bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
            'mode': info.st_mode, 'links': info.st_nlink}


def validate_owner(compute, host):
    region = host['region']
    require(region in REGIONS and host['ssh_alias'] == 'lce-u100-' + region, 'Unknown fleet host')
    expected_id = f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.Compute/virtualMachines/{host["ssh_alias"]}'
    require(host['resource_id'].lower() == expected_id.lower(), 'Plan resource identity differs')
    require(compute.get('subscriptionId', '').lower() == SUBSCRIPTION.lower()
            and compute.get('resourceGroupName', '').lower() == GROUP.lower()
            and compute.get('name') == host['ssh_alias'], 'SSH host is not the planned task VM')
    tags = {x.get('name'): x.get('value') for x in compute.get('tagsList', [])}
    require(tags.get('task') == TASK, 'VM task ownership tag differs')
    return {'name': compute['name'], 'resource_group': compute['resourceGroupName'],
            'subscription_id': compute['subscriptionId'], 'resource_id': host['resource_id'], 'task_tag': TASK}


def ownership(host):
    # Compute metadata only: never request identity tokens or user-data.
    req = urllib.request.Request('http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01',
                                 headers={'Metadata': 'true'})
    with urllib.request.urlopen(req, timeout=5) as response:
        compute = json.load(response)
    return validate_owner(compute, host)


def validate_request(request):
    require(request.get('source_root') == str(SOURCE_ROOT), 'Archive source must be the exact task WORK directory')
    require(request.get('output_root') == str(OUTPUT_ROOT), 'Archive output must be the dedicated sibling directory')
    require(request.get('action') in {'inspect', 'archive'}, 'Unknown archive action')
    require(re.fullmatch('[0-9a-f]{64}', request.get('plan_sha256', '')) is not None, 'Invalid plan digest')
    require(re.fullmatch('[0-9a-f]{64}', request.get('worker_sha256', '')) is not None, 'Invalid worker digest')
    require(re.fullmatch('[A-Za-z0-9_.-]{1,80}', request.get('attempt', '')) is not None, 'Unsafe archive attempt label')
    host = request.get('host') or {}
    require(host.get('ssh_alias') == 'lce-u100-' + host.get('region', '') and host.get('region') in REGIONS,
            'Unknown host alias')
    names = host.get('files')
    require(isinstance(names, list) and names and names == sorted(set(names)), 'Exact file allowlist missing or duplicated')
    for name in names:
        safe_relative(name)
    return host


def inventory(root, allowed, *, check_locks=True):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Missing or linked source root')
    # Do not traverse a linked ancestor to reach an otherwise ordinary directory.
    require(not any(p.is_symlink() for p in root.parents), 'Linked source ancestor')
    rows, directories, found = {}, {}, set()
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat(); relative = path.relative_to(root).as_posix()
        require(not stat.S_ISLNK(info.st_mode), 'Symlink in operational source tree')
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'Special file in operational source tree')
        if stat.S_ISDIR(info.st_mode):
            if '__pycache__' not in path.relative_to(root).parts:
                directories[relative] = metadata(info)
            continue
        require(info.st_nlink == 1, 'Hardlinked operational file is forbidden')
        require(path.name not in SECRET_NAMES and path.suffix.lower() not in {'.pem', '.p12', '.pfx', '.key'},
                'Access-material filename in operational tree')
        if '__pycache__' in path.relative_to(root).parts or path.suffix == '.pyc':
            continue
        require(relative in allowed, 'Unreviewed operational filename: ' + relative)
        found.add(relative)
        require(info.st_size <= MAX_BYTES, 'Operational file exceeds archive limit')
        if check_locks and path.suffix == '.lock':
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError('An operational worker still holds a lock') from error
            finally:
                os.close(fd)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(fd, 'rb') as stream:
                before = metadata(os.fstat(stream.fileno()))
                require(before == metadata(info), 'File identity changed before inventory read')
                body = stream.read()
                after = metadata(os.fstat(stream.fileno()))
            require(before == after == metadata(path.lstat()), 'File changed during inventory read')
        except Exception:
            raise
        require(not any(marker in body for marker in SECRET_MARKERS), 'Credential-like material found; archive refused')
        rows[relative] = {'bytes': len(body), 'sha256': sha_bytes(body), 'stat': before}
    require(found == set(allowed), 'Reviewed operational files are missing')
    require(sum(row['bytes'] for row in rows.values()) <= MAX_BYTES, 'Operational tree exceeds archive byte limit')
    return {'files': rows, 'directories': directories,
            'bytes': sum(row['bytes'] for row in rows.values()), 'file_count': len(rows)}


def remote_gates(host):
    fleet_path = SOURCE_ROOT / 'cloud/verification-fleet-plan.json'
    require(sha_file(fleet_path) == host['fleet_plan_sha256'], 'Remote fleet plan differs')
    assigned = [x for x in read(fleet_path)['assignments'] if x['ssh_alias'] == host['ssh_alias']]
    require({x['model'] for x in assigned} == {x['model'] for x in host['models']}, 'Archive must cover every model assigned to host')
    by_model = {x['model']: x for x in assigned}
    evidence = []
    for entry in host['models']:
        require(all(entry[k] == by_model[entry['model']][k] for k in ['directory', 'manifest_sha256', 'run_hash']),
                'Remote assigned model binding differs')
        model = entry['model']; run = REPO / 'runs' / entry['directory']
        manifest = read(run / 'manifest.json')
        require(sha_file(run / 'manifest.json') == entry['manifest_sha256'] and manifest['run_hash'] == entry['run_hash'],
                'Remote frozen manifest differs')
        require((run / 'manifest.json').stat().st_nlink == 1, 'Live manifest is hardlinked')
        ids = {s['attempt_id'] for s in manifest['slots']}
        require(len(ids) == 1200, 'Remote manifest schedule differs')
        for relative in ['generations', 'evaluations/v2_threads1']:
            paths = {p.stem for p in (run / relative).glob('*.json') if re.fullmatch('[0-9a-f]{64}', p.stem)}
            require(paths == ids, 'Remote model generation/verification is incomplete')
        revision = run / 'evaluations/v2_threads1'
        require(not list(revision.rglob('*.claim')) and not list(revision.rglob('*.retained')),
                'Verifier claims or retained incidents remain')
        statuses = []
        for suffix in ['', '-aux-v1'] + (['-sol-burst-v1'] if model == 'gpt-6-sol' else []):
            path = SOURCE_ROOT / 'cloud' / (model + suffix + '-status.json')
            status = read(path)
            require(status.get('state') == 'complete' and status.get('checked') == status.get('generated') == 1200
                    and status.get('active_workers') == 0 and not status.get('errors'), 'Cloud checker partition not drained')
            statuses.append({'path': str(path.relative_to(SOURCE_ROOT)), 'sha256': sha_file(path)})
        job_path = SOURCE_ROOT / 'reporting/jobs' / (model + '.json'); job = read(job_path)
        require(job.get('state') == 'complete', 'Cloud report is not complete')
        report = REPO / 'runs/om100-max-suite-high-20261014' / ('report-' + model) / 'report-file-manifest.json'
        require(sha_file(report) == entry['report_manifest_sha256'] == job.get('manifest_sha256'), 'Cloud report manifest binding differs')
        evidence.append({'model': model, 'statuses': statuses, 'report_job_sha256': sha_file(job_path),
                         'report_manifest_sha256': sha_file(report), 'generation_count': 1200, 'verification_count': 1200})
    return evidence


def make_archive(root, expected, destination):
    # Every member is byte-copied into a regular tar member; tar never discovers
    # links or follows symlinks. Secret detection precedes each member write.
    with Path(destination).open('xb') as raw:
        os.chmod(destination, 0o600)
        with gzip.GzipFile(fileobj=raw, filename='', mode='wb', compresslevel=3, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, item in sorted(expected['files'].items()):
                    path = Path(root) / safe_relative(name)
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(fd, 'rb') as stream:
                        before = metadata(os.fstat(stream.fileno())); body = stream.read(); after = metadata(os.fstat(stream.fileno()))
                    require(before == after == item['stat'] == metadata(path.lstat()), 'Source metadata changed while archiving')
                    require(len(body) == item['bytes'] and sha_bytes(body) == item['sha256'], 'Source content changed while archiving')
                    require(not any(marker in body for marker in SECRET_MARKERS), 'Credential-like content refused')
                    entry = tarfile.TarInfo(name); entry.size = len(body)
                    entry.mode = stat.S_IMODE(item['stat']['mode']); entry.mtime = item['stat']['mtime_ns'] // 1000000000
                    entry.uid = entry.gid = 0; entry.uname = entry.gname = ''
                    archive.addfile(entry, io.BytesIO(body))
        raw.flush(); os.fsync(raw.fileno())


def verify_archive(path, expected):
    found = {}
    with gzip.open(path, 'rb') as compressed:
        with tarfile.open(fileobj=compressed, mode='r|') as archive:
            for item in archive:
                safe_relative(item.name)
                require(item.isreg() and item.name not in found and item.name in expected['files'], 'Unexpected or linked archive member')
                wanted = expected['files'][item.name]
                require(item.size == wanted['bytes'], 'Archive member size differs')
                stream = archive.extractfile(item); h = hashlib.sha256(); size = 0
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    size += len(block); h.update(block)
                require(size == wanted['bytes'] and h.hexdigest() == wanted['sha256'], 'Archive member hash differs')
                found[item.name] = {'bytes': size, 'sha256': h.hexdigest()}
        while compressed.read(1024 * 1024):
            pass  # Check the full gzip trailer/CRC, without filesystem extraction.
    require(set(found) == set(expected['files']), 'Archive members are missing')
    return {'files': len(found), 'bytes': sum(x['bytes'] for x in found.values()), 'archive_sha256': sha_file(path)}


def main():
    request = json.load(sys.stdin); host = validate_request(request)
    owner = ownership(host); gates = remote_gates(host)
    before = inventory(SOURCE_ROOT, host['files'])
    if request['action'] == 'inspect':
        print(json.dumps({'status': 'ready', 'host': host['ssh_alias'], 'owner': owner, 'gates': gates,
                          'file_count': before['file_count'], 'bytes': before['bytes']})); return
    output = OUTPUT_ROOT / host['ssh_alias'] / request['plan_sha256'] / request['attempt']
    require(not output.exists(), 'Archive attempt already exists; preserve it and use a new attempt label')
    require(not any(p.is_symlink() for p in [output, *output.parents] if p.exists()), 'Linked archive output ancestor')
    output.mkdir(parents=True, exist_ok=False)
    archive = output / 'operational.tar.gz'; inventory_path = output / 'inventory.json'
    write_new(inventory_path, before)
    make_archive(SOURCE_ROOT, before, archive)
    checked = verify_archive(archive, before)
    after = inventory(SOURCE_ROOT, host['files'])
    require(before == after and remote_gates(host) == gates, 'Operational files or completion gates changed during archive')
    require(ownership(host) == owner, 'VM ownership changed during archive')
    receipt = {'schema_version': 1, 'status': 'verified_remote_archive', 'host': host['ssh_alias'],
               'created_at_utc': stamp(), 'owner': owner, 'plan_sha256': request['plan_sha256'],
               'worker_source_sha256': request['worker_sha256'], 'source_root': str(SOURCE_ROOT),
               'archive': str(archive), 'archive_bytes': archive.stat().st_size,
               'inventory': str(inventory_path), 'inventory_sha256': sha_file(inventory_path), **checked,
               'source_before_after_identical': True, 'source_files_modified': False,
               'model_calls': 0, 'resource_changes': False, 'gates': gates}
    write_new(output / 'receipt.json', receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
