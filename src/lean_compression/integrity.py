"""Fail-closed gates for stored generations, the implemented policy, a main run's pilot report, and CLI evidence paths."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from .io import digest

# The one policy the pinned engine and scorer apply: Evaluator.lean expands only
# Mathlib- and EvaluatorCandidate-owned declarations (the runner sends no
# expand_modules), counts original leaf spans and rejects unindexed dependencies;
# metrics.py makes a reachable cycle unavailable. Records and cohort identities
# carry these labels, so any other configured value would mislabel results.
IMPLEMENTED_POLICY = {("environment", "boundary_policy"): "expand_mathlib_and_evaluator_v1",
                      ("environment", "source_token_policy"): "original_syntax_v1",
                      ("extraction", "unknown_dependency"): "error",
                      ("extraction", "dependency_cycle"): "metrics_unavailable"}


def require_implemented_policy(config: dict) -> None:
    """Never record a boundary or extraction policy label that was not applied."""
    for (section, key), implemented in IMPLEMENTED_POLICY.items():
        values = config.get(section)
        value = values.get(key) if isinstance(values, dict) else None
        if value != implemented:
            raise ValueError(f"Unsupported {section}.{key} {value!r}: the pinned engine and scorer implement only {implemented!r}")


def is_content_digest(image) -> bool:
    """A complete image ID (sha256: and 64 lowercase hex digits) or repository digest (name@sha256:...).

    Anything else, such as sha256:latest or a truncated ID, can resolve to a mutable tag or an ID-prefix match.
    """
    return isinstance(image, str) and re.fullmatch(r"(?:[^\s@]+@)?sha256:[0-9a-f]{64}", image) is not None


def verified_generation(generation: dict, slot: dict) -> dict:
    """A stored generation must belong to its scheduled slot and match its own content hash."""
    attempt = slot["attempt_id"]
    if not isinstance(generation, dict):
        raise ValueError(f"Stored generation is not a JSON object: {attempt}")
    for key in ("attempt_id", "prompt_hash"):
        if generation.get(key) != slot[key]:
            raise ValueError(f"Stored generation {key} does not match scheduled attempt {attempt}")
    if "raw_response" not in generation or generation.get("raw_response_hash") != digest(generation["raw_response"]):
        raise ValueError(f"Stored generation raw_response does not match its raw_response_hash: {attempt}")
    # Earlier imports stored any JSON value; only a completed generation is read as proof text.
    if generation.get("generation_status", "completed") == "completed" and not isinstance(generation["raw_response"], str):
        raise ValueError(f"Stored generation has no raw_response text: {attempt}")
    return generation


def same_stored_response(existing: dict, slot: dict, raw: str) -> bool:
    """Compare a re-imported response with the intact stored content, not its recorded hash field."""
    return digest(verified_generation(existing, slot)["raw_response"]) == digest(raw)


def portfolio_config(config: dict) -> dict:
    """Give the baseline's single portfolio check the frozen total portfolio budget.

    The portfolio is one `first | ...` proof, so its total verification budget is the
    validation wall budget. Metric extraction is outside it and keeps
    extraction.wall_seconds, as for references and model proofs.
    """
    budget = config.get("tactic_portfolio_total_wall_seconds")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget < math.inf:
        raise ValueError("baseline requires a positive tactic_portfolio_total_wall_seconds")
    return {**config, "validation": {**config["validation"], "wall_seconds": budget}}


def require_reference_directory(path: Path) -> None:
    if not path.is_dir():
        raise ValueError(f"--references must be an existing verify-references output directory: {path}")
    if not (path / "policy.json").is_file():
        raise ValueError(f"--references has no policy.json, so it is not a verify-references output directory: {path}")


def pilot_report(path: Path) -> dict:
    """The analyze output a main freeze cites: analysis.json and the SHA-256 of its exact bytes."""
    if not (path / "analysis.json").is_file():
        raise ValueError(f"--pilot-report must be an analyze output directory with an analysis.json: {path}")
    data = (path / "analysis.json").read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "analysis": json.loads(data.decode("utf-8"))}


def require_completed_pilot(config: dict, report: dict | None, model_snapshot: str) -> None:
    """A main run's preregistration cites the analysis of a complete production pilot of its model and protocol."""
    expected = config.get("preregistration", {}).get("pilot_report_hash")
    if not expected:
        raise ValueError("Main runs require a preregistration containing the completed pilot report hash")
    if report is None or report.get("sha256") != expected:
        raise ValueError("Main runs require the pilot report (--pilot-report) whose analysis.json SHA-256 is "
                         "preregistration.pilot_report_hash")
    analysis = report["analysis"] if isinstance(report.get("analysis"), dict) else {}
    run = analysis["run_provenance"] if isinstance(analysis.get("run_provenance"), dict) else {}
    models = analysis["models"] if isinstance(analysis.get("models"), dict) else {}
    problems = [field for field, ok in (
        ("development", run.get("development") is False),
        ("split", run.get("split") == "pilot"),
        ("experiment", run.get("experiment") == config.get("experiment")),
        ("protocol_version", run.get("protocol_version") == config.get("protocol_version")),
        # The plan's full pilot is per model (docs/evaluation-plan.md), so another model's pilot never admits this one.
        ("model_snapshot", run.get("model_snapshot") == model_snapshot and model_snapshot in models),
        ("run_complete", bool(models) and all(isinstance(model, dict) and model.get("run_complete") is True
                                              for model in models.values()))) if not ok]
    if problems:
        raise ValueError("The pilot report is not the analysis of a complete production pilot of this model and protocol "
                         f"({', '.join(problems)})")
