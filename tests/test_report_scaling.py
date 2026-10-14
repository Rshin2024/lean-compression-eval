"""analyze at scale: graphs stay in their evaluation files, reports appear whole, report text is UTF-8.

Fixtures are temporary development runs and examples/pipeline-demo. No network,
model, Docker daemon, or Lean run is used.
"""
from collections import Counter
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from lean_compression import cli
from lean_compression.analysis import analyze
from lean_compression.io import digest, measurement_hash, read_json, verification_request_hash, write_json
from lean_compression.store import create_run, record_generation, records, with_graph

PROJECT = Path(__file__).resolve().parents[1]
DEMO_RUN, DEMO_REFS = PROJECT / "examples/pipeline-demo", PROJECT / "examples/demo-reference-check"
REPORT_FILES = ["all_scheduled_attempts.json", "analysis.json", "audit", "contrasts_by_stratum.csv", "correctness.csv",
                "correctness_by_stratum.csv", "cost_attempts.csv", "cost_summary.csv", "costs.json",
                "paired_tasks.csv", "report.html", "resource_usage.csv"]


def run_cli(*argv):
    """Return the exit status, the printed JSON result (None on failure), and stderr."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            cli.main([str(arg) for arg in argv])
        except SystemExit as stopped:
            return stopped.code, None, stderr.getvalue()
    return 0, json.loads(stdout.getvalue()), stderr.getvalue()


def graph(seed, size=2500):
    """A dependency graph of about 0.7 MB. Analysis reads the stored metrics, never the graph."""
    names = [f"Fixture.decl_{seed}_{i}" for i in range(size)]
    nodes = {name: {"primitive": False, "signature": {names[(i + 1) % size]: 1, "Nat": 2},
                    "body": {names[(i + 7) % size]: 3, "Nat.succ": 1}} for i, name in enumerate(names)}
    return {**nodes, "Nat": {"primitive": True}, "Nat.succ": {"primitive": True}}


def large_run(root, task_count=3, samples=4):
    """A development run whose attempts are measured successes with large graphs, and its reference checks.

    Two slots stay open: one was never generated and one never evaluated.
    """
    path = root / "run"
    config = read_json(PROJECT / "examples/demo-config.json")
    config["samples_per_goal_condition"] = samples
    env = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
           "mathlib_commit": config["environment"]["mathlib_commit"]}
    tasks = [{"task_id": f"large_{i}", "family_id": f"family_{i}", "domain": "sets_logic", "split": "pilot",
              "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "",
              "premise_context": "", "forbidden_declarations": [], "target_name": f"target_{i}",
              "binders": f"(P : Prop) (h{i} : P)", "proposition": "P"} for i in range(task_count)]
    manifest = create_run(path, tasks, config, env, split="pilot", model="reference_fixture", development=True)
    write_json(path / "evaluations/v1/policy.json", {"engine_hash": "fixture-engine", "version": "v1",
                                                     "config_hash": manifest["config_hash"], "run_hash": manifest["run_hash"]})
    for index, slot in enumerate(manifest["slots"][1:]):
        generation = record_generation(path, slot, "by simp", generation_status="completed")
        if index == 0:
            continue
        write_json(path / "evaluations/v1" / f"{slot['attempt_id']}.json", {
            "attempt_id": slot["attempt_id"], "validity_status": "success", "metric_status": "available",
            "metrics": {"metric_status": "available", "W_S": 4, "W_B": 3 + index % 3, "U_S_decimal": "5",
                        "U_B_decimal": str(1000 + 7 * index), "D": 2},
            "validation": {"validity_status": "success", "resources": {"elapsed_seconds": 1.5}},
            "extraction": {"root": f"Fixture.decl_{index}_0", "nodes": graph(index),
                           "source_tokens": {"W_S": 4, "W_B": 3 + index % 3}, "resources": {"elapsed_seconds": 2.5}},
            "generation": generation, "generation_hash": digest(generation), "adapted_response": "by simp"})
    write_json(root / "refs/policy.json", {"kind": "verify-references", "environment_hash": "fixture",
                                           "engine_hash": "fixture-engine", "measurement_hash": measurement_hash(config),
                                           "config_hash": digest(config)})
    for index, task in enumerate(tasks):
        write_json(root / "refs" / f"{digest(task['task_id'])}.json", {
            "task_id": task["task_id"], "record_kind": "verified_reference", "validity_status": "success",
            "metric_status": "available", "environment_hash": "fixture",
            "verification_request_hash": verification_request_hash(task),
            "metrics": {"metric_status": "available", "W_S": 4, "W_B": 6, "U_S_decimal": "5", "U_B_decimal": "900", "D": 2},
            "extraction": {"root": f"Fixture.decl_{-1 - index}_0", "nodes": graph(-1 - index)}})
    return manifest


class GraphFreeRowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_rows_name_their_evaluation_file_instead_of_carrying_its_graph(self):
        full, rows = records(DEMO_RUN, "v2"), records(DEMO_RUN, "v2", graphs=False)
        self.assertEqual(len(rows), len(full))
        for complete, row in zip(full, rows):
            source = row["evaluation_record"]
            self.assertEqual(source["path"], f"evaluations/v2/{row['attempt_id']}.json")
            self.assertEqual(hashlib.sha256((DEMO_RUN / source["path"]).read_bytes()).hexdigest(), source["sha256"])
            self.assertNotIn("nodes", row["extraction"])
            # Nothing else is left out, and the graph comes back unchanged from the file.
            self.assertEqual(with_graph(DEMO_RUN, row), {**complete, "evaluation_record": source})
        self.assertEqual(with_graph(None, full[0]), full[0])

    def test_changed_or_unlocated_evidence_is_refused(self):
        run = self.root / "run"
        shutil.copytree(DEMO_RUN, run)
        row = records(run, "v2", graphs=False)[0]
        with self.assertRaisesRegex(ValueError, "needs its run directory"):
            with_graph(None, row)
        path = run / row["evaluation_record"]["path"]
        write_json(path, {**read_json(path), "evaluated_at": "later"}, immutable=False)
        with self.assertRaisesRegex(ValueError, "changed after it was read"):
            with_graph(run, row)

    def test_rows_are_streamed_as_the_same_json_document(self):
        for rows in (records(DEMO_RUN, "v2"), [], [{"proposition": "∀ x : ℕ, x = x", "sample_index": 0}]):
            cli.write_rows(self.root / "streamed.json", rows)
            write_json(self.root / "whole.json", rows, immutable=False)
            self.assertEqual((self.root / "streamed.json").read_bytes(), (self.root / "whole.json").read_bytes())


class ReportScaleTests(unittest.TestCase):
    def test_analysis_memory_does_not_grow_with_the_dependency_graphs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = large_run(root)
            evaluations = [path for path in (root / "run/evaluations/v1").glob("*.json") if path.name != "policy.json"]
            references = {read_json(path)["task_id"]: read_json(path) for path in (root / "refs").glob("*.json")
                          if path.name != "policy.json"}
            evidence = sum(path.stat().st_size for path in [*evaluations, *(root / "refs").glob("*.json")])
            with patch("lean_compression.analysis.analyze", wraps=analyze) as analysis:
                tracemalloc.start()
                try:
                    code, result, log = run_cli("analyze", "--run", root / "run", "--version", "v1",
                                                "--references", root / "refs", "--output", root / "report")
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
            self.assertEqual(code, 0, log)
            # Holding every graph, as analyze once did, needs about five times the evidence size.
            self.assertLess(peak, evidence / 2, (peak, evidence))
            self.assertEqual(sorted(analysis.call_args.args[2]), sorted(references))
            self.assertFalse(any("nodes" in value["extraction"] for value in analysis.call_args.args[2].values()))
            report = root / "report"
            rows = read_json(report / "all_scheduled_attempts.json")
            self.assertEqual(len(rows), len(manifest["slots"]))
            self.assertEqual(rows, json.loads(json.dumps(records(root / "run", "v1", graphs=False))))
            self.assertEqual(Counter(row["validity_status"] for row in rows),
                             {"success": len(evaluations), "not_evaluated": 1, "not_generated": 1})
            self.assertEqual(len([row for row in rows if "evaluation_record" in row]), len(evaluations))
            self.assertFalse(any("nodes" in (row.get("extraction") or {}) for row in rows))
            # The statistics are the ones computed from the complete records.
            complete = analyze(manifest["tasks"], records(root / "run", "v1"), references, manifest["config"])
            self.assertEqual(read_json(report / "analysis.json")["models"], json.loads(json.dumps(complete["models"])))
            self.assertEqual(read_json(report / "analysis.json")["missing_reference_task_ids"], [])
            # Every task is audited here, and each evaluated attempt is exported with its complete graph.
            exported = 0
            for code_name, label in read_json(report / "audit/private/labels.json").items():
                original = root / "run/evaluations/v1" / f"{label['attempt_id']}.json"
                if not original.exists():
                    continue
                nodes = read_json(original)["extraction"]["nodes"]
                dependencies = report / "audit/review" / label["task_code"] / code_name / "dependencies.json"
                self.assertEqual(read_json(report / "audit/private" / label["complete_record"])["extraction"]["nodes"], nodes)
                self.assertEqual(read_json(dependencies)["nodes"], nodes)
                exported += 1
            self.assertEqual(exported, len(evaluations))


class AtomicReportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reports = self.root / "reports"

    def analyze(self, output):
        return run_cli("analyze", "--run", DEMO_RUN, "--version", "v2", "--references", DEMO_REFS, "--output", output)

    def test_a_failed_analysis_leaves_nothing_behind_and_can_be_rerun(self):
        output = self.reports / "pilot"
        with patch("lean_compression.audit.write_audit_bundle", side_effect=RuntimeError("disk full")):
            code, _, log = self.analyze(output)
        self.assertEqual((code, log.strip()), (2, "disk full"))
        with patch("lean_compression.audit.write_audit_bundle", side_effect=AttributeError("legacy record")), \
                self.assertRaises(AttributeError):
            self.analyze(output)
        self.assertEqual(list(self.reports.iterdir()), [])
        code, result, log = self.analyze(output)
        self.assertEqual(code, 0, log)
        self.assertEqual(sorted(path.name for path in output.iterdir()), REPORT_FILES)
        self.assertEqual(list(self.reports.iterdir()), [output])

    def test_existing_analyses_and_other_files_are_never_overwritten(self):
        existing, other, empty = self.root / "existing", self.root / "other", self.root / "empty"
        for directory in (existing, other, empty):
            directory.mkdir()
        (existing / "analysis.json").write_text("{}")
        (other / "notes.txt").write_text("keep")
        (self.root / "file").write_text("keep")
        for output, message in ((existing, "already contains an analysis"), (other, "is not an empty directory"),
                                (self.root / "file", "is not an empty directory")):
            code, _, log = self.analyze(output)
            self.assertEqual(code, 2)
            self.assertIn(message, log)
        self.assertEqual([path.name for path in existing.iterdir()], ["analysis.json"])
        self.assertEqual((existing / "analysis.json").read_text(), "{}")
        self.assertEqual([path.name for path in other.iterdir()], ["notes.txt"])
        self.assertEqual((self.root / "file").read_text(), "keep")
        code, _, log = self.analyze(empty)
        self.assertEqual(code, 0, log)
        self.assertEqual(sorted(path.name for path in empty.iterdir()), REPORT_FILES)

    def test_a_report_published_meanwhile_is_kept(self):
        output = self.reports / "pilot"
        def competitor(*args, **kwargs):
            output.mkdir()
            (output / "analysis.json").write_text('{"published": "first"}')
        with patch("lean_compression.audit.write_audit_bundle", side_effect=competitor):
            code, _, log = self.analyze(output)
        self.assertEqual(code, 2)
        self.assertIn("already contains an analysis", log)
        self.assertEqual(read_json(output / "analysis.json"), {"published": "first"})
        self.assertEqual(list(self.reports.iterdir()), [output])


CHILD = r"""
import locale, sys
from pathlib import Path
from lean_compression import cli
from lean_compression.analysis import analyze, write_report
from lean_compression.readiness import build_readiness_report, write_readiness_report
print(locale.getpreferredencoding(False), locale.setlocale(locale.LC_CTYPE), flush=True)
out = Path(sys.argv[1])
cli.main(["analyze", "--run", sys.argv[2], "--version", "v2", "--references", sys.argv[3], "--output", str(out / "report")])
task = {"task_id": "ensembles_\u2205_01", "family_id": "family_\u2205", "domain": "sets_logic", "split": "pilot",
        "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
        "forbidden_declarations": [], "target_name": "target_empty", "binders": "(P : Prop) (h : P)", "proposition": "P"}
attempts = [{"task_id": task["task_id"], "model_snapshot": "mod\u00e8le-\u2205", "condition": condition, "sample_index": 0,
             "validity_status": "success", "metric_status": "available",
             "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": "7"}}
            for condition in ("standard", "mathematical_compression", "source_concision")]
write_report(analyze([task], attempts, {}, {"samples_per_goal_condition": 1, "bootstrap_replicates": 10}), out / "names")
write_readiness_report(build_readiness_report([task], [], {"enforce_quotas": False}), out / "readiness")
"""


class ReportEncodingTests(unittest.TestCase):
    def test_report_text_is_utf8_under_any_locale(self):
        # With UTF-8 mode and C-locale coercion off, LC_ALL=C is ASCII on macOS and glibc, so the test runs
        # there too; the named locales are macOS ones. PYTHONUTF8=0 also covers interpreters defaulting to UTF-8 mode.
        for locale_name in ("C", "en_US.ISO8859-1", "en_US.US-ASCII"):
            with self.subTest(locale=locale_name), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                env = {"PATH": "/usr/bin:/bin", "LC_ALL": locale_name, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
                       "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1])}
                child = subprocess.run([sys.executable, "-c", CHILD, str(out), str(DEMO_RUN), str(DEMO_REFS)],
                                       env=env, capture_output=True, timeout=120)
                first = child.stdout.decode("ascii", "replace").split("\n")[0]
                self.assertTrue(first, child.stderr.decode("utf-8", "replace"))  # A child that never started fails.
                encoding, _, active = first.partition(" ")
                if active != locale_name:
                    self.skipTest(f"Locale {locale_name} is not installed here (the interpreter fell back to {active or 'nothing'})")
                if encoding.replace("-", "").upper() == "UTF8":
                    self.skipTest(f"Locale {locale_name} uses UTF-8 here, so it cannot show a locale-dependent encoding")
                self.assertEqual(child.returncode, 0, child.stderr.decode("utf-8", "replace"))
                for name in ("analysis.json", "correctness.csv", "paired_tasks.csv", "resource_usage.csv", "report.html"):
                    (out / "report" / name).read_bytes().decode("utf-8")
                self.assertIn("×", (out / "report/report.html").read_text(encoding="utf-8"))
                # Task and model names outside the locale's character set are written, not mangled or refused.
                for name in ("correctness.csv", "paired_tasks.csv", "resource_usage.csv", "report.html"):
                    self.assertIn("modèle-∅", (out / "names" / name).read_text(encoding="utf-8"))
                self.assertIn("ensembles_∅_01", (out / "names/paired_tasks.csv").read_text(encoding="utf-8"))
                self.assertIn("ensembles_∅_01", (out / "readiness/readiness.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
