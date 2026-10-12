"""Shared imported-node index: content identity, task-private declarations, engine binding."""
from pathlib import Path
import unittest

from lean_compression.graph_cache import index_verified_closures, merge_candidate
from lean_compression.io import digest, read_json
from lean_compression.metrics import score_graph

ROOT = Path(__file__).resolve().parents[1]
POLICY = "expand_mathlib_and_evaluator_v1"
# Smallest committed references that import one auxiliary under two owners
# (DFunLike.coe.hcongr_6), or whose context redeclares a Mathlib name another
# reference imports with identical (Finset.card) or different content
# (Nat.instLinearOrder._proof_1, Finset.card_insert_of_notMem).
COMMITTED_REFERENCES = (
    "library-verified-final/lib_sets_logic_main_equiv_basic_07.*.json",
    "library-verified-final/lib_analysis_inequalities_main_log_basic_10.*.json",
    "library-verified-final/lib_linear_algebra_combinatorics_main_finset_card_01.*.json",
    "fresh-verified-final/fresh_linear_algebra_combinatorics_10.json",
    "library-verified-final/lib_number_theory_pilot_nat_basic_04.*.json",
    "library-verified-final/lib_linear_algebra_combinatorics_main_finset_card_05.*.json",
    "library-verified-final/lib_linear_algebra_combinatorics_main_finset_card_09.*.json",
    "fresh-verified-final/fresh_linear_algebra_combinatorics_06.json")


def node(name, body=None, signature=None, owner="Mathlib.Test", primitive=False):
    return {"name": name, "owner_module": owner, "primitive": primitive,
            "signature": signature or {}, "body": body if body is not None else {},
            "type_hash": "lean-type-hash", "value_hash": "lean-value-hash"}


def record(nodes, task_id="task", engine="engine"):
    return {"task_id": task_id, "validity_status": "success", "environment_hash": "environment",
            "boundary_policy": POLICY, "boundary_policy_hash": digest(POLICY), "engine_hash": engine,
            "source_hash": "source", "extraction": {"metric_status": "available", "nodes": nodes}}


def private(item):
    return item["owner_module"] == "EvaluatorCandidate" or item["owner_module"].startswith("EvaluatorCandidate.")


def envelope(nodes, engine="engine", full=False):
    """A closure's imported names with its evaluator-owned (or all) nodes."""
    return {"environment_hash": "environment", "boundary_policy": POLICY, "engine_hash": engine,
            "imported_node_names": [item["name"] for item in nodes if not private(item)],
            "nodes": nodes if full else [item for item in nodes if private(item)]}


def score(graph):
    return score_graph(graph, "root", 3, 4)


def foo_tasks():
    p = node("p", owner="Init", primitive=True)
    root = node("root", {"Foo": 1}, {"p": 1}, owner="EvaluatorCandidate")
    return {"imports": [p, node("Foo", {"p": 1}, {"p": 1}, owner="Mathlib.Defs"), root],
            "redeclares": [p, node("Foo", {"p": 50}, {"p": 1}, owner="EvaluatorCandidate"), root],
            "copies": [p, node("Foo", {"p": 1}, {"p": 1}, owner="EvaluatorCandidate"), root]}


class GraphCacheIndexTests(unittest.TestCase):
    def test_owner_module_is_metadata_not_content_identity(self):
        def task(owner):
            return [node("p", owner="Init", primitive=True), node("Foo", {"p": 1}, {"p": 1}, owner="Mathlib.Defs"),
                    node("Foo.eq_1", {"Foo": 1}, {"p": 1}, owner=owner),
                    node("root", {"Foo.eq_1": 2}, {"p": 1}, owner="EvaluatorCandidate")]
        first, second = task("Mathlib.Logic.Equiv.Defs"), task("Mathlib.Algebra.Group.Hom.Defs")
        index = index_verified_closures([record(first, "a"), record(second, "b")], "environment", POLICY)
        self.assertEqual(index["nodes"]["Foo.eq_1"]["observed_owner_modules"],
                         ["Mathlib.Algebra.Group.Hom.Defs", "Mathlib.Logic.Equiv.Defs"])
        self.assertNotIn("owner_module", index["nodes"]["Foo.eq_1"])
        alone = index_verified_closures([record(first)], "environment", POLICY)
        self.assertEqual(alone["base_cache"]["Foo.eq_1"]["cache_key"], index["base_cache"]["Foo.eq_1"]["cache_key"])
        merged = merge_candidate(alone, envelope(second, full=True))
        self.assertEqual(score(merged), score(second))
        self.assertEqual(score(merged)["metric_status"], "available")
        for field, value in (("primitive", True), ("body", {"Foo": 2})):
            changed = task("Mathlib.Algebra.Group.Hom.Defs")
            changed[2][field] = value
            with self.assertRaisesRegex(ValueError, "conflicting imported declaration in verified closures: Foo.eq_1"):
                index_verified_closures([record(first, "a"), record(changed, "b")], "environment", POLICY)
            with self.assertRaisesRegex(ValueError, "conflicts with trusted imported declaration: Foo.eq_1"):
                merge_candidate(alone, envelope(changed, full=True))

    def test_context_redeclaration_stays_private_to_its_task(self):
        tasks = foo_tasks()
        index = index_verified_closures([record(nodes, name) for name, nodes in tasks.items()], "environment", POLICY)
        for name, nodes in tasks.items():
            for full in (False, True):
                self.assertEqual(score(merge_candidate(index, envelope(nodes, full=full))), score(nodes), name)
        self.assertEqual(score(merge_candidate(index, envelope(tasks["redeclares"])))["U_decimal"], "52")
        self.assertEqual(index["nodes"]["Foo"]["observed_owner_modules"], ["Mathlib.Defs"])
        self.assertEqual(index["nodes"]["Foo"]["body"], {"p": "1"})
        self.assertIn("Foo", index["excluded_evaluator_node_names"])

    def test_index_never_fills_a_name_the_candidate_does_not_import(self):
        redeclares = foo_tasks()["redeclares"]
        index = index_verified_closures([record(foo_tasks()["imports"])], "environment", POLICY)
        partial = envelope(redeclares)
        partial["nodes"] = [redeclares[-1]]
        merged = merge_candidate(index, partial)
        self.assertNotIn("Foo", merged)
        result = score(merged)
        self.assertEqual((result["metric_status"], result["reason"], result["reasons"][0]["node"]),
                         ("unavailable", "missing_dependency", "Foo"))
        self.assertEqual(score(merge_candidate(index, envelope(redeclares)))["U_decimal"], "52")
        mislabeled = envelope(redeclares)
        mislabeled["imported_node_names"].append("Foo")
        with self.assertRaisesRegex(ValueError, "own declaration as imported: Foo"):
            merge_candidate(index, mislabeled)
        for value in (None, "p", [1]):
            malformed = envelope(redeclares)
            malformed["imported_node_names"] = value
            with self.assertRaisesRegex(ValueError, "imported_node_names"):
                merge_candidate(index, malformed)
        del malformed["imported_node_names"]
        with self.assertRaisesRegex(ValueError, "imported_node_names"):
            merge_candidate(index, malformed)

    def test_extractor_engine_binds_index_records_and_envelopes(self):
        nodes = foo_tasks()["imports"]
        with self.assertRaisesRegex(ValueError, "share one nonempty extractor engine_hash"):
            index_verified_closures([record(nodes, "a"), record(nodes, "b", "other")], "environment", POLICY)
        index = index_verified_closures([record(nodes, "a"), record(nodes, "b")], "environment", POLICY)
        self.assertEqual((index["index_version"], index["engine_hash"]), ("verified_imported_closure_index_v3", "engine"))
        self.assertEqual(index_verified_closures([record(nodes)], "environment", POLICY, "engine")["engine_hash"], "engine")
        unbound = record(nodes)
        del unbound["engine_hash"]
        with self.assertRaisesRegex(ValueError, "share one nonempty extractor engine_hash"):
            index_verified_closures([unbound], "environment", POLICY)
        with self.assertRaisesRegex(ValueError, "record engine_hash does not match"):
            index_verified_closures([record(nodes)], "environment", POLICY, "other")
        other = index_verified_closures([record(nodes, engine="other")], "environment", POLICY)
        self.assertNotEqual(index["base_cache"]["Foo"]["cache_key"], other["base_cache"]["Foo"]["cache_key"])
        self.assertEqual(score(merge_candidate(index, envelope(nodes))), score(nodes))
        for engine in ("other", None):
            with self.assertRaisesRegex(ValueError, "record engine_hash does not match"):
                merge_candidate(index, envelope(nodes, engine))
        unpinned = {key: value for key, value in index.items() if key not in ("engine_hash", "index_hash")}
        older = {**index, "index_version": "verified_imported_closure_index_v2"}
        for stale, message in ((unpinned, "lacks its extractor engine_hash"), (older, "unsupported environment index version")):
            stale["index_hash"] = digest({key: value for key, value in stale.items() if key != "index_hash"})
            with self.assertRaisesRegex(ValueError, message):
                merge_candidate(stale, envelope(nodes))

    def test_committed_references_index_and_merge_like_uncached_scoring(self):
        found = [sorted((ROOT / "data" / "private").glob(pattern)) for pattern in COMMITTED_REFERENCES]
        if any(len(paths) != 1 for paths in found):
            self.skipTest("committed reference evidence is unavailable")
        records = [read_json(paths[0]) for paths in found]
        index = index_verified_closures(records, records[0]["environment_hash"], records[0]["boundary_policy"])
        self.assertEqual(index["nodes"]["DFunLike.coe.hcongr_6"]["observed_owner_modules"],
                         ["Mathlib.Algebra.Group.Hom.Defs", "Mathlib.Logic.Equiv.Defs"])
        self.assertLessEqual({"Finset.card", "Finset.card_insert_of_notMem", "Nat.instLinearOrder._proof_1"},
                             set(index["excluded_evaluator_node_names"]) & index["nodes"].keys())
        for rec in records:
            extraction, tokens = rec["extraction"], rec["extraction"]["source_tokens"]
            imported = [item["name"] for item in extraction["nodes"] if not private(item)]
            for nodes in ([item for item in extraction["nodes"] if private(item)], extraction["nodes"]):
                merged = merge_candidate(index, {"environment_hash": rec["environment_hash"],
                                                 "boundary_policy_hash": rec["boundary_policy_hash"],
                                                 "engine_hash": rec["engine_hash"],
                                                 "imported_node_names": imported, "nodes": nodes})
                scored = score_graph(merged, extraction["root"], tokens["W_S"], tokens["W_B"])
                self.assertEqual(scored, score_graph(extraction["nodes"], extraction["root"], tokens["W_S"], tokens["W_B"]),
                                 rec["task_id"])
                # Committed evidence predates the scorer identity stamp; every measured value must still agree.
                self.assertEqual({k: v for k, v in scored.items() if k != "scorer_hash"}, rec["metrics"], rec["task_id"])


if __name__ == "__main__":
    unittest.main()
