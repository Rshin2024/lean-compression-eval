"""Reporting accuracy: memory scopes and extraction heartbeats in the resource summary, the reference directory
index-closures takes, the pooled bootstrap's shared helper, baseline tasks left pending by checker incidents, and the
README's list of Lean-gated tests.

Lean, the Docker client and the engine process are fakes; no Lean, Docker daemon, network or model is used.
"""
from collections import Counter
from contextlib import nullcontext, redirect_stderr, redirect_stdout
import csv
import hashlib
import io
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_reporting_remaining

import test_baseline_tactics as portfolio
import test_runtime_gates as gates

from lean_compression import cli
from lean_compression.analysis import CONDITIONS, CONTAINER_PEAK_SCOPE, _resource_summary, analyze, write_report
from lean_compression.cli import dispatch, parser
from lean_compression.io import read_json
from lean_compression.runner import evaluate_proof

PROJECT = Path(__file__).resolve().parents[1]
DEMO_RUN, DEMO_REFS = PROJECT / "examples/pipeline-demo", PROJECT / "examples/demo-reference-check"
REAL_POPEN = subprocess.Popen
MIB = 1024 * 1024


def scheduled(index, validation=None, extraction=None):
    """A scheduled attempt of task t, with the resources its validation and extraction runs recorded."""
    return {"task_id": "t", "model_snapshot": "m", "condition": CONDITIONS[0], "sample_index": index,
            "validity_status": "success", "metric_status": "unavailable", "metrics": None,
            **({"validation": {"resources": validation}} if validation is not None else {}),
            **({"extraction": {"resources": extraction}} if extraction is not None else {})}


def resource_report(attempts):
    """The analysis of task t's first condition, its measurements, its resource_usage.csv rows and report.html."""
    report = analyze([{"task_id": "t", "family_id": "f", "domain": "d"}], attempts, {},
                     {"samples_per_goal_condition": len(attempts), "bootstrap_replicates": 0})
    with tempfile.TemporaryDirectory() as directory:
        paths = write_report(report, directory)
        with open(paths["resources"], encoding="utf-8", newline="") as handle:
            rows = {row["measurement"]: row for row in csv.DictReader(handle) if row["condition"] == CONDITIONS[0]}
        page = Path(paths["html"]).read_text(encoding="utf-8")
    return report["models"]["m"]["conditions"][CONDITIONS[0]]["resources"]["measurements"], rows, page


class PeakMemoryScopeTests(unittest.TestCase):
    """Issue 7: a container's cgroup high-water mark and a process's RSS peak are kept apart and labelled."""

    def test_container_and_process_peaks_are_summarized_apart_and_labelled(self):
        attempts = [scheduled(0, {"peak_memory_mib": 4096.0, "peak_memory_scope": CONTAINER_PEAK_SCOPE}),
                    # Docker records from before peak_memory_scope was recorded.
                    scheduled(1, {"peak_memory_mib": 2048.0}), scheduled(2, {"peak_memory_bytes": 1024 * MIB}),
                    # Records that supply one Lean process's resident set size, which no runner of this harness writes.
                    scheduled(3, {"peak_rss_mib": 900.0}, {"max_rss_bytes": 950 * MIB}),
                    # A record holding both keeps each in its own scope; a requested limit is no peak.
                    scheduled(4, {"peak_memory_mib": 3000.0, "peak_memory_scope": CONTAINER_PEAK_SCOPE, "peak_rss_mib": 700.0}),
                    scheduled(5, {"peak_memory_mib": None, "peak_memory_reason": "not_instrumented", "memory_mib_requested": 4096})]
        measurements, rows, page = resource_report(attempts)
        summary = lambda name: tuple(measurements[name][key] for key in ("observed_count", "invalid_count", "median", "max", "sum"))
        # One pooled measurement used to mix the 900 MiB RSS peak in (median 2048 MiB) and hide the 700 MiB one.
        self.assertEqual(summary("validation_peak_memory_mib"), (4, 0, 2524.0, 4096.0, None))
        self.assertEqual(summary("validation_peak_rss_mib"), (2, 0, 800.0, 900.0, None))
        self.assertEqual(summary("extraction_peak_memory_mib"), (0, 0, None, None, None))
        self.assertEqual(summary("extraction_peak_rss_mib"), (1, 0, 950.0, 950.0, None))
        for stage in ("validation", "extraction"):
            container, process = rows[f"{stage}_peak_memory_mib"], rows[f"{stage}_peak_rss_mib"]
            self.assertIn(CONTAINER_PEAK_SCOPE, container["description"])
            self.assertIn("resident set size", process["description"])
            for row in (container, process):
                self.assertIn("Never pooled", row["description"])
            self.assertIn(f"<td>{stage} peak memory MiB, container (page cache included)</td>", page)
            self.assertIn(f"<td>{stage} peak RSS MiB, Lean process (not recorded by this harness)</td>", page)
        self.assertIn("shown in separate rows and never pooled", page)

    def test_a_container_peak_recorded_under_another_scope_is_invalid_not_pooled(self):
        measurements, _, _ = resource_report([
            scheduled(0, {"peak_memory_mib": 4096.0, "peak_memory_scope": CONTAINER_PEAK_SCOPE}),
            scheduled(1, {"peak_memory_mib": 512.0, "peak_memory_scope": "peak resident set size of the Lean process"}),
            scheduled(2, {"peak_memory_bytes": 512 * MIB, "peak_memory_scope": ["unrecognized"]})])
        container = measurements["validation_peak_memory_mib"]
        self.assertEqual((container["observed_count"], container["invalid_count"], container["max"]), (1, 2, 4096.0))
        self.assertEqual(measurements["validation_peak_rss_mib"]["observed_count"], 0)

    @unittest.skipUnless(gates.SHELLS, "POSIX sh is unavailable")
    def test_the_docker_runners_peak_is_a_container_peak(self):
        # The runner's Docker branch, with run-lean playing the container against fake cgroup v2 files.
        container = gates.DockerMemoryEvidenceTests("test_cgroup_v2_and_v1_evidence_is_recorded_with_the_peak_scope")
        container.setUp()
        self.addCleanup(container.doCleanups)
        checked = container.execute(gates.V2)
        self.assertEqual((checked["validity_status"], checked["resources"]["peak_memory_scope"]),
                         ("success", CONTAINER_PEAK_SCOPE))
        measurements = _resource_summary([{"validation": checked}])["measurements"]
        self.assertEqual((measurements["validation_peak_memory_mib"]["max"], measurements["validation_peak_memory_mib"]["invalid_count"],
                          measurements["validation_peak_rss_mib"]["observed_count"]), (4096.0, 0, 0))


class ExtractionHeartbeatTests(unittest.TestCase):
    """Issue 8: the extraction run's heartbeats are summarized where recorded, labelled as a repeat of validation's."""

    def test_extraction_heartbeats_are_summarized_apart_from_validation(self):
        measurements, rows, page = resource_report([
            scheduled(0, {"heartbeats_used": 4.5}, {"heartbeats_used": 4.75}),
            scheduled(1, {"heartbeats_used": 10.0}, {"heartbeats_used": 10.0}),
            # An extraction stopped by its time limit reports none; a failed check runs no extraction.
            scheduled(2, {"heartbeats_used": 2.0}, {"heartbeats_used": None, "heartbeats_used_reason": "not_reported_by_engine"}),
            scheduled(3, {"heartbeats_used": None})])
        summary = lambda name: tuple(measurements[name][key] for key in ("unit", "observed_count", "missing_count", "sum", "max"))
        self.assertEqual(summary("validation_heartbeats"), ("maxHeartbeats", 3, 1, 16.5, 10.0))
        self.assertEqual(summary("extraction_heartbeats"), ("maxHeartbeats", 2, 2, 14.75, 10.0))
        description = rows["extraction_heartbeats"]["description"]
        for text in ("in the extraction run", "second reading of the validation work", "never add it to validation_heartbeats",
                     "successful checks only"):
            self.assertIn(text, description)
        self.assertIn("in the validation run", rows["validation_heartbeats"]["description"])
        self.assertIn("<td>extraction heartbeats (a repeat of the validation check; not additive)</td>", page)
        self.assertIn("never added to validation heartbeats", page)

    def test_the_runner_records_what_the_summary_reads_for_both_runs(self):
        def popen(cmd, *args, **kwargs):
            # The engine's result is written where Lean would write it; a real process exits cleanly.
            mode = json.loads(Path(cmd[-2]).read_text(encoding="utf-8"))["mode"]
            total = {"validate": 2.5, "extract": 2.75}[mode]
            Path(cmd[-1]).write_text(json.dumps({
                "validity_status": "success", "metric_status": "not_requested" if mode == "validate" else "unavailable",
                "metric_error": "stubbed extraction", "candidate_declarations": ["evaluation_target"],
                "heartbeats": {"raw": {"total": total * 1000}, "max_heartbeats_units": {"total": total}}}))
            return REAL_POPEN([sys.executable, "-c", ""], stdout=kwargs["stdout"], stderr=kwargs["stderr"])
        config = {"validation": {"isolation": "macos_sandbox", "max_heartbeats": 200000, "wall_seconds": 30,
                                 "permitted_axioms": []},
                  "extraction": {"wall_seconds": 30}, "environment": {"boundary_policy": "expand_mathlib_and_evaluator_v1"}}
        environment = {"environment_hash": "fixture", "lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
                       "lean_search_paths": ["/opt/lean/lib/lean"]}
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory).resolve() / "Evaluator.lean"
            engine.write_text("-- engine\n")
            with patch("platform.system", return_value="Darwin"), patch("subprocess.Popen", popen):
                result = evaluate_proof({"task_id": "t", "proposition": "True"}, "trivial", environment, engine, config)
        self.assertEqual((result["validation"]["resources"]["heartbeats_used"], result["extraction"]["resources"]["heartbeats_used"]),
                         (2.5, 2.75))
        measurements = _resource_summary([result])["measurements"]
        self.assertEqual((measurements["validation_heartbeats"]["sum"], measurements["extraction_heartbeats"]["sum"]), (2.5, 2.75))


class IndexClosuresReferenceTests(unittest.TestCase):
    """Issue 9: index-closures indexes the closures of a verify-references directory only."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def references(self, name, policy):
        """The demo reference checks under another policy.json."""
        target = self.root / name
        shutil.copytree(DEMO_REFS, target)
        (target / "policy.json").chmod(0o644)
        (target / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
        return target

    def index(self, references, output):
        err = io.StringIO()
        argv = ["index-closures", "--references", references, "--environment", PROJECT / "configs/environment.hashed.json",
                "--config", PROJECT / "examples/demo-config.json", "--output", output]
        with redirect_stdout(io.StringIO()), redirect_stderr(err), patch("lean_compression.cli.load_environment", read_json):
            try:
                cli.main([str(value) for value in argv])
            except SystemExit as stop:
                return stop.code, err.getvalue()
        return 0, err.getvalue()

    def test_a_baseline_or_other_directory_is_refused_before_indexing(self):
        policy = read_json(DEMO_REFS / "policy.json")
        # A baseline's records are tactic proofs: the kind decides, whatever the records hold.
        cases = (("baseline", {**policy, "kind": "baseline"}, "'baseline'"), ("rescore", {**policy, "kind": "rescore"}, "'rescore'"),
                 ("no kind", {key: value for key, value in policy.items() if key != "kind"}, "None"),
                 ("not an object", [policy], "None"))
        for name, value, kind in cases:
            with self.subTest(name):
                references, output = self.references(name, value), self.root / f"{name}.json"
                self.assertEqual(self.index(references, output), (2, "--references must be a verify-references output "
                                                                      f"directory, but its policy.json has kind {kind}: {references}\n"))
                self.assertFalse(output.exists())

    def test_a_verify_references_directory_is_indexed_and_other_json_is_not_a_closure(self):
        references = self.references("checks", read_json(DEMO_REFS / "policy.json"))
        # As for freeze's reference checks, a JSON file that is not a record object is not a reference record.
        (references / "notes.json").write_text('["not", "a", "record"]', encoding="utf-8")
        output = self.root / "index.json"
        self.assertEqual(self.index(references, output), (0, ""))
        self.assertEqual([source["task_id"] for source in read_json(output)["sources"]], ["smoke_ag_01"])


FAMILIES = {"algebra": {"fA": 3, "fB": 1, "fC": 2}, "analysis": {"fD": 2, "fE": 2}, "number_theory": {"fF": 1}}


def measured(total, depth):
    return {"metric_status": "available", "W_S": 1, "W_B": 3, "U_S_decimal": "1", "U_B_decimal": str(total - 1), "D": depth}


def multi_domain(cross_domain=False):
    """Tasks of three domains in families of one to three tasks, two samples per condition, and their references.

    Every log2 is an integer and every task mean a multiple of 1/2, so each bootstrap sum is exact and the intervals
    are the same under any Python version or platform. Failed, unmeasurable and missing samples vary the support of
    each contrast. With cross_domain, one family spans two domains, so families are resampled across domains.
    """
    tasks = [{"task_id": f"{family}_{index}", "family_id": family, "domain": domain, "origin": "fresh", "split": "pilot"}
             for domain, families in FAMILIES.items() for family, size in families.items() for index in range(size)]
    for number, task in enumerate(tasks):
        task["stratum"] = ("easy", "hard", "medium")[number % 3]
    if cross_domain:
        tasks[-2]["family_id"] = "fA"
    attempts = []
    for number, task in enumerate(tasks):
        for arm, condition in enumerate(CONDITIONS):
            for sample in range(2):
                code = (7 * number + 3 * arm + 5 * sample) % 9
                if arm == 2 and task["family_id"] not in ("fB", "fD"):
                    code = min(code, 1)  # Only two families can support compression_vs_concision.
                if code == 2 and sample:
                    continue
                attempt = {"task_id": task["task_id"], "model_snapshot": "model", "condition": condition,
                           "sample_index": sample, "validity_status": "elaboration_failure" if code == 0 else "success",
                           "metric_status": "available" if code > 1 else "unavailable",
                           "metrics": measured(8 << code % 4, 1 + code % 3) if code > 1 else {"reason": "extractor_timeout"}}
                attempts.append(attempt)
    references = {task["task_id"]: measured(16, 2) for task in tasks}
    return tasks, attempts, references


def inline_pooled_bootstrap(vectors, strata, families, replicates, seed):
    """The pooled resampling loop analyze() ran inline before it called _family_bootstrap, verbatim."""
    rng = random.Random(seed)
    boot_values = {key: [] for key in vectors}
    support_minimum = {key: None for key in vectors}
    for _ in range(replicates):
        family_weights = Counter()
        for cluster_ids in strata.values():
            family_weights.update(rng.choices(cluster_ids, k=len(cluster_ids)))
        task_weights = {task_id: multiplicity for family, multiplicity in family_weights.items()
                        for task_id in families[family]}
        for key, values in vectors.items():
            denominator = sum(task_weights.get(task_id, 0) for task_id in values)
            if denominator:
                boot_values[key].append(sum(value * task_weights.get(task_id, 0)
                                            for task_id, value in values.items()) / denominator)
            minimum = support_minimum[key]
            support_minimum[key] = denominator if minimum is None else min(minimum, denominator)
    return boot_values, support_minimum


POOLED = ("estimate", "confidence_interval", "one_sided_lower_bound", "bootstrap_defined", "bootstrap_undefined",
          "minimum_bootstrap_task_multiplicity", "task_count", "family_count")


def pooled(report):
    """Each model's pooled contrast estimates and intervals, as canonical JSON bytes.

    Geometric ratios are left out: exp2 of a non-integer goes through the platform's pow(), so their last bits may
    depend on it. The whole-report comparison with the inline loop covers them on this platform.
    """
    return json.dumps({model: {name: {label: {key: effect[key] for key in POOLED} for label, effect in contrast.items()
                                      if isinstance(effect, dict) and "estimate" in effect}
                               for name, contrast in value["contrasts"].items()}
                       for model, value in report["models"].items()}, sort_keys=True, allow_nan=False).encode()


class PooledBootstrapTests(unittest.TestCase):
    """Issue 12: analyze() resamples the pooled contrasts with _family_bootstrap, which the stratum breakdown already
    used to replay the pooled draws, instead of its own copy of that loop."""

    # SHA-256 of pooled() for each fixture as analyze() gave it with the inline loop, before it called the helper.
    # Python 3.11 and 3.14 agree on these.
    INLINE = {"documented example": "0b099eed5148eef186fbdd4cfaeb3abb3fe4e1e59c9963e7b1c916dbaafdd01a",
              "nested domains": "3ac143932e655163ece40862cbc41dba9b7a8ec4b280da0b687ab008049b8151",
              "cross-domain family": "aefd4982cd2ddbbb9ac1e543fafd2e7ca34a74786ddf3270c6cff110bf96e281"}

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.demo = Path(cls.temp.name) / "pipeline-demo"
        shutil.copytree(DEMO_RUN, cls.demo)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def analyses(self, bootstrap=None):
        """Each fixture's analysis bytes: the documented example's analysis.json, and the synthetic reports."""
        with patch("lean_compression.analysis._family_bootstrap", bootstrap) if bootstrap else nullcontext():
            output = Path(tempfile.mkdtemp(dir=self.temp.name)) / "report"
            argv = ["analyze", "--run", self.demo, "--version", "v2", "--references", DEMO_REFS, "--output", output]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                dispatch(parser().parse_args([str(value) for value in argv]))
            result = {"documented example": (output / "analysis.json").read_bytes()}
            for name, cross in (("nested domains", False), ("cross-domain family", True)):
                tasks, attempts, references = multi_domain(cross)
                report = analyze(tasks, attempts, references, {"samples_per_goal_condition": 2,
                                                               "bootstrap_replicates": 400, "bootstrap_seed": 11})
                result[name] = json.dumps(report, sort_keys=True, allow_nan=False).encode()
        return result

    def test_the_synthetic_fixtures_exercise_stratified_and_unstratified_resampling(self):
        reports = {name: json.loads(value) for name, value in self.analyses().items()}
        nested, crossed = reports["nested domains"], reports["cross-domain family"]
        self.assertEqual((nested["bootstrap"]["stratified_by_domain"], nested["bootstrap"]["strata"],
                          nested["bootstrap"]["singleton_strata"]),
                         (True, {"algebra": 3, "analysis": 2, "number_theory": 1}, ["number_theory"]))
        self.assertEqual((crossed["bootstrap"]["stratified_by_domain"], crossed["bootstrap"]["strata"]), (False, {"all": 6}))
        for report in (nested, crossed):
            effects = [effect for contrast in report["models"]["model"]["contrasts"].values()
                       for effect in contrast.values() if isinstance(effect, dict) and "estimate" in effect]
            # Some intervals have width, some replicates resample no supporting task.
            self.assertTrue(any(low < high for low, high in (effect["confidence_interval"] for effect in effects
                                                              if None not in effect["confidence_interval"])))
            self.assertTrue(any(effect["bootstrap_undefined"] for effect in effects))

    def test_pooled_estimates_and_intervals_are_byte_identical_to_the_inline_loop(self):
        helper, inline = self.analyses(), self.analyses(inline_pooled_bootstrap)
        for name in self.INLINE:
            with self.subTest(name):
                # The whole report, pooled and stratum contrasts alike, with the helper and with the inline loop.
                self.assertEqual(helper[name], inline[name])
                report = json.loads(helper[name])
                self.assertEqual(hashlib.sha256(pooled(report)).hexdigest(), self.INLINE[name])


class BaselineIncidentTests(unittest.TestCase):
    """Issue 13: a task the baseline command left pending after a logged checker incident is unresolved, not missing."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def baseline(self, config, outcomes=None, limit=None):
        argv = portfolio.baseline_argv(self.root) + ([] if limit is None else ["--limit", limit])
        result, log = portfolio.quiet(argv, portfolio.FakeEngine(outcomes))
        self.assertIsInstance(result, dict, log)
        return result

    def summary(self, output):
        result, log = portfolio.analyze(self.root, "baseline", output)
        self.assertIsInstance(result, dict, log)
        page = (self.root / output / "report.html").read_text(encoding="utf-8")
        with (self.root / output / "automation_baseline.csv").open(encoding="utf-8", newline="") as handle:
            outcomes = {row["task_id"]: row["portfolio_outcome"] for row in csv.DictReader(handle)}
        return portfolio.summary(self.root, output), page, outcomes

    def test_a_logged_incident_is_unresolved_until_a_rerun_checks_the_task(self):
        portfolio.frozen_run(self.root, portfolio.DEVELOPMENT)
        # solved: a checker incident; unsolved: checked and rejected; spent: never reached under --limit 2.
        result = self.baseline(portfolio.DEVELOPMENT, {("solved", "by assumption"): ("infrastructure_error", 3.0)}, limit=2)
        self.assertEqual((result["new_checks"], result["checker_incidents"]), (1, 1))
        log = self.root / "baseline/incidents.jsonl"
        with log.open("a", encoding="utf-8") as handle:
            # Not a checker incident; a task outside the run; a line a killed writer left unterminated.
            handle.write(json.dumps({"event": "note", "task_id": "spent"}) + "\n")
            handle.write(json.dumps({"event": "checker_incident", "task_id": "elsewhere", "validity_status": "environment_error"}) + "\n")
            handle.write('{"event": "checker_incident", "task_id": "spent", "validity_status"')
        summary, page, outcomes = self.summary("report")
        self.assertEqual((summary["status"], summary["missing_task_ids"], summary["unresolved_task_ids"],
                          summary["checker_incident_task_ids"], summary["incident_log_unreadable_lines"]),
                         ("incomplete", ["spent"], ["solved"], ["solved"], 1))
        self.assertEqual({task_id: (row["outcome"], row["portfolio_solved"], row["validity_status"], row["checker_incident"])
                          for task_id, row in summary["per_task"].items()},
                         {"solved": ("unresolved", None, None, "infrastructure_error"),
                          "spent": ("missing", None, None, None), "unsolved": ("unsolved", False, "parse_failure", None)})
        self.assertEqual({key: summary["overall"][key] for key in ("solved", "unsolved", "unresolved", "missing")},
                         {"solved": 0, "unsolved": 1, "unresolved": 1, "missing": 1})
        self.assertEqual(outcomes, {"solved": "unresolved", "spent": "missing", "unsolved": "unsolved"})
        self.assertIn("Baseline incomplete:</strong> 1 tasks have no baseline record and 1 have unresolved checker outcomes, "
                      "1 of them without a record because their latest check was a checker incident logged in "
                      "<code>incidents.jsonl</code>; rerunning the baseline command into the same directory checks them again.",
                      page)
        self.assertIn("Unreadable lines in <code>incidents.jsonl</code>, whose tasks are not identified: 1.", page)
        self.assertIn("<td>solved</td><td>solved</td><td>number_theory</td><td>unresolved</td>"
                      "<td>no record (checker incident: infrastructure_error)</td><td>no record</td>", page)
        # A rerun into the same directory checks the pending tasks; their records decide.
        self.assertEqual(self.baseline(portfolio.DEVELOPMENT)["new_checks"], 2)
        summary, page, outcomes = self.summary("report-rerun")
        self.assertEqual((summary["status"], summary["unresolved_task_ids"], summary["checker_incident_task_ids"]),
                         ("complete", [], []))
        self.assertEqual(outcomes, {"solved": "solved", "spent": "unsolved", "unsolved": "unsolved"})
        self.assertNotIn("Baseline incomplete", page)

    def test_a_legacy_single_proof_baseline_reads_its_incidents_too(self):
        portfolio.frozen_run(self.root, portfolio.LEGACY_CONFIG)
        self.baseline(portfolio.LEGACY_CONFIG, {("solved", portfolio.LEGACY): ("infrastructure_error", 4.0)})
        summary, _, outcomes = self.summary("report")
        self.assertEqual((summary["portfolio_semantics"], summary["unresolved_task_ids"], summary["missing_task_ids"]),
                         ("legacy_single_proof", ["solved"], []))
        self.assertEqual(outcomes["solved"], "unresolved")


class ReadmeLeanTestListTests(unittest.TestCase):
    """Issue 14: the README names every test module that runs the pinned Lean, in Docker or not."""

    def test_every_lean_gated_test_module_is_listed(self):
        paragraph = next(line for line in (PROJECT / "README.md").read_text(encoding="utf-8").splitlines()
                         if "REQUIRE_LEAN=1" in line and "need no Lean" in line)
        listed = set(re.findall(r"`tests/(test_\w+\.py)`", paragraph))
        modules = {path.name: path.read_text(encoding="utf-8") for path in (PROJECT / "tests").glob("test_*.py")}
        # test_lean_runtime_minor checks the runtime locator itself with a stand-in binary, and this module only
        # names the markers; neither starts Lean.
        markers = re.compile(r"pinned_runtime\(|\.LeanIntegrationTests\b|LEAN_COMPRESSION_DOCKER_ENV")
        gated = {name for name, source in modules.items()
                 if markers.search(source) and name not in ("test_lean_runtime_minor.py", Path(__file__).name)}
        self.assertEqual(gated - listed, set())
        self.assertEqual(listed - set(modules), set())
        self.assertIn("tests/test_round3.py", paragraph)


if __name__ == "__main__":
    unittest.main()
