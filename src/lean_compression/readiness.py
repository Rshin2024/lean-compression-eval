"""Offline, evidence-backed dataset readiness; no model outputs are consulted."""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import html
import json
from pathlib import Path

from .analysis import _distribution
from .dataset import (ANSWER_ACCESS_MIN_CHARS, LITERAL_REFLEXIVITY, NEAR_VERBATIM_MAX_COMPONENT_FRACTION,
                      NEAR_VERBATIM_MAX_TOKEN_EDITS, NEAR_VERBATIM_TOKENS_PER_EDIT, SHORT_EXACT_MATCH_MIN_CHARS,
                      SINGLE_TACTIC, screen_answer_access, validate_manifest)
from .io import digest, task_input_hash, verification_request_hash, write_json
from .metrics import exact_int

AUDIT_FLAGS = ("reference_verified", "explicit_signature_frozen", "answer_access_audited",
               "family_reviewed", "metric_closure_verified")
DEFAULT_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def _body_source(record):
    for field in ("proof", "adapted_response"):
        if isinstance(record.get(field), str):
            return record[field]
    for section in ("extraction", "validation"):
        tokens = (record.get(section) or {}).get("source_tokens") or {}
        source, offset = tokens.get("source"), tokens.get("proof_offset")
        if isinstance(source, str) and type(offset) is int and 0 <= offset <= len(source.encode()):
            try:
                return source.encode()[offset:].decode()
            except UnicodeDecodeError:
                pass
    return None


def _source_control(record):
    """The source-shape label of a reference body, without any of its text.

    The tactic of a single_tactic_wrapper is not recorded, per task or as a per-tactic tally: for
    "by <tactic>" it is the whole private answer, and a tally over the labeled task IDs that the report
    lists would leave each of them a handful of candidates to try.
    """
    source = _body_source(record)
    if source is None:
        return {"classification": None, "source_available": False}
    compact = " ".join(source.split())
    if compact in LITERAL_REFLEXIVITY:
        classification = "literal_reflexivity"
    elif SINGLE_TACTIC.fullmatch(compact):
        classification = "single_tactic_wrapper"
    else:
        classification = None
    return {"classification": classification, "source_available": True,
            "interpretation": "Source shape only; neither brevity nor one tactic establishes mathematical difficulty."}


def _quantile_exact(values, numerator, denominator):
    if not values:
        return None
    values = sorted(values)
    position = Fraction((len(values) - 1) * numerator, denominator)
    low = position.numerator // position.denominator
    high = min(low + 1, len(values) - 1)
    return Fraction(values[low]) + (values[high] - values[low]) * (position - low)


def _serialize_fraction(value):
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _bin(value, cuts):
    if cuts is None:
        return None
    return "low" if value <= cuts[0] else "middle" if value <= cuts[1] else "high"


def _identity(record, task):
    verified_input = record.get("verification_request_hash")
    if verified_input is not None:
        return "verification_request_hash" if verified_input == verification_request_hash(task) else None
    supplied = record.get("task_input_hash")
    if supplied is not None:
        return "task_input_hash" if supplied == task_input_hash(task) else None
    if record.get("task_hash") == digest(task):
        return "exact_full_task_hash_legacy"
    return None


def build_readiness_report(tasks: list[dict], verified_records: list[dict] | dict,
                           config: dict | None = None, *, task_file_sha256: str | None = None,
                           ignored_records: list[dict] | None = None) -> dict:
    """Report concrete dataset evidence and provisional source/depth coverage.

    Runtime verification hashes can link evidence after prompt-only presentation
    changes; model prompt inputs remain separately hashed. Source compilation
    alone never counts as independent kernel replay. A legacy
    record without task_input_hash is accepted only if its complete task_hash
    exactly equals the current task object, and this identity method is visible.
    Rows without a string task_id appear only as structural errors.
    """
    config = dict(config or {})
    settings = {**config, **config.get("readiness", {})}
    quotas = bool(settings.get("enforce_quotas", True))
    structural = validate_manifest(tasks, enforce_quotas=quotas)
    identified = [task for task in tasks if isinstance(task, dict) and isinstance(task.get("task_id"), str)]
    task_map = {task["task_id"]: task for task in identified}
    if len(task_map) != len(identified):
        raise ValueError("readiness requires unambiguous unique task IDs")
    if isinstance(verified_records, dict):
        verified_records = list(verified_records.values())
    by_task = defaultdict(list)
    seen_record_hashes = set()
    ignored = list(ignored_records or [])
    for record in verified_records:
        if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
            ignored.append({"reason": "not_a_task_reference_record"})
            continue
        if record["task_id"] not in task_map:
            ignored.append({"task_id": record["task_id"], "reason": "task_not_in_population"})
            continue
        fingerprint = digest(record)
        if fingerprint in seen_record_hashes:
            continue
        seen_record_hashes.add(fingerprint)
        by_task[record["task_id"]].append(record)
    permitted = set(config.get("validation", {}).get("permitted_axioms", DEFAULT_AXIOMS))
    expected = {"environment_hash": settings.get("environment_hash"),
                "engine_hash": settings.get("engine_hash"),
                "boundary_policy_hash": settings.get("boundary_policy_hash"),
                "source_token_policy_hash": settings.get("source_token_policy_hash")}
    if expected["boundary_policy_hash"] is None and config.get("environment", {}).get("boundary_policy"):
        expected["boundary_policy_hash"] = digest(config["environment"]["boundary_policy"])
    per_task, observed_provenance = {}, defaultdict(set)
    controls = defaultdict(list)
    exact_match_chars, generic_shapes, near_verbatim = {}, {}, {}
    for task_id, task in task_map.items():
        candidates = by_task[task_id]
        matching = [(record, _identity(record, task)) for record in candidates if _identity(record, task)]
        reasons = []
        if not candidates:
            reasons.append("missing_reference_record")
        elif not matching:
            reasons.append("missing_or_mismatched_task_input_identity")
        elif len(matching) > 1:
            reasons.append("multiple_matching_reference_records")
        record, method = matching[0] if len(matching) == 1 else ({}, None)
        kernel_verified, metric_complete = False, False
        wb = depth = None
        evidence_provenance = {}
        if record:
            validation = record.get("validation") or {}
            kernel_verified = (record.get("validity_status") == "success"
                               and validation.get("validity_status") == "success"
                               and validation.get("kernel_replay") is True)
            if not kernel_verified:
                reasons.append("independent_kernel_replay_not_verified")
            if not isinstance(validation.get("axioms"), list):
                reasons.append("missing_axiom_audit")
            elif set(validation["axioms"]) - permitted:
                reasons.append("disallowed_reference_axioms")
            if not validation.get("trusted_type_expression"):
                reasons.append("missing_trusted_target_evidence")
            for field, wanted in expected.items():
                actual = record.get(field)
                evidence_provenance[field] = actual
                if actual is None:
                    reasons.append("missing_" + field)
                else:
                    observed_provenance[field].add(str(actual))
                    if wanted is not None and actual != wanted:
                        reasons.append("mismatched_" + field)
            metrics = record.get("metrics") or {}
            extraction = record.get("extraction") or {}
            if record.get("metric_status") != "available" or metrics.get("metric_status") != "available":
                reasons.append("reference_metrics_unavailable")
            else:
                try:
                    wb, depth = exact_int(metrics["W_B"]), exact_int(metrics["D"])
                    if not exact_int(metrics["W_S"]):
                        raise ValueError("zero W_S")
                    us, ub = exact_int(metrics["U_S_decimal"]), exact_int(metrics["U_B_decimal"])
                    if us + ub == 0:
                        raise ValueError("zero expansion")
                    metric_complete = True
                except (KeyError, ValueError, TypeError):
                    wb = depth = None
                    reasons.append("malformed_reference_metrics")
            if extraction.get("metric_status") != "available" or "nodes" not in extraction:
                reasons.append("missing_dependency_evidence")
            tokens = extraction.get("source_tokens") or {}
            if not tokens.get("source") or not isinstance(tokens.get("body_spans"), list):
                reasons.append("missing_source_span_evidence")
            elif wb is not None and tokens.get("W_B") != wb:
                reasons.append("source_count_evidence_mismatch")
            control = _source_control(record)
            if control["classification"]:
                controls[control["classification"]].append(task_id)
            screen = screen_answer_access(task, _body_source(record))
            exposed = screen["exact_match"]
            if exposed:
                exact_match_chars[task_id] = screen["exact_match_chars"]
                if screen["generic_shape"]:
                    generic_shapes[task_id] = screen["generic_shape"]
            if screen["near_verbatim"]:
                near_verbatim[task_id] = screen["near_verbatim"][0]
        else:
            control = {"classification": None, "source_available": False}
            exposed = None
        audit = task.get("audit")
        audits = {flag: isinstance(audit, dict) and audit.get(flag) is True for flag in AUDIT_FLAGS}
        pending = [flag for flag, passed in audits.items() if not passed]
        evidence_complete = bool(record) and not reasons
        per_task[task_id] = {"task_id": task_id, "split": task.get("split"), "domain": task.get("domain"),
            "origin": task.get("origin"), "family_id": task.get("family_id"),
            "task_input_hash": task_input_hash(task), "reference_record_count": len(candidates),
            "verification_request_hash": verification_request_hash(task),
            "matching_reference_record_count": len(matching), "identity_method": method,
            "identity_scope": "Lean runtime inputs; prompt premise presentation is separately frozen"
                if method == "verification_request_hash" else "Full mathematical/model input or complete task snapshot",
            "stale_or_unmatched_reference_count": len(candidates) - len(matching),
            "independent_kernel_verified": kernel_verified, "metrics_complete": metric_complete,
            "reference_evidence_complete": evidence_complete, "missing_evidence": reasons,
            "audit_flags": audits, "pending_audits": pending, "source_control": control,
            "reference_proof_in_prompt": exposed,
            "reference_W_B": wb, "reference_D": depth, "provenance": evidence_provenance,
            "eligible_for_strata": kernel_verified and metric_complete and evidence_complete}

    # Separate homogeneous cohorts preserve useful archived measurements
    # without silently pooling different engine/token-policy scales.
    cohorts = defaultdict(list)
    for task_id, row in per_task.items():
        row["reference_evidence_complete_for_recorded_pin"] = row["reference_evidence_complete"]
        if row["eligible_for_strata"]:
            cohorts[digest(row["provenance"])].append(task_id)
    reference_cohorts = []
    for cohort_id, ids in sorted(cohorts.items()):
        reference_cohorts.append({"cohort_id": cohort_id, "provenance": per_task[ids[0]]["provenance"],
            "task_ids": ids, "tasks": len(ids), "reference_W_B": _distribution([per_task[t]["reference_W_B"] for t in ids]),
            "reference_D": _distribution([per_task[t]["reference_D"] for t in ids])})
    # A caller's expected pins may deliberately select one cohort. Incompatible
    # records remain missing evidence, but do not contaminate that cohort's bins.
    observed_provenance = defaultdict(set)
    for row in per_task.values():
        if row["reference_evidence_complete"]:
            for field, actual in row["provenance"].items():
                if actual is not None:
                    observed_provenance[field].add(str(actual))
    mixed = {field: sorted(values) for field, values in observed_provenance.items() if len(values) > 1}
    for field in mixed:
        if expected[field] is None:
            for row in per_task.values():
                if row["provenance"].get(field) is not None:
                    row["missing_evidence"].append("mixed_unselected_" + field)
                    row["reference_evidence_complete"] = False
                    row["eligible_for_strata"] = False
    fit_split = settings.get("strata_fit_split", "pilot")
    fit_ids = [t for t, row in per_task.items() if row["eligible_for_strata"]
               and (fit_split == "all" or row["split"] == fit_split)]
    cuts = {}
    fixed = True
    for field, name in (("reference_W_B", "source_length_cutpoints"), ("reference_D", "dependency_depth_cutpoints")):
        supplied = settings.get(name)
        if supplied is not None:
            if not isinstance(supplied, (list, tuple)) or len(supplied) != 2:
                raise ValueError(name + " requires exactly two ordered nonnegative cutpoints")
            try:
                pair = [Fraction(str(value)) for value in supplied]
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError("invalid " + name) from exc
            if not 0 <= pair[0] <= pair[1]:
                raise ValueError(name + " must be nonnegative and ordered")
            cuts[field] = pair
        else:
            fixed = False
            values = [per_task[t][field] for t in fit_ids]
            cuts[field] = [_quantile_exact(values, 1, 4), _quantile_exact(values, 3, 4)] if values else None
    for row in per_task.values():
        row["reference_length_bin"] = _bin(row["reference_W_B"], cuts["reference_W_B"]) if row["eligible_for_strata"] else None
        row["reference_depth_bin"] = _bin(row["reference_D"], cuts["reference_D"]) if row["eligible_for_strata"] else None

    def summarize(ids):
        selected = [per_task[t] for t in ids]
        measured = [row for row in selected if row["eligible_for_strata"]]
        matrix = Counter((row["reference_length_bin"], row["reference_depth_bin"]) for row in measured
                         if row["reference_length_bin"] and row["reference_depth_bin"])
        return {"tasks": len(ids), "families": len({str(row["family_id"]) for row in selected}),
                "kernel_verified_references": sum(row["independent_kernel_verified"] for row in selected),
                "complete_reference_evidence": sum(row["reference_evidence_complete"] for row in selected),
                "audit_complete_tasks": sum(not row["pending_audits"] for row in selected),
                "measured_stratum_population": len(measured),
                "reference_W_B": _distribution([row["reference_W_B"] for row in measured]),
                "reference_D": _distribution([row["reference_D"] for row in measured]),
                "length_depth_cross_strata": {length: {depth: matrix[length, depth]
                    for depth in ("low", "middle", "high")} for length in ("low", "middle", "high")},
                "missing_evidence_counts": dict(Counter(reason for row in selected for reason in row["missing_evidence"])),
                "source_control_counts": dict(Counter(row["source_control"]["classification"] for row in selected
                                                       if row["source_control"]["classification"]))}

    groups = {"all": summarize(list(task_map))}
    for dimensions in (("split",), ("domain",), ("origin",), ("split", "domain", "origin")):
        keys = sorted({tuple(str(task.get(dimension, "unknown")) for dimension in dimensions) for task in identified})
        for values in keys:
            name = "/".join(f"{dimension}:{value}" for dimension, value in zip(dimensions, values))
            groups[name] = summarize([t["task_id"] for t in identified
                if tuple(str(t.get(dimension, "unknown")) for dimension in dimensions) == values])
    family_domains, family_splits = defaultdict(set), defaultdict(set)
    for task in identified:
        family_domains[str(task.get("family_id"))].add(str(task.get("domain")))
        family_splits[str(task.get("family_id"))].add(str(task.get("split")))
    cutpoint_source = "caller_supplied" if fixed else "observed_reference_25th_and_75th_percentiles"
    # Exact matches gate from ANSWER_ACCESS_MIN_CHARS, and from SHORT_EXACT_MATCH_MIN_CHARS unless the
    # proof is a generic shape (listed, not gated); near-verbatim proof components gate (dataset.py).
    flagged = [t for t, chars in exact_match_chars.items() if chars >= ANSWER_ACCESS_MIN_CHARS]
    short = [t for t, chars in exact_match_chars.items() if SHORT_EXACT_MATCH_MIN_CHARS <= chars < ANSWER_ACCESS_MIN_CHARS]
    unchecked = [t for t, row in per_task.items() if row["reference_proof_in_prompt"] is None]
    gates = {"structural_manifest_valid": not structural,
             "quotas_enforced": quotas,
             "all_reference_evidence_complete": bool(per_task) and all(row["reference_evidence_complete"] for row in per_task.values()),
             "all_required_audits_complete": bool(per_task) and all(not row["pending_audits"] for row in per_task.values()),
             "reference_proofs_absent_from_prompts": bool(per_task) and not flagged and not unchecked and not near_verbatim
                 and all(t in generic_shapes for t in short),
             "reference_provenance_consistent": not mixed,
             "length_depth_cutpoints_frozen": fixed and settings.get("strata_frozen") is True,
             "difficulty_coverage_reviewed": settings.get("difficulty_review_complete") is True}
    report = {"readiness_version": "reference_evidence_readiness_v2", "task_manifest_hash": digest(tasks),
              "task_manifest_hash_method": "sha256_of_canonical_json_of_parsed_task_list",
              "task_file_sha256": task_file_sha256,
              "status": "ready_for_dataset_freeze" if all(gates.values()) else "not_ready_for_dataset_freeze",
              "scope": "Dataset evidence only; model pin, budget, infrastructure and preregistration gates are separate.",
              "gates": gates, "structural_errors": structural, "groups": groups, "tasks": per_task,
              "audit_flag_counts": {flag: {"complete": sum(row["audit_flags"][flag] for row in per_task.values()),
                  "pending": sum(not row["audit_flags"][flag] for row in per_task.values())} for flag in AUDIT_FLAGS},
              "provenance_values": {key: sorted(values) for key, values in observed_provenance.items()},
              "reference_cohorts": reference_cohorts,
              "mixed_provenance": mixed, "expected_provenance": expected,
              "ignored_reference_records": ignored,
              "readiness_settings": {"enforce_quotas": quotas, "strata_fit_split": fit_split, "cutpoint_source": cutpoint_source,
                  "supplied_pins": {field: settings.get(field) for field in expected},
                  "readiness_config_hash": digest(config.get("readiness", {})), "config_hash": digest(config)},
              "answer_access_screen": {
                  "rule": f"whitespace-normalized reference proof of at least {ANSWER_ACCESS_MIN_CHARS} characters occurs in the model-visible prompt",
                  "flagged_task_ids": flagged,
                  "short_exact_match_rule": f"whitespace-normalized reference proof of {SHORT_EXACT_MATCH_MIN_CHARS}-"
                                            f"{ANSWER_ACCESS_MIN_CHARS - 1} characters occurs in the model-visible prompt and "
                                            "is not a generic shape (a literal reflexivity proof, one bare tactic, or a name "
                                            "bound by the target's binders)",
                  "short_exact_match_task_ids": [t for t in short if t not in generic_shapes],
                  "generic_short_exact_match_task_ids": [t for t in short if t in generic_shapes],
                  "near_verbatim_rule": "a premise-context proof component (a declaration value, or an anonymous-"
                                        "constructor component, field value, fun body, tactic, tactic term or rewrite "
                                        "rule inside one) is at most max("
                                        f"{NEAR_VERBATIM_MAX_TOKEN_EDITS}, n/{NEAR_VERBATIM_TOKENS_PER_EDIT} rounded up) "
                                        "token edits (insertions, deletions, substitutions or swaps of two adjacent "
                                        "tokens, delimited by whitespace, brackets, commas and semicolons) from a "
                                        f"reference proof of n tokens and at least {SHORT_EXACT_MATCH_MIN_CHARS} "
                                        "characters that is not a generic shape, changing at most "
                                        f"{NEAR_VERBATIM_MAX_COMPONENT_FRACTION} of its name components, rounded",
                  "near_verbatim_task_ids": list(near_verbatim),
                  "near_verbatim_matches": near_verbatim,
                  "unchecked_task_ids": unchecked,
                  "interpretation": "Flagged, short exact and near-verbatim tasks show a copyable answer and block the gate "
                                    "until the answer-access audit resolves them; generic short exact matches are listed "
                                    "without blocking it; an unflagged task is not thereby cleared."},
              "source_only_controls": dict(controls),
              "family_structure": {"families": len(family_domains),
                  "cross_domain_families": {family: sorted(domains) for family, domains in family_domains.items() if len(domains)>1},
                  "cross_split_families": {family: sorted(splits) for family, splits in family_splits.items() if len(splits)>1}},
              "proposed_reference_strata": {
                  "status": "frozen_by_config" if gates["length_depth_cutpoints_frozen"] else "provisional_do_not_treat_as_difficulty",
                  "method": cutpoint_source,
                  "fit_split": fit_split, "fit_task_ids": fit_ids,
                  "fit_population_complete": bool(fit_ids) and len(fit_ids) == sum(
                      fit_split == "all" or task.get("split") == fit_split for task in identified),
                  "source_length_cutpoints": None if cuts["reference_W_B"] is None else list(map(_serialize_fraction, cuts["reference_W_B"])),
                  "dependency_depth_cutpoints": None if cuts["reference_D"] is None else list(map(_serialize_fraction, cuts["reference_D"])),
                  "bin_rule": "low <= first cut; first < middle <= second cut; high > second cut; preserve ties",
                  "warning": "These bins measure reference source length and dependency depth, not mathematical difficulty. Incomplete coverage or tied cutpoints can leave empty cells."},
              "interpretation": ["No evaluated model outcomes are inputs to this report.",
                  "Source compilation flags do not replace independent kernel and target/axiom evidence.",
                  "Hash matching establishes identity of recorded inputs, not absence of equivalent theorems or pretraining exposure.",
                  "A verification_request_hash links Lean inputs only; model-visible premise context has a separate frozen task_input_hash.",
                  "Readiness does not prove mathematical novelty, independent families, or adequate statistical power."]}
    report["report_hash"] = digest(report)
    return report


def write_readiness_report(report: dict, destination: str | Path) -> dict:
    """Write immutable JSON and a concise HTML diagnostic with inspectable IDs."""
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "readiness.json", report)
    page = ["<!doctype html><html><meta charset='utf-8'><title>Dataset readiness</title>",
            "<style>body{max-width:1100px;margin:3rem auto;padding:0 1rem;font:16px system-ui;color:#17212b}"
            "table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{padding:.65rem;border-bottom:1px solid #ccd5df;text-align:left}"
            "th{background:#edf2f5}.notice{background:#fff3d3;padding:1rem}code{overflow-wrap:anywhere}summary{cursor:pointer}</style>",
            "<h1>Dataset readiness</h1><p class='notice'><strong>" + html.escape(report["status"].replace("_", " ")) +
            "</strong>. Reference evidence and curation diagnostic; no model outcomes are included.</p>",
            "<p>Source: supplied public task manifest and independent reference records. Canonical task-list digest "
            "(SHA-256 of canonical JSON of the parsed tasks, not of the file bytes): <code>" + report["task_manifest_hash"] +
            "</code>. Task file SHA-256: <code>" + html.escape(str(report.get("task_file_sha256") or "not recorded")) + "</code>.</p>"]
    settings = report.get("readiness_settings")
    if settings:
        page.append("<p>Effective settings: quotas " + ("enforced" if settings["enforce_quotas"] else "<strong>not enforced</strong>") +
                    "; strata fit split " + html.escape(str(settings["strata_fit_split"])) + "; cutpoint source " +
                    html.escape(str(settings["cutpoint_source"])) + "; supplied pins " +
                    html.escape(json.dumps(settings["supplied_pins"], sort_keys=True)) + "; readiness configuration digest <code>" +
                    html.escape(str(settings["readiness_config_hash"])) + "</code>; full configuration digest <code>" +
                    html.escape(str(settings["config_hash"])) + "</code>.</p>")
    page.append("<h2>Dataset gates</h2><table><tr><th>Gate</th><th>Status</th></tr>")
    for gate, passed in report["gates"].items():
        page.append("<tr><td>" + html.escape(gate.replace("_", " ")) + "</td><td>" + ("Complete" if passed else "Pending") + "</td></tr>")
    page.append("</table><h2>Population and measured reference coverage</h2><table><tr><th>Population</th><th>Tasks</th>"
                "<th>Families</th><th>Kernel verified</th><th>Complete evidence</th><th>Median W_B</th><th>Median D</th></tr>")
    for name, summary in report["groups"].items():
        page.append("<tr><td>" + html.escape(name) + "</td>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in (
            summary["tasks"], summary["families"], summary["kernel_verified_references"], summary["complete_reference_evidence"],
            summary["reference_W_B"]["median"], summary["reference_D"]["median"])) + "</tr>")
    strata = report["proposed_reference_strata"]
    page.extend(["</table><h2>Reference length and depth coverage</h2><p class='notice'>" + html.escape(strata["warning"]) + "</p>",
                 "<p>Fit population: " + html.escape(str(strata["fit_split"])) + "; " + str(len(strata["fit_task_ids"])) +
                 " measured tasks. Source-length cutpoints: " + html.escape(str(strata["source_length_cutpoints"])) +
                 ". Depth cutpoints: " + html.escape(str(strata["dependency_depth_cutpoints"])) + ".</p>",
                 "<table><tr><th>Reference W_B bin</th><th>Low D</th><th>Middle D</th><th>High D</th></tr>"])
    for length, cells in report["groups"]["all"]["length_depth_cross_strata"].items():
        page.append("<tr><td>" + length + "</td>" + "".join(f"<td>{cells[depth]}</td>" for depth in ("low", "middle", "high")) + "</tr>")
    page.append("</table><h2>Source-shape controls</h2><p>These source labels do not establish mathematical difficulty. "
                "Tactic names are not shown: a one-tactic reference's tactic is its whole private answer.</p><ul>")
    for classification, ids in report["source_only_controls"].items():
        page.append("<li>" + html.escape(classification) + ": " + str(len(ids)) + " tasks</li>")
    page.append("</ul><h2>Homogeneous reference cohorts</h2><p>Archived measurements under different pins are shown separately; "
                "they are not pooled into the proposed strata.</p><table><tr><th>Cohort</th><th>Tasks</th><th>Engine</th>"
                "<th>Token policy</th><th>Median W_B</th><th>Median D</th></tr>")
    for cohort in report["reference_cohorts"]:
        values = (cohort["cohort_id"][:12], cohort["tasks"], str(cohort["provenance"]["engine_hash"])[:12],
                  str(cohort["provenance"]["source_token_policy_hash"])[:12],
                  cohort["reference_W_B"]["median"], cohort["reference_D"]["median"])
        page.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in values) + "</tr>")
    page.append("</table>")
    screen = report.get("answer_access_screen")
    if screen:
        near = screen.get("near_verbatim_matches") or {}
        page.append("<h2>Answer-access screen</h2><p>Flagged when a " + html.escape(screen["rule"]) + ": " +
                    (html.escape(", ".join(screen["flagged_task_ids"])) or "none") + ". Short exact matches gated when a " +
                    html.escape(screen.get("short_exact_match_rule", "")) + ": " +
                    (html.escape(", ".join(screen["short_exact_match_task_ids"])) or "none") +
                    ". Generic short exact matches, not gated: " +
                    (html.escape(", ".join(screen.get("generic_short_exact_match_task_ids", []))) or "none") +
                    ". Near-verbatim copies, gated when " + html.escape(screen.get("near_verbatim_rule", "")) + ": " +
                    (html.escape("; ".join(f"{t} (line {m['premise_context_line']}, {m['token_edits']} token "
                                           f"edit{'' if m['token_edits'] == 1 else 's'}: {m['snippet']})"
                                           for t, m in near.items())) or "none") +
                    ". Unchecked: " + str(len(screen["unchecked_task_ids"])) + " tasks. " +
                    html.escape(screen["interpretation"]) + "</p>")
    page.append("<h2>Task-level pending evidence</h2>")
    for task_id, row in report["tasks"].items():
        if row["missing_evidence"] or row["pending_audits"]:
            page.append("<details><summary>" + html.escape(task_id) + "</summary><p>Evidence: " +
                html.escape(", ".join(row["missing_evidence"]) or "complete") + "</p><p>Audits: " +
                html.escape(", ".join(row["pending_audits"]) or "complete") + "</p></details>")
    page.append("<p><a href='readiness.json'>Complete distributions, cross-strata, identities and missing-evidence records</a></p></html>")
    content = "\n".join(page)
    target = directory / "readiness.html"
    if target.exists() and target.read_text(encoding="utf-8") != content:
        raise FileExistsError("Refusing to overwrite a different readiness report: " + str(target))
    target.write_text(content, encoding="utf-8")
    return {"json": str(directory / "readiness.json"), "html": str(target)}
