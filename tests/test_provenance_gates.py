"""Provenance gates: production freeze, frozen reference anchors, scorer identity, and versions.

CLI tests call cli.main on temporary copies of examples/pipeline-demo and
examples/demo-reference-check. No network, model, Docker daemon, or Lean run is used.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lean_compression import cli, metrics
from lean_compression.dataset import DOMAINS
from lean_compression.io import digest, file_hash, measurement_hash, read_json, read_jsonl, verification_request_hash, write_json
from lean_compression.metrics import score_graph
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
DEMO_RUN, DEMO_REFS = PROJECT / "examples/pipeline-demo", PROJECT / "examples/demo-reference-check"
IMAGE = "sha256:" + "0" * 64
AUDITED = dict.fromkeys(("reference_verified", "explicit_signature_frozen", "answer_access_audited",
                         "family_reviewed", "metric_closure_verified"), True)
DELETE = object()


def run_cli(*argv):
    """Return the exit status, the printed JSON result (None on failure), and stderr.

    Environment records here are synthetic or describe the demo's host, and Lean never runs;
    test_environment_integrity covers their verification.
    """
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
            patch("lean_compression.cli.load_environment", read_json), \
            patch("lean_compression.environment.verify_environment"):
        try:
            cli.main([str(arg) for arg in argv])
        except SystemExit as stopped:
            return stopped.code, None, stderr.getvalue()
    return 0, json.loads(stdout.getvalue()), stderr.getvalue()


def task(name, split="pilot", domain="sets_logic", origin="fresh", index=0):
    return {"task_id": name, "family_id": "family_" + name, "domain": domain, "split": split, "origin": origin,
            "stratum": "provisional_easy", "imports": ["Init"], "context": "", "premise_context": "",
            "forbidden_declarations": [], "target_name": "target_" + name, "binders": f"(P : Prop) (h{index} : P)",
            "proposition": "P", "audit": dict(AUDITED)}


def production(tasks):
    """A production configuration, container environment, and matching verified references."""
    config = read_json(PROJECT / "configs/pilot.json")
    config["validation"].update(isolation="docker", docker_image=IMAGE)
    config["generation"].update(snapshot_pinned=True, settings_resolved=True)
    env = {"environment_hash": "container", "lean_toolchain": config["environment"]["lean_toolchain"],
           "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "full",
           "runtime_backend": "docker", "docker_image": IMAGE,
           "source_exclusion_audit": {"lean_sources": 0, "git_directories": 0}}
    # Checked before the generation settings were final; only the measurement digest has to match.
    policy = {"kind": "verify-references", "environment_hash": "container", "engine_hash": "reference-engine",
              "measurement_hash": measurement_hash(config), "config_hash": "earlier-generation-settings"}
    refs = {t["task_id"]: {"task_id": t["task_id"], "record_kind": "verified_reference", "validity_status": "success",
                           "metric_status": "available", "environment_hash": "container", "proof": "by assumption",
                           "engine_hash": "reference-engine", "verification_request_hash": verification_request_hash(t)}
            for t in tasks if t["split"] == "pilot"}
    return {"config": config, "env": env, "tasks": tasks, "refs": refs, "policy": policy, "split": "pilot"}


class ProductionFreezeGateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = production([task("one", index=1), task("two", index=2)])

    def freeze(self, fixture, name="run"):
        return create_run(self.root / name, fixture["tasks"], fixture["config"], fixture["env"], split=fixture["split"],
                          model="pinned-model", development=False, reference_records=fixture["refs"],
                          reference_policy=fixture["policy"], pilot_report=fixture.get("pilot_report"))

    def test_valid_production_freeze_binds_reference_anchors_policy_and_engine(self):
        manifest = self.freeze(self.base)
        self.assertFalse(manifest["development"])
        self.assertEqual(manifest["reference_anchors"], {name: digest(ref) for name, ref in self.base["refs"].items()})
        self.assertEqual(manifest["reference_policy_hash"], digest(self.base["policy"]))
        self.assertEqual(manifest["reference_engine_hash"], "reference-engine")
        self.assertEqual(manifest["measurement_hash"], measurement_hash(self.base["config"]))

    def test_each_missing_prerequisite_has_its_own_error(self):
        cases = [  # expected error, fixture part, key path, replacement
            ("explicitly pinned model snapshot", "config", ("generation", "snapshot_pinned"), False),
            ("reasoning and sampling settings", "config", ("generation", "settings_resolved"), False),
            ("full source and compiled environment hashes", "env", ("hash_mode",), "development_metadata_only"),
            ("enforced Docker CPU and memory profile", "config", ("validation", "isolation"), "macos_sandbox"),
            ("Verify/audit every task", "tasks", (1, "audit", "family_reviewed"), False),
            ("collected inside the pinned runtime image", "env", ("runtime_backend",), "host"),
            ("Runtime image differs", "env", ("docker_image",), "sha256:" + "1" * 64),
            ("Environment mathlib_commit differs", "env", ("mathlib_commit",), "other-commit"),
            ("forbidden-source exclusion audit", "env", ("source_exclusion_audit",), {"lean_sources": 3, "git_directories": 0}),
            ("actual verified reference records", "refs", (), None),
            ("verify-references directory with its policy.json", "policy", (), None),
            ("verify-references directory with its policy.json", "policy", ("kind",), "baseline"),
            ("verify-references directory with its policy.json", "policy", ("engine_hash",), DELETE),
            ("made in a different environment", "policy", ("environment_hash",), "host"),
            ("different validation/measurement settings", "policy", ("measurement_hash",), "other-settings"),
            # An older policy has no measurement digest, so its whole-config hash must match.
            ("different validation/measurement settings", "policy", ("measurement_hash",), DELETE),
            (r"one \(record_kind\)", "refs", ("one", "record_kind"), "trusted_tactic_baseline"),
            (r"one \(validity_status\)", "refs", ("one", "validity_status"), "elaboration_failure"),
            (r"one \(metric_status\)", "refs", ("one", "metric_status"), "unavailable"),
            (r"one \(environment_hash\)", "refs", ("one", "environment_hash"), "host"),
            (r"one \(engine_hash\)", "refs", ("one", "engine_hash"), "older-engine"),
            (r"one \(task identity\)", "refs", ("one", "verification_request_hash"), "another-task"),
            (r"verified reference: two \(no record\)\. Finish verify-references", "refs", ("two",), DELETE),
        ]
        for index, (message, part, path, value) in enumerate(cases):
            with self.subTest(message, part=part, path=path):
                fixture = copy.deepcopy(self.base)
                if not path:
                    fixture[part] = value
                else:
                    parent = fixture[part]
                    for key in path[:-1]:
                        parent = parent[key]
                    if value is DELETE:
                        del parent[path[-1]]
                    else:
                        parent[path[-1]] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.freeze(fixture, f"run{index}")
                self.assertFalse((self.root / f"run{index}").exists())
        main = copy.deepcopy(self.base)
        for item in main["tasks"]:
            item["split"] = "main"
        main["split"] = "main"
        with self.assertRaisesRegex(ValueError, "completed pilot report hash"):
            self.freeze(main)
        main["config"]["preregistration"] = {"pilot_report_hash": digest("completed pilot report")}
        # The hash must name the analysis of a complete production pilot (test_freeze_and_capture covers that gate).
        main["pilot_report"] = {"sha256": digest("completed pilot report"), "analysis": {
            "run_provenance": {"development": False, "split": "pilot", "experiment": main["config"]["experiment"],
                               "protocol_version": main["config"]["protocol_version"], "model_snapshot": "pinned-model"},
            "models": {"pinned-model": {"run_complete": True}}}}
        self.assertEqual(self.freeze(main)["split"], "main")

    def test_reference_checks_bind_measurement_settings_not_generation_or_analysis(self):
        later = copy.deepcopy(self.base)
        later["config"]["generation"].update(temperature=None, model_snapshot="pinned-model")
        later["config"]["analysis"]["bootstrap_replicates"] = 2000
        self.assertFalse(self.freeze(later, "later")["development"])
        legacy = copy.deepcopy(self.base)
        del legacy["policy"]["measurement_hash"]
        legacy["policy"]["config_hash"] = digest(legacy["config"])
        self.freeze(legacy, "legacy")
        # Changed policy labels, such as environment.boundary_policy, are refused earlier as unimplemented.
        for section, key, value in (("validation", "permitted_axioms", ["propext"]), ("validation", "max_heartbeats", 1),
                                    ("extraction", "wall_seconds", 1), ("environment", "primitive_manifest_hash", "other"),
                                    (None, "metric_profile", "other_profile")):
            changed = copy.deepcopy(self.base)
            (changed["config"][section] if section else changed["config"])[key] = value
            with self.assertRaisesRegex(ValueError, "different validation/measurement settings"):
                self.freeze(changed, "changed")

    def test_development_freeze_refuses_reference_checks_analyze_would_reject(self):
        def freeze(fixture, name):
            return create_run(self.root / name, fixture["tasks"], fixture["config"], fixture["env"], split="pilot",
                              model="pinned-model", development=True, reference_records=fixture["refs"],
                              reference_policy=fixture["policy"])
        for field, message in (("environment_hash", "different environment"),
                               ("measurement_hash", "different validation/measurement settings")):
            fixture = copy.deepcopy(self.base)
            fixture["policy"][field] = "other"
            with self.assertRaisesRegex(ValueError, message):
                freeze(fixture, field)
        # Development binds a verified record for every task too, or nothing; only measurability is not required.
        partial = copy.deepcopy(self.base)
        del partial["refs"]["two"]
        with self.assertRaisesRegex(ValueError, r"two \(no record\)"):
            freeze(partial, "partial")
        self.assertFalse((self.root / "partial").exists())
        unmeasurable = copy.deepcopy(self.base)
        unmeasurable["refs"]["two"]["metric_status"] = "unavailable"
        manifest = freeze(unmeasurable, "unmeasurable")
        self.assertEqual(manifest["reference_anchors"], {name: digest(ref) for name, ref in unmeasurable["refs"].items()})
        self.assertEqual(manifest["reference_engine_hash"], "reference-engine")

    def test_evaluate_refuses_an_engine_other_than_the_reference_engine(self):
        engine = self.root / "engine/Evaluator.lean"
        engine.parent.mkdir()
        engine.write_text("-- stand-in for the engine that verified the references\n")
        fixture = copy.deepcopy(self.base)
        fixture["policy"]["engine_hash"] = file_hash(engine)
        for ref in fixture["refs"].values():
            ref["engine_hash"] = file_hash(engine)
        self.freeze(fixture)
        with self.assertRaisesRegex(ValueError, "verified this run's frozen references"):
            evaluate_run(self.root / "run", ENGINE)
        self.assertFalse((self.root / "run/evaluations").exists())
        self.assertEqual(evaluate_run(self.root / "run", engine)["new_evaluations"], 0)


class CliFreezeTests(unittest.TestCase):
    """The production freeze path through the CLI: quotas, audits, and the reference directory."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        tasks = []
        for split, count in (("pilot", 10), ("main", 40)):
            for domain in DOMAINS:
                for index in range(count):
                    origin = "library" if split == "main" and index < 30 else "fresh"
                    tasks.append(task(f"{split}_{domain}_{index:02d}", split, domain, origin, len(tasks)))
        fixture = production(tasks)
        # freeze hashes its --engine, which must be the engine that verified the references.
        for record in (fixture["policy"], *fixture["refs"].values()):
            record["engine_hash"] = file_hash(ENGINE)
        self.write_tasks("tasks.jsonl", tasks)
        write_json(self.root / "config.json", fixture["config"])
        write_json(self.root / "env.json", fixture["env"])
        for name, policy in (("refs", fixture["policy"]), ("baseline", {**fixture["policy"], "kind": "baseline"})):
            write_json(self.root / name / "policy.json", policy)
            for task_id, ref in fixture["refs"].items():
                write_json(self.root / name / f"{digest(task_id)}.json", ref)
        self.tasks = tasks

    def write_tasks(self, name, tasks):
        (self.root / name).write_text("".join(json.dumps(item) + "\n" for item in tasks))

    def freeze(self, refs="refs", tasks="tasks.jsonl", run="run"):
        return run_cli("freeze", "--tasks", self.root / tasks, "--environment", self.root / "env.json",
                       "--config", self.root / "config.json", "--split", "pilot", "--model", "pinned-model",
                       "--engine", ENGINE, "--run", self.root / run,
                       *(["--reference-checks", self.root / refs] if refs else []))

    def test_production_freeze_through_the_cli(self):
        code, result, log = self.freeze()
        self.assertEqual(code, 0, log)
        self.assertEqual((result["development"], result["scheduled_attempts"]), (False, 600))
        manifest = read_json(self.root / "run/manifest.json")
        self.assertEqual(manifest["reference_policy_hash"], digest(read_json(self.root / "refs/policy.json")))
        self.assertEqual(len(manifest["reference_anchors"]), 50)
        self.assertEqual(manifest["reference_engine_hash"], file_hash(ENGINE))

    def test_cli_freeze_refusals_write_nothing(self):
        unaudited = copy.deepcopy(self.tasks)
        unaudited[-1]["audit"]["family_reviewed"] = False
        self.write_tasks("unaudited.jsonl", unaudited)
        self.write_tasks("short.jsonl", self.tasks[:-1])
        for message, options in (("verify-references directory with its policy.json", {"refs": "baseline"}),
                                 ("family_reviewed is not true", {"tasks": "unaudited.jsonl"}),
                                 ("expected 250 tasks, found 249", {"tasks": "short.jsonl"}),
                                 ("actual verified reference records", {"refs": None})):
            with self.subTest(message):
                code, result, log = self.freeze(**options, run=message)
                self.assertEqual(code, 2)
                self.assertIn(message, log)
                self.assertFalse((self.root / message).exists())


class AnalyzeReferenceGateTests(unittest.TestCase):
    """analyze on a copy of the documented example, and on runs frozen again from it."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.demo = Path(cls.temp.name) / "pipeline-demo"
        shutil.copytree(DEMO_RUN, cls.demo)
        cls.manifest = read_json(cls.demo / "manifest.json")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.refs = self.references("refs")

    def references(self, name, policy=None, record=None):
        """A copy of the demo reference checks, optionally with changed policy fields or record."""
        target = self.root / name
        shutil.copytree(DEMO_REFS, target)
        if policy:
            write_json(target / "policy.json", {**read_json(target / "policy.json"), **policy}, immutable=False)
        if record:
            path = self.record_path(target)
            write_json(path, record(read_json(path)), immutable=False)
        return target

    @staticmethod
    def record_path(directory):
        return directory / f"{digest('smoke_ag_01')}.json"

    def analyze(self, references, version="v2", run=None, output="report"):
        argv = ["analyze", "--run", run or self.demo, "--version", version, "--output", self.root / output]
        code, result, log = run_cli(*argv, *(["--references", references] if references else []))
        return code, read_json(self.root / output / "analysis.json") if code == 0 else None, log

    def freeze_demo(self, name, references=None, config=None):
        """Freeze the demo task again with the current code, as a development run."""
        (self.root / "tasks.jsonl").write_text("".join(json.dumps(item) + "\n" for item in self.manifest["tasks"]))
        write_json(self.root / f"{name}.config.json", config or self.manifest["config"])
        write_json(self.root / "env.json", {key: self.manifest["environment"][key] for key in
                                            ("environment_hash", "lean_toolchain", "mathlib_commit", "hash_mode")},
                   immutable=False)
        # Reference checks bind the engine that made them; the demo's is the archived 3c093c02.
        engine = PROJECT / "lean/versions/Evaluator-3c093c02.lean" if references else ENGINE
        code, result, log = run_cli("freeze", "--development", "--tasks", self.root / "tasks.jsonl",
                                    "--environment", self.root / "env.json", "--config", self.root / f"{name}.config.json",
                                    "--split", "pilot", "--model", "reference_fixture", "--engine", engine,
                                    "--run", self.root / name, *(["--reference-checks", references] if references else []))
        self.assertEqual(code, 0, log)
        return self.root / name

    def test_documented_example_still_analyzes_and_records_reference_provenance(self):
        code, report, log = self.analyze(self.refs)
        self.assertEqual(code, 0, log)
        self.assertEqual(report["run_provenance"]["reference_policy_hash"], digest(read_json(self.refs / "policy.json")))
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "not_frozen")
        self.assertEqual(report["run_provenance"]["reference_anchor_digests"],
                         {"smoke_ag_01": digest(read_json(self.record_path(self.refs)))})
        self.assertEqual(report["run_provenance"]["reference_metrics_recomputed"], [])
        self.assertEqual(report["provenance"]["scorer_hash"], "legacy")
        model = report["models"]["reference_fixture"]
        self.assertTrue(model["compression_conclusions_ready"])
        self.assertEqual([model["conditions"]["standard"]["attainment"][t]["rate"] for t in "012"], [1, 0, 0])

    def test_reference_evidence_that_does_not_match_the_run_is_refused(self):
        other_task = dict.fromkeys(("verification_request_hash", "task_input_hash", "task_hash"), "another-task")
        cases = [
            ("Reference environment differs", self.references("env", policy={"environment_hash": "host"}), "v2"),
            ("Reference measurement engine differs", self.refs, "v1"),
            ("validation/measurement configuration differs", self.references("config", policy={"config_hash": "other"}), "v2"),
            ("not automation baseline records",
             self.references("kind", record=lambda r: {**r, "record_kind": "trusted_tactic_baseline"}), "v2"),
            ("incompatible environment provenance",
             self.references("record-env", record=lambda r: {**r, "environment_hash": "host"}), "v2"),
            ("does not match its frozen task record", self.references("task", record=lambda r: {**r, **other_task}), "v2"),
            # Legacy attempts with a reference scored by other metric code: never pooled silently.
            ("cannot combine metrics from different scorers",
             self.references("scorer", record=lambda r: {**r, "metrics": {**r["metrics"], "scorer_hash": "other"}}), "v2"),
        ]
        for message, references, version in cases:
            with self.subTest(message):
                output = f"report-{references.name}-{version}"
                code, report, log = self.analyze(references, version, output=output)
                self.assertEqual(code, 2)
                self.assertIn(message, log)
                self.assertFalse((self.root / output).exists())
        code, report, log = self.analyze(None, "")
        self.assertEqual(code, 2)
        self.assertIn("one safe directory name", log)

    def test_analyze_accepts_only_the_reference_records_frozen_with_the_run(self):
        run = self.freeze_demo("frozen", self.refs)
        self.assertEqual(read_json(run / "manifest.json")["reference_anchors"],
                         {"smoke_ag_01": digest(read_json(self.record_path(self.refs)))})
        code, report, log = self.analyze(self.refs, "v1", run, "frozen-report")
        self.assertEqual(code, 0, log)
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "frozen_per_task")
        # The same policy with another record for the task, as a later re-verification or swap would leave.
        spliced = self.references("spliced", record=lambda r: {**r, "proof": "by ring_nf", "evaluated_at": "later"})
        code, report, log = self.analyze(spliced, "v1", run, "spliced-report")
        self.assertEqual(code, 2)
        self.assertIn("Reference record for smoke_ag_01 is not the one frozen with the run", log)
        swapped = self.references("swapped", policy={"references_hash": "other proofs"})
        code, report, log = self.analyze(swapped, "v1", run, "swapped-report")
        self.assertEqual(code, 2)
        self.assertIn("Reference policy differs from the one frozen with the run", log)

    def test_older_manifests_check_the_frozen_reference_set(self):
        run = self.freeze_demo("frozen", self.refs)
        new_fields = ("run_hash", "measurement_hash", "reference_anchors", "reference_proof_hashes", "reference_policy_hash",
                      "reference_engine_hash")
        legacy = {key: value for key, value in read_json(run / "manifest.json").items() if key not in new_fields}
        legacy["run_hash"] = digest(legacy)
        write_json(self.root / "legacy/manifest.json", legacy)
        code, report, log = self.analyze(self.refs, "v1", self.root / "legacy", "legacy-report")
        self.assertEqual(code, 0, log)
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "frozen_set")
        spliced = self.references("spliced", record=lambda r: {**r, "proof": "by ring_nf"})
        code, report, log = self.analyze(spliced, "v1", self.root / "legacy", "spliced-report")
        self.assertEqual(code, 2)
        self.assertIn("Reference records differ from the set frozen with the run", log)

    def test_reference_checks_are_matched_on_measurement_settings(self):
        config = copy.deepcopy(self.manifest["config"])
        config["generation"]["temperature"] = None
        config["analysis"]["bootstrap_replicates"] = 50
        run = self.freeze_demo("later-settings", config=config)
        measured = self.references("measured", policy={"measurement_hash": measurement_hash(self.manifest["config"])})
        code, report, log = self.analyze(measured, "v1", run, "measured-report")
        self.assertEqual(code, 0, log)
        self.assertEqual(report["run_provenance"]["reference_anchor_binding"], "not_frozen")
        # An older policy without the digest still has to match the whole configuration.
        code, report, log = self.analyze(self.refs, "v1", run, "legacy-report")
        self.assertEqual(code, 2)
        self.assertIn("validation/measurement configuration differs", log)
        stricter = copy.deepcopy(self.manifest["config"])
        stricter["validation"]["max_heartbeats"] = 1
        other = self.references("stricter", policy={"measurement_hash": measurement_hash(stricter)})
        code, report, log = self.analyze(other, "v1", run, "stricter-report")
        self.assertEqual(code, 2)
        self.assertIn("validation/measurement configuration differs", log)


class RescoreVersionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run, self.refs = self.root / "run", self.root / "refs"
        shutil.copytree(DEMO_RUN, self.run)
        shutil.copytree(DEMO_REFS, self.refs)

    def rescore(self, source, destination):
        return run_cli("rescore", "--run", self.run, "--from-version", source, "--version", destination)

    def analyze(self, version, output):
        code, result, log = run_cli("analyze", "--run", self.run, "--version", version, "--references", self.refs,
                                    "--output", self.root / output)
        return code, read_json(self.root / output / "analysis.json") if code == 0 else None, log

    def test_rescore_writes_a_new_version_with_its_own_policy(self):
        code, result, log = self.rescore("v2", "v3")
        self.assertEqual((code, result), (0, {"rescored_records": 3, "version": "v3"}), log)
        source, policy = (read_json(self.run / f"evaluations/{version}/policy.json") for version in ("v2", "v3"))
        self.assertEqual({key: policy[key] for key in ("kind", "version", "source_version", "source_policy_hash",
                                                       "engine_hash", "scorer_hash", "run_hash")},
                         {"kind": "rescore", "version": "v3", "source_version": "v2", "source_policy_hash": digest(source),
                          "engine_hash": source["engine_hash"], "scorer_hash": metrics.SCORER_HASH,
                          "run_hash": source["run_hash"]})
        rescored = [read_json(path) for path in (self.run / "evaluations/v3").glob("*.json") if path.name != "policy.json"]
        self.assertEqual({row["metrics"]["scorer_hash"] for row in rescored}, {metrics.SCORER_HASH})
        self.assertEqual({row["amendment"]["source_version"] for row in rescored}, {"v2"})
        # The demo run already records its v1 supersession.
        *committed, event = read_jsonl(self.run / "amendments.jsonl")
        self.assertEqual(committed, read_jsonl(DEMO_RUN / "amendments.jsonl"))
        self.assertEqual((event["source"], event["destination"], event["records"]), ("v2", "v3", 3))
        code, result, log = run_cli("evaluate", "--run", self.run, "--engine", ENGINE, "--version", "v3")
        self.assertEqual(code, 2)
        self.assertIn("Evaluation version v3 was written by rescore", log)

    def test_rescore_refuses_unsafe_existing_or_missing_versions(self):
        before = sorted(path.relative_to(self.run) for path in self.run.rglob("*"))
        for source, destination, message in (("v2", "", "one safe directory name"), ("v2", ".", "one safe directory name"),
                                             ("v2", "..", "one safe directory name"), ("v2", "a/b", "one safe directory name"),
                                             ("v2", ".hidden", "one safe directory name"), ("", "v3", "one safe directory name"),
                                             ("v2", "v1", "v1 already exists"), ("v2", "v2", "v2 already exists"),
                                             ("v9", "v3", "v9 does not exist")):
            with self.subTest(source=source, destination=destination):
                code, result, log = self.rescore(source, destination)
                self.assertEqual(code, 2)
                self.assertIn(message, log)
        self.assertEqual(sorted(path.relative_to(self.run) for path in self.run.rglob("*")), before)
        # A copied version directory, and one whose records another engine produced, are not rescored.
        shutil.copytree(self.run / "evaluations/v2", self.run / "evaluations/v8")
        code, result, log = self.rescore("v8", "v9")
        self.assertIn("does not describe version v8 of this run", log)
        write_json(self.run / "evaluations/v8/policy.json",
                   {**read_json(self.run / "evaluations/v8/policy.json"), "version": "v8"}, immutable=False)
        record = next(path for path in (self.run / "evaluations/v8").glob("*.json") if path.name != "policy.json")
        write_json(record, {**read_json(record), "engine_hash": "other-engine"}, immutable=False)
        code, result, log = self.rescore("v8", "v9")
        self.assertEqual(code, 2)
        self.assertIn("not produced by the engine named in its version policy", log)
        self.assertFalse((self.run / "evaluations/v9").exists())

    def test_evaluate_refuses_versions_it_did_not_write(self):
        record = next(path for path in (self.run / "evaluations/v2").glob("*.json") if path.name != "policy.json")
        (self.run / "evaluations/v5").mkdir()
        shutil.copy(record, self.run / "evaluations/v5" / record.name)
        for version, message in (("v5", "Evaluation version v5 holds records without a policy"),
                                 ("v2", "Evaluation version v2 was written by another engine, code or configuration"),
                                 ("", "one safe directory name")):
            with self.subTest(version):
                code, result, log = run_cli("evaluate", "--run", self.run, "--engine", ENGINE, "--version", version)
                self.assertEqual(code, 2)
                self.assertIn(message, log)
        self.assertFalse((self.run / "evaluations/v5/policy.json").exists())

    def test_analyze_rescores_frozen_anchors_with_the_attempts_scorer(self):
        def corrected(graph, root, W_S, W_B):
            # Stand-in for a scoring fix in metrics.py: body expansion counts four times as large.
            result = score_graph(graph, root, W_S, W_B)
            total = int(result["U_S_decimal"]) + 4 * int(result["U_B_decimal"])
            return {**result, "U_B_decimal": str(4 * int(result["U_B_decimal"])), "U_decimal": str(total),
                    "T0": {**result["T0"], "numerator_decimal": str(total)}}
        with patch("lean_compression.metrics.score_graph", corrected):
            self.assertEqual(self.rescore("v2", "v3")[0], 0)
            code, report, log = self.analyze("v3", "corrected")
        self.assertEqual(code, 0, log)
        model = report["models"]["reference_fixture"]
        # Every arm replays the reference proof, so no attempt attains twice the reference T0.
        self.assertEqual([model["conditions"]["standard"]["attainment"][t]["rate"] for t in "012"], [1, 0, 0])
        self.assertEqual(report["run_provenance"]["reference_metrics_recomputed"], ["smoke_ag_01"])
        self.assertEqual(report["provenance"]["scorer_hash"], metrics.SCORER_HASH)
        # Attempts scored by other code than the running scorer cannot be matched; that is refused.
        with patch("lean_compression.metrics.SCORER_HASH", "older-scorer"):
            self.assertEqual(self.rescore("v2", "v4")[0], 0)
        code, report, log = self.analyze("v4", "mixed")
        self.assertEqual(code, 2)
        self.assertIn("cannot combine metrics from different scorers (legacy, older-scorer)", log)
        self.assertIn("rescore the evaluation version with the current metric code", log)


class ReferencePolicyTests(unittest.TestCase):
    def test_reference_policy_records_measurement_settings_and_scorer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks.jsonl").write_text(json.dumps(task("ref_one", index=1)) + "\n")
            (root / "refs.jsonl").write_text('{"task_id": "ref_one", "proof": "h1"}\n')
            write_json(root / "env.json", {"environment_hash": "fixture"})
            config = read_json(PROJECT / "configs/development.json")
            stricter = copy.deepcopy(config)
            stricter["validation"]["max_heartbeats"] = 1
            write_json(root / "stricter.json", stricter)
            outcome = {"validity_status": "elaboration_failure", "metric_status": "unavailable",
                       "engine_hash": file_hash(ENGINE)}
            for name, expected in ((PROJECT / "configs/development.json", 0), (root / "stricter.json", 2)):
                with patch("lean_compression.runner.evaluate_proof", return_value=outcome):
                    code, result, log = run_cli("verify-references", "--tasks", root / "tasks.jsonl",
                                                "--references", root / "refs.jsonl", "--environment", root / "env.json",
                                                "--config", name, "--engine", ENGINE, "--output", root / "checks")
                self.assertEqual(code, expected, log)
            policy = read_json(root / "checks/policy.json")
            self.assertEqual((policy["measurement_hash"], policy["scorer_hash"]),
                             (measurement_hash(config), metrics.SCORER_HASH))
            self.assertIn("use a new output directory", log)


class ScorerIdentityTests(unittest.TestCase):
    def test_scores_carry_the_scorer_identity(self):
        graph = {"p": {"primitive": True}, "r": {"primitive": False, "signature": {"p": 1}, "body": {"p": 2}}}
        self.assertEqual(score_graph(graph, "r", 1, 1)["scorer_hash"], file_hash(metrics.__file__))
        self.assertEqual(score_graph({}, "r", 1, 1)["scorer_hash"], metrics.SCORER_HASH)

    def test_failure_reason_does_not_depend_on_string_hashing(self):
        child = ("import json\n"
                 "from lean_compression.metrics import score_graph\n"
                 "node = lambda body: {'primitive': False, 'signature': {}, 'body': body}\n"
                 "graphs = [{'r': node({'x1': 1, 'x2': 1, 'x3': 1})},\n"
                 "          {'p': {'primitive': True}, 'a': node({'ghost': 1}), 'b': {'signature': {}, 'body': {}},\n"
                 "           'c': node({'p': 'bad'}), 'r': {**node({'a': 1, 'b': 1, 'c': 1}), 'signature': {'p': 1}}}]\n"
                 "print(json.dumps([score_graph(g, 'r', 1, 1)['reasons'] for g in graphs], sort_keys=True))\n")
        source = str(Path(metrics.__file__).resolve().parents[1])
        outputs = {subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, check=True,
                                  env={**os.environ, "PYTHONHASHSEED": str(seed), "PYTHONPATH": source}).stdout
                   for seed in range(6)}
        self.assertEqual(len(outputs), 1, outputs)


if __name__ == "__main__":
    unittest.main()
