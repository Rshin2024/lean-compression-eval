"""Offline temporary-tree tests. No SSH, IMDS, live archives or resource actions."""
import gzip
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import archive_host
import remote_archive as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'source'; self.root.mkdir()
        (self.root / 'cloud').mkdir()
        (self.root / 'cloud/worker.py').write_text('print("offline fixture")\n')
        (self.root / 'cloud/status.json').write_text('{"state":"complete"}\n')
        (self.root / 'cloud/events.jsonl').write_text('{"event":"complete"}\n')
        self.allowed = ['cloud/events.jsonl', 'cloud/status.json', 'cloud/worker.py']

    def host(self):
        return {'region': 'westus2', 'ssh_alias': 'lce-u100-westus2',
                'resource_id': f'/subscriptions/{archive.SUBSCRIPTION}/resourceGroups/{archive.GROUP}/providers/Microsoft.Compute/virtualMachines/lce-u100-westus2',
                'files': self.allowed}

    def request(self):
        return {'action': 'inspect', 'host': self.host(), 'source_root': str(archive.SOURCE_ROOT),
                'output_root': str(archive.OUTPUT_ROOT), 'plan_sha256': 'a' * 64, 'worker_sha256': 'b' * 64,
                'attempt': 'offline-test'}

    def test_exact_request_scope(self):
        archive.validate_request(self.request())
        for key, value in [('source_root', '/home/leanrunner'), ('output_root', str(archive.SOURCE_ROOT)),
                           ('attempt', '../escape'), ('plan_sha256', 'wrong')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                archive.validate_request({**self.request(), key: value})

    def test_ownership_requires_exact_group_subscription_name_and_tag(self):
        compute = {'name': 'lce-u100-westus2', 'resourceGroupName': archive.GROUP,
                   'subscriptionId': archive.SUBSCRIPTION, 'tagsList': [{'name': 'task', 'value': archive.TASK}]}
        archive.validate_owner(compute, self.host())
        for key, value in [('name', 'unrelated-vm'), ('resourceGroupName', 'other'),
                           ('subscriptionId', 'other'), ('tagsList', [])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                archive.validate_owner({**compute, key: value}, self.host())

    def test_private_filenames_refused_before_content_read(self):
        (self.root / 'id_ed25519').write_text('offline-not-a-real-key')
        with self.assertRaisesRegex(ValueError, 'Access-material filename'):
            archive.inventory(self.root, self.allowed + ['id_ed25519'])

    def test_secret_like_content_is_never_archived(self):
        (self.root / 'cloud/status.json').write_text('sk-proj-offline-test-not-a-real-key')
        with self.assertRaisesRegex(ValueError, 'Credential-like'):
            archive.inventory(self.root, self.allowed)

    def test_symlink_refused(self):
        (self.root / 'linked.json').symlink_to(self.root / 'cloud/status.json')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            archive.inventory(self.root, self.allowed + ['linked.json'])

    def test_hardlink_refused_only_temporary_files_linked(self):
        os.link(self.root / 'cloud/status.json', self.root / 'hardlink.json')
        with self.assertRaisesRegex(ValueError, 'Hardlinked'):
            archive.inventory(self.root, self.allowed + ['hardlink.json'])

    def test_unreviewed_file_refused(self):
        (self.root / 'new.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            archive.inventory(self.root, self.allowed)

    def test_missing_reviewed_file_refused(self):
        (self.root / 'cloud/status.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            archive.inventory(self.root, self.allowed)

    def test_bytecode_excluded_without_ignoring_links(self):
        (self.root / '__pycache__').mkdir()
        (self.root / '__pycache__/unused.pyc').write_bytes(b'generated-cache')
        result = archive.inventory(self.root, self.allowed)
        self.assertEqual(set(result['files']), set(self.allowed))
        (self.root / '__pycache__/link.pyc').symlink_to(self.root / 'cloud/status.json')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            archive.inventory(self.root, self.allowed)

    def test_live_lock_blocks_snapshot(self):
        import fcntl
        lock_path = self.root / 'cloud/worker.lock'; lock_path.write_text('')
        with lock_path.open() as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'still holds a lock'):
                archive.inventory(self.root, self.allowed + ['cloud/worker.lock'])

    def test_archive_roundtrip_and_source_unchanged(self):
        before = archive.inventory(self.root, self.allowed)
        target = self.base / 'archive.tar.gz'
        archive.make_archive(self.root, before, target)
        result = archive.verify_archive(target, before)
        self.assertEqual(result['files'], len(self.allowed))
        self.assertEqual(archive.inventory(self.root, self.allowed), before)
        with tarfile.open(target, 'r:gz') as stream:
            self.assertTrue(all(x.isreg() for x in stream))

    def test_same_name_source_replacement_blocks_archive(self):
        before = archive.inventory(self.root, self.allowed)
        p = self.root / 'cloud/status.json'; p.write_text('{"state":"different"}\n')
        with self.assertRaisesRegex(ValueError, 'changed while archiving'):
            archive.make_archive(self.root, before, self.base / 'archive.tar.gz')

    def test_archive_is_exclusive_and_never_overwrites(self):
        before = archive.inventory(self.root, self.allowed); target = self.base / 'archive.tar.gz'
        archive.make_archive(self.root, before, target); expected = archive.sha_file(target)
        with self.assertRaises(FileExistsError):
            archive.make_archive(self.root, before, target)
        self.assertEqual(archive.sha_file(target), expected)

    def malicious_archive(self, name, *, kind=tarfile.REGTYPE, body=b'wrong'):
        path = self.base / 'malicious.tar.gz'
        with tarfile.open(path, 'w:gz') as stream:
            member = tarfile.TarInfo(name); member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(body); stream.addfile(member, io.BytesIO(body))
            else:
                member.linkname = '/private/never-read'; stream.addfile(member)
        return path

    def test_tar_path_traversal_refused_without_extraction(self):
        expected = archive.inventory(self.root, self.allowed)
        with self.assertRaisesRegex(ValueError, 'Unsafe archive member'):
            archive.verify_archive(self.malicious_archive('../outside'), expected)

    def test_tar_link_member_refused_without_following(self):
        expected = archive.inventory(self.root, self.allowed)
        with self.assertRaisesRegex(ValueError, 'linked archive member'):
            archive.verify_archive(self.malicious_archive(self.allowed[0], kind=tarfile.SYMTYPE), expected)

    def test_tar_payload_tamper_refused(self):
        expected = archive.inventory(self.root, self.allowed)
        body = b'X' * expected['files'][self.allowed[0]]['bytes']
        with self.assertRaisesRegex(ValueError, 'member hash differs'):
            archive.verify_archive(self.malicious_archive(self.allowed[0], body=body), expected)

    def test_truncated_gzip_refused(self):
        expected = archive.inventory(self.root, self.allowed); path = self.base / 'archive.tar.gz'
        archive.make_archive(self.root, expected, path); path.write_bytes(path.read_bytes()[:-8])
        with self.assertRaises((EOFError, tarfile.ReadError, OSError)):
            archive.verify_archive(path, expected)

    def test_explicit_plan_authorization_required_before_gates_or_network(self):
        plan = {'hosts': {'known': {}}}
        with patch.object(archive_host, 'load_plan', return_value=(plan, 'a' * 64)), \
             patch.object(archive_host, 'local_gates') as gates, patch.object(archive_host, 'remote_call') as remote:
            with self.assertRaisesRegex(ValueError, 'reviewed plan SHA'):
                archive_host.run('archive', 'known', None)
            gates.assert_not_called(); remote.assert_not_called()

    def test_newer_graph_mtime_blocks_old_hash_receipt(self):
        path = self.root / 'cloud/status.json'
        cutoff = path.stat().st_mtime
        archive_host.corroborate_backup_file(path, {'bytes': path.stat().st_size}, cutoff)
        os.utime(path, (cutoff + 10, cutoff + 10))
        with self.assertRaisesRegex(ValueError, 'newer than full-hash receipt'):
            archive_host.corroborate_backup_file(path, {'bytes': path.stat().st_size}, cutoff)


if __name__ == '__main__':
    unittest.main()
