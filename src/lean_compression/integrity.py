"""Fail-closed gates for stored generations, the implemented policy, a main run's pilot report, and CLI evidence paths."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from .io import digest

# The pinned engine expands Mathlib- and EvaluatorCandidate-owned declarations,
# plus exact owner-module names supplied in expand_modules. The legacy policy
# sends no additional modules; the v2 descriptor freezes their explicit list.
# Source tokens, unknown dependencies and cycles retain their original policy.
IMPLEMENTED_POLICY = {("environment", "boundary_policy"): "expand_mathlib_and_evaluator_v1",
                      ("environment", "source_token_policy"): "original_syntax_v1",
                      ("extraction", "unknown_dependency"): "error",
                      ("extraction", "dependency_cycle"): "metrics_unavailable"}
EXPANDED_MODULE_POLICY = "expand_mathlib_and_evaluator_plus_modules_v2"
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")


def boundary_expansion_modules(policy: object) -> list[str] | None:
    """Validate the frozen boundary and return exact additional owner modules.

    None preserves the legacy engine request, including its absence of an
    expand_modules field. A descriptor is canonical only with a sorted,
    distinct, nonempty list; no prefix matching or wildcard expansion is added.
    """
    if policy == IMPLEMENTED_POLICY[("environment", "boundary_policy")]:
        return None
    if isinstance(policy, dict) and set(policy) == {"name", "extra_modules"}:
        modules = policy["extra_modules"]
        if (policy["name"] == EXPANDED_MODULE_POLICY and isinstance(modules, list) and modules
                and all(isinstance(module, str) and _MODULE_NAME.fullmatch(module) for module in modules)
                and modules == sorted(set(modules))):
            return list(modules)
    raise ValueError("Unsupported environment.boundary_policy: expected "
                     "'expand_mathlib_and_evaluator_v1' or a descriptor with name "
                     f"{EXPANDED_MODULE_POLICY!r} and a sorted, distinct, nonempty extra_modules list "
                     "of valid module names, with no other fields")


def require_implemented_policy(config: dict) -> None:
    """Never record a boundary or extraction policy label that was not applied."""
    for (section, key), implemented in IMPLEMENTED_POLICY.items():
        values = config.get(section)
        value = values.get(key) if isinstance(values, dict) else None
        if (section, key) == ("environment", "boundary_policy"):
            boundary_expansion_modules(value)
            continue
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
    """Give the baseline's portfolio checks the frozen total portfolio budget as their validation budget.

    A legacy portfolio is one `first | ...` proof checked under all of it; separate alternatives share
    it (baseline.run_portfolio). Metric extraction is outside it and keeps extraction.wall_seconds,
    as for references and model proofs.
    """
    budget = config.get("tactic_portfolio_total_wall_seconds")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0 < budget < math.inf:
        raise ValueError("baseline requires a positive tactic_portfolio_total_wall_seconds")
    return {**config, "validation": {**config["validation"], "wall_seconds": budget}}


def portfolio_tactics(config: dict) -> list | None:
    """The frozen portfolio's alternatives, each checked as its own proof `by <tactic>` in this order.

    None means a legacy configuration, whose one tactic_portfolio string is checked as a single proof:
    there a tactic unknown under a task's imports is a parse failure of every alternative. A
    configuration that names both keys, even with a null value, cannot say which was meant.
    """
    if "tactic_portfolio_tactics" not in config:
        if not isinstance(config.get("tactic_portfolio"), str) or not config["tactic_portfolio"].strip():
            raise ValueError("The configuration names no tactic portfolio: set tactic_portfolio_tactics, "
                             "a non-empty list of tactics")
        return None
    if "tactic_portfolio" in config:
        raise ValueError("The configuration names both tactic_portfolio_tactics and the legacy tactic_portfolio "
                         "string; keep only tactic_portfolio_tactics")
    tactics = config["tactic_portfolio_tactics"]
    if not (isinstance(tactics, list) and tactics and all(isinstance(tactic, str) and tactic.strip() for tactic in tactics)
            and len(set(tactics)) == len(tactics)):
        raise ValueError("tactic_portfolio_tactics must be a non-empty list of distinct, non-blank tactics")
    return tactics


def frozen_portfolio(config: dict) -> list | None:
    """The one portfolio check of freeze, baseline and analyze --baseline; portfolio_tactics' result.

    config_hash binds a baseline to the run's frozen configuration, so freeze refuses what the other two
    would: a run frozen without one well-formed portfolio and a positive total budget could never get one.
    """
    tactics = portfolio_tactics(config)
    portfolio_config(config)
    return tactics


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
