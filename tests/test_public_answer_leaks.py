"""Public files never carry a private reference proof.

README.md, docs/, src/ and tests/ must not quote a private reference proof, and the readiness report built from the
committed evidence (the README's readiness command) must name neither a private proof nor the tactic of a one-tactic
reference.  Failure messages cite files and task IDs only, never the proof text.
"""
import ast
from contextlib import redirect_stderr, redirect_stdout
import html
from io import StringIO
import json
from pathlib import Path
import re
import tempfile
import unittest

from lean_compression.cli import main
from lean_compression.dataset import SHORT_EXACT_MATCH_MIN_CHARS, SINGLE_TACTIC, proof_tokens
from lean_compression.io import read_jsonl

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data" / "private"
REFERENCE_FILES = ("references.jsonl", "smoke_references.jsonl", "fresh_main_references.jsonl",
                   "library_candidate_references.jsonl")


def compact(text):
    return " ".join(text.split())


def private_proofs():
    """Whitespace-normalized private reference proofs -> their task IDs.  A proof is listed if it has at least
    SHORT_EXACT_MATCH_MIN_CHARS (9) characters; a tactic script is also listed without its leading "by" (a quote of
    the script alone) when that still has 9 characters and more than one token."""
    proofs = {}
    for name in REFERENCE_FILES:
        for row in read_jsonl(PRIVATE / name):
            text = compact(row["proof"])
            variants = [text]
            if text.startswith("by ") and len(text) - 3 >= SHORT_EXACT_MATCH_MIN_CHARS and len(proof_tokens(text[3:])) > 1:
                variants.append(text[3:])
            for variant in variants:
                if len(variant) >= SHORT_EXACT_MATCH_MIN_CHARS:
                    proofs.setdefault(variant, set()).add(row["task_id"])
    return proofs


def quoted(texts, proofs):
    """The task IDs whose proofs occur in any of the (whitespace-normalized) texts."""
    return sorted({task for proof, tasks in proofs.items() if any(proof in text for text in texts) for task in tasks})


def strings(value):
    """Every key and string value of a JSON value."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


def run_cli(*args):
    out, err = StringIO(), StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            main([str(arg) for arg in args])
        except SystemExit as exit:
            code = exit.code
    return code, out.getvalue(), err.getvalue()


@unittest.skipUnless(all((PRIVATE / name).exists() for name in REFERENCE_FILES), "private reference files are unavailable")
class PublicTextLeakTests(unittest.TestCase):
    def setUp(self):
        self.proofs = private_proofs()

    def test_readme_and_docs_quote_no_private_reference_proof(self):
        paths = [ROOT / "README.md"] + sorted((ROOT / "docs").glob("*.md")) + sorted((ROOT / "docs").glob("*.txt"))
        self.assertGreater(len(paths), 2)
        leaks = {str(path.relative_to(ROOT)): found for path in paths
                 if (found := quoted([compact(path.read_text(encoding="utf-8"))], self.proofs))}
        self.assertEqual(leaks, {}, "private reference proofs of these tasks are quoted (proof text not shown)")

    def test_source_and_tests_quote_no_private_reference_proof(self):
        paths = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
        leaks = {}
        for path in paths:
            source = path.read_text(encoding="utf-8")
            # Comments and docstrings as written, and every string literal as Python decodes it.
            texts = [compact(source)] + [compact(node.value) for node in ast.walk(ast.parse(source))
                                         if isinstance(node, ast.Constant) and isinstance(node.value, str)]
            found = quoted(texts, self.proofs)
            if found:
                leaks[str(path.relative_to(ROOT))] = found
        self.assertEqual(leaks, {}, "private reference proofs of these tasks are quoted (proof text not shown)")

    def test_the_scan_sees_quotes_however_they_are_laid_out(self):
        proof, tasks = max(((p, t) for p, t in self.proofs.items() if p.startswith("by ") and " " in p[3:]),
                           key=lambda item: (len(item[0]), item[0]))
        for text in (f"See `{proof}`.", "Quoted across lines:\n    " + proof.replace(" ", "\n      ", 2),
                     f"Without its by: `{proof[3:]}`"):
            self.assertLessEqual(tasks, set(quoted([compact(text)], self.proofs)))
        self.assertEqual(quoted([compact("by positivity, rfl and foo_ne_right h are no private proofs")], self.proofs), [])


COMMITTED_READINESS_INPUTS = {
    "tasks": ROOT / "data/public/tasks.jsonl",
    "references": [PRIVATE / "library-verified-final", PRIVATE / "fresh-verified-final", PRIVATE / "smoke-verified-final"],
    "original_tasks": [PRIVATE / "library-before-premise-context.jsonl", PRIVATE / "smoke-before-audit-metadata.jsonl"],
    "config": ROOT / "configs/development.json",
}


@unittest.skipUnless(all(path.exists() for path in [COMMITTED_READINESS_INPUTS["tasks"], COMMITTED_READINESS_INPUTS["config"],
                                                     *COMMITTED_READINESS_INPUTS["references"],
                                                     *COMMITTED_READINESS_INPUTS["original_tasks"],
                                                     *(PRIVATE / name for name in REFERENCE_FILES)]),
                     "committed readiness inputs or private references are unavailable")
class CommittedReadinessLeakTests(unittest.TestCase):
    """The README's readiness command, run on the committed data, publishes no private proof and no tactic name."""

    @classmethod
    def setUpClass(cls):
        inputs = COMMITTED_READINESS_INPUTS
        args = ["readiness", "--tasks", inputs["tasks"]]
        for path in inputs["references"]:
            args += ["--references", path]
        for path in inputs["original_tasks"]:
            args += ["--original-tasks", path]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "dataset-readiness"
            code, _, err = run_cli(*args, "--config", inputs["config"], "--output", output)
            if code != 0:
                raise AssertionError(f"readiness exited {code}: {err[-2000:]}")
            cls.report = json.loads((output / "readiness.json").read_text(encoding="utf-8"))
            cls.page = (output / "readiness.html").read_text(encoding="utf-8")
        cls.proofs = private_proofs()
        cls.single_tactic = {}
        for name in REFERENCE_FILES:
            for row in read_jsonl(PRIVATE / name):
                match = SINGLE_TACTIC.fullmatch(compact(row["proof"]))
                if match:
                    cls.single_tactic[row["task_id"]] = match.group(1)

    def test_report_and_page_quote_no_private_reference_proof(self):
        texts = [compact(text) for text in strings(self.report)] + [compact(html.unescape(self.page))]
        self.assertEqual(quoted(texts, self.proofs), [], "private reference proofs of these tasks are quoted")

    def test_no_task_row_or_tally_names_a_single_tactic_reference(self):
        self.assertTrue(self.single_tactic, "the committed references include one-tactic proofs")
        self.assertNotIn("tactic", set(keys(self.report)))
        # The source-shape label is kept, for exactly the one-tactic references.
        self.assertEqual(sorted(self.report["source_only_controls"]["single_tactic_wrapper"]), sorted(self.single_tactic))
        for task_id in self.single_tactic:
            control = self.report["tasks"][task_id]["source_control"]
            self.assertEqual(control["classification"], "single_tactic_wrapper", task_id)
            self.assertEqual(set(control), {"classification", "source_available", "interpretation"}, task_id)
        names = set(self.single_tactic.values())
        named = {value for value in strings(self.report) for name in names if value in (name, "by " + name)}
        self.assertEqual(named, set(), "a string of the report is a one-tactic reference's tactic")
        page = html.unescape(self.page)
        self.assertEqual([name for name in sorted(names) if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w.?!])", page)], [],
                         "the readiness page names the tactic of a one-tactic reference")
        self.assertIn("<li>single_tactic_wrapper: " + str(len(self.single_tactic)) + " tasks</li>", self.page)


if __name__ == "__main__":
    unittest.main()
