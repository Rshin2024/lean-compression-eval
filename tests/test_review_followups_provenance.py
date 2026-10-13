"""Review follow-ups: complete reference anchors at freeze, references re-checked after an engine fix,
anchor rescoring, report publishing, and the legacy reference-set digest.

The Lean engine is replaced by a fake and the engine files are stand-ins whose hashes differ; no network,
model, Docker daemon or Lean run is used. Report tests use examples/pipeline-demo.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tracemalloc
import unittest
from unittest.mock import patch

from lean_compression import cli, metrics
from lean_compression.io import (digest, file_hash, measurement_hash, now, read_json, read_jsonl, task_input_hash,
                                 verification_request_hash, write_json)
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run, load_run, record_generation

PROJECT = Path(__file__).resolve().parents[1]
DEMO_RUN, DEMO_REFS = PROJECT / "examples/pipeline-demo", PROJECT / "examples/demo-reference-check"
REPORT_FILES = ["all_scheduled_attempts.json", "analysis.json", "audit", "contrasts_by_stratum.csv", "correctness.csv",
                "correctness_by_stratum.csv", "paired_tasks.csv", "report.html", "resource_usage.csv"]
CONFIG = read_json(PROJECT / "configs/development.json")
CONFIG["samples_per_goal_condition"] = 1
CONFIG["analysis"]["bootstrap_replicates"] = 20
TASKS = [{"task_id": f"tiny_0{i}", "family_id": f"family_{i}", "domain": "sets_logic", "split": "pilot",
          "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
          "forbidden_declarations": [], "target_name": f"tiny_target_0{i}", "binders": f"(P : Prop) (h{i} : P)",
          "proposition": "P"} for i in (1, 2)]
PROOFS = {"tiny_01": "h1", "tiny_02": "h2"}
# A stored dependency graph: scoring it gives U_S = 1, U_B = 2 and T0 = 3/2.
GRAPH = {"metric_status": "available", "root": "root", "source_tokens": {"W_S": 1, "W_B": 1},
         "nodes": [{"name": "p", "primitive": True},
                   {"name": "root", "primitive": False, "signature": {"p": 1}, "body": {"p": 2}}]}


def run_cli(*argv):
    """Return the exit status, the printed JSON result (None on failure), and stderr.

    The environment records here are synthetic; test_environment_integrity covers the CLI's check of real ones.
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
            patch("lean_compression.cli.load_environment", read_json), patch("lean_compression.environment.verify_environment"):
        try:
            cli.main([str(arg) for arg in argv])
        except SystemExit as stopped:
            return stopped.code, None, stderr.getvalue()
    return 0, json.loads(stdout.getvalue()), stderr.getvalue()


def lean(*, incidents=(), failures=(), unmeasurable=(), scorer=None):
    """A stand-in for runner.evaluate_proof: Lean's verdict and measurement for a proof, from the engine given."""
    def evaluate_proof(task, proof, environment, engine, config):
        result = {"engine_hash": file_hash(engine), "environment_hash": environment["environment_hash"],
                  "verification_request_hash": verification_request_hash(task), "task_input_hash": task_input_hash(task),
                  "task_hash": digest(task), "source_hash": digest(proof), "evaluated_at": now()}
        if task["task_id"] in incidents:
            return {**result, "validity_status": "infrastructure_error", "metric_status": "unavailable", "metrics": None,
                    "error": "docker: Cannot connect to the Docker daemon"}
        if task["task_id"] in failures:
            return {**result, "validity_status": "elaboration_failure", "metric_status": "unavailable", "metrics": None,
                    "validation": {"validity_status": "elaboration_failure"}}
        if scorer:
            # Scored by older metric code that left the extracted graph unmeasured.
            measured = {"metric_status": "unavailable", "scorer_hash": scorer, "reasons": [{"code": "dependency_cycle"}]}
        else:
            measured = {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": "7", "D": 2}
            if task["task_id"] in unmeasurable:
                measured = {"metric_status": "unavailable", "reasons": [{"code": "dependency_cycle"}]}
        return {**result, "validity_status": "success", "metric_status": measured["metric_status"], "metrics": measured,
                "validation": {"validity_status": "success", "kernel_replay": True}, "extraction": GRAPH}
    return evaluate_proof


class Pipeline(unittest.TestCase):
    """A two-task development pipeline through the CLI, with two stand-in engines E1 and E2."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "tasks.jsonl").write_text("".join(json.dumps(task) + "\n" for task in TASKS))
        self.write_references("refs.jsonl", PROOFS)
        write_json(self.root / "config.json", CONFIG)
        self.write_environment("env.json", "fixture")
        self.engines = {}
        for name in ("E1", "E2"):
            self.engines[name] = self.root / name / "Evaluator.lean"
            self.engines[name].parent.mkdir()
            self.engines[name].write_text(f"-- stand-in for engine {name}\n")

    def write_references(self, name, proofs):
        (self.root / name).write_text("".join(json.dumps({"task_id": k, "proof": v}) + "\n" for k, v in proofs.items()))

    def write_environment(self, name, environment_hash):
        write_json(self.root / name, {"environment_hash": environment_hash, "lean_toolchain": CONFIG["environment"]["lean_toolchain"],
                                      "mathlib_commit": CONFIG["environment"]["mathlib_commit"]})

    def verify(self, output, engine="E1", *, references="refs.jsonl", environment="env.json", config="config.json", **verdicts):
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean(**verdicts)):
            code, result, log = run_cli("verify-references", "--tasks", self.root / "tasks.jsonl", "--references",
                                        self.root / references, "--environment", self.root / environment, "--config",
                                        self.root / config, "--engine", self.engines[engine], "--output", self.root / output)
        self.assertEqual(code, 0, log)
        return result

    def freeze(self, checks="checks", run="run", engine="E1"):
        return run_cli("freeze", "--development", "--tasks", self.root / "tasks.jsonl", "--environment", self.root / "env.json",
                       "--config", self.root / "config.json", "--split", "pilot", "--model", "reference_fixture",
                       "--engine", self.engines[engine], "--run", self.root / run,
                       *(["--reference-checks", self.root / checks] if checks else []))

    def generate(self, run="run"):
        for slot in load_run(self.root / run)["slots"]:
            record_generation(self.root / run, slot, PROOFS[slot["task_id"]])

    def evaluate(self, version, engine="E1", checks=None, run="run", **verdicts):
        """Exit status, result, stderr and the number of Lean checks made."""
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean(**verdicts)) as checker:
            code, result, log = run_cli("evaluate", "--run", self.root / run, "--engine", self.engines[engine],
                                        "--version", version, *(["--reference-checks", self.root / checks] if checks else []))
        return code, result, log, checker.call_count

    def analyze(self, version, references, output, run="run"):
        code, result, log = run_cli("analyze", "--run", self.root / run, "--version", version, "--output", self.root / output,
                                    *(["--references", self.root / references] if references else []))
        return code, read_json(self.root / output / "analysis.json") if code == 0 else None, log

    def anchors(self, checks):
        return {read_json(path)["task_id"]: digest(read_json(path)) for path in (self.root / checks).glob("*.json")
                if path.name != "policy.json"}

    def attainment(self, report):
        return [report["models"]["reference_fixture"]["conditions"]["standard"]["attainment"][t]["rate"] for t in "012"]


class FreezeBindsEveryAnchorTests(Pipeline):
    def test_missing_incomplete_failed_or_unverified_reference_checks_are_refused(self):
        self.verify("pending", incidents=("tiny_01",))
        self.verify("failed", failures=("tiny_02",))
        (self.root / "no-policy").mkdir()
        for path in (self.root / "failed").glob("*.json"):
            if path.name != "policy.json":
                shutil.copy(path, self.root / "no-policy" / path.name)
        self.verify("checks")
        shutil.copytree(self.root / "checks", self.root / "baseline")
        write_json(self.root / "baseline/policy.json", {**read_json(self.root / "checks/policy.json"), "kind": "baseline"},
                   immutable=False)
        for checks, message in (("typo", "Reference checks directory not found"),
                                ("no-policy", "verify-references directory with its policy.json"),
                                ("baseline", "verify-references directory with its policy.json"),
                                ("pending", "tiny_01 (no record). Finish verify-references first"),
                                ("failed", "tiny_02 (record_kind, validity_status)")):
            with self.subTest(checks):
                code, result, log = self.freeze(checks, run=f"run-{checks}")
                self.assertEqual(code, 2)
                self.assertIn(message, log)
                self.assertFalse((self.root / f"run-{checks}").exists())
        self.assertIn("rerun it until it reports no checker incidents", self.freeze("pending", run="again")[2])

    def test_the_readme_flow_freezes_after_the_rerun_and_then_analyzes(self):
        result = self.verify("checks", incidents=("tiny_01",))
        self.assertEqual((result["checker_incidents"], result["verified"]), (1, 1))
        self.assertEqual(self.freeze()[0], 2)
        self.assertEqual(self.verify("checks")["verified"], 2)
        code, result, log = self.freeze()
        self.assertEqual(code, 0, log)
        manifest = read_json(self.root / "run/manifest.json")
        self.assertEqual(manifest["reference_anchors"], self.anchors("checks"))
        self.assertEqual(manifest["reference_proof_hashes"], {task: digest(proof) for task, proof in PROOFS.items()})
        self.assertEqual(manifest["reference_engine_hash"], file_hash(self.engines["E1"]))
        self.generate()
        self.assertEqual(self.evaluate("v1")[:2], (0, {"new_evaluations": 6, "evaluation_version": "v1", "checker_incidents": 0}))
        code, report, log = self.analyze("v1", "checks", "report")
        self.assertEqual(code, 0, log)
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "frozen_per_task")
        self.assertEqual(report["run_provenance"]["reference_anchor_digests"], self.anchors("checks"))
        self.assertEqual((report["missing_reference_task_ids"], self.attainment(report)), ([], [1, 0, 0]))

    def test_a_verified_but_unmeasurable_anchor_is_bound_in_development(self):
        self.verify("checks", unmeasurable=("tiny_02",))
        code, result, log = self.freeze()
        self.assertEqual(code, 0, log)
        self.assertEqual(read_json(self.root / "run/manifest.json")["reference_anchors"], self.anchors("checks"))

    def test_without_reference_checks_nothing_is_bound(self):
        code, result, log = self.freeze(checks=None, engine="E2")
        self.assertEqual(code, 0, log)
        manifest = read_json(self.root / "run/manifest.json")
        self.assertEqual([manifest[key] for key in ("reference_anchors", "reference_proof_hashes", "reference_policy_hash",
                                                    "reference_engine_hash")], [None] * 4)
        # Only the engine is bound: the one given to freeze.
        self.assertEqual(manifest["engine_hash"], file_hash(self.engines["E2"]))
        self.generate()
        code, result, log, checks = self.evaluate("v0", engine="E1")
        self.assertEqual((code, checks), (2, 0))
        self.assertIn("frozen with this run", log)
        self.assertEqual(self.evaluate("v1", engine="E2")[0], 0)
        self.verify("checks", engine="E2")
        code, report, log = self.analyze("v1", "checks", "report")
        self.assertEqual(code, 0, log)
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "not_frozen")
        code, report, log = self.analyze("v1", None, "plain")
        self.assertEqual(code, 0, log)
        self.assertIsNone(report["run_provenance"]["reference_anchor_binding"])

    def test_analyze_says_when_a_task_had_no_frozen_anchor(self):
        # A run frozen by earlier code with a partial anchor set, then completed by a verify-references rerun.
        self.verify("checks")
        self.assertEqual(self.freeze()[0], 0)
        manifest = read_json(self.root / "run/manifest.json")
        del manifest["reference_anchors"]["tiny_02"]
        manifest.pop("run_hash")
        manifest["run_hash"] = digest(manifest)
        write_json(self.root / "run/manifest.json", manifest, immutable=False)
        code, report, log = self.analyze("v1", "checks", "report")
        self.assertEqual(code, 2)
        self.assertIn("No reference record for tiny_02 was frozen with the run", log)


class EngineFixAmendmentTests(Pipeline):
    """A checker or extractor fix after generation: references re-checked by the new engine bind a new version."""

    def setUp(self):
        super().setUp()
        self.verify("checks")
        self.assertEqual(self.freeze()[0], 0)
        self.generate()
        self.assertEqual(self.evaluate("v1")[0], 0)
        self.verify("rechecks", engine="E2")

    def amendments(self):
        path = self.root / "run/amendments.jsonl"
        return read_jsonl(path) if path.exists() else []

    def test_another_engine_is_refused_before_any_check(self):
        code, result, log, checks = self.evaluate("v3", engine="E2")
        self.assertEqual((code, checks), (2, 0))
        self.assertIn("pass that as --reference-checks", log)
        self.assertFalse((self.root / "run/evaluations/v3").exists())

    def test_rechecked_references_bind_a_new_version_that_analyze_then_requires(self):
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks")
        self.assertEqual((code, result["new_evaluations"], checks), (0, 6, 6), log)
        policy = read_json(self.root / "run/evaluations/v3/policy.json")
        self.assertEqual((policy["engine_hash"], policy["reference_policy_hash"], policy["reference_anchors"],
                          policy["frozen_engine_hash"]),
                         (file_hash(self.engines["E2"]), digest(read_json(self.root / "rechecks/policy.json")),
                          self.anchors("rechecks"), file_hash(self.engines["E1"])))
        frozen = read_json(self.root / "run/manifest.json")
        event, = self.amendments()
        self.assertEqual({key: value for key, value in event.items() if key != "recorded_at"}, {
            "kind": "reference_recheck", "version": "v3", "engine_hash": file_hash(self.engines["E2"]),
            "reference_policy_hash": policy["reference_policy_hash"], "reference_anchors": self.anchors("rechecks"),
            "unanchored_reference_tasks": {},
            "frozen_engine_hash": file_hash(self.engines["E1"]), "frozen_reference_policy_hash": frozen["reference_policy_hash"],
            "frozen_reference_anchors": self.anchors("checks")})
        # Resuming needs the same binding and logs no second amendment.
        self.assertEqual(self.evaluate("v3", engine="E2", checks="rechecks")[:2],
                         (0, {"new_evaluations": 0, "evaluation_version": "v3", "checker_incidents": 0}))
        self.assertEqual(self.evaluate("v3", engine="E2")[0], 2)
        self.verify("rechecks-again", engine="E2")
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks-again")
        self.assertEqual(code, 2)
        self.assertIn("Evaluation version v3 was written by other reference checks", log)
        self.assertEqual(len(self.amendments()), 1)
        # analyze v3 accepts only the bound records; v1 keeps the anchors frozen with the run.
        code, report, log = self.analyze("v3", "rechecks", "v3-report")
        self.assertEqual(code, 0, log)
        self.assertEqual((report["run_provenance"]["reference_anchor_binding"], report["run_provenance"]["reference_anchor_digests"]),
                         ("amended_per_task", self.anchors("rechecks")))
        self.assertEqual((report["missing_reference_task_ids"], self.attainment(report)), ([], [1, 0, 0]))
        code, report, log = self.analyze("v1", "checks", "v1-report")
        self.assertEqual((code, report["run_provenance"]["reference_anchor_binding"]), (0, "frozen_per_task"), log)
        self.write_references("reordered.jsonl", dict(reversed(PROOFS.items())))
        self.verify("reordered", engine="E2", references="reordered.jsonl")
        for version, references, message in (("v3", "checks", "Reference measurement engine differs from evaluated outputs"),
                                             ("v1", "rechecks", "Reference measurement engine differs from evaluated outputs"),
                                             ("v3", "rechecks-again", "is not the one bound to this evaluation version"),
                                             ("v3", "reordered", "Reference policy differs from the one bound to this evaluation version")):
            with self.subTest(version=version, references=references):
                code, report, log = self.analyze(version, references, f"refused-{version}-{references}")
                self.assertEqual(code, 2)
                self.assertIn(message, log)
        # A rescore keeps the version's graphs, so it keeps their anchors.
        self.assertEqual(run_cli("rescore", "--run", self.root / "run", "--from-version", "v3", "--version", "v4")[0], 0)
        rescored = read_json(self.root / "run/evaluations/v4/policy.json")
        self.assertEqual((rescored["reference_policy_hash"], rescored["reference_anchors"], rescored["frozen_engine_hash"]),
                         (policy["reference_policy_hash"], policy["reference_anchors"], policy["frozen_engine_hash"]))
        code, report, log = self.analyze("v4", "rechecks", "v4-report")
        self.assertEqual((code, report["run_provenance"]["reference_anchor_binding"]), (0, "amended_per_task"), log)

    def test_a_version_started_by_several_workers_logs_its_amendment_once(self):
        policy = self.root / "run/evaluations/v3/policy.json"
        def stopped(path, value, **options):
            # This worker stops before publishing the version, so it logs no amendment: a worker starting the
            # version alongside it may publish another binding.
            if path == policy:
                raise OSError("worker stopped")
            return write_json(path, value, **options)
        with patch("lean_compression.runner.write_json", side_effect=stopped):
            self.assertEqual(self.evaluate("v3", engine="E2", checks="rechecks")[:2], (2, None))
        self.assertEqual((len(self.amendments()), policy.exists()), (0, False))
        code, result, log, checks = self.evaluate("v3", engine="E2", checks="rechecks")
        self.assertEqual((code, result["new_evaluations"], checks), (0, 6, 6), log)
        self.assertEqual(len(self.amendments()), 1)

    def test_rechecks_must_be_the_frozen_proofs_complete_and_from_this_engine(self):
        self.verify("pending", engine="E2", incidents=("tiny_02",))
        self.write_references("other-proofs.jsonl", {"tiny_01": "h1", "tiny_02": "by exact h2"})
        self.verify("other-proofs", engine="E2", references="other-proofs.jsonl")
        self.write_environment("other-env.json", "other")
        self.verify("other-env", engine="E2", environment="other-env.json")
        stricter = copy.deepcopy(CONFIG)
        stricter["validation"]["max_heartbeats"] = 1
        write_json(self.root / "stricter.json", stricter)
        self.verify("stricter", engine="E2", config="stricter.json")
        self.assertEqual(self.freeze(checks=None, run="unbound")[0], 0)
        for engine, checks, run, message in (
                ("E2", "pending", "run", "tiny_02 (no record)"),
                ("E2", "other-proofs", "run", "must check the proofs frozen with the run: tiny_02"),
                ("E2", "other-env", "run", "made in a different environment"),
                ("E2", "stricter", "run", "different validation/measurement settings"),
                ("E2", "checks", "run", "must come from the engine being evaluated"),
                ("E2", "missing", "run", "Reference checks directory not found"),
                ("E1", "checks", "run", "This engine verified the frozen references"),
                ("E2", "rechecks", "unbound", "frozen without reference checks")):
            with self.subTest(checks=checks, engine=engine, run=run):
                code, result, log, checked = self.evaluate("v3", engine=engine, checks=checks, run=run)
                self.assertEqual((code, checked), (2, 0))
                self.assertIn(message, log)
                self.assertFalse((self.root / run / "evaluations/v3").exists())
        self.assertEqual(self.amendments(), [])

    def test_production_rechecks_unanchor_an_unmeasurable_proof(self):
        image = "sha256:" + "0" * 64
        config = read_json(PROJECT / "configs/pilot.json")
        config["validation"].update(isolation="docker", docker_image=image)
        config["generation"].update(snapshot_pinned=True, settings_resolved=True)
        env = {"environment_hash": "container", "lean_toolchain": config["environment"]["lean_toolchain"],
               "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "full", "runtime_backend": "docker",
               "docker_image": image, "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
        tasks = [{**task, "audit": dict.fromkeys(("reference_verified", "explicit_signature_frozen", "answer_access_audited",
                                                   "family_reviewed", "metric_closure_verified"), True)} for task in TASKS]

        def checks(engine, unmeasurable=()):
            policy = {"kind": "verify-references", "environment_hash": "container", "engine_hash": file_hash(self.engines[engine]),
                      "measurement_hash": measurement_hash(config)}
            records = {}
            for task in tasks:
                record = lean(unmeasurable=unmeasurable)(task, PROOFS[task["task_id"]], env, self.engines[engine], config)
                records[task["task_id"]] = {**record, "task_id": task["task_id"], "proof": PROOFS[task["task_id"]],
                                            "record_kind": "verified_reference"}
            return records, policy
        create_run(self.root / "production", tasks, config, env, split="pilot", model="pinned-model",
                   reference_records=checks("E1")[0], reference_policy=checks("E1")[1])
        for name, unmeasurable in (("unmeasurable", ("tiny_02",)), ("measured", ())):
            records, policy = checks("E2", unmeasurable)
            write_json(self.root / name / "policy.json", policy)
            for record in records.values():
                write_json(self.root / name / f"{digest(record['task_id'])}.json", record)
        # Round 3: the engine's own verdict that a frozen proof cannot be measured unanchors only that task.
        result = evaluate_run(self.root / "production", self.engines["E2"], version="v3", reference_checks=self.root / "unmeasurable")
        self.assertEqual(result["unanchored_reference_tasks"], ["tiny_02"])
        policy, anchors = read_json(self.root / "production/evaluations/v3/policy.json"), self.anchors("unmeasurable")
        self.assertEqual(policy["reference_anchors"], {"tiny_01": anchors["tiny_01"]})
        self.assertEqual(policy["unanchored_reference_tasks"], {"tiny_02": {
            "validity_status": "success", "metric_status": "unavailable", "record_digest": anchors["tiny_02"]}})
        result = evaluate_run(self.root / "production", self.engines["E2"], version="v4", reference_checks=self.root / "measured")
        self.assertEqual(result, {"new_evaluations": 0, "evaluation_version": "v4", "checker_incidents": 0})
        self.assertEqual(read_json(self.root / "production/evaluations/v4/policy.json")["reference_anchors"], self.anchors("measured"))


class AnchorRescoreTests(Pipeline):
    def test_a_frozen_anchor_the_older_scorer_left_unavailable_is_rescored_like_the_attempts(self):
        self.verify("checks", scorer="older-scorer")
        self.assertEqual(self.freeze()[0], 0)
        self.generate()
        self.assertEqual(self.evaluate("v1", scorer="older-scorer")[0], 0)
        code, report, log = self.analyze("v1", "checks", "before")
        self.assertEqual(code, 0, log)
        # Nothing was measured by the running scorer yet, so nothing is rescored.
        self.assertEqual((report["missing_reference_task_ids"], report["run_provenance"]["reference_metrics_recomputed"]),
                         (["tiny_01", "tiny_02"], []))
        self.assertEqual(run_cli("rescore", "--run", self.root / "run", "--from-version", "v1", "--version", "v2")[0], 0)
        code, report, log = self.analyze("v2", "checks", "after")
        self.assertEqual(code, 0, log)
        self.assertEqual((report["missing_reference_task_ids"], report["run_provenance"]["reference_metrics_recomputed"]),
                         ([], ["tiny_01", "tiny_02"]))
        self.assertEqual(report["provenance"]["scorer_hash"], metrics.SCORER_HASH)
        # The same stored graph in every arm and anchor: each attempt attains T0(reference), never twice it.
        self.assertEqual(self.attainment(report), [1, 0, 0])
        self.assertEqual(report["run_provenance"]["reference_anchor_digests"], self.anchors("checks"))


CHILD = r"""
import sys, time
from pathlib import Path
from unittest.mock import patch
from lean_compression import cli
def stalled(*args, **kwargs):
    Path(sys.argv[2]).write_text("staged")
    time.sleep(120)
with patch("lean_compression.audit.write_audit_bundle", stalled):
    cli.main(["analyze", "--run", sys.argv[3], "--version", "v2", "--references", sys.argv[4], "--output", sys.argv[1]])
"""


class ReportPublishingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def analyze(self, output):
        return run_cli("analyze", "--run", DEMO_RUN, "--version", "v2", "--references", DEMO_REFS, "--output", output)

    def hidden(self, *directories):
        return [path for directory in directories for path in directory.iterdir() if path.name.endswith(".partial")]

    def read_only(self, directory):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        directory.chmod(0o555)
        self.addCleanup(directory.chmod, 0o755)

    def test_an_existing_empty_directory_is_filled_in_place(self):
        output = self.root / "report"
        output.mkdir(mode=0o700)
        before = output.stat()
        code, result, log = self.analyze(output)
        self.assertEqual(code, 0, log)
        after = output.stat()
        self.assertEqual((after.st_ino, stat.S_IMODE(after.st_mode)), (before.st_ino, 0o700))
        self.assertEqual(sorted(path.name for path in output.iterdir()), REPORT_FILES)
        # The rows pair conditions with raw responses, so they are as private as the unblinding key.
        modes = {stat.S_IMODE((output / name).stat().st_mode) for name in ("all_scheduled_attempts.json", "audit/private/labels.json")}
        self.assertEqual(modes, {0o600})
        self.assertEqual(self.hidden(self.root), [])

    def test_output_dot_from_inside_the_destination(self):
        output = self.root / "report"
        output.mkdir()
        env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"}
        child = subprocess.run([sys.executable, "-c", "import os, sys\nfrom lean_compression import cli\n"
                                "cli.main(sys.argv[1:])\nprint(sorted(os.listdir('.')))", "analyze", "--run", str(DEMO_RUN),
                                "--version", "v2", "--references", str(DEMO_REFS), "--output", "."],
                               cwd=output, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(child.returncode, 0, child.stderr)
        # The process's own working directory, the destination itself, now shows the report.
        self.assertEqual(child.stdout.strip().splitlines()[-1], str(REPORT_FILES))

    def test_a_read_only_parent_or_a_long_name_does_not_matter_for_an_existing_directory(self):
        parent = self.root / "shared"
        (parent / "report").mkdir(parents=True)
        self.read_only(parent)
        code, result, log = self.analyze(parent / "report")
        self.assertEqual(code, 0, log)
        self.assertEqual(sorted(path.name for path in (parent / "report").iterdir()), REPORT_FILES)
        for name in ("r" * 230, "s" * 254):
            with self.subTest(length=len(name)):
                (self.root / name).mkdir()
                self.assertEqual(self.analyze(self.root / name)[0], 0)
                self.assertEqual(self.analyze(self.root / ("n" + name[1:]))[0], 0)
                self.assertEqual(sorted(path.name for path in (self.root / ("n" + name[1:])).iterdir()), REPORT_FILES)
        self.assertEqual(self.hidden(self.root), [])

    def test_an_unwritable_destination_fails_before_the_analysis(self):
        parent = self.root / "shared"
        parent.mkdir()
        self.read_only(parent)
        with patch("lean_compression.cli.records", side_effect=AssertionError("analysis started")):
            code, result, log = self.analyze(parent / "report")
        self.assertEqual(code, 2)
        self.assertIn(f"Cannot write a report to {parent / 'report'}", log)

    def test_an_entry_published_meanwhile_is_kept_and_nothing_is_mixed_in(self):
        output = self.root / "report"
        output.mkdir()
        from lean_compression.audit import write_audit_bundle

        def competitor(*args, **kwargs):
            write_audit_bundle(*args, **kwargs)
            (output / "analysis.json").write_text('{"published": "first"}')
        with patch("lean_compression.audit.write_audit_bundle", side_effect=competitor):
            code, result, log = self.analyze(output)
        self.assertEqual(code, 2)
        self.assertIn("already contains an analysis", log)
        # Every other entry was moved in before analysis.json; all are taken back out.
        self.assertEqual([path.name for path in output.iterdir()], ["analysis.json"])
        self.assertEqual(read_json(output / "analysis.json"), {"published": "first"})
        self.assertEqual(self.hidden(self.root, output), [])

    def kill_analyze(self, output):
        """Start analyze in a child, stop it with SIGKILL once its report is staged, and return the leftovers."""
        env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"}
        ready = self.root / "staged"
        ready.unlink(missing_ok=True)
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(output), str(ready), str(DEMO_RUN), str(DEMO_REFS)], env=env)
        deadline = time.monotonic() + 60
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        child.kill()
        child.wait()
        return self.hidden(output.parent, *([output] if output.is_dir() else []))

    def test_staging_left_by_a_killed_analyze_is_removed_by_the_next_one(self):
        reports = self.root / "reports"
        reports.mkdir()
        for name, existing in (("new", False), ("existing", True)):
            with self.subTest(name):
                output = reports / name
                if existing:
                    output.mkdir()
                stale, = self.kill_analyze(output)
                self.assertEqual(stale.parent, output if existing else reports)
                self.assertTrue((stale / "analysis.json").exists())
                code, result, log = self.analyze(output)
                self.assertEqual(code, 0, log)
                self.assertIn(f"Removed {stale}", log)
                self.assertEqual(sorted(path.name for path in output.iterdir()), REPORT_FILES)
                self.assertEqual(self.hidden(reports, output), [])

    def test_only_its_own_stale_staging_directories_are_removed(self):
        output = self.root / "report"
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True).stdout
        owner = {"host": socket.gethostname(), "pid": int(dead), "target": str(output)}
        kept = {"no-marker": None, "other-target": {**owner, "target": str(self.root / "elsewhere")},
                "other-host": {**owner, "host": "another-host"}, "running": {**owner, "pid": os.getpid()},
                "bad-pid": {**owner, "pid": 0}}
        for name, marker in kept.items():
            (self.root / f".report.analyze-{name}.partial").mkdir()
            if marker:
                write_json(self.root / f".report.analyze-{name}.partial" / cli.STAGING_MARKER, marker)
        (self.root / "real").mkdir()
        write_json(self.root / "real" / cli.STAGING_MARKER, owner)
        (self.root / ".report.analyze-link.partial").symlink_to(self.root / "real")
        (self.root / ".report.analyze-file.partial").write_text("not a directory")
        (self.root / ".other.analyze-1-2.partial").mkdir()
        write_json(self.root / ".other.analyze-1-2.partial" / cli.STAGING_MARKER, owner)
        (self.root / ".report.analyze-1-2.partial").mkdir()
        write_json(self.root / ".report.analyze-1-2.partial" / cli.STAGING_MARKER, owner)
        before = sorted(path.name for path in self.root.iterdir())
        code, result, log = self.analyze(output)
        self.assertEqual(code, 0, log)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()),
                         sorted(name for name in before if name != ".report.analyze-1-2.partial") + ["report"])
        self.assertTrue((self.root / "real" / cli.STAGING_MARKER).exists())


def big_graph(seed, size=1500):
    names = [f"Fixture.decl_{seed}_{i}" for i in range(size)]
    nodes = {name: {"primitive": False, "signature": {names[(i + 1) % size]: 1, "Nat": 2}, "body": {names[(i + 7) % size]: 3}}
             for i, name in enumerate(names)}
    return {**nodes, "Nat": {"primitive": True}}


class LegacyReferenceSetTests(unittest.TestCase):
    """Runs frozen before per-task anchors bind the whole reference set by one digest."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def files(self, records):
        paths = {}
        for index, (task_id, record) in enumerate(records.items()):
            paths[task_id] = self.root / "set" / f"{index}.json"
            write_json(paths[task_id], record)
        return {task_id: (path, cli.hashlib.sha256(path.read_bytes()).hexdigest()) for task_id, path in paths.items()}

    def test_the_streamed_digest_is_the_digest_of_the_records(self):
        records = {"z_last": {"b": [1, 2.5, None, True], "a": "ensembles ∅ \"quoted\" \\ \n"}, "ensembles_∅_01": {"k": {"y": 1, "x": 2}},
                   "a\u0001b": {"metrics": {"T0": {"numerator_decimal": "3"}}}, "": {}}
        self.assertEqual(cli.set_digest(self.files(records)), digest(records))
        self.assertEqual(cli.set_digest({}), digest({}))

    def test_a_record_changed_between_the_two_reads_is_refused(self):
        files = self.files({"one": {"value": 1}, "two": {"value": 2}})
        write_json(files["two"][0], {"value": 3}, immutable=False)
        with self.assertRaisesRegex(ValueError, "changed while it was read"):
            cli.set_digest(files)

    def test_analysis_of_a_legacy_run_does_not_hold_the_reference_graphs(self):
        config = read_json(PROJECT / "examples/demo-config.json")
        config["analysis"]["bootstrap_replicates"] = 20
        env = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
               "mathlib_commit": config["environment"]["mathlib_commit"]}
        tasks = [{**TASKS[0], "task_id": f"large_{i}", "family_id": f"family_{i}", "target_name": f"target_{i}"} for i in range(30)]
        manifest = create_run(self.root / "new", tasks, config, env, split="pilot", model="reference_fixture", development=True)
        records = {}
        for index, task in enumerate(tasks):
            records[task["task_id"]] = {
                "task_id": task["task_id"], "record_kind": "verified_reference", "validity_status": "success",
                "metric_status": "available", "environment_hash": "fixture", "engine_hash": "fixture-engine",
                "verification_request_hash": verification_request_hash(task),
                "metrics": {"metric_status": "available", "W_S": 4, "W_B": 6, "U_S_decimal": "5", "U_B_decimal": "900", "D": 2},
                "extraction": {"root": f"Fixture.decl_{index}_0", "nodes": big_graph(index)}}
            write_json(self.root / "refs" / f"{digest(task['task_id'])}.json", records[task["task_id"]])
        write_json(self.root / "refs/policy.json", {"kind": "verify-references", "environment_hash": "fixture",
                                                    "engine_hash": "fixture-engine", "config_hash": digest(config)})
        # What a1da54a's create_run wrote: the reference set's digest and none of the later fields.
        legacy = {key: value for key, value in manifest.items() if key not in (
            "run_hash", "measurement_hash", "reference_anchors", "reference_proof_hashes", "reference_policy_hash",
            "reference_engine_hash")}
        legacy["reference_records_hash"] = digest(records)
        legacy["run_hash"] = digest(legacy)
        write_json(self.root / "run/manifest.json", legacy)
        write_json(self.root / "run/evaluations/v1/policy.json", {"engine_hash": "fixture-engine", "version": "v1",
                                                                  "config_hash": legacy["config_hash"], "run_hash": legacy["run_hash"]})
        evidence = sum(path.stat().st_size for path in (self.root / "refs").glob("*.json"))
        tracemalloc.start()
        try:
            code, result, log = run_cli("analyze", "--run", self.root / "run", "--version", "v1", "--references",
                                        self.root / "refs", "--output", self.root / "report")
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(code, 0, log)
        report = read_json(self.root / "report/analysis.json")
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "frozen_set")
        self.assertEqual(report["missing_reference_task_ids"], [])
        # Holding every parsed record, as the set check once did, takes several times the evidence size.
        self.assertLess(peak, evidence / 2, (peak, evidence))


if __name__ == "__main__":
    unittest.main()
