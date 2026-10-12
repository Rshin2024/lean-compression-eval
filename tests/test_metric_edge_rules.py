import unittest

from lean_compression.metrics import score_graph


def node(signature=None, body=None):
    return {"primitive": False, "signature": signature or {}, "body": body if body is not None else {}}


class MetricEdgeRuleTests(unittest.TestCase):
    def unavailable(self, result, reason):
        self.assertEqual((result["metric_status"], result.get("reason")), ("unavailable", reason))
        self.assertIsNone(result["T0"])

    def test_depth_spans_signature_dependencies(self):
        # The statement uses a depth-2 declaration; the body only a primitive.
        graph = {"p": {"primitive": True}, "S1": node({}, {"p": 1}), "S2": node({}, {"S1": 1}),
                 "root": node({"S2": 1}, {"p": 1})}
        result = score_graph(graph, "root", 1, 1)
        self.assertEqual(result["metric_status"], "available")
        self.assertEqual((result["U_S_decimal"], result["U_B_decimal"], result["D"]), ("1", "1", 3))

    def test_zero_multiplicity_references_create_no_edges(self):
        chain = {"p": {"primitive": True}, "d1": node({}, {"p": 1}), "d2": node({}, {"d1": 1})}
        for side in ("signature", "body"):
            with self.subTest(side):
                root = node({"p": 1}, {"p": 1})
                root[side].update(d2=0, unindexed=0)
                result = score_graph({**chain, "root": root}, "root", 1, 1)
                self.assertEqual(result["metric_status"], "available")
                self.assertEqual((result["U_decimal"], result["D"], result["reachable_node_count"]), ("2", 1, 2))

    def test_primitive_root_has_no_signature_body_decomposition(self):
        self.unavailable(score_graph({"p": {"primitive": True}}, "p", 1, 1),
                         "primitive_root_has_no_signature_body_decomposition")

    def test_primitive_flag_must_be_boolean(self):
        for flag in (1, 0, "true", None):
            with self.subTest(flag=flag):
                graph = {"p": {"primitive": flag}, "root": node({"p": 1}, {"p": 1})}
                self.unavailable(score_graph(graph, "root", 1, 1), "missing_primitive_classification")

    def test_zero_signature_source_count_leaves_ratios_unavailable(self):
        result = score_graph({"p": {"primitive": True}, "root": node({"p": 1}, {"p": 1})}, "root", 0, 3)
        self.unavailable(result, "zero_signature_source_count")
        self.assertIsNone(result["I0"])


if __name__ == "__main__":
    unittest.main()
