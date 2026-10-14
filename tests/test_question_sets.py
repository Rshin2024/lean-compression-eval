import fcntl
import hashlib
import html
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.question_sets import adopt_question_set, load_question_set, STATUS


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fixture():
    groups = ["algebra", "analysis", "combinatorics", "cs_probability"]
    rows, references = [], []
    for index in range(100):
        name = f"q{index + 1:03d}"
        statement = f"theorem {name} (P : Prop) : P → P"
        reference = statement + " := by\n  exact fun h => h -- PRIVATE_REFERENCE"
        references.append(reference)
        rows.append({
            "id": f"OM100-{index + 1:03d}", "task_id": f"family-{index % 19}",
            "family": f"{index % 19:03d}", "selection_group": groups[index // 25],
            "title": f"Question {index + 1}", "question": "Prove the given implication.",
            "statement": statement, "subject": "Logic", "declaration_name": f"Demo.{name}",
            "source_path": "lean/OAI/Fixture.lean", "source_commit": "a" * 40, "hf_commit": "b" * 40,
            "start_line": 2 * index + 1, "end_line": 2 * index + 2, "reference_line_count": 2,
            "reference_declaration_sha256": digest(reference.encode()),
            "proof_summary": "PRIVATE_SUMMARY", "extraction_notes": "PRIVATE_NOTES",
            "standalone_extraction_compiled": False,
        })
    source = ("\n".join(references) + "\n").encode()
    for row in rows:
        row["source_sha256"] = digest(source)
    return rows, references, source


def catalogue(rows, references):
    def code(value):
        return "<pre><code>" + html.escape(value) + "</code></pre>"
    cards = []
    for row, reference in zip(rows, references):
        cards.append(
            f'<article class="candidate" id="{row["id"].lower()}" '
            f'data-family="{row["family"]}" data-subject="{row["subject"]}">'
            '<div class="declaration"><code>' + row["declaration_name"] + '</code></div>'
            '<details class="statement">' + code(row["statement"]) + '</details>'
            '<details class="reference">' + code(reference) + '</details>'
            '<div class="hash"><code>' + row["source_sha256"] + '</code></div></article>'
        )
    # This remains inert data; no browser or JS interpreter is part of adoption.
    return ('<!doctype html><html><body><script>throw new Error("DO NOT EXECUTE");</script>'
            + "".join(cards) + '</body></html>').encode()


class QuestionSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rows, self.references, self.source = fixture()
        self.candidates = self.root / "input.jsonl"
        self.catalogue = self.root / "input.html"
        self.destination = self.root / "question-set-v1"
        self.save_inputs()

    def save_inputs(self):
        self.candidates.write_bytes(b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in self.rows))
        self.catalogue.write_bytes(catalogue(self.rows, self.references))

    def adopt(self, **kwargs):
        return adopt_question_set(self.candidates, self.catalogue, self.destination, **kwargs)

    def test_freezes_exact_private_inputs_and_only_allowlisted_public_fields(self):
        result = self.adopt()
        self.assertEqual(result["status"]["status"], STATUS)
        self.assertIs(result["status"]["model_generation_enabled"], False)
        self.assertEqual(result["manifest"]["counts"]["tasks"], 100)
        self.assertEqual(result["manifest"]["counts"]["families"], 19)
        self.assertEqual(len(result["selection"]), 100)
        self.assertEqual(result["selection"][0]["record_kind"], "selected_question_v1")
        self.assertEqual(result["selection"][0]["task_id"], "OM100-001")
        self.assertEqual(result["selection"][19]["source_parent_task_id"], "family-0")
        public = (self.destination / "selection.jsonl").read_text()
        for private in ("PRIVATE_REFERENCE", "PRIVATE_NOTES", "PRIVATE_SUMMARY", "proof_summary", "reference_declaration\""):
            self.assertNotIn(private, public)
        self.assertEqual((self.destination / "private/candidates.jsonl").read_bytes(), self.candidates.read_bytes())
        self.assertEqual((self.destination / "private/catalogue.html").read_bytes(), self.catalogue.read_bytes())
        refs = [json.loads(line) for line in (self.destination / "private/references.jsonl").read_text().splitlines()]
        self.assertEqual([r["reference_declaration"] for r in refs], self.references)
        self.assertTrue(all(set(v.values()) == {"not_checked"} for v in result["status"]["per_task"].values()))

    def test_reuse_is_idempotent_but_different_inputs_are_rejected(self):
        first = self.adopt()
        self.assertEqual(first, self.adopt())
        self.rows[0]["proof_summary"] = "changed private metadata"
        self.save_inputs()
        with self.assertRaisesRegex(ValueError, "different inputs"):
            self.adopt()
        self.assertEqual(first, load_question_set(self.destination))

    def test_public_private_status_and_manifest_tampering_are_rejected(self):
        self.adopt()
        for relative in ("selection.jsonl", "private/references.jsonl", "status.json", "manifest.json"):
            with self.subTest(relative=relative):
                path = self.destination / relative
                original = path.read_bytes()
                path.write_bytes(original.replace(b"not_checked", b"verified", 1) if relative == "status.json"
                                 else original.replace(b"OM100-001", b"OM100-999", 1))
                with self.assertRaisesRegex(ValueError, "integrity"):
                    self.adopt()
                path.write_bytes(original)

    def test_unexpected_file_or_symlink_invalidates_bundle(self):
        self.adopt()
        extra = self.destination / "extra.txt"
        extra.write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "inventory"):
            load_question_set(self.destination)
        extra.unlink()
        extra.symlink_to(self.candidates)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            load_question_set(self.destination)

    def test_duplicate_id_and_unexpected_candidate_field_are_rejected(self):
        self.rows[1]["id"] = self.rows[0]["id"]
        self.save_inputs()
        with self.assertRaisesRegex(ValueError, "unique, contiguous"):
            self.adopt()
        self.rows[1]["id"] = "OM100-002"
        self.rows[0]["proof"] = "by trivial"
        self.save_inputs()
        with self.assertRaisesRegex(ValueError, "Unexpected candidate fields"):
            self.adopt()
        self.assertFalse(self.destination.exists())

    def test_duplicate_declaration_and_wrong_group_count_are_rejected(self):
        self.rows[1]["declaration_name"] = self.rows[0]["declaration_name"]
        self.save_inputs()
        with self.assertRaisesRegex(ValueError, "duplicate declaration"):
            self.adopt()
        self.rows[1]["declaration_name"] = "Demo.q002"
        self.rows[0]["selection_group"] = "analysis"
        self.save_inputs()
        with self.assertRaisesRegex(ValueError, "25 candidates"):
            self.adopt()

    def test_malformed_html_and_modified_reference_are_rejected(self):
        original = self.catalogue.read_bytes()
        self.catalogue.write_bytes(original.replace(b"</code></pre>", b"</pre></code>", 1))
        with self.assertRaisesRegex(ValueError, "Malformed catalogue"):
            self.adopt()
        self.catalogue.write_bytes(original.replace(b"PRIVATE_REFERENCE", b"CHANGED_REFERENCE", 1))
        with self.assertRaisesRegex(ValueError, "reference declaration hash"):
            self.adopt()

    def test_source_verification_checks_bytes_and_exact_spans(self):
        with patch("lean_compression.question_sets._fetch_source", return_value=self.source) as fetch:
            result = self.adopt(verify_upstream=True)
        fetch.assert_called_once_with("a" * 40, "lean/OAI/Fixture.lean")
        self.assertTrue(result["manifest"]["upstream_sources_verified"])
        self.assertEqual(result["status"]["source_verification"], "verified")
        self.assertEqual((self.destination / "private/sources/lean/OAI/Fixture.lean").read_bytes(), self.source)
        self.assertEqual(load_question_set(self.destination), result)

    def test_failed_source_hash_leaves_no_half_destination(self):
        with patch("lean_compression.question_sets._fetch_source", return_value=b"bad source"):
            with self.assertRaisesRegex(ValueError, "whole-file hash"):
                self.adopt(verify_upstream=True)
        self.assertFalse(self.destination.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         [".question-set-v1.adoption.lock", "input.html", "input.jsonl"])

    def test_matching_file_hash_with_wrong_reference_span_is_rejected(self):
        other = self.source.replace(b"PRIVATE_REFERENCE", b"OTHER_REFERENCE", 1)
        for row in self.rows:
            row["source_sha256"] = digest(other)
        self.save_inputs()
        with patch("lean_compression.question_sets._fetch_source", return_value=other):
            with self.assertRaisesRegex(ValueError, "source span"):
                self.adopt(verify_upstream=True)

    def test_unsafe_source_path_and_unpinned_commit_are_rejected_before_fetch(self):
        for key, value in (("source_path", "lean/OAI/../../outside.lean"), ("source_commit", "main")):
            original = self.rows[0][key]
            self.rows[0][key] = value
            self.save_inputs()
            with patch("lean_compression.question_sets._fetch_source") as fetch:
                with self.assertRaises(ValueError):
                    self.adopt(verify_upstream=True)
                fetch.assert_not_called()
            self.rows[0][key] = original

    def test_verified_upgrade_requires_new_immutable_destination(self):
        self.adopt()
        with self.assertRaisesRegex(ValueError, "new destination"):
            self.adopt(verify_upstream=True)

    def test_adoption_lock_excludes_live_writer_and_releases_without_deletion(self):
        lock = self.root / ".question-set-v1.adoption.lock"
        with lock.open("wb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "already active"):
                self.adopt()
        self.assertEqual(self.adopt()["status"]["status"], STATUS)

    def test_size_limit_and_unicode_jsonl_line_separators(self):
        self.rows[0]["question"] = "First sentence\u2028second sentence\u2029third sentence"
        self.save_inputs()
        self.assertEqual(self.adopt()["selection"][0]["question"], self.rows[0]["question"])
        with patch("lean_compression.question_sets.MAX_CANDIDATE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "size limit"):
                self.adopt()


if __name__ == "__main__":
    unittest.main()
