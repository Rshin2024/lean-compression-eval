import copy
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.graph_cache import index_verified_closures, merge_candidate, write_index
from lean_compression.io import digest
from lean_compression.metrics import score_graph

POLICY = {"expanded_modules": ["Mathlib", "EvaluatorCandidate"], "sort": "atomic"}


def node(name, body=None, signature=None, owner="Mathlib.Test", primitive=False):
    return {"name": name, "owner_module": owner, "primitive": primitive,
            "signature": signature or {}, "body": body if body is not None else {},
            "type_hash": "lean-type-hash", "value_hash": "lean-value-hash"}


def record(nodes, task_id="task"):
    return {"task_id": task_id, "validity_status": "success", "environment_hash": "environment",
            "boundary_policy_hash": digest(POLICY), "source_hash": "source",
            "extraction": {"metric_status": "available", "nodes": nodes}}


class GraphCacheTests(unittest.TestCase):
    def weighted_graph(self):
        return [node("p", owner="Init", primitive=True), node("q", owner="Init", primitive=True),
                node("A", {"p": 2, "q": 1}), node("B", {"A": 4, "p": 1}),
                node("target", {"B": 2}, {"A": 1}, owner="EvaluatorCandidate")]

    def test_weighted_base_index_excludes_candidate_source_cost_and_merges(self):
        nodes = self.weighted_graph()
        index = index_verified_closures([record(nodes)], "environment", POLICY)
        self.assertNotIn("target", index["nodes"])
        self.assertEqual(index["base_cache"]["A"]["metrics"]["U_decimal"], "3")
        self.assertEqual(index["base_cache"]["B"]["metrics"]["U_decimal"], "13")
        self.assertEqual(index["base_cache"]["B"]["metrics"]["D"], 2)
        self.assertEqual(index["primitive_manifest"], ["p", "q"])
        self.assertNotIn("W_S", index["base_cache"]["B"]["metrics"])
        candidate = {"environment_hash": "environment", "boundary_policy_hash": digest(POLICY),
                     "nodes": [nodes[-1]]}
        merged = merge_candidate(index, candidate)
        self.assertEqual(score_graph(merged, "target", 5, 8)["U_decimal"], "29")
        self.assertNotIn("target", index["nodes"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            write_index(index, path)
            write_index(index, path)
            self.assertEqual(json.loads(path.read_text()), index)

    def test_weighted_histogram_changes_cache_key_even_when_expr_hash_collides(self):
        original = self.weighted_graph()
        modified = copy.deepcopy(original)
        modified[2]["body"]["p"] = 3
        first = index_verified_closures([record(original)], "environment", POLICY)
        second = index_verified_closures([record(modified)], "environment", POLICY)
        self.assertNotEqual(first["base_cache"]["A"]["cache_key"], second["base_cache"]["A"]["cache_key"])
        with self.assertRaisesRegex(ValueError, "conflicting imported"):
            index_verified_closures([record(original), record(modified, "other")], "environment", POLICY)

    def test_cycles_missing_nodes_and_zero_nodes_remain_unavailable(self):
        nodes = [node("a", {"b": 1}), node("b", {"a": 2}), node("cycle_user", {"a": 1}),
                 node("missing", {"unknown": 1}), node("zero"), node("zero_user", {"zero": 1}),
                 node("p", owner="Init", primitive=True)]
        index = index_verified_closures([record(nodes)], "environment", POLICY)
        for name in ("a", "b", "cycle_user"):
            self.assertEqual(index["base_cache"][name]["metrics"]["reason"], "dependency_cycle")
        self.assertEqual(index["base_cache"]["missing"]["metrics"]["reason"], "missing_dependency")
        self.assertEqual(index["base_cache"]["zero"]["metrics"]["reason"], "zero_reference_nonprimitive")
        self.assertEqual(index["base_cache"]["zero_user"]["metrics"]["reason"], "dependency_metrics_unavailable")
        self.assertEqual(index["base_cache"]["p"]["metrics"]["metric_status"], "available")

    def test_environment_and_boundary_are_required_and_match_on_merge(self):
        index = index_verified_closures([record(self.weighted_graph())], "environment", POLICY)
        for key in ("environment_hash", "boundary_policy_hash"):
            rec = record(self.weighted_graph())
            rec[key] = "other"
            with self.assertRaisesRegex(ValueError, key):
                index_verified_closures([rec], "environment", POLICY)
            envelope = {"environment_hash": "environment", "boundary_policy_hash": digest(POLICY), "nodes": []}
            envelope[key] = "other"
            with self.assertRaisesRegex(ValueError, key):
                merge_candidate(index, envelope)
        with self.assertRaisesRegex(ValueError, "provenance envelope"):
            merge_candidate(index, [])

    def test_unverified_or_unowned_nodes_are_rejected(self):
        rec = record(self.weighted_graph())
        rec["validity_status"] = "elaboration_failure"
        with self.assertRaisesRegex(ValueError, "verified reference"):
            index_verified_closures([rec], "environment", POLICY)
        rec = record(self.weighted_graph())
        del rec["extraction"]["nodes"][0]["owner_module"]
        with self.assertRaisesRegex(ValueError, "owner_module"):
            index_verified_closures([rec], "environment", POLICY)

    def test_versioned_string_boundary_policy_uses_its_exact_hash(self):
        policy = "expand_mathlib_and_evaluator_v1"
        rec = record(self.weighted_graph())
        rec["boundary_policy_hash"] = digest(policy)
        index = index_verified_closures([rec], "environment", policy)
        self.assertEqual(index["boundary_policy"], policy)
        merged = merge_candidate(index, {"environment_hash": "environment", "boundary_policy": policy,
                                         "nodes": [self.weighted_graph()[-1]]})
        self.assertEqual(score_graph(merged, "target", 5, 8)["U_decimal"], "29")
        with self.assertRaisesRegex(ValueError, "boundary_policy_hash"):
            index_verified_closures([rec], "environment", policy + "_changed")

    def test_cache_tampering_and_candidate_conflicts_are_rejected(self):
        index = index_verified_closures([record(self.weighted_graph())], "environment", POLICY)
        bad = node("A", {"p": 999})
        with self.assertRaisesRegex(ValueError, "conflicts with trusted"):
            merge_candidate(index, {"environment_hash": "environment", "boundary_policy": POLICY, "nodes": [bad]})
        index["base_cache"]["A"]["metrics"]["U_decimal"] = "999"
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            merge_candidate(index, {"environment_hash": "environment", "boundary_policy": POLICY, "nodes": []})

    def test_deep_graph_does_not_recurse_and_absent_body_is_distinct(self):
        nodes = [node("p", owner="Init", primitive=True)]
        nodes[0]["body"] = None
        previous = "p"
        for i in range(1500):
            name = f"n{i}"
            nodes.append(node(name, {previous: 2}))
            previous = name
        index = index_verified_closures([record(nodes)], "environment", POLICY)
        self.assertEqual(index["base_cache"][previous]["metrics"]["D"], 1500)
        self.assertEqual(index["base_cache"]["p"]["metrics"]["body_state"], "absent")

    def test_unexpanded_atomic_body_is_unmeasured_not_absent(self):
        p = node("p", owner="Init", primitive=True)
        p.update(body=None, body_present=True, histogram_status="atomic_boundary_not_expanded")
        index = index_verified_closures([record([p])], "environment", POLICY)
        self.assertEqual(index["base_cache"]["p"]["metrics"]["body_state"], "unmeasured_atomic")
        self.assertEqual(index["base_cache"]["p"]["metrics"]["U_decimal"], "1")
        changed = copy.deepcopy(p)
        changed["body_present"] = False
        other = index_verified_closures([record([changed])], "environment", POLICY)
        self.assertNotEqual(index["base_cache"]["p"]["cache_key"], other["base_cache"]["p"]["cache_key"])


if __name__ == "__main__":
    unittest.main()
