"""Task-weighted, family-clustered analysis of every scheduled attempt.

No provider calls or Lean execution are needed to rerun this module. Conditional
compression and unconditional correctness/attainment are deliberately separate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import csv
import html
import json
import math
from pathlib import Path
import random
from statistics import mean
from typing import Any

from .metrics import decimal, exact_int, log2_int
from .outcomes import outcome_class

CONDITIONS = ("standard", "mathematical_compression", "source_concision")
CONTRASTS = {"compression_vs_standard": ("mathematical_compression", "standard"),
             "compression_vs_concision": ("mathematical_compression", "source_concision")}


def _quantile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low, high = math.floor(index), math.ceil(index)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _exp2(value):
    if value is None or value > 1023 or value < -1074:
        return None
    return 2.0 ** value


def _exact_quantile(values, probability: Fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = position.numerator // position.denominator
    high = min(low + 1, len(ordered) - 1)
    answer = Fraction(ordered[low]) + (ordered[high] - ordered[low]) * (position - low)
    return decimal(answer.numerator) if answer.denominator == 1 else (
        decimal(answer.numerator) + "/" + decimal(answer.denominator))


def _distribution(values):
    """Raw quantiles are exact decimal/rational strings, also for huge counts."""
    if not values:
        return {"count": 0, "zero_count": 0, "median": None, "q1": None, "q3": None}
    positive_logs = [log2_int(value) if isinstance(value, int)
                     else log2_int(value.numerator) - log2_int(value.denominator)
                     for value in values if value > 0]
    return {"count": len(values), "zero_count": sum(value == 0 for value in values),
            "median": _exact_quantile(values, Fraction(1, 2)),
            "q1": _exact_quantile(values, Fraction(1, 4)),
            "q3": _exact_quantile(values, Fraction(3, 4)),
            "positive_log2": {"count": len(positive_logs),
                              "median": _quantile(positive_logs, .5),
                              "q1": _quantile(positive_logs, .25),
                              "q3": _quantile(positive_logs, .75)}}


def _unresolved(attempt):
    """Unfinished, checker, transport and unrecognized outcomes are never model failures."""
    return bool(attempt.get("scheduled_outcome_incomplete")) or outcome_class(attempt.get("validity_status")) == "incomplete"


def _measurement(attempt):
    metrics = attempt.get("metrics") or {}
    if attempt.get("validity_status") != "success":
        return None, "invalid"
    if attempt.get("metric_status", metrics.get("metric_status")) != "available":
        reason = (attempt.get("metric_reason") or attempt.get("metric_unavailable_reason")
                  or metrics.get("reason") or "metrics_unavailable")
        return None, str(reason)
    try:
        ws, wb = exact_int(metrics["W_S"]), exact_int(metrics["W_B"])
        us = exact_int(metrics.get("U_S_decimal", metrics.get("U_S")))
        ub = exact_int(metrics.get("U_B_decimal", metrics.get("U_B")))
        if ws == 0 or us + ub == 0:
            raise ValueError("undefined compression ratio")
        lu, lw = log2_int(us + ub), log2_int(ws + wb)
        depth = metrics.get("D")
        if depth is not None:
            depth = exact_int(depth, "D")
        return {"W_S": ws, "W_B": wb, "U_S": us, "U_B": ub,
                "W": ws + wb, "U": us + ub, "I0": Fraction(wb, ws), "D": depth,
                "log2_U": lu, "log2_W": lw, "log2_T0": lu - lw,
                "log2_W_B": log2_int(wb), "log2_U_B": log2_int(ub)}, None
    except (ValueError, TypeError, KeyError) as exc:
        return None, "malformed_metrics: " + str(exc)


def _reference_ratio(reference):
    if not isinstance(reference, dict):
        return None
    metrics = reference.get("metrics", reference)
    if not isinstance(metrics, dict):
        return None
    if reference.get("validity_status", "success") != "success" or (
        reference.get("metric_status", metrics.get("metric_status", "available")) != "available"
    ):
        return None
    try:
        if "T0" in metrics and metrics["T0"]:
            numerator = exact_int(metrics["T0"]["numerator_decimal"])
            denominator = exact_int(metrics["T0"]["denominator_decimal"])
        else:
            numerator = (exact_int(metrics.get("U_S_decimal", metrics.get("U_S")))
                         + exact_int(metrics.get("U_B_decimal", metrics.get("U_B"))))
            denominator = exact_int(metrics["W_S"]) + exact_int(metrics["W_B"])
        if not numerator or not denominator:
            return None
        return numerator, denominator
    except (ValueError, TypeError, KeyError):
        return None


def _reference_log(reference):
    value = _reference_ratio(reference)
    return None if value is None else log2_int(value[0]) - log2_int(value[1])


def scorer_identity(record) -> str:
    """The metric code that scored a record; records scored before it was stamped share one legacy identity."""
    metrics = record.get("metrics", record) if isinstance(record, dict) else None
    return str(metrics.get("scorer_hash") or "legacy") if isinstance(metrics, dict) else "legacy"


def _composition(ids, task_map):
    return {"task_count": len(ids),
            "family_count": len({task_map[task_id]["family_id"] for task_id in ids}),
            "domains": dict(sorted(Counter(task_map[task_id]["domain"] for task_id in ids).items())),
            "origins": dict(sorted(Counter(task_map[task_id].get("origin", "unknown")
                                            for task_id in ids).items())),
            "splits": dict(sorted(Counter(task_map[task_id].get("split", "unknown")
                                           for task_id in ids).items()))}


def _average(values):
    return mean(values) if values else None


def _resource_number(value, *, integral=False):
    """Missing observations are not zero; requested limits are not measurements."""
    if value is None:
        return None, False
    if integral:
        try:
            return exact_int(value, "resource token count"), False
        except ValueError:
            return None, True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, True
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None, True
    return (number, False) if math.isfinite(number) and number >= 0 else (None, True)


def _first_recorded(mapping, keys):
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


# Peak memory by scope, never pooled. The Docker runner records the container's cgroup high-water mark, with this
# peak_memory_scope (older Docker records omit it). No runner here records a process's peak RSS, and native runs record
# no peak; peak_rss/max_rss fields are read only from records that supply them.
CONTAINER_PEAK_SCOPE = "cgroup high-water mark of all memory charged to the container, page cache included"
PEAK_FIELDS = {"container": (("peak_memory_mib",), ("peak_memory_bytes",)),
               "process_rss": (("peak_rss_mib", "max_rss_mib"), ("peak_rss_bytes", "max_rss_bytes"))}


def _peak_memory_mib(resources, scope="container"):
    """A recorded peak of this scope in MiB; a container peak whose record states another scope is invalid, not pooled."""
    mib, octets = PEAK_FIELDS[scope]
    value, divisor = _first_recorded(resources, mib), 1
    if value is None:
        value, divisor = _first_recorded(resources, octets), 1024 * 1024
    if value is None:
        # In particular, memory_mib_requested is not a measured peak.
        return None, False
    if scope == "container" and resources.get("peak_memory_scope") not in (None, CONTAINER_PEAK_SCOPE):
        return None, True
    parsed, invalid = _resource_number(value)
    return (parsed / divisor if parsed is not None else None), invalid


def _usage_mapping(value):
    """Absent usage is missing; a recorded usage value that is not an object is invalid."""
    if value is None:
        return {}, False
    return (value, False) if isinstance(value, dict) else ({}, True)


HEARTBEATS = ("Lean heartbeats (maxHeartbeats units) of candidate elaboration, including the elaborator's own kernel "
              "checks, plus kernel replay in the {} run; not bounded by or comparable with validation.max_heartbeats. "
              "Engines that report them do so for successful checks only.")
DESCRIPTIONS = {
    "validation_heartbeats": HEARTBEATS.format("validation"),
    "extraction_heartbeats": HEARTBEATS.format("extraction") + " The extraction run repeats that check before it builds "
                             "the graph, so this is a second reading of the validation work, not the cost of extraction; "
                             "never add it to validation_heartbeats.",
    "generation_elapsed_seconds": "Recorded generation workflow wall time, including retries/backoff for the built-in "
                                  "provider; not model-only compute.",
    **{stage + "_peak_memory_mib": "Container peak: the " + CONTAINER_PEAK_SCOPE + " (Docker). Never pooled with "
       "process RSS peaks; a peak recorded under another peak_memory_scope is invalid." for stage in ("validation", "extraction")},
    **{stage + "_peak_rss_mib": "Process peak: a Lean process's peak resident set size, from peak_rss/max_rss fields "
       "of records that supply them; no runner of this harness records them (native runs record no peak: "
       "peak_memory_reason not_instrumented). Not the memory charged to a container. Never pooled with container peaks."
       for stage in ("validation", "extraction")}}
# Report labels that name each measurement's scope where its key alone would not.
RESOURCE_LABELS = {"generation_elapsed_seconds": "generation workflow wall seconds",
                   "extraction_heartbeats": "extraction heartbeats (a repeat of the validation check; not additive)",
                   **{f"{stage}_peak_memory_mib": f"{stage} peak memory MiB, container (page cache included)"
                      for stage in ("validation", "extraction")},
                   **{f"{stage}_peak_rss_mib": f"{stage} peak RSS MiB, Lean process (not recorded by this harness)"
                      for stage in ("validation", "extraction")}}


def _resource_summary(records):
    names = {"validation_elapsed_seconds": "seconds", "extraction_elapsed_seconds": "seconds",
             "validation_heartbeats": "maxHeartbeats", "extraction_heartbeats": "maxHeartbeats",
             "generation_elapsed_seconds": "seconds", "validation_peak_memory_mib": "MiB",
             "extraction_peak_memory_mib": "MiB", "validation_peak_rss_mib": "MiB", "extraction_peak_rss_mib": "MiB",
             "input_tokens": "tokens", "output_tokens": "tokens", "reasoning_tokens": "tokens"}
    observed = {name: [] for name in names}
    invalid_counts = Counter()
    for record in records:
        validation = (record.get("validation") or {}).get("resources") or {}
        extraction = (record.get("extraction") or {}).get("resources") or {}
        generation = record.get("generation") or {}
        usage, invalid_usage = _usage_mapping(generation.get("usage"))
        generation_resources = generation.get("resources") or {}
        latency = _first_recorded(generation, ("generation_elapsed_seconds", "elapsed_seconds", "latency_seconds"))
        if latency is None:
            latency = generation_resources.get("elapsed_seconds")
        # As before, an absent or empty details object defers to the next; a non-object is invalid.
        output_details, invalid_details = _usage_mapping(next((usage[key] for key in (
            "output_tokens_details", "completion_tokens_details") if usage.get(key) not in (None, {})), None))
        values = {
            "validation_elapsed_seconds": _resource_number(validation.get("elapsed_seconds")),
            "extraction_elapsed_seconds": _resource_number(extraction.get("elapsed_seconds")),
            "validation_heartbeats": _resource_number(validation.get("heartbeats_used")),
            "extraction_heartbeats": _resource_number(extraction.get("heartbeats_used")),
            "generation_elapsed_seconds": _resource_number(latency),
            "validation_peak_memory_mib": _peak_memory_mib(validation),
            "extraction_peak_memory_mib": _peak_memory_mib(extraction),
            "validation_peak_rss_mib": _peak_memory_mib(validation, "process_rss"),
            "extraction_peak_rss_mib": _peak_memory_mib(extraction, "process_rss"),
            "input_tokens": _resource_number(_first_recorded(usage, ("input_tokens", "prompt_tokens")), integral=True),
            "output_tokens": _resource_number(_first_recorded(usage, ("output_tokens", "completion_tokens")), integral=True),
            "reasoning_tokens": _resource_number(usage.get("reasoning_tokens")
                if usage.get("reasoning_tokens") is not None else output_details.get("reasoning_tokens"), integral=True)}
        if invalid_usage:
            values.update(input_tokens=(None, True), output_tokens=(None, True), reasoning_tokens=(None, True))
        elif invalid_details and usage.get("reasoning_tokens") is None:
            values["reasoning_tokens"] = (None, True)
        for name, (value, invalid) in values.items():
            if value is not None:
                observed[name].append(value)
            invalid_counts[name] += invalid
    summary = {}
    for name, values in observed.items():
        summary[name] = {"unit": names[name], "observed_count": len(values),
                         "missing_count": len(records) - len(values), "invalid_count": invalid_counts[name],
                         "sum": sum(values) if values and names[name] != "MiB" else None,
                         "median": _quantile(values, .5), "p95": _quantile(values, .95),
                         "max": max(values) if values else None,
                         "sum_scope": "not_applicable_for_peak_memory" if names[name] == "MiB" else "observed_records_only",
                         "reason": "no_recorded_measurements" if not values else None}
        if name in DESCRIPTIONS:
            summary[name]["description"] = DESCRIPTIONS[name]
    return {"population": "all_scheduled_attempts_including_failures", "scheduled_attempts": len(records),
            "measurements": summary,
            "notes": ["Missing or unrecorded values are not zero; extraction may not have run after a validation failure.",
                      "Elapsed sums and quantiles include only observed records; sums are not whole-run totals when coverage is incomplete.",
                      "Requested memory limits are not peak-memory measurements.",
                      "Container high-water marks (page cache included) and Lean-process RSS peaks are separate "
                      "measurements and are never pooled.",
                      "Extraction heartbeats are the extraction run's second reading of the validation check; they are not "
                      "added to validation heartbeats.",
                      "Generation latency is unavailable unless explicitly stored on the generation record; it is not inferred from timestamps or transport logs.",
                      "Built-in provider generation_elapsed_seconds measures the entire generation workflow, including fixed transport retries/backoff, not model-only computation.",
                      "Reasoning-token counts may already be included in provider output-token counts; these columns must not be added together.",
                      "No API cost is estimated without an explicit pricing schedule."]}


def _interval(point, replicates, confidence, requested, task_count, family_count):
    tail = (1.0 - confidence) / 2
    return {"estimate": point,
            "confidence_interval": [_quantile(replicates, tail), _quantile(replicates, 1 - tail)],
            "confidence_level": confidence, "method": "percentile_family_cluster_bootstrap",
            "one_sided_lower_bound": _quantile(replicates, 1 - confidence),
            "bootstrap_defined": len(replicates),
            "bootstrap_undefined": requested - len(replicates),
            "task_count": task_count, "family_count": family_count,
            "uncertainty_warning": "fewer_than_two_supported_families" if family_count < 2 else None}


def _protocol_provenance(provenance, records, pins):
    """Seed frozen protocol pins; recorded pins and checker identities must agree."""
    for field in ("lean_toolchain", "mathlib_commit"):
        if pins.get(field) is not None:
            if provenance.get(field) not in (None, str(pins[field])):
                raise ValueError("recorded " + field + " differs from the frozen protocol environment")
            provenance[field] = str(pins[field])
    # Failures and unmeasurable verdicts carry no token-policy hash, so the engine is checked directly.
    for field in ("engine_hash", "evaluator_source_hash"):
        values = {str(record[field]) for record in records
                  if isinstance(record, dict) and record.get(field) is not None}
        if len(values) > 1:
            raise ValueError("cannot combine different " + field + " values in one analysis")
        provenance[field] = next(iter(values)) if values else None


EASY_CONTROL_STRATA = ("easy", "provisional_easy")
STRATUM_STATISTICS = ("success_rate_difference", "mean_log2_T0_difference")


def _easy_control_strata(settings):
    labels = settings.get("easy_control_strata", EASY_CONTROL_STRATA)
    labels = [labels] if isinstance(labels, str) else labels
    if not isinstance(labels, (list, tuple)) or not all(isinstance(label, str) for label in labels):
        raise ValueError("easy_control_strata must list frozen stratum labels")
    return sorted(set(labels))


def _stratum_groups(task_ids, task_map, settings):
    """Nonempty task groups by frozen stratum label and by easy-control class."""
    easy = _easy_control_strata(settings)
    stratum = {task_id: str(task_map[task_id].get("stratum") or "unknown") for task_id in task_ids}
    groups = {"stratum_class:easy_control": [t for t in task_ids if stratum[t] in easy],
              "stratum_class:non_control": [t for t in task_ids if stratum[t] not in easy]}
    for value in sorted(set(stratum.values())):
        groups["stratum:" + value] = [t for t in task_ids if stratum[t] == value]
    return {group: ids for group, ids in groups.items() if ids}


def _family_bootstrap(vectors, clusters, families, replicates, seed):
    """Task-weighted means of each vector over whole-family resamples within each cluster, and their least support.

    The draws depend only on the clusters and the seed, so the pooled contrasts and their stratum subgroups share them.
    """
    rng = random.Random(seed)
    boot_values = {key: [] for key in vectors}
    support_minimum = {key: None for key in vectors}
    for _ in range(replicates):
        family_weights = Counter()
        for cluster_ids in clusters.values():
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


def _stratum_breakdown(vectors, task_ids, task_map, families, clusters, bootstrap, settings):
    """Primary paired contrasts within each frozen stratum and the easy-control class.

    Exploratory subgroups restrict the pooled per-task vectors and resample the same
    theorem families as the pooled bootstrap; pooled estimates are not changed.
    """
    groups = {"stratum_class:easy_control": [], "stratum_class:non_control": [],
              **_stratum_groups(task_ids, task_map, settings)}
    subgroup = {(group, name, label): {task_id: values[task_id] for task_id in ids if task_id in values}
                for group, ids in groups.items()
                for (name, label), values in vectors.items() if label in STRATUM_STATISTICS}
    boot_values, support_minimum = _family_bootstrap(subgroup, clusters, families,
                                                     bootstrap["replicates"], bootstrap["seed"])
    breakdown = {"field": "stratum", "easy_control_strata": _easy_control_strata(settings),
                 "status": "exploratory_subgroups_not_separately_powered",
                 "groups": {group: {"composition": _composition(ids, task_map), "task_ids": ids,
                                    "distribution_group": group if ids else None, "contrasts": {}}
                            for group, ids in groups.items()}}
    for (group, name, label), values in subgroup.items():
        result = _interval(_average(list(values.values())), boot_values[group, name, label],
                           bootstrap["confidence_level"], bootstrap["replicates"], len(values),
                           len({task_map[task_id]["family_id"] for task_id in values}))
        result["minimum_bootstrap_task_multiplicity"] = support_minimum[group, name, label]
        if "log2" in label:
            result["geometric_ratio"] = _exp2(result["estimate"])
            result["geometric_ratio_interval"] = [_exp2(value) for value in result["confidence_interval"]]
        breakdown["groups"][group]["contrasts"].setdefault(name, {})[label] = result
    return breakdown


def analyze(tasks: list[dict], attempts: list[dict], references: dict,
            config: dict | None = None) -> dict:
    """Analyze a frozen schedule, counting missing slots in its denominator.

    ``config`` may contain a nested ``analysis`` object. Important settings:
    samples_per_goal_condition (4), sample_index_base (0), bootstrap_replicates
    (10000), bootstrap_seed (20261011), confidence_level (.95), split (optional),
    model_snapshots (optional). Every model is analyzed independently. Duplicate
    schedule identities and unknown task/condition/sample identities are errors.
    easy_control_strata (easy, provisional_easy) names the easy-control values of
    the optional task ``stratum`` label. The frozen ``config["environment"]``
    lean_toolchain and mathlib_commit pins seed the provenance; pooled records
    that disagree with them, or mix engine_hash or evaluator_source_hash values,
    are errors.
    """
    config = dict(config or {})
    settings = {**config, **config.get("analysis", {})}
    sample_count = exact_int(settings.get("samples_per_goal_condition", 4))
    index_base = exact_int(settings.get("sample_index_base", 0))
    replicates = exact_int(settings.get("bootstrap_replicates", 10000))
    seed = exact_int(settings.get("bootstrap_seed", 20261011))
    confidence = float(settings.get("confidence_level", .95))
    if sample_count == 0 or not 0 < confidence < 1:
        raise ValueError("positive sample count and confidence strictly between 0 and 1 required")
    thresholds = [0, 1, 2]
    all_task_map = {}
    for task in tasks:
        if any(not task.get(key) for key in ("task_id", "family_id", "domain")):
            raise ValueError("each task requires task_id, family_id and domain")
        if task["task_id"] in all_task_map:
            raise ValueError("duplicate task_id: " + task["task_id"])
        all_task_map[task["task_id"]] = task
    split = settings.get("split")
    task_map = {key: value for key, value in all_task_map.items()
                if split is None or value.get("split") == split}
    task_ids = sorted(task_map)
    if not task_ids:
        raise ValueError("analysis population contains no tasks")
    models = settings.get("model_snapshots")
    if models is None:
        models = sorted({attempt["model_snapshot"] for attempt in attempts})
    if isinstance(models, str):
        models = [models]
    if len(set(models)) != len(models):
        raise ValueError("duplicate configured model snapshots")
    lookup, seen, ignored = {}, set(), 0
    for attempt in attempts:
        key = (attempt.get("model_snapshot"), attempt.get("task_id"),
               attempt.get("condition"), attempt.get("sample_index"))
        if key in seen:
            raise ValueError("duplicate scheduled attempt identity: " + repr(key))
        seen.add(key)
        model, task_id, condition, sample_index = key
        if task_id not in all_task_map:
            raise ValueError("unknown task in attempt: " + str(task_id))
        if condition not in CONDITIONS:
            raise ValueError("unknown prompt condition: " + str(condition))
        if type(sample_index) is not int or sample_index not in range(index_base, index_base + sample_count):
            raise ValueError("sample_index outside frozen schedule: " + str(sample_index))
        if task_id not in task_map or model not in models:
            ignored += 1
            continue
        lookup[key] = attempt

    families = defaultdict(list)
    family_domains = defaultdict(set)
    for task_id in task_ids:
        family = task_map[task_id]["family_id"]
        families[family].append(task_id)
        family_domains[family].add(task_map[task_id]["domain"])
    nested = all(len(domains) == 1 for domains in family_domains.values())
    strata = defaultdict(list)
    for family in sorted(families):
        strata[next(iter(family_domains[family])) if nested else "all"].append(family)
    missing_refs = [task_id for task_id in task_ids if _reference_log(references.get(task_id)) is None]
    report = {
        "analysis_version": "task_weighted_family_bootstrap_v1",
        "metric_profile": "paper_aligned_v1", "population": _composition(task_ids, task_map),
        "samples_per_goal_condition": sample_count, "sample_index_base": index_base,
        "bootstrap": {"replicates": replicates, "seed": seed, "confidence_level": confidence,
                      "cluster_unit": "theorem_family", "stratified_by_domain": nested,
                      "strata": {name: len(ids) for name, ids in sorted(strata.items())},
                      "cross_domain_families": [family for family, domains in family_domains.items()
                                                if len(domains) > 1],
                      "singleton_strata": [name for name, ids in strata.items() if len(ids) == 1]},
        "ignored_out_of_population_attempts": ignored,
        "missing_reference_task_ids": missing_refs, "models": {},
        "interpretation": ["Compression comparisons condition on validity and metric availability.",
                           "All scheduled slots, including missing records, count in correctness denominators.",
                           "Point estimates weight tasks equally; bootstrap resamples whole theorem families.",
                           "Larger U/W alone does not establish better reasoning."]}
    provenance = {}
    reachable_primitive_manifests = set()
    for field in ("metric_profile", "lean_toolchain", "mathlib_commit", "environment_hash",
                  "primitive_manifest_hash", "source_token_policy_hash", "boundary_policy_hash"):
        values = set()
        for record in list(lookup.values()) + list(references.values()):
            if not isinstance(record, dict):
                continue
            for layer in (record, record.get("environment") or {}, record.get("metrics") or {}):
                value = layer.get(field)
                if value is not None:
                    scope = layer.get("primitive_manifest_scope", record.get("primitive_manifest_scope", ""))
                    if field == "primitive_manifest_hash" and str(scope).startswith("reachable closure"):
                        reachable_primitive_manifests.add(str(value))
                        continue
                    values.add(str(value))
        if len(values) > 1:
            raise ValueError("cannot combine different " + field + " values in one analysis")
        provenance[field] = next(iter(values)) if values else None
    report["provenance"] = provenance
    report["provenance"]["reachable_primitive_manifest_count"] = len(reachable_primitive_manifests)
    report["provenance"]["reachable_primitive_manifest_note"] = (
        "Per-root reachable primitive sets may differ under the same environment and boundary policy; "
        "their hashes are not corpus-wide boundary identities.")
    boundary_config = config.get("environment", {}).get("boundary_policy",
        config.get("metric_policy", config.get("metrics", config.get("extraction", {}))))
    report["provenance"]["configured_boundary_policy"] = boundary_config
    # Attainment compares attempt and reference metrics, so both must come from one scorer.
    scorers = sorted({scorer_identity(a) for a in lookup.values() if _measurement(a)[0] is not None}
                     | {scorer_identity(r) for r in references.values() if _reference_ratio(r) is not None})
    if len(scorers) > 1:
        raise ValueError("cannot combine metrics from different scorers (" + ", ".join(scorers) + ") in one analysis; "
                         "rescore the evaluation version with the current metric code")
    report["provenance"]["scorer_hash"] = scorers[0] if scorers else None
    _protocol_provenance(report["provenance"], list(lookup.values()) + list(references.values()),
                         config.get("environment") or {})

    for model in models:
        grouped, per_task, measured_rows, signature_values = {}, {}, [], defaultdict(set)
        missing_slots = []
        incomplete_slots = []
        for task_id in task_ids:
            per_task[task_id] = {}
            reference_ratio = _reference_ratio(references.get(task_id))
            reference_metrics = references.get(task_id, {})
            reference_metrics = reference_metrics.get("metrics", reference_metrics)
            try:
                signature_values[task_id].add((exact_int(reference_metrics["W_S"]),
                    exact_int(reference_metrics.get("U_S_decimal", reference_metrics.get("U_S")))))
            except (ValueError, TypeError, KeyError):
                pass
            for condition in CONDITIONS:
                records, valid, measures, missing_metric_reasons = [], [], [], Counter()
                for sample_index in range(index_base, index_base + sample_count):
                    key = (model, task_id, condition, sample_index)
                    attempt = lookup.get(key)
                    if attempt is None:
                        missing_slots.append({"task_id": task_id, "condition": condition,
                                              "sample_index": sample_index})
                        attempt = {"validity_status": "missing_record", "sample_index": sample_index}
                    records.append(attempt)
                    if _unresolved(attempt):
                        incomplete_slots.append({"task_id": task_id, "condition": condition,
                                                 "sample_index": sample_index,
                                                 "status": attempt.get("validity_status")})
                    if attempt.get("validity_status") == "success":
                        valid.append(attempt)
                    measurement, reason = _measurement(attempt)
                    if measurement is not None:
                        measures.append(measurement)
                        signature_values[task_id].add((measurement["W_S"], measurement["U_S"]))
                        measured_rows.append({"task_id": task_id, "condition": condition,
                                              "sample_index": sample_index, "measurement": measurement})
                    elif attempt.get("validity_status") == "success":
                        missing_metric_reasons[reason] += 1
                summary = {"success_rate": len(valid) / sample_count,
                           "first_attempt_success": int(records[0].get("validity_status") == "success"),
                           "any_sample_success": int(bool(valid)),
                           "valid_count": len(valid), "measurable_count": len(measures),
                           "metric_missing_valid_count": len(valid) - len(measures),
                           "missing_metric_reasons": dict(missing_metric_reasons),
                           "mean_log2_T0": _average([m["log2_T0"] for m in measures]),
                           "mean_log2_U": _average([m["log2_U"] for m in measures]),
                           "mean_log2_W": _average([m["log2_W"] for m in measures]),
                           "mean_log2_W_B": _average([m["log2_W_B"] for m in measures])
                               if measures and all(m["W_B"] > 0 for m in measures) else None,
                           "mean_D": _average([m["D"] for m in measures if m["D"] is not None]),
                           "attainment": {str(t): None if reference_ratio is None else
                               sum(m["U"] * reference_ratio[1] >=
                                   m["W"] * reference_ratio[0] * (1 << t) for m in measures) / sample_count
                               for t in thresholds}}
                per_task[task_id][condition] = summary
                grouped[task_id, condition] = records

        conditions = {}
        for condition in CONDITIONS:
            summaries = [per_task[task_id][condition] for task_id in task_ids]
            scheduled = len(task_ids) * sample_count
            valid_count = sum(item["valid_count"] for item in summaries)
            measurable_count = sum(item["measurable_count"] for item in summaries)
            failures, unresolved, missing_reasons = Counter(), Counter(), Counter()
            for task_id in task_ids:
                for record in grouped[task_id, condition]:
                    if record.get("validity_status") != "success":
                        failures[record.get("validity_status", "missing_status")] += 1
                        if _unresolved(record):
                            unresolved[record.get("validity_status", "missing_status")] += 1
                missing_reasons.update(per_task[task_id][condition]["missing_metric_reasons"])
            conditions[condition] = {
                "scheduled_attempts": scheduled, "valid_attempts": valid_count,
                "measurable_valid_attempts": measurable_count,
                "measurable_task_count": sum(item["measurable_count"] > 0 for item in summaries),
                "measurable_family_count": len({task_map[t]["family_id"] for t in task_ids
                                                  if per_task[t][condition]["measurable_count"]}),
                "success_rate": mean(item["success_rate"] for item in summaries),
                "first_attempt_success_rate": mean(item["first_attempt_success"] for item in summaries),
                "four_sample_success_rate" if sample_count == 4 else "any_sample_success_rate":
                    mean(item["any_sample_success"] for item in summaries),
                "metric_coverage_among_valid": measurable_count / valid_count if valid_count else None,
                "metric_coverage_among_scheduled": measurable_count / scheduled,
                "missing_metric_reasons": dict(missing_reasons), "failure_statuses": dict(failures),
                "failure_breakdown": {"model_failures": dict(failures - unresolved),
                                      "unresolved_outcomes": dict(unresolved)},
                "attainment": {str(t): {"rate": None if missing_refs else
                    mean(item["attainment"][str(t)] for item in summaries),
                    "denominator_scheduled_attempts": scheduled,
                    "reason": "missing_reference_metrics" if missing_refs else None,
                    "attaining_valid_measurable_attempts": None if missing_refs else
                        round(sum(item["attainment"][str(t)] * sample_count for item in summaries)),
                    "fraction_of_measurable_valid_attaining": None if missing_refs or not measurable_count else
                        sum(item["attainment"][str(t)] * sample_count for item in summaries) / measurable_count}
                    for t in thresholds},
                "resources": _resource_summary([record for task_id in task_ids for record in grouped[task_id, condition]])}

        common = [task_id for task_id in task_ids if all(
            per_task[task_id][condition]["measurable_count"] for condition in CONDITIONS)]
        vectors, contrasts, paired_rows = {}, {}, []
        for name, (b, control) in CONTRASTS.items():
            paired = [task_id for task_id in task_ids if per_task[task_id][b]["measurable_count"]
                      and per_task[task_id][control]["measurable_count"]]
            b_solved = {task_id for task_id in task_ids if per_task[task_id][b]["valid_count"]}
            c_solved = {task_id for task_id in task_ids if per_task[task_id][control]["valid_count"]}
            selection = {"B_only_solved": len(b_solved - c_solved),
                         "control_only_solved": len(c_solved - b_solved),
                         "both_solved": len(b_solved & c_solved),
                         "neither_solved": len(set(task_ids) - b_solved - c_solved),
                         "B_only_measurable": sum(bool(per_task[t][b]["measurable_count"])
                             and not per_task[t][control]["measurable_count"] for t in task_ids),
                         "control_only_measurable": sum(bool(per_task[t][control]["measurable_count"])
                             and not per_task[t][b]["measurable_count"] for t in task_ids),
                         "B_valid_unmeasurable_attempts": sum(per_task[t][b]["metric_missing_valid_count"] for t in task_ids),
                         "control_valid_unmeasurable_attempts": sum(per_task[t][control]["metric_missing_valid_count"] for t in task_ids)}
            contrasts[name] = {"conditions": [b, control], "paired_population": _composition(paired, task_map),
                               "paired_task_ids": paired, "selection": selection,
                               "common_intersection_population": _composition(common, task_map)}
            for label, key in (("success_rate_difference", "success_rate"),
                               ("first_attempt_difference", "first_attempt_success"),
                               ("any_sample_difference", "any_sample_success")):
                vectors[name, label] = {t: per_task[t][b][key] - per_task[t][control][key] for t in task_ids}
            for label, key in (("mean_log2_T0_difference", "mean_log2_T0"),
                               ("mean_log2_U_difference", "mean_log2_U"),
                               ("mean_log2_W_difference", "mean_log2_W")):
                vectors[name, label] = {t: per_task[t][b][key] - per_task[t][control][key] for t in paired}
            vectors[name, "common_mean_log2_T0_difference"] = {
                t: per_task[t][b]["mean_log2_T0"] - per_task[t][control]["mean_log2_T0"] for t in common}
            vectors[name, "mean_depth_difference"] = {t: per_task[t][b]["mean_D"] - per_task[t][control]["mean_D"]
                for t in paired if per_task[t][b]["mean_D"] is not None and per_task[t][control]["mean_D"] is not None}
            vectors[name, "mean_log2_W_B_difference"] = {
                t: per_task[t][b]["mean_log2_W_B"] - per_task[t][control]["mean_log2_W_B"]
                for t in paired if per_task[t][b]["mean_log2_W_B"] is not None
                and per_task[t][control]["mean_log2_W_B"] is not None}
            for threshold in thresholds:
                vectors[name, f"attainment_{threshold}_difference"] = {} if missing_refs else {
                    t: per_task[t][b]["attainment"][str(threshold)] -
                       per_task[t][control]["attainment"][str(threshold)] for t in task_ids}
            for task_id in paired:
                paired_rows.append({"contrast": name, "task_id": task_id,
                                    "family_id": task_map[task_id]["family_id"],
                                    "domain": task_map[task_id]["domain"],
                                    "origin": task_map[task_id].get("origin", "unknown"),
                                    "B_measurable_samples": per_task[task_id][b]["measurable_count"],
                                    "control_measurable_samples": per_task[task_id][control]["measurable_count"],
                                    **{label: vectors[name, label][task_id] for label in
                                       ("mean_log2_T0_difference", "mean_log2_U_difference", "mean_log2_W_difference")}})

        # Resample each domain's families, preserving all task/sample/arm rows.
        # A large family contributes more tasks to the statistic, as in the
        # equal-task point estimand; it does not receive equal-family weight.
        boot_values, support_minimum = _family_bootstrap(vectors, strata, families, replicates, seed)
        for (name, label), values in vectors.items():
            family_count = len({task_map[task_id]["family_id"] for task_id in values})
            result = _interval(_average(list(values.values())), boot_values[name, label], confidence,
                               replicates, len(values), family_count)
            result["minimum_bootstrap_task_multiplicity"] = support_minimum[name, label]
            if "log2" in label:
                result["geometric_ratio"] = _exp2(result["estimate"])
                result["geometric_ratio_interval"] = [_exp2(value) for value in result["confidence_interval"]]
            contrasts[name][label] = result
        for name in contrasts:
            estimates = [contrasts[name][label]["estimate"] for label in
                         ("mean_log2_T0_difference", "mean_log2_U_difference", "mean_log2_W_difference")]
            contrasts[name]["decomposition_residual"] = None if any(x is None for x in estimates) else (
                estimates[0] - (estimates[1] - estimates[2]))

        distribution_groups = {"all": task_ids}
        for category in ("domain", "origin"):
            for value in sorted({task_map[t].get(category, "unknown") for t in task_ids}):
                distribution_groups[f"{category}:{value}"] = [t for t in task_ids
                    if task_map[t].get(category, "unknown") == value]
        distribution_groups.update(_stratum_groups(task_ids, task_map, settings))
        distributions = {}
        for group, ids in distribution_groups.items():
            selected = set(ids)
            distributions[group] = {"population": "measurable_valid_attempts",
                                     "composition": _composition(ids, task_map), "conditions": {},
                                     "correctness_population": "all_scheduled_attempts",
                                     "correctness_by_condition": {condition: {
                                         "success_rate": mean(per_task[t][condition]["success_rate"] for t in ids),
                                         "first_attempt_success_rate": mean(per_task[t][condition]["first_attempt_success"] for t in ids),
                                         "any_sample_success_rate": mean(per_task[t][condition]["any_sample_success"] for t in ids),
                                         "scheduled_attempts": len(ids) * sample_count,
                                         "valid_attempts": sum(per_task[t][condition]["valid_count"] for t in ids),
                                         "measurable_attempts": sum(per_task[t][condition]["measurable_count"] for t in ids)}
                                         for condition in CONDITIONS}}
            for condition in CONDITIONS:
                selected_measures = [row["measurement"] for row in measured_rows
                                     if row["task_id"] in selected and row["condition"] == condition]
                distributions[group]["conditions"][condition] = {field: _distribution(
                    [m[field] for m in selected_measures if m[field] is not None])
                    for field in ("W_B", "U_B", "W", "U", "I0", "D")}
        audit_rng = random.Random(settings.get("audit_seed", seed))
        audit = {}
        for name in CONTRASTS:
            rows = sorted((row for row in paired_rows if row["contrast"] == name),
                          key=lambda row: (row["mean_log2_T0_difference"], row["task_id"]))
            ids = sorted(row["task_id"] for row in rows)
            audit[name] = {"largest_negative": [row["task_id"] for row in rows if row["mean_log2_T0_difference"] < 0][:10],
                           "largest_positive": [row["task_id"] for row in reversed(rows) if row["mean_log2_T0_difference"] > 0][:10],
                           "random_paired": audit_rng.sample(ids, min(20, len(ids))),
                           "review_instruction": "Review all available outputs from all three conditions for each selected task."}
        signature_errors = [task_id for task_id, values in signature_values.items() if len(values) > 1]
        report["models"][model] = {"conditions": conditions, "contrasts": contrasts,
                                   "per_task": per_task, "paired_tasks": paired_rows,
                                   "common_intersection": {**_composition(common, task_map), "task_ids": common},
                                   "missing_scheduled_records": missing_slots,
                                   "incomplete_scheduled_outcomes": incomplete_slots,
                                   "run_complete": not incomplete_slots,
                                   "correctness_conclusions_ready": not incomplete_slots,
                                   "fixed_signature_mismatch_task_ids": signature_errors,
                                   "compression_conclusions_ready": not signature_errors and not missing_refs and not incomplete_slots and
                                       all(contrasts[name]["paired_population"]["task_count"] > 0 for name in CONTRASTS) and
                                       all(item["metric_coverage_among_valid"] == 1 or item["valid_attempts"] == 0
                                           for item in conditions.values()),
                                   "distributions": distributions, "audit_sample": audit}
        report["models"][model]["stratum_breakdown"] = _stratum_breakdown(
            vectors, task_ids, task_map, families, strata, report["bootstrap"], settings)
    return report


def _scatter_svg(model_report):
    """An inspectable SVG of per-task mean source and expansion log counts."""
    points = [(task_id, condition, row["mean_log2_W"], row["mean_log2_U"])
              for task_id, arms in model_report["per_task"].items()
              for condition, row in arms.items() if row["measurable_count"]]
    if not points:
        return "<p>No measurable valid samples are available for the scatterplot.</p>"
    colors = dict(zip(CONDITIONS, ("#51728f", "#bc4f24", "#178071")))
    xs, ys = [p[2] for p in points], [p[3] for p in points]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    xpad, ypad = max((xmax - xmin) * .07, .5), max((ymax - ymin) * .07, .5)
    xmin, xmax, ymin, ymax = xmin - xpad, xmax + xpad, ymin - ypad, ymax + ypad
    x = lambda value: 72 + (value - xmin) / (xmax - xmin) * 620
    y = lambda value: 345 - (value - ymin) / (ymax - ymin) * 290
    svg = ["<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 760 450' role='img' "
           "aria-label='Per-task mean log source size versus mean log expansion size'>",
           "<rect width='760' height='450' fill='white'/>",
           "<g font-family='system-ui,sans-serif' font-size='12' fill='#17212b'>",
           "<text x='72' y='24' font-size='16'>Source size and dependency expansion</text>"]
    for step in range(5):
        xv, yv = xmin + step * (xmax - xmin) / 4, ymin + step * (ymax - ymin) / 4
        svg.extend([f"<path d='M{x(xv):.2f},55V345' stroke='#e3e9ee'/>",
                    f"<path d='M72,{y(yv):.2f}H692' stroke='#e3e9ee'/>",
                    f"<text x='{x(xv):.2f}' y='366' text-anchor='middle'>{xv:.2f}</text>",
                    f"<text x='62' y='{y(yv)+4:.2f}' text-anchor='end'>{yv:.2f}</text>"])
    for task_id, condition, xv, yv in points:
        label = html.escape(f"{task_id}; {condition}; mean log2 W={xv:.5g}; mean log2 U={yv:.5g}")
        svg.append(f"<circle cx='{x(xv):.2f}' cy='{y(yv):.2f}' r='4' fill='{colors[condition]}' opacity='.65'><title>{label}</title></circle>")
    svg.extend(["<text x='382' y='394' text-anchor='middle'>Mean log2 total source tokens W</text>",
                "<text transform='translate(18 200) rotate(-90)' text-anchor='middle'>Mean log2 total expansion U</text>"])
    for index, condition in enumerate(CONDITIONS):
        svg.append(f"<circle cx='{80 + index * 220}' cy='425' r='5' fill='{colors[condition]}'/>"
                   f"<text x='{92 + index * 220}' y='429'>{html.escape(condition)}</text>")
    svg.append("</g></svg>")
    return "".join(svg)


def _attainment_svg(model_report):
    colors = dict(zip(CONDITIONS, ("#51728f", "#bc4f24", "#178071")))
    svg = ["<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 340' role='img' "
           "aria-label='Verified compression attainment among all scheduled attempts'>",
           "<rect width='600' height='340' fill='white'/><g font-family='system-ui,sans-serif' font-size='12'>",
           "<text x='65' y='24' font-size='16'>Verified compression attainment</text>"]
    for index in range(5):
        rate, yy = index / 4, 260 - index * 50
        svg.append(f"<path d='M65,{yy}H530' stroke='#e3e9ee'/><text x='55' y='{yy+4}' text-anchor='end'>{rate:.0%}</text>")
    for threshold in range(3):
        xx = 90 + threshold * 205
        svg.append(f"<text x='{xx}' y='284' text-anchor='middle'>{1 << threshold}× reference T0</text>")
    for condition in CONDITIONS:
        points = []
        for threshold in range(3):
            rate = model_report["conditions"][condition]["attainment"][str(threshold)]["rate"]
            if rate is not None:
                xx, yy = 90 + threshold * 205, 260 - 200 * rate
                points.append(f"{xx},{yy:.3f}")
                svg.append(f"<circle cx='{xx}' cy='{yy:.3f}' r='4' fill='{colors[condition]}'><title>"
                           f"{condition}: {rate:.1%} at {1 << threshold}x</title></circle>")
        if points:
            svg.append(f"<polyline points='{' '.join(points)}' fill='none' stroke='{colors[condition]}' stroke-width='2'/>")
    svg.append("<text x='300' y='322' text-anchor='middle'>Invalid and unmeasurable attempts do not attain a threshold.</text></g></svg>")
    return "".join(svg)


def _paired_effects_svg(model_report):
    contrasts = [(name, model_report["contrasts"][name]) for name in CONTRASTS]
    values = [row["mean_log2_T0_difference"] for row in model_report["paired_tasks"]]
    bounds = [value for _, c in contrasts for value in
              c["mean_log2_T0_difference"]["confidence_interval"] if value is not None]
    if not values:
        return "<p>No paired measurable tasks are available for an effect plot.</p>"
    low, high = min([0, *values, *bounds]), max([0, *values, *bounds])
    padding = max((high - low) * .08, .25)
    low, high = low - padding, high + padding
    x = lambda value: 70 + (value - low) / (high - low) * 620
    svg = ["<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 760 375' role='img' "
           "aria-label='Paired task log compression effects with family bootstrap intervals'>",
           "<rect width='760' height='375' fill='white'/><g font-family='system-ui,sans-serif' font-size='12' fill='#17212b'>",
           "<text x='70' y='25' font-size='16'>Paired task compression effects</text>",
           f"<path d='M{x(0):.2f},50V305' stroke='#667884' stroke-dasharray='4 4'/>"]
    for step in range(5):
        value = low + step * (high - low) / 4
        svg.append(f"<text x='{x(value):.2f}' y='329' text-anchor='middle'>{value:.3g}</text>")
    for index, (name, contrast) in enumerate(contrasts):
        yy = 116 + index * 133
        effect = contrast["mean_log2_T0_difference"]
        estimable = effect["family_count"] >= 2
        svg.append(f"<text x='70' y='{yy-45}'>{html.escape(name)} · {effect['task_count']} paired tasks / {effect['family_count']} families"
                   + ("" if estimable else " · interval not estimable (&lt;2 families)") + "</text>")
        for row in model_report["paired_tasks"]:
            if row["contrast"] != name:
                continue
            jitter = (sum(row["task_id"].encode()) % 45) - 22
            value = row["mean_log2_T0_difference"]
            svg.append(f"<circle cx='{x(value):.2f}' cy='{yy+jitter}' r='3.5' fill='#6388a4' opacity='.45'><title>"
                       f"{html.escape(row['task_id'])}: {value:.5g} log2 T0 change</title></circle>")
        lo, hi = effect["confidence_interval"]
        if estimable and lo is not None and hi is not None:
            svg.append(f"<path d='M{x(lo):.2f},{yy}H{x(hi):.2f}' stroke='#b34c20' stroke-width='4'/>")
        if effect["estimate"] is not None:
            xx = x(effect["estimate"])
            svg.append(f"<path d='M{xx:.2f},{yy-7}L{xx+7:.2f},{yy}L{xx:.2f},{yy+7}L{xx-7:.2f},{yy}Z' fill='#b34c20'/>")
    svg.append("<text x='380' y='361' text-anchor='middle'>B minus control, log2 T0 · dots: tasks · orange: mean and bootstrap interval</text></g></svg>")
    return "".join(svg)


def _success_compression_svg(model_report):
    colors = dict(zip(CONDITIONS, ("#51728f", "#bc4f24", "#178071")))
    points = []
    for condition in CONDITIONS:
        scores = [arms[condition]["mean_log2_T0"] for arms in model_report["per_task"].values()
                  if arms[condition]["measurable_count"]]
        if scores:
            points.append((condition, model_report["conditions"][condition]["success_rate"], mean(scores)))
    if not points:
        return "<p>No measurable valid outputs are available for the success/compression profile.</p>"
    ymin, ymax = min(point[2] for point in points), max(point[2] for point in points)
    pad = max((ymax - ymin) * .15, .5)
    ymin, ymax = ymin - pad, ymax + pad
    x = lambda value: 85 + value * 585
    y = lambda value: 285 - (value - ymin) / (ymax - ymin) * 225
    svg = ["<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 760 455' role='img' "
           "aria-label='All-attempt success rate versus conditional mean log compression'>",
           "<rect width='760' height='455' fill='white'/><g font-family='system-ui,sans-serif' font-size='12' fill='#17212b'>",
           "<text x='85' y='25' font-size='16'>Correctness and conditional compression</text>"]
    for step in range(5):
        xx, value = x(step / 4), ymin + step * (ymax - ymin) / 4
        svg.append(f"<path d='M{xx:.2f},60V285' stroke='#e3e9ee'/><text x='{xx:.2f}' y='308' text-anchor='middle'>{step/4:.0%}</text>")
        svg.append(f"<path d='M85,{y(value):.2f}H670' stroke='#e3e9ee'/><text x='75' y='{y(value)+4:.2f}' text-anchor='end'>{value:.3g}</text>")
    for index, (condition, success, compression) in enumerate(points):
        summary = model_report["conditions"][condition]
        svg.append(f"<circle cx='{x(success):.2f}' cy='{y(compression):.2f}' r='{5+index*3}' fill='none' stroke='{colors[condition]}' stroke-width='2.5'><title>"
                   f"{condition}: success {success:.1%}; conditional mean log2 T0 {compression:.5g}</title></circle>")
        svg.append(f"<circle cx='92' cy='{376+index*26}' r='5' fill='{colors[condition]}'/><text x='105' y='{380+index*26}'>"
                   f"{condition}: {summary['measurable_task_count']} measurable tasks / {summary['measurable_family_count']} families</text>")
    svg.extend(["<text x='375' y='337' text-anchor='middle'>Success rate across all scheduled attempts</text>",
                "<text transform='translate(24 175) rotate(-90)' text-anchor='middle'>Conditional task mean log2 T0</text></g></svg>"])
    return "".join(svg)


def _status_counts(counts):
    return html.escape(", ".join(f"{status}: {count}" for status, count in (counts or {}).items())) or "none"


def _pending_logs(run_provenance):
    """Name the logged checker incidents and provider outages that leave slots pending."""
    text = ""
    incidents = run_provenance.get("checker_incidents") or {}
    if incidents.get("unresolved_attempts"):
        text += (f" Attempts with unresolved checker incidents: {incidents['unresolved_attempts']} (" +
                 _status_counts(incidents.get("unresolved_statuses")) + "; phases: " +
                 _status_counts(incidents.get("unresolved_phases")) + "), logged in <code>" +
                 html.escape(str(incidents.get("log"))) + "</code>; rerunning evaluate retries them.")
    inherited = run_provenance.get("inherited_checker_incidents") or {}
    if inherited.get("unresolved_attempts"):
        text += (f" Attempts still pending after checker incidents in source version <code>" +
                 html.escape(str(inherited.get("source_version"))) + f"</code>: {inherited['unresolved_attempts']} (" +
                 _status_counts(inherited.get("unresolved_statuses")) + "; phases: " +
                 _status_counts(inherited.get("unresolved_phases")) + "), logged in <code>" +
                 html.escape(str(inherited.get("log"))) + "</code>; rescore copies only records, so rerun evaluate on "
                 "that version and rescore it into a new version.")
    outages = run_provenance.get("provider_outages") or {}
    if outages.get("unresolved_attempts"):
        text += (f" Attempts whose provider calls have no recorded model outcome: {outages['unresolved_attempts']}, "
                 "logged in <code>" + html.escape(str(outages.get("log"))) + "</code>.")
        if outages.get("prompt_rejected_attempts"):
            text += (f" Of these, {outages['prompt_rejected_attempts']} have a prompt the provider rejected as "
                     "<code>invalid_prompt</code>; <code>generate</code> sends them again only with "
                     "<code>--retry-rejected</code>.")
    unreadable = sum(log.get("unreadable_lines") or 0 for log in (incidents, inherited, outages))
    if unreadable:
        text += f" Unreadable log lines, whose events are not counted here: {unreadable}."
    return text


def _interval_text(effect):
    """Estimate and interval; unsupported family resampling is never shown as precision."""
    number = lambda value: "undefined" if value is None else f"{value:.4g}"
    if effect["family_count"] < 2:
        interval = "not estimable (<2 families)"
    else:
        interval = ", ".join(map(number, effect["confidence_interval"]))
    return html.escape(number(effect["estimate"]) + " [" + interval + "]")


def _replicates_text(effect):
    return f"{effect['bootstrap_defined']} / {effect['bootstrap_defined'] + effect['bootstrap_undefined']}"


def _bootstrap_note(bootstrap):
    note = (f"Intervals are {bootstrap['confidence_level'] * 100:g}% percentile intervals over "
            f"{bootstrap['replicates']} requested theorem-family bootstrap replicates. Replicates that resample "
            "no supporting task are undefined and excluded; effects supported by fewer than two families are "
            "not estimable.")
    if bootstrap.get("singleton_strata"):
        note += (" Single-family bootstrap strata, which resampling cannot vary: " +
                 ", ".join(sorted(bootstrap["singleton_strata"])) + ".")
    return "<p>" + html.escape(note) + "</p>"


def _stratum_html(model_report, level):
    """Easy controls are reported separately from the remaining tasks."""
    breakdown = model_report.get("stratum_breakdown")
    if not breakdown:
        return ""
    classes = ("stratum_class:easy_control", "stratum_class:non_control")
    body = ["<h3>Easy controls and other strata (exploratory)</h3><p>Classes use the frozen task stratum labels; "
            "easy-control labels: <code>" + html.escape(", ".join(breakdown["easy_control_strata"])) + "</code>. "
            "The pooled results above include every task; these subgroups are not separately powered. "
            "Per-label tables: <a href='correctness_by_stratum.csv'>correctness by stratum</a> · "
            "<a href='contrasts_by_stratum.csv'>contrasts by stratum</a>.</p>",
            "<table><tr><th>Class</th><th>Tasks / families</th><th>Condition</th><th>Valid / scheduled</th>"
            "<th>Success</th><th>First attempt</th><th>Any of scheduled samples</th></tr>"]
    for group in classes:
        label, composition = html.escape(group.split(":", 1)[1]), breakdown["groups"][group]["composition"]
        size = f"{composition['task_count']} / {composition['family_count']}"
        rates = model_report["distributions"].get(group, {}).get("correctness_by_condition", {})
        if not rates:
            body.append(f"<tr><td>{label}</td><td>{size}</td><td colspan='5'>No analyzed task belongs to this class.</td></tr>")
        for condition, rate in rates.items():
            body.append(f"<tr><td>{label}</td><td>{size}</td><td>{html.escape(condition)}</td>"
                        f"<td>{rate['valid_attempts']} / {rate['scheduled_attempts']}</td><td>{rate['success_rate']:.1%}</td>"
                        f"<td>{rate['first_attempt_success_rate']:.1%}</td><td>{rate['any_sample_success_rate']:.1%}</td></tr>")
    body.append("</table><table><tr><th>Class</th><th>Contrast</th><th>Success-rate difference [" + level + " interval]</th>"
                "<th>Paired tasks / families</th><th>Mean log2 T0 change [" + level + " interval]</th>"
                "<th>T0 bootstrap defined / requested</th></tr>")
    for group in classes:
        for name, effects in breakdown["groups"][group]["contrasts"].items():
            effect = effects["mean_log2_T0_difference"]
            body.append("<tr><td>" + html.escape(group.split(":", 1)[1]) + "</td><td>" + html.escape(name) + "</td><td>" +
                        _interval_text(effects["success_rate_difference"]) + "</td><td>" +
                        f"{effect['task_count']} / {effect['family_count']}</td><td>" + _interval_text(effect) +
                        "</td><td>" + _replicates_text(effect) + "</td></tr>")
    body.append("</table>")
    return "".join(body)


def _write_stratum_csvs(report, directory):
    """Per-stratum correctness and paired contrasts; the pooled tables are unchanged."""
    tables = {"correctness_by_stratum": [], "contrasts_by_stratum": []}
    for model, model_report in report["models"].items():
        for group, distribution in model_report.get("distributions", {}).items():
            if group.startswith(("stratum:", "stratum_class:")):
                tables["correctness_by_stratum"].extend(
                    {"model": model, "group": group, "condition": condition,
                     "task_count": distribution["composition"]["task_count"],
                     "family_count": distribution["composition"]["family_count"], **rate}
                    for condition, rate in distribution["correctness_by_condition"].items())
        for group, value in model_report.get("stratum_breakdown", {}).get("groups", {}).items():
            for name, effects in value["contrasts"].items():
                for statistic, effect in effects.items():
                    low, high = effect["confidence_interval"]
                    tables["contrasts_by_stratum"].append({
                        "model": model, "group": group, "contrast": name, "statistic": statistic,
                        "estimate": effect["estimate"], "interval_low": low, "interval_high": high,
                        "confidence_level": effect["confidence_level"], "task_count": effect["task_count"],
                        "family_count": effect["family_count"], "bootstrap_defined": effect["bootstrap_defined"],
                        "bootstrap_undefined": effect["bootstrap_undefined"],
                        "uncertainty_warning": effect["uncertainty_warning"],
                        "geometric_ratio": effect.get("geometric_ratio")})
    paths = {key: directory / (key + ".csv") for key in tables}
    for key, rows in tables.items():
        with paths[key].open("w", newline="", encoding="utf-8") as handle:
            if rows:
                writer = csv.DictWriter(handle, list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return paths


def write_report(report: dict, destination: str | Path) -> dict[str, str]:
    """Write reanalysis JSON and human-readable tables without external deps."""
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {"json": directory / "analysis.json", "correctness": directory / "correctness.csv",
             "paired": directory / "paired_tasks.csv", "resources": directory / "resource_usage.csv",
             "html": directory / "report.html"}
    paths["json"].write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    correctness_rows, paired_rows, resource_rows = [], [], []
    for model, model_report in report["models"].items():
        for condition, summary in model_report["conditions"].items():
            breakdown = summary.get("failure_breakdown", {})
            correctness_rows.append({"model": model, "condition": condition,
                **{key: summary.get(key) for key in ("scheduled_attempts", "valid_attempts",
                    "success_rate", "first_attempt_success_rate", "four_sample_success_rate",
                    "any_sample_success_rate", "metric_coverage_among_valid")},
                "model_failure_attempts": sum(breakdown.get("model_failures", {}).values()),
                "unresolved_attempts": sum(breakdown.get("unresolved_outcomes", {}).values())})
            for measurement, resource in summary.get("resources", {}).get("measurements", {}).items():
                resource_rows.append({"model": model, "condition": condition, "measurement": measurement,
                                      "population": "all_scheduled_attempts_including_failures",
                                      "scheduled_attempts": summary["scheduled_attempts"], **resource,
                                      "description": resource.get("description", "")})
        paired_rows.extend({"model": model, **row} for row in model_report["paired_tasks"])
    paths.update(_write_stratum_csvs(report, directory))
    for key, rows in (("correctness", correctness_rows), ("paired", paired_rows), ("resources", resource_rows)):
        with paths[key].open("w", newline="", encoding="utf-8") as handle:
            if rows:
                writer = csv.DictWriter(handle, list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    from .baseline import baseline_html, write_baseline_csv
    paths.update(write_baseline_csv(report, directory))
    body = ["<!doctype html><html><meta charset='utf-8'><title>Lean compression evaluation</title>",
            "<style>body{max-width:1100px;margin:3rem auto;font:16px system-ui;padding:0 1rem;color:#17212b}"
            "table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{padding:.6rem;text-align:left;border-bottom:1px solid #ccd5df}"
            "th{background:#eaf0f5}code{font-size:.9em}.notice{padding:1rem;background:#fff4d8}</style>",
            "<h1>Lean compression evaluation</h1><p>Paper-aligned measurement under a frozen primitive boundary.</p>",
            "<p class='notice'>Compression is conditional on success and metric availability. Larger expansion can increase T0. "
            "These measurements do not establish better reasoning.</p>",
            "<p>Population: " + str(report["population"]["task_count"]) + " tasks, " +
            str(report["population"]["family_count"]) + " theorem families. Bootstrap: " +
            str(report["bootstrap"]["replicates"]) + " replicates.</p>"]
    run_provenance = report.get("run_provenance", {})
    if run_provenance.get("label"):
        body.insert(4, "<p class='notice'><strong>" + html.escape(run_provenance["label"]) + "</strong></p>")
    if run_provenance.get("development"):
        body.insert(5, "<p class='notice'>Development run. This page is a pipeline check; it is not a confirmatory evaluation.</p>")
    body.append("<p>Analysis version: <code>" + html.escape(report["analysis_version"]) + "</code>. "
                "Metric profile: <code>" + html.escape(report["metric_profile"]) + "</code>. "
                "Source: frozen scheduled attempt records and verified reference records; "
                "the figures below are generated directly from analysis.json.</p>")
    for field in ("run_hash", "evaluation_version", "model_snapshot"):
        if run_provenance.get(field):
            body.append("<p>" + html.escape(field) + ": <code>" + html.escape(str(run_provenance[field])) + "</code></p>")
    for model, model_report in report["models"].items():
        body.append("<h2>" + html.escape(model) + "</h2>")
        if model_report.get("incomplete_scheduled_outcomes"):
            statuses = Counter(str(row["status"]) for row in model_report["incomplete_scheduled_outcomes"])
            body.append("<p class='notice'><strong>Run incomplete:</strong> " +
                str(len(model_report["incomplete_scheduled_outcomes"])) + " scheduled outcomes remain unresolved (" +
                html.escape(", ".join(f"{status}: {count}" for status, count in sorted(statuses.items()))) +
                "). The all-slot proportions below are provisional accounting, not completed reliability estimates. "
                "Checker or infrastructure errors, provider failures, unrecognized statuses and a returned model mismatch "
                "are not classified as incorrect mathematical proofs." + _pending_logs(run_provenance) + "</p>")
        if not model_report["compression_conclusions_ready"]:
            body.append("<p class='notice'>Coverage or signature/reference checks remain unresolved; see analysis.json.</p>")
        body.append("<h3>Correctness: all scheduled attempts</h3><table><tr><th>Condition</th><th>Valid / scheduled</th>"
                    "<th>Success</th><th>First attempt</th><th>Any of scheduled samples</th><th>Metric coverage among valid</th>"
                    "<th>Model failures</th><th>Unresolved outcomes</th></tr>")
        for condition, summary in model_report["conditions"].items():
            coverage = summary["metric_coverage_among_valid"]
            breakdown = summary.get("failure_breakdown", {})
            body.append("<tr><td>" + html.escape(condition) + "</td><td>" +
                f"{summary['valid_attempts']} / {summary['scheduled_attempts']}</td><td>{summary['success_rate']:.1%}</td>"
                f"<td>{summary['first_attempt_success_rate']:.1%}</td><td>" +
                f"{summary.get('four_sample_success_rate', summary.get('any_sample_success_rate')):.1%}</td><td>" +
                ("undefined" if coverage is None else f"{coverage:.1%}") + "</td><td>" +
                _status_counts(breakdown.get("model_failures")) + "</td><td>" +
                _status_counts(breakdown.get("unresolved_outcomes")) + "</td></tr>")
        level = f"{report['bootstrap']['confidence_level'] * 100:g}%"
        body.append("</table><h3>Compression: paired measurable tasks</h3><table><tr><th>Contrast</th><th>Tasks</th>"
                    "<th>Families</th><th>Mean log2 T0 change [" + level + " interval]</th><th>Bootstrap defined / requested</th>"
                    "<th>Geometric ratio</th><th>Mean log2 U change</th><th>Mean log2 W change</th></tr>")
        for name, contrast in model_report["contrasts"].items():
            value = contrast["mean_log2_T0_difference"]
            def format_number(number):
                return "undefined" if number is None else f"{number:.4g}"
            body.append("<tr><td>" + html.escape(name) + "</td><td>" + str(value["task_count"]) + "</td><td>" +
                str(value["family_count"]) + "</td><td>" + _interval_text(value) + "</td><td>" + _replicates_text(value) + "</td><td>" +
                format_number(value["geometric_ratio"]) + "</td><td>" +
                format_number(contrast["mean_log2_U_difference"]["estimate"]) + "</td><td>" +
                format_number(contrast["mean_log2_W_difference"]["estimate"]) + "</td></tr>")
        body.append("</table>")
        body.append(_bootstrap_note(report["bootstrap"]))
        body.append(_stratum_html(model_report, level))
        body.append("<h3>Resource use: all scheduled attempts</h3><p>Failures are included. Missing observations are unavailable, "
                    "never zero; peak memory uses recorded peaks, not requested limits. A container's cgroup high-water mark, "
                    "page cache included, and a Lean process's peak RSS are different scopes, shown in separate rows and never "
                    "pooled. Extraction heartbeats are the extraction run's repeat of the validation check, not extra work, and "
                    "are never added to validation heartbeats. Token columns are separate provider counts "
                    "and must not be added together. No API cost is inferred.</p><table><tr><th>Condition</th><th>Measurement</th>"
                    "<th>Observed / scheduled</th><th>Median</th><th>p95</th><th>Observed sum</th></tr>")
        for condition, summary in model_report["conditions"].items():
            for measurement, resource in summary.get("resources", {}).get("measurements", {}).items():
                def resource_number(value):
                    if value is None:
                        return "unavailable"
                    return f"{value:,}" if isinstance(value, int) else f"{value:,.4g}"
                body.append("<tr><td>" + html.escape(condition) + "</td><td>" +
                    html.escape(RESOURCE_LABELS.get(measurement, measurement.replace("_", " "))) + "</td><td>" +
                    f"{resource['observed_count']} / {summary['scheduled_attempts']}</td><td>" +
                    resource_number(resource["median"]) + "</td><td>" + resource_number(resource["p95"]) +
                    "</td><td>" + ("not applicable" if resource["unit"] == "MiB" else resource_number(resource["sum"])) + "</td></tr>")
        body.append("</table><p>Generation latency is shown only when explicitly stored. Observed sums cover recorded values only; "
                    "missing measurements may include stages that were never invoked. Recorded generation workflow time includes "
                    "the built-in provider's retries/backoff and does not measure model-only computation. "
                    "<a href='resource_usage.csv'>Download resource usage</a>.</p>")
        body.append("<h3>Source and expansion</h3><p>Each point is one task and condition, averaging log counts over "
                    "its measurable valid samples. Hover for task identity. Failed and unmeasurable samples have no plotted value.</p>")
        body.append(_scatter_svg(model_report))
        body.append("<h3>All-attempt robustness</h3><p>Population: all scheduled attempts, averaged equally across tasks. "
                    "Colors match the source/expansion plot. Missing reference metrics make attainment unavailable.</p>")
        body.append(_attainment_svg(model_report))
        body.append("<h3>Paired effects</h3><p>Population: paired measurable tasks within each contrast. "
                    "Task means include every valid measurable sample; vertical dot spacing is for readability. "
                    "The orange interval resamples theorem families.</p>")
        body.append(_paired_effects_svg(model_report))
        body.append("<h3>Correctness and compression profile</h3><p>The horizontal axis uses all scheduled attempts; "
                    "the vertical axis averages task mean log T0 only where an arm has measurable valid samples. "
                    "Arm-specific populations can differ. This descriptive plot is not a combined quality score.</p>")
        body.append(_success_compression_svg(model_report))
    body.append(baseline_html(report))
    body.append("<p><a href='analysis.json'>Full analysis and missing-population accounting</a> · "
                "<a href='correctness.csv'>Correctness table</a> · <a href='paired_tasks.csv'>Paired task table</a> · "
                "<a href='resource_usage.csv'>Resource usage</a></p></html>")
    paths["html"].write_text("\n".join(body), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}
