"""The trusted tactic-portfolio baseline: what frozen automation already solves.

This spec anchor is descriptive. It is neither a preregistered decision quantity
nor a matched LLM-compute baseline. Records come from the ``baseline`` command
and must share the run's environment, configuration, portfolio budget, checker
engine and frozen task identities. Missing and unresolved tasks stay visible;
none count as solved.

The frozen tactic_portfolio_tactics are checked as separate proofs, in order,
until one succeeds, sharing tactic_portfolio_total_wall_seconds (run_portfolio).
A configuration with only the legacy tactic_portfolio string has it checked as
one proof; such baselines still load, labelled legacy_single_proof.
"""
from __future__ import annotations

from collections import Counter
import csv
import html
import math
from pathlib import Path
from statistics import mean

from .integrity import frozen_portfolio, portfolio_config, portfolio_tactics
from .io import digest, read_json, reference_matches_task
from .outcomes import outcome_class

# The policy.json fields load_baseline checks; analysis.json reports only these
# (portfolio_tactics only for separate checks).
POLICY_FIELDS = ("kind", "engine_hash", "environment_hash", "config_hash", "portfolio_validation_wall_seconds")
SEPARATE, LEGACY = "separate_checks", "legacy_single_proof"
GROUPS = ("solved", "unsolved", "unknown")
LABELS = {"solved": "Portfolio solved", "unsolved": "Portfolio unsolved", "unknown": "Baseline missing/unresolved"}
# The checker ran the portfolio and rejected it, or the fixed budget ran out.
# Any other status is a checker, environment or harness incident: unresolved.
UNSOLVED = ("parse_failure", "output_format_failure", "elaboration_failure", "target_mismatch",
            "forbidden_dependency", "validation_error", "disallowed_axiom", "kernel_replay_failure",
            "verification_timeout", "verification_resource_limit")
NOT_SUPPLIED = {"supplied": False, "status": "not_supplied",
                "note": "No trusted tactic-portfolio baseline was supplied (analyze --baseline); "
                        "this report does not establish what existing automation solves."}
INTERPRETATION = [
    "Descriptive comparison anchor: not a preregistered decision quantity and not a matched LLM-compute baseline.",
    "Solved means the frozen portfolio passed the same Lean check as model outputs, under its own total budget. "
    "Checker rejections and exhausted budgets are unsolved; checker, environment and harness incidents are unresolved.",
    "Missing and unresolved tasks never count as solved: solve rates use every run task as denominator, "
    "cross-tabs place them in the unknown group, and the section is marked incomplete.",
    "Cross-tabs weight tasks equally; compression uses task means over measurable valid samples. No intervals are computed."]
# The interpretation's last item, by portfolio semantics.
NOTES = {SEPARATE: "Each alternative is checked as a separate proof, in the frozen order, within what the earlier "
                   "checks left of the shared budget (process start-up included); the first to pass solves the task. "
                   "An alternative that is rejected, cannot parse, runs out of heartbeats or hits a resource limit "
                   "leaves the rest of the budget to the next, but one that reaches its wall-clock limit has spent it, "
                   "so no later alternative runs.",
         LEGACY: "Legacy portfolio: its tactic_portfolio string was checked as one proof per task, so a tactic unknown "
                 "under a task's imports made the whole portfolio a parse failure and none of its alternatives ran. "
                 "Current configurations list tactic_portfolio_tactics, each checked as a separate proof."}
STDERR_TAIL = 4096  # characters of each alternative's stderr its record keeps
ARM_FIELDS = ("success_rate", "any_sample_success", "valid_count", "measurable_count", "mean_log2_T0")
FIELDS = ("model", "task_id", "family_id", "domain", "portfolio_outcome", "portfolio_solved", "portfolio_solved_by",
          "baseline_validity_status", "condition", *ARM_FIELDS)


def _seconds(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < math.inf


def run_portfolio(task: dict, tactics: list, environment: dict, engine: Path, config: dict) -> dict:
    """Check each alternative as its own proof `by <tactic>`, in the frozen order, until one succeeds.

    The alternatives share the frozen total budget: each check gets what the elapsed times of the checks
    before it (validation.resources.elapsed_seconds, process start-up included) left of it, and none
    starts once it is spent. A rejected or unparsable alternative, or one that ran out of heartbeats or
    hit a resource limit, leaves the rest to the next. The runner stops a check at its wall-clock limit
    only once all of it has elapsed, so such a timeout spends the budget and no later alternative runs.
    The result is the deciding check's (the success, else the last attempted) with every attempted
    alternative's outcome and evidence. A checker incident, or a check without a recorded elapsed time,
    is returned at once as an incident that leaves the task pending; an engine changed between checks
    stops the run.
    """
    from .runner import evaluate_proof
    remaining, attempts, engines = portfolio_config(config)["validation"]["wall_seconds"], [], set()
    for tactic in tactics:
        proof = "by " + tactic
        value = evaluate_proof(task, proof, environment, engine,
                               {**config, "validation": {**config["validation"], "wall_seconds": remaining}})
        checked = value["validation"] if isinstance(value.get("validation"), dict) else {}
        elapsed, stderr = (checked.get("resources") or {}).get("elapsed_seconds"), checked.get("stderr")
        # Only the deciding check's whole result is stored: what stopped the others is kept here.
        attempts.append({"tactic": tactic, "validity_status": value["validity_status"], "elapsed_seconds": elapsed,
                         "wall_seconds_limit": remaining, "failure_reason": checked.get("failure_reason"),
                         "error": checked.get("error"), "process_returncode": checked.get("process_returncode"),
                         "stderr_tail": stderr[-STDERR_TAIL:] if isinstance(stderr, str) else None,
                         "resources": checked.get("resources")})
        engines.add(value["engine_hash"])
        if len(engines) > 1:
            raise RuntimeError("Engine changed during this run; use a new output directory")
        if outcome_class(value["validity_status"]) == "incomplete":
            break
        if not _seconds(elapsed):
            value = {**value, "validity_status": "infrastructure_error", "failure_reason": "portfolio_elapsed_unrecorded"}
            break
        remaining -= elapsed
        if value["validity_status"] == "success" or remaining <= 0:
            break
    return {**value, "proof": proof, "portfolio_tactics": list(tactics), "portfolio_alternatives": attempts}


def _read(path: Path):
    try:
        return read_json(path)
    except ValueError as error:
        raise ValueError(f"Malformed JSON in baseline directory: {path}: {error}") from error


BUDGET, ORDER = "was not checked under the frozen portfolio budget", "does not follow the frozen portfolio order"


def _resources(record: dict) -> dict:
    validation = record.get("validation")
    resources = validation.get("resources") if isinstance(validation, dict) else None
    return resources if isinstance(resources, dict) else {}


def _portfolio_problem(record: dict, tactics: list, budget) -> str | None:
    """Why a separate-checks record is not run_portfolio's evidence under the frozen budget, if it is not."""
    attempts = record.get("portfolio_alternatives")
    if not (isinstance(attempts, list) and 0 < len(attempts) <= len(tactics) and all(isinstance(a, dict) for a in attempts)):
        return ORDER
    *earlier, final = attempts
    if ([attempt.get("tactic") for attempt in attempts] != tactics[:len(attempts)] or record.get("proof") != "by " + final["tactic"]
            or record.get("validity_status") != final.get("validity_status")
            or any(attempt.get("validity_status") not in UNSOLVED for attempt in earlier)):
        return ORDER
    remaining = budget
    for attempt in attempts:
        # Each started with what the recorded elapsed times before it left, while some was left.
        if remaining <= 0 or attempt.get("wall_seconds_limit") != remaining or not _seconds(attempt.get("elapsed_seconds")):
            return BUDGET
        remaining -= attempt["elapsed_seconds"]
    resources = _resources(record)
    if (resources.get("wall_seconds_limit"), resources.get("elapsed_seconds")) != (final["wall_seconds_limit"], final["elapsed_seconds"]):
        return BUDGET
    # Only a success or a spent budget ends the run before its last alternative.
    if final.get("validity_status") != "success" and len(attempts) < len(tactics) and remaining > 0:
        return ORDER
    return None


def load_baseline(directory: Path, manifest: dict, rows: list[dict], version_policy: Path) -> tuple[dict, dict, int]:
    """Fail closed unless every record is portfolio evidence for this run's frozen checker."""
    if not (directory / "policy.json").is_file():
        raise ValueError("Baseline directory has no policy.json; create it with the baseline command: " + str(directory))
    policy = _read(directory / "policy.json")
    environment_hash = manifest["environment"]["environment_hash"]
    if not isinstance(policy, dict) or policy.get("kind") != "baseline" or not policy.get("engine_hash"):
        raise ValueError("Baseline directory was not produced by the baseline command")
    if policy.get("environment_hash") != environment_hash:
        raise ValueError("Baseline environment differs from the evaluated environment")
    if policy.get("config_hash") != manifest["config_hash"]:
        raise ValueError("Baseline portfolio/validation configuration differs from the run")
    # Older baseline commands checked the portfolio under validation.wall_seconds instead.
    budget = manifest["config"].get("tactic_portfolio_total_wall_seconds")
    if budget is None:
        raise ValueError("The run's frozen configuration has no tactic_portfolio_total_wall_seconds; no baseline can match it")
    tactics = frozen_portfolio(manifest["config"])
    if policy.get("portfolio_validation_wall_seconds") != budget:
        raise ValueError("Baseline did not check the portfolio under the frozen tactic_portfolio_total_wall_seconds; "
                         "rerun the baseline command into a new output directory")
    if tactics is not None and policy.get("portfolio_tactics") != tactics:
        raise ValueError("Baseline policy does not record the frozen tactic_portfolio_tactics as its alternatives; "
                         "rerun the baseline command into a new output directory")
    engines = {row["engine_hash"] for row in rows if row.get("engine_hash")}
    if version_policy.exists():
        engines.add(read_json(version_policy).get("engine_hash"))
    if not engines:
        raise ValueError(f"Evaluation version {version_policy.parent.name} records no checker engine "
                         "to compare with the baseline")
    if engines - {policy["engine_hash"]}:
        raise ValueError("Baseline checker engine differs from evaluated outputs")
    tasks = {task["task_id"]: task for task in manifest["tasks"]}
    found, outside = {}, set()
    for path in sorted(directory.glob("*.json")):
        if path.name == "policy.json":
            continue
        record = _read(path)
        if (not isinstance(record, dict) or record.get("record_kind") != "trusted_tactic_baseline"
                or not isinstance(record.get("task_id"), str)):
            raise ValueError("Not a trusted tactic baseline record: " + path.name)
        if (record.get("environment_hash") != environment_hash or record.get("engine_hash") != policy["engine_hash"]
                or (record.get("proof") != manifest["config"]["tactic_portfolio"] if tactics is None
                    else record.get("portfolio_tactics") != tactics)):
            raise ValueError("Baseline record has incompatible environment, engine or portfolio provenance: " + path.name)
        # A legacy portfolio was one check under the whole budget.
        problem = ((BUDGET if _resources(record).get("wall_seconds_limit") != budget else None) if tactics is None
                   else _portfolio_problem(record, tactics, budget))
        if problem:
            raise ValueError(f"Baseline record {problem}: {path.name}")
        task_id = record["task_id"]
        if task_id in found or task_id in outside:
            raise ValueError("Duplicate baseline records for task " + task_id)
        if task_id not in tasks:
            outside.add(task_id)
        elif not reference_matches_task(record, tasks[task_id]):
            raise ValueError("Baseline record does not match its frozen task: " + task_id)
        else:
            found[task_id] = record
    return policy, found, len(outside)


def logged_incidents(directory: Path) -> tuple[dict, int]:
    """Each task's latest checker incident status in the directory's incidents.jsonl, and its unreadable lines.

    The baseline command writes no record for a checker incident, so the log is what shows such a task unresolved.
    """
    from .store import _log_lines
    latest, unreadable = {}, 0
    for event in _log_lines(directory / "incidents.jsonl"):
        if event is None:
            unreadable += 1
        elif event.get("event") == "checker_incident" and isinstance(event.get("task_id"), str):
            latest[event["task_id"]] = str(event.get("validity_status") or "unreported")
    return latest, unreadable


def _mean(values):
    return mean(values) if values else None


def _tally(ids, per_task):
    counts = Counter(per_task[task_id]["outcome"] for task_id in ids)
    return {"tasks": len(ids), "families": len({per_task[task_id]["family_id"] for task_id in ids}),
            **{outcome: counts[outcome] for outcome in ("solved", "unsolved", "unresolved", "missing")},
            "solve_rate": counts["solved"] / len(ids) if ids else None}


def summarize_baseline(policy: dict, found: dict, manifest: dict, result: dict, outside: int = 0,
                       incidents: tuple[dict, int] = ({}, 0)) -> dict:
    """Solve rates over every run task; cross-tabs reuse the analysis's per-task summaries.

    incidents is logged_incidents(): a task without a record whose check was a logged checker incident is unresolved.
    """
    tactics = portfolio_tactics(manifest["config"])
    latest, unreadable = incidents
    per_task = {}
    for task in sorted(manifest["tasks"], key=lambda task: task["task_id"]):
        record = found.get(task["task_id"], {})
        status = record.get("validity_status")
        # Only a Lean verdict is a success; a success claim without one is unresolved.
        verdict = (record.get("validation") or {}).get("validity_status")
        incident = None if record else latest.get(task["task_id"])
        outcome = ("unresolved" if incident else "missing" if not record else "solved" if status == verdict == "success"
                   else "unsolved" if status in UNSOLVED else "unresolved")
        # Separate checks: each attempted alternative, in order; the last decided the task.
        attempts = [{key: attempt.get(key) for key in ("tactic", "validity_status", "elapsed_seconds")}
                    for attempt in record.get("portfolio_alternatives", ())] if tactics is not None and record else None
        per_task[task["task_id"]] = {"family_id": task["family_id"], "domain": task["domain"], "outcome": outcome,
                                     "portfolio_solved": {"solved": True, "unsolved": False}.get(outcome),
                                     "solved_by": attempts[-1]["tactic"] if attempts and outcome == "solved" else None,
                                     "alternatives": attempts,
                                     "validity_status": status, "metric_status": record.get("metric_status"),
                                     "record_digest": digest(record) if record else None, "checker_incident": incident}
    group = {task_id: row["outcome"] if row["outcome"] in GROUPS else "unknown" for task_id, row in per_task.items()}
    models = {}
    for model, model_report in result["models"].items():
        arms = model_report["per_task"]
        members = {name: [task_id for task_id in arms if group.get(task_id, "unknown") == name] for name in GROUPS}
        conditions, contrasts = {}, {}
        for condition in model_report["conditions"]:
            conditions[condition] = {}
            for name, ids in members.items():
                summaries = [arms[task_id][condition] for task_id in ids]
                measured = [summary for summary in summaries if summary["measurable_count"]]
                conditions[condition][name] = {
                    "tasks": len(summaries), "success_rate": _mean([s["success_rate"] for s in summaries]),
                    "any_sample_success_rate": _mean([s["any_sample_success"] for s in summaries]),
                    "measurable_tasks": len(measured),
                    **{f"mean_task_log2_{key}": _mean([s[f"mean_log2_{key}"] for s in measured]) for key in ("T0", "U", "W")}}
        for contrast in model_report["contrasts"]:
            contrasts[contrast] = {}
            for name in GROUPS:
                changes = [row["mean_log2_T0_difference"] for row in model_report["paired_tasks"]
                           if row["contrast"] == contrast and group.get(row["task_id"], "unknown") == name]
                contrasts[contrast][name] = {"paired_tasks": len(changes), "mean_log2_T0_difference": _mean(changes)}
        models[model] = {"conditions": conditions, "contrasts": contrasts}
    missing = [task_id for task_id, row in per_task.items() if row["outcome"] == "missing"]
    unresolved = [task_id for task_id, row in per_task.items() if row["outcome"] == "unresolved"]
    complete = not missing and not unresolved
    solved_by, semantics = Counter(row["solved_by"] for row in per_task.values()), LEGACY if tactics is None else SEPARATE
    return {"supplied": True, "status": "complete" if complete else "incomplete", "complete": complete,
            "interpretation": [*INTERPRETATION, NOTES[semantics]],
            "policy": {key: policy[key] for key in POLICY_FIELDS + (() if tactics is None else ("portfolio_tactics",))},
            "portfolio_semantics": semantics,
            "portfolio": manifest["config"]["tactic_portfolio"] if tactics is None else tactics,
            "solved_by_tactic": None if tactics is None else {tactic: solved_by[tactic] for tactic in tactics},
            "overall": _tally(list(per_task), per_task),
            "by_domain": {domain: _tally([t for t, row in per_task.items() if row["domain"] == domain], per_task)
                          for domain in sorted({row["domain"] for row in per_task.values()})},
            "missing_task_ids": missing, "unresolved_task_ids": unresolved,
            # Unresolved because the latest check of a task without a record was a logged checker incident.
            "checker_incident_task_ids": [task_id for task_id, row in per_task.items() if row["checker_incident"]],
            "incident_log_unreadable_lines": unreadable,
            "records_outside_run": outside, "per_task": per_task, "models": models}


def automation_baseline(directory: Path | None, manifest: dict, result: dict, rows: list[dict],
                        version_policy: Path) -> dict:
    """The analysis.json section; explicit even when no baseline was supplied."""
    if directory is None:
        return dict(NOT_SUPPLIED)
    policy, found, outside = load_baseline(Path(directory), manifest, rows, version_policy)
    return summarize_baseline(policy, found, manifest, result, outside, logged_incidents(Path(directory)))


def write_baseline_csv(report: dict, directory: str | Path) -> dict[str, Path]:
    """Each task's portfolio outcome beside every model/condition result."""
    summary = report.get("automation_baseline") or NOT_SUPPLIED
    if not summary.get("supplied"):
        return {}
    rows = []
    for model, model_report in report["models"].items() or [("", {})]:
        for task_id, task in summary["per_task"].items():
            for condition, arm in ((model_report.get("per_task") or {}).get(task_id) or {"": {}}).items():
                rows.append({"model": model, "task_id": task_id, "family_id": task["family_id"],
                             "domain": task["domain"], "portfolio_outcome": task["outcome"],
                             "portfolio_solved": task["portfolio_solved"], "portfolio_solved_by": task["solved_by"],
                             "baseline_validity_status": task["validity_status"], "condition": condition,
                             **{key: arm.get(key) for key in ARM_FIELDS}})
    path = Path(directory) / "automation_baseline.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"automation_baseline": path}


def _rate(value):
    return "undefined" if value is None else f"{value:.1%}"


def _number(value):
    return "undefined" if value is None else f"{value:.4g}"


def baseline_html(report: dict) -> str:
    """Report section; an absent baseline is stated, never implied."""
    summary = report.get("automation_baseline") or NOT_SUPPLIED
    escape = lambda value: html.escape(str(value))
    if not summary.get("supplied"):
        return "<h2>Automation baseline</h2><p class='notice'>" + escape(NOT_SUPPLIED["note"]) + "</p>"
    budget = summary["policy"].get("portfolio_validation_wall_seconds")
    budget = "(validation budget " + ("not recorded" if budget is None else escape(budget) + " s") + ")"
    separate = summary["portfolio_semantics"] == SEPARATE
    body = ["<h2>Automation baseline: trusted tactic portfolio</h2>",
            "<p class='notice'>" + " ".join(map(escape, summary["interpretation"])) + "</p>",
            "<p>Portfolio alternatives, each checked as its own proof <code>by &lt;tactic&gt;</code> in this order until "
            "one succeeds: " + ", ".join("<code>" + escape(tactic) + "</code>" for tactic in summary["portfolio"]) +
            f"; one fixed total budget per task {budget} under the run's environment, configuration and checker engine. "
            "Tasks solved by each: " + ", ".join(f"<code>{escape(tactic)}</code> {count}" for tactic, count in
                                                 summary["solved_by_tactic"].items()) + ".</p>"
            if separate else
            "<p>Legacy portfolio: <code>" + escape(summary["portfolio"]) + "</code>, checked as one proof per task "
            f"{budget} under the run's environment, configuration and checker engine.</p>"]
    if summary["records_outside_run"]:
        body.append(f"<p>{summary['records_outside_run']} baseline records for tasks outside this run were not used.</p>")
    if not summary["complete"]:
        logged, unreadable = len(summary.get("checker_incident_task_ids") or ()), summary.get("incident_log_unreadable_lines")
        body.append(f"<p class='notice'><strong>Baseline incomplete:</strong> {len(summary['missing_task_ids'])} "
                    f"tasks have no baseline record and {len(summary['unresolved_task_ids'])} have unresolved "
                    "checker outcomes" + (f", {logged} of them without a record because their latest check was a checker "
                                          "incident logged in <code>incidents.jsonl</code>; rerunning the baseline command "
                                          "into the same directory checks them again" if logged else "") +
                    ". They are not counted as solved; see analysis.json." +
                    (f" Unreadable lines in <code>incidents.jsonl</code>, whose tasks are not identified: {unreadable}."
                     if unreadable else "") + "</p>")
    body.append("<table><tr><th>Population</th><th>Tasks</th><th>Solved</th><th>Unsolved</th><th>Unresolved</th>"
                "<th>Missing</th><th>Solve rate</th></tr>")
    for label, row in [("All tasks", summary["overall"]), *(("Domain: " + domain, row) for domain, row
                                                             in summary["by_domain"].items())]:
        body.append("<tr><td>" + escape(label) + "</td>" + "".join(f"<td>{row[key]}</td>" for key in
                    ("tasks", "solved", "unsolved", "unresolved", "missing")) + f"<td>{_rate(row['solve_rate'])}</td></tr>")
    body.append("</table>")
    for model, cross in summary["models"].items():
        sizes = next(iter(cross["conditions"].values()), {})
        body.append("<h3>" + escape(model) + ": model outcomes by portfolio result</h3><table><tr><th rowspan='2'>"
                    "Condition</th><th colspan='3'>Success rate, all scheduled attempts</th><th colspan='3'>Mean task "
                    "log2 T0, measurable valid samples</th></tr><tr>" +
                    "".join(f"<th>{LABELS[name]} ({sizes.get(name, {}).get('tasks', 0)} tasks)</th>" for name in GROUPS) +
                    "".join(f"<th>{LABELS[name]}</th>" for name in GROUPS) + "</tr>")
        for condition, cells in cross["conditions"].items():
            body.append("<tr><td>" + escape(condition) + "</td>" +
                        "".join(f"<td>{_rate(cells[name]['success_rate'])}</td>" for name in GROUPS) +
                        "".join(f"<td>{_number(cells[name]['mean_task_log2_T0'])} ({cells[name]['measurable_tasks']} "
                                "measurable tasks)</td>" for name in GROUPS) + "</tr>")
        body.append("</table><table><tr><th>Paired mean log2 T0 change</th>" +
                    "".join(f"<th>{LABELS[name]}</th>" for name in GROUPS) + "</tr>")
        for contrast, cells in cross["contrasts"].items():
            body.append("<tr><td>" + escape(contrast) + "</td>" +
                        "".join(f"<td>{_number(cells[name]['mean_log2_T0_difference'])} "
                                f"({cells[name]['paired_tasks']} paired tasks)</td>" for name in GROUPS) + "</tr>")
        body.append("</table>")
    body.append(f"<details><summary>Per-task portfolio outcomes ({len(summary['per_task'])} tasks)</summary><table>"
                "<tr><th>Task</th><th>Family</th><th>Domain</th><th>Portfolio</th>" +
                ("<th>Validity status of the last alternative attempted</th><th>Attempted alternatives (validity "
                 "status, seconds)</th>" if separate else "<th>Baseline validity status</th>") + "</tr>")
    for task_id, row in summary["per_task"].items():
        attempts = "; ".join(f"{attempt['tactic']}: {attempt['validity_status']} ({attempt['elapsed_seconds']:.1f} s)"
                             for attempt in row["alternatives"] or ())
        incident = row.get("checker_incident")
        body.append("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in
                    (task_id, row["family_id"], row["domain"], row["outcome"],
                     row["validity_status"] or "no record" + (f" (checker incident: {incident})" if incident else ""),
                     *([attempts or "no record"] if separate else []))) + "</tr>")
    body.append("</table></details><p><a href='automation_baseline.csv'>Per-task baseline and model outcome table</a></p>")
    return "".join(body)
