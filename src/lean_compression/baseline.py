"""The trusted tactic-portfolio baseline: what frozen automation already solves.

This spec anchor is descriptive. It is neither a preregistered decision quantity
nor a matched LLM-compute baseline. Records come from the ``baseline`` command
and must share the run's environment, configuration, portfolio budget, checker
engine and frozen task identities. Missing and unresolved tasks stay visible;
none count as solved.
"""
from __future__ import annotations

from collections import Counter
import csv
import html
from pathlib import Path
from statistics import mean

from .io import digest, read_json, reference_matches_task

# The policy.json fields load_baseline checks; analysis.json reports only these.
POLICY_FIELDS = ("kind", "engine_hash", "environment_hash", "config_hash", "portfolio_validation_wall_seconds")
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
ARM_FIELDS = ("success_rate", "any_sample_success", "valid_count", "measurable_count", "mean_log2_T0")
FIELDS = ("model", "task_id", "family_id", "domain", "portfolio_outcome", "portfolio_solved",
          "baseline_validity_status", "condition", *ARM_FIELDS)


def _read(path: Path):
    try:
        return read_json(path)
    except ValueError as error:
        raise ValueError(f"Malformed JSON in baseline directory: {path}: {error}") from error


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
    if policy.get("portfolio_validation_wall_seconds") != budget:
        raise ValueError("Baseline did not check the portfolio under the frozen tactic_portfolio_total_wall_seconds; "
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
                or record.get("proof") != manifest["config"].get("tactic_portfolio")):
            raise ValueError("Baseline record has incompatible environment, engine or portfolio provenance: " + path.name)
        validation = record.get("validation")
        resources = validation.get("resources") if isinstance(validation, dict) else None
        if not isinstance(resources, dict) or resources.get("wall_seconds_limit") != budget:
            raise ValueError("Baseline record was not checked under the frozen portfolio budget: " + path.name)
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


def _mean(values):
    return mean(values) if values else None


def _tally(ids, per_task):
    counts = Counter(per_task[task_id]["outcome"] for task_id in ids)
    return {"tasks": len(ids), "families": len({per_task[task_id]["family_id"] for task_id in ids}),
            **{outcome: counts[outcome] for outcome in ("solved", "unsolved", "unresolved", "missing")},
            "solve_rate": counts["solved"] / len(ids) if ids else None}


def summarize_baseline(policy: dict, found: dict, manifest: dict, result: dict, outside: int = 0) -> dict:
    """Solve rates over every run task; cross-tabs reuse the analysis's per-task summaries."""
    per_task = {}
    for task in sorted(manifest["tasks"], key=lambda task: task["task_id"]):
        record = found.get(task["task_id"], {})
        status = record.get("validity_status")
        # Only a Lean verdict is a success; a success claim without one is unresolved.
        verdict = (record.get("validation") or {}).get("validity_status")
        outcome = ("missing" if not record else "solved" if status == verdict == "success"
                   else "unsolved" if status in UNSOLVED else "unresolved")
        per_task[task["task_id"]] = {"family_id": task["family_id"], "domain": task["domain"], "outcome": outcome,
                                     "portfolio_solved": {"solved": True, "unsolved": False}.get(outcome),
                                     "validity_status": status, "metric_status": record.get("metric_status"),
                                     "record_digest": digest(record) if record else None}
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
    return {"supplied": True, "status": "complete" if complete else "incomplete", "complete": complete,
            "interpretation": list(INTERPRETATION), "policy": {key: policy[key] for key in POLICY_FIELDS},
            "portfolio": manifest["config"].get("tactic_portfolio"),
            "overall": _tally(list(per_task), per_task),
            "by_domain": {domain: _tally([t for t, row in per_task.items() if row["domain"] == domain], per_task)
                          for domain in sorted({row["domain"] for row in per_task.values()})},
            "missing_task_ids": missing, "unresolved_task_ids": unresolved,
            "records_outside_run": outside, "per_task": per_task, "models": models}


def automation_baseline(directory: Path | None, manifest: dict, result: dict, rows: list[dict],
                        version_policy: Path) -> dict:
    """The analysis.json section; explicit even when no baseline was supplied."""
    if directory is None:
        return dict(NOT_SUPPLIED)
    policy, found, outside = load_baseline(Path(directory), manifest, rows, version_policy)
    return summarize_baseline(policy, found, manifest, result, outside)


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
                             "portfolio_solved": task["portfolio_solved"],
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
    body = ["<h2>Automation baseline: trusted tactic portfolio</h2>",
            "<p class='notice'>" + " ".join(map(escape, summary["interpretation"])) + "</p>",
            "<p>Portfolio: <code>" + escape(summary["portfolio"]) + "</code>, one fixed-budget run per task "
            "(validation budget " + ("not recorded" if budget is None else escape(budget) + " s") + ") "
            "under the run's environment, configuration and checker engine.</p>"]
    if summary["records_outside_run"]:
        body.append(f"<p>{summary['records_outside_run']} baseline records for tasks outside this run were not used.</p>")
    if not summary["complete"]:
        body.append(f"<p class='notice'><strong>Baseline incomplete:</strong> {len(summary['missing_task_ids'])} "
                    f"tasks have no baseline record and {len(summary['unresolved_task_ids'])} have unresolved "
                    "checker outcomes. They are not counted as solved; see analysis.json.</p>")
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
                "<tr><th>Task</th><th>Family</th><th>Domain</th><th>Portfolio</th><th>Baseline validity status</th></tr>")
    for task_id, row in summary["per_task"].items():
        body.append("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in
                    (task_id, row["family_id"], row["domain"], row["outcome"], row["validity_status"] or "no record")) + "</tr>")
    body.append("</table></details><p><a href='automation_baseline.csv'>Per-task baseline and model outcome table</a></p>")
    return "".join(body)
