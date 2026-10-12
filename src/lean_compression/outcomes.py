"""One outcome taxonomy for generation records, evaluation verdicts, and run accounting.

Only allowlisted statuses are model outcomes. Checker, infrastructure, transport and
provider failures, unfinished slots, and any unrecognized status are never counted as
incorrect proofs; they leave the scheduled run incomplete.
"""
from __future__ import annotations

# A generation record's own model outcome. Only "completed" is sent to Lean. A response whose own model
# text could not be stored as received is "malformed_response"; its bytes stay in the run's captures.
GENERATION_OUTCOMES = ("completed", "refusal", "truncated_output", "malformed_response")
# Built-in provider statuses kept as generation evidence; their slots stay incomplete.
# Earlier versions also stored transport and provider failures this way.
PROVIDER_STATUSES = ("model_version_mismatch", "transport_error", "provider_failure")
# Final verdicts attributable to the scheduled model. Evaluator.lean reports validation_error
# only while auditing the candidate's own elaborated closure.
MODEL_FAILURES = ("output_format_failure", "parse_failure", "elaboration_failure", "target_mismatch",
                  "forbidden_dependency", "disallowed_axiom", "validation_error", "kernel_replay_failure",
                  "verification_timeout", "verification_resource_limit", "refusal", "truncated_output",
                  "malformed_response")
# The checker or its environment failed. These are incidents to retry, never verdicts.
CHECKER_ERRORS = ("infrastructure_error", "evaluator_error", "environment_error", "forbidden_environment")
# Final outcomes of the separate extraction process: the proof stays valid, its metrics unavailable.
# Any other extraction status that outcome_class() calls incomplete is a checker incident, as in validation.
EXTRACTOR_OUTCOMES = ("extractor_timeout", "extractor_resource_limit")


def outcome_class(status) -> str:
    """Return success, model_failure or incomplete; unrecognized statuses fail closed."""
    if status == "success":
        return "success"
    return "model_failure" if status in MODEL_FAILURES else "incomplete"
