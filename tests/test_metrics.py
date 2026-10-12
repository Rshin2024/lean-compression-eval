import math
import unittest

from lean_compression.metrics import decimal, exact_int, log2_int, score_graph


def node(signature=None, body=None):
    return {"primitive": False, "signature": signature or {}, "body": body if body is not None else {}}


class MetricTests(unittest.TestCase):
    def test_paper_weighted_diamond(self):
        graph = {"p": {"primitive": True}, "q": {"primitive": True},
                 "A": node({}, {"p": 2, "q": 1}),
                 "B": node({}, {"A": 4, "p": 1}),
                 "target": node({"A": 1}, {"B": 2})}
        result = score_graph(graph, "target", 5, 8)
        self.assertEqual(result["metric_status"], "available")
        self.assertEqual((result["U_S_decimal"], result["U_B_decimal"], result["D"]), ("3", "26", 3))
        self.assertEqual(result["T0"]["numerator_decimal"], "29")
        self.assertEqual(result["T0"]["denominator_decimal"], "13")
        self.assertAlmostEqual(result["log2_T0"], math.log2(29 / 13))
        self.assertEqual(result["I0"]["numerator_decimal"], "8")

    def test_beyond_float_and_decimal_conversion_limits(self):
        enormous = 2 ** 20_000
        graph = {"p": {"primitive": True}, "r": node({"p": 1}, {"p": enormous})}
        result = score_graph(graph, "r", 1, 2)
        self.assertEqual(result["metric_status"], "available")
        self.assertEqual(exact_int(result["U_B_decimal"]), enormous)
        self.assertEqual(exact_int(result["T0"]["numerator_decimal"]), enormous + 1)
        self.assertEqual(result["log2_U_B"], 20_000)
        self.assertTrue(math.isfinite(result["log2_T0"]))
        self.assertEqual(exact_int(decimal(enormous)), enormous)

    def test_deep_graph_is_iterative(self):
        graph = {"p": {"primitive": True}}
        previous = "p"
        for index in range(2500):
            name = f"n{index}"
            graph[name] = node({}, {previous: 2})
            previous = name
        result = score_graph(graph, previous, 1, 1)
        self.assertEqual(result["D"], 2500)
        self.assertEqual(exact_int(result["U_B_decimal"]), 2 ** 2500)

    def test_missing_dependency_never_becomes_primitive(self):
        result = score_graph({"r": node({}, {"unknown": 1})}, "r", 1, 1)
        self.assertEqual(result["reason"], "missing_dependency")
        self.assertIsNone(result["T0"])
        self.assertEqual(result["I0"]["numerator_decimal"], "1")

    def test_cycles_are_unavailable_including_self_loop(self):
        for graph in ({"r": node({}, {"r": 1})},
                      {"r": node({}, {"a": 2}), "a": node({}, {"r": 3})}):
            result = score_graph(graph, "r", 1, 1)
            self.assertEqual(result["reason"], "dependency_cycle")
            self.assertIsNone(result["D"])

    def test_zero_body_is_not_absent_body(self):
        graph = {"p": {"primitive": True}, "r": node({"p": 1}, {})}
        result = score_graph(graph, "r", 1, 1)
        self.assertEqual(result["metric_status"], "available")
        self.assertEqual(result["U_B_decimal"], "0")
        self.assertIsNone(result["log2_U_B"])
        graph["r"]["body"] = None
        result = score_graph(graph, "r", 1, 1)
        self.assertEqual(result["body_state"], "absent")
        self.assertIsNone(result["U_B_decimal"])
        self.assertEqual(result["reason"], "root_body_absent")

    def test_zero_nonprimitive_is_not_promoted(self):
        result = score_graph({"r": node()}, "r", 1, 0)
        self.assertEqual(result["reason"], "zero_reference_nonprimitive")
        self.assertIsNone(result["U_decimal"])

    def test_boundary_is_explicit_and_only_reachable_nodes_count(self):
        graph = {"p": {"primitive": True, "signature": {}, "body": {"absent": 1}},
                 "unused": node({}, {"unused": 1}), "r": node({"p": 1}, {"p": 2})}
        result = score_graph(graph, "r", 1, 1)
        self.assertEqual(result["U_decimal"], "3")
        self.assertEqual(result["D"], 1)
        graph["p"].pop("primitive")
        self.assertEqual(score_graph(graph, "r", 1, 1)["reason"], "missing_primitive_classification")

    def test_invalid_counts_and_list_input(self):
        for count in (-1, 1.5, True, "2.1"):
            result = score_graph({"r": node({}, {"p": count}), "p": {"primitive": True}}, "r", 1, 1)
            self.assertEqual(result["reason"], "invalid_histogram")
        result = score_graph({"nodes": [{"name": "p", "primitive": True},
            {"name": "r", **node({"p": "2"}, {"p": "3"})}]}, "r", 1, 1)
        self.assertEqual(result["U_decimal"], "5")
        self.assertIsNone(log2_int(0))


if __name__ == "__main__":
    unittest.main()
