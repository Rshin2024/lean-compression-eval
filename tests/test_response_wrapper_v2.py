import unittest
from lean_compression.prompts import adapt_response


class ResponseWrapperTests(unittest.TestCase):
    def test_uniform_single_wrapper_only(self):
        for raw in ["```lean\nby exact h\n```", "```lean4\nby exact h\n```", "<PROOF>\nby exact h\n</PROOF>"]:
            self.assertEqual(adapt_response(raw, "single_outer_wrapper_v2"), "by exact h")
        self.assertEqual(adapt_response("by exact h", "single_outer_wrapper_v2"), "by exact h")

    def test_no_candidate_selection_prose_removal_or_nested_unwrapping(self):
        for raw in ["Here is the proof:\n```lean\nby exact h\n```", "<PROOF>by exact h</PROOF><PROOF>by assumption</PROOF>",
                    "```lean\nby exact h\n```\n```lean\nby assumption\n```"]:
            self.assertEqual(adapt_response(raw, "single_outer_wrapper_v2"), raw)
        self.assertEqual(adapt_response("```lean\n<PROOF>by exact h</PROOF>\n```", "single_outer_wrapper_v2"),
                         "<PROOF>by exact h</PROOF>")

    def test_legacy_policies_remain_unchanged(self):
        raw = "<PROOF>by exact h</PROOF>"
        self.assertEqual(adapt_response(raw, "identity_v1"), raw)
        self.assertEqual(adapt_response(raw, "single_fence_v1"), raw)
