"""Disjoint workers partition one immutable schedule without duplicate calls."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_provider_followups import RunFixture, reply
from lean_compression.io import read_json, write_json
from lean_compression.providers import generate_run


class GenerationShardTests(RunFixture):
    def test_shards_cover_every_slot_once_and_resume_without_requests(self):
        original = (self.run / "manifest.json").read_bytes()
        with self.provider(lambda request: reply()):
            for index in range(4):
                before = {p.stem for p in (self.run / "generations").glob("*.json")}
                result = generate_run(self.run, shard_index=index, shard_count=4)
                after = {p.stem for p in (self.run / "generations").glob("*.json")}
                expected = {s["attempt_id"] for i, s in enumerate(self.slots) if i % 4 == index}
                self.assertEqual(after - before, expected)
                self.assertEqual(result["new_generation_records"], len(expected))
            count = len(self.requests)
            for index in range(4):
                self.assertEqual(generate_run(self.run, shard_index=index, shard_count=4)["new_generation_records"], 0)
            self.assertEqual(len(self.requests), count)
        self.assertEqual(count, len(self.slots))
        self.assertEqual((self.run / "manifest.json").read_bytes(), original)

    def test_stale_call_blocks_only_its_partition_and_is_not_repeated(self):
        marker = self.run / "inflight" / (self.slots[0]["attempt_id"] + ".json")
        write_json(marker, {"attempt_id": self.slots[0]["attempt_id"], "started_at": "2026-10-13T00:00:00+00:00"})
        with self.provider(lambda request: reply()):
            self.assertGreater(generate_run(self.run, shard_index=1, shard_count=2)["new_generation_records"], 0)
            sent = len(self.requests)
            with self.assertRaisesRegex(RuntimeError, "in-flight"):
                generate_run(self.run, shard_index=0, shard_count=2)
            self.assertEqual(len(self.requests), sent)

    def test_invalid_partition_refused_before_provider_access(self):
        with patch("lean_compression.providers.urllib.request.urlopen") as http:
            for index, count in [(0, 0), (-1, 2), (2, 2), (True, 2)]:
                with self.assertRaises(ValueError):
                    generate_run(self.run, shard_index=index, shard_count=count)
            http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
