"""Inspectable token-cost estimates for all scheduled slots, never invoice totals."""
from __future__ import annotations

from collections import defaultdict
import csv
import html
import json
from pathlib import Path

from .costs import estimate_cost, summarize_costs, validate_pricing
from .io import digest


LIMITATIONS = [
    "API token estimates use reported usage and the supplied frozen price schedule; they are not invoices.",
    "Each scheduled slot includes at most one recorded generation. Additional retries, provider errors, "
    "and unresolved transport calls are excluded unless accounted for separately; their costs are unknown, not zero.",
    "Local and cloud verification compute, other tools, taxes, credits, discounts and billing adjustments are excluded.",
    "Failed, refused and truncated generations are included when their recorded usage can be priced.",
    "Missing usage or pricing is unavailable. Observed subtotals cover priced records only; "
    "a complete scheduled total requires every scheduled generation to be recorded and priced.",
    "Output tokens already include reasoning or thinking; those subsets are never added a second time.",
]
_ESTIMATE_FIELDS = (
    "status", "currency", "total_usd", "components", "pricing_hash", "usage_hash", "pricing_as_of",
    "provider", "model", "service_tier", "region", "input_total_tokens", "long_context_applied",
    "long_context_threshold_input_tokens", "assumptions", "reasons", "reason",
)
_SUMMARY_FIELDS = (
    "status", "currency", "scheduled_attempts", "generated_attempts", "not_generated_attempts",
    "priced_attempts", "unavailable_generated_attempts", "complete", "total_usd", "generated_total_usd",
    "observed_sum_usd", "observed_sum_scope", "pricing_hash", "pricing_as_of", "reason_counts", "assumption_counts",
)
_COMPONENTS = ("input", "cache_read", "cache_write", "cache_write_1h", "output")


def _optional_text(value, field):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"cost record {field} must be a string or null")
    return value


def build_cost_report(records: list[dict], pricing: dict | None, *, provider=None, model=None) -> dict:
    """Price every supplied scheduled row, including failed and missing generations.

    Rows identify ``task_id``, ``model_snapshot`` (or ``model``), ``condition`` and
    ``sample_index``, and may contain ``attempt_id``, ``validity_status`` and a
    ``generation`` dict. Only allowlisted identity and cost evidence is exported;
    proofs, prompts, provider response bodies and arbitrary record metadata are not.
    The price schedule is supplied by the caller; this function never looks it up.
    """
    normalized, attempts, identities, attempt_ids = [], [], set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("cost report records must be objects")
        row = {
            "attempt_id": _optional_text(record.get("attempt_id"), "attempt_id"),
            "task_id": record.get("task_id"),
            "model_snapshot": record.get("model_snapshot") or record.get("model") or model,
            "condition": record.get("condition"),
            "sample_index": record.get("sample_index"),
            "validity_status": _optional_text(record.get("validity_status"), "validity_status"),
            "provider": _optional_text(record.get("provider") or provider, "provider"),
        }
        for field in ("task_id", "model_snapshot", "condition"):
            if not isinstance(row[field], str) or not row[field]:
                raise ValueError(f"cost report rows require {field}")
        if type(row["sample_index"]) is not int or row["sample_index"] < 0:
            raise ValueError("cost report rows require a nonnegative sample_index")
        identity = tuple(row[k] for k in ("model_snapshot", "task_id", "condition", "sample_index"))
        if identity in identities or (row["attempt_id"] and row["attempt_id"] in attempt_ids):
            raise ValueError("duplicate scheduled identity in cost report")
        identities.add(identity)
        if row["attempt_id"]:
            attempt_ids.add(row["attempt_id"])
        generation = record.get("generation")
        normalized.append({**row, "generation": generation})
        result = estimate_cost(generation, pricing, provider=row["provider"], model=row["model_snapshot"])
        generation_status = (generation.get("generation_status") if isinstance(generation, dict)
                             else None)
        attempts.append({**row, "provider": result.get("provider") or row["provider"],
                         "generated": generation is not None,
                         "generation_status": _optional_text(generation_status, "generation_status"),
                         "cost": {key: result.get(key) for key in _ESTIMATE_FIELDS}})

    def summarize(rows):
        return summarize_costs(rows, pricing, provider=provider, model=model)

    def grouped(*dimensions):
        groups = defaultdict(list)
        for row in normalized:
            groups[tuple(row[key] for key in dimensions)].append(row)
        return [{**dict(zip(dimensions, identity)), "summary": summarize(rows)}
                for identity, rows in sorted(groups.items())]

    try:
        pricing_hash = digest(pricing) if isinstance(pricing, dict) else None
    except (ValueError, TypeError, OverflowError, RecursionError):
        pricing_hash = None
    pricing_evidence = None
    if isinstance(pricing, dict):
        try:
            validate_pricing(pricing, provider or pricing.get("provider"), pricing.get("model"))
        except ValueError:
            pass
        else:
            pricing_evidence = {key: pricing.get(key) for key in (
                "schema_version", "provider", "model", "currency", "as_of", "service_tier", "region",
                "rates_per_million", "long_context", "sources")}
            pricing_evidence = json.loads(json.dumps(pricing_evidence, allow_nan=False))
    summary = summarize(normalized)
    return {
        "schema_version": 1, "scope": "scheduled_generation_token_estimates", "currency": "USD",
        "pricing_hash": pricing_hash,
        "pricing_as_of": pricing_evidence["as_of"] if pricing_evidence else None,
        "pricing_schedule": pricing_evidence,
        "summary": summary, "attempts": attempts,
        "by_question": grouped("task_id"), "by_model": grouped("model_snapshot"),
        "by_condition": grouped("condition"),
        "by_question_model_condition": grouped("task_id", "model_snapshot", "condition"),
        "limitations": LIMITATIONS.copy(),
    }


def _csv_value(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    # Keep labels safe when a CSV is opened in a spreadsheet application.
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        value = "'" + value
    return value


def _attempt_csv_rows(report):
    for attempt in report["attempts"]:
        cost = attempt["cost"]
        row = {key: attempt.get(key) for key in (
            "attempt_id", "task_id", "model_snapshot", "condition", "sample_index",
            "validity_status", "generation_status", "generated", "provider")}
        row.update({"cost_status": cost.get("status"), **{key: cost.get(key) for key in (
            "currency", "total_usd", "pricing_hash", "usage_hash", "pricing_as_of", "service_tier",
            "region", "input_total_tokens", "long_context_applied", "assumptions", "reasons")}})
        for category in _COMPONENTS:
            item = (cost.get("components") or {}).get(category) or {}
            label = "uncached_input" if category == "input" else category
            for field in ("tokens", "rate_per_million", "multiplier", "cost_usd"):
                row[f"{label}_{field}"] = item.get(field)
        yield row


def _summary_csv_rows(report):
    groups = [("all", {"summary": report["summary"]})]
    for key in ("by_question", "by_model", "by_condition", "by_question_model_condition"):
        groups.extend((key, item) for item in report[key])
    for group, item in groups:
        yield {"group_type": group, **{key: item.get(key) for key in ("task_id", "model_snapshot", "condition")},
               **{key: item["summary"].get(key) for key in _SUMMARY_FIELDS}}


def write_cost_report(costreport: dict, directory: str | Path) -> dict[str, str]:
    """Export cost evidence only. This never reads model proofs or makes API calls."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {"costs": directory / "costs.json", "cost_attempts": directory / "cost_attempts.csv",
             "cost_summary": directory / "cost_summary.csv"}
    paths["costs"].write_text(json.dumps(costreport, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for key, rows in (("cost_attempts", list(_attempt_csv_rows(costreport))),
                      ("cost_summary", list(_summary_csv_rows(costreport)))):
        # Include headers even if no attempts were scheduled.
        fields = list(rows[0]) if rows else ["attempt_id", "task_id", "model_snapshot", "condition", "sample_index",
                                           "cost_status", "total_usd", "pricing_hash", "usage_hash"]
        with paths[key].open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows({key: _csv_value(value) for key, value in row.items()} for row in rows)
    return {key: str(path) for key, path in paths.items()}


def cost_html(costreport: dict) -> str:
    """A self-contained report section: partial amounts are explicitly subtotals."""
    escape = lambda value: html.escape(str(value))
    money = lambda value: "unavailable" if value is None else "$" + escape(value)
    summary = costreport["summary"]
    body = ["<section id='costs'><h2>Estimated API token costs</h2>",
            "<p class='notice'><strong>Recorded calls only; not an invoice.</strong> "
            "Unknown retry and transport charges, other API calls, and local/cloud verification compute are excluded.</p>",
            "<p>Observed priced subtotal: <strong>" + money(summary.get("observed_sum_usd")) + "</strong>. "
            "Complete scheduled estimate: <strong>" + money(summary.get("total_usd")) + "</strong>. "
            f"Priced generations: {escape(summary['priced_attempts'])} / {escape(summary['generated_attempts'])}; "
            f"scheduled slots: {escape(summary['scheduled_attempts'])}; "
            f"generation records absent: {escape(summary['not_generated_attempts'])}.</p>",
            "<p>Price schedule SHA-256: <code>" + escape(costreport.get("pricing_hash") or "not configured") +
            "</code>; date: " + escape(costreport.get("pricing_as_of") or "unavailable") + ". "
            "Failed and truncated generations are included. Missing usage is never treated as free.</p>"]
    schedule = costreport.get("pricing_schedule") or {}
    if costreport.get("pricing_basis"):
        basis = costreport["pricing_basis"]
        label = {"frozen_run_pricing": "Pricing frozen with the run",
                 "no_frozen_pricing": "No pricing was frozen with this run",
                 "explicit_retrospective_pricing": "Explicit retrospective pricing; these prices were supplied after the run"}.get(basis, basis)
        body.append("<p><strong>Pricing basis:</strong> " + escape(label) + ".</p>")
    if schedule.get("sources"):
        body.append("<p>Frozen pricing sources: " + " · ".join(
            "<a href='" + html.escape(url, quote=True) + "'>" + escape(url) + "</a>"
            for url in schedule["sources"]) + ".</p>")
    transport = costreport.get("transport_coverage")
    if isinstance(transport, dict):
        body.append("<h3>Transport coverage</h3><p>" + escape(transport.get("scope", "")) +
                    "</p><table><tr><th>Historical event</th><th>Count</th></tr>")
        for key, label in (("logged_request_attempts", "Request starts logged"),
                           ("http_error_events", "HTTP error events"),
                           ("request_not_sent_events", "Requests not sent"),
                           ("outcome_unknown_events", "Outcome unknown events"),
                           ("no_model_outcome_events", "No model outcome events"),
                           ("unreadable_log_lines", "Unreadable log lines")):
            value = transport.get(key)
            body.append("<tr><td>" + label + "</td><td>" +
                        escape("unavailable" if value is None else value) + "</td></tr>")
        reconciled = transport.get("invoice_reconciled")
        body.append("</table><p>Invoice reconciled: " +
                    ("yes" if reconciled is True else "no" if reconciled is False else "unavailable") + ".</p>")
    def table(title, items, dimensions):
        content = ["<h3>" + title + "</h3><table><tr>"]
        content.extend("<th>" + escape(label) + "</th>" for _, label in dimensions)
        content.append("<th>Scheduled</th><th>Recorded</th><th>Priced</th><th>Recorded but unpriced</th>"
                       "<th>Observed subtotal (USD)</th><th>Complete scheduled estimate (USD)</th></tr>")
        for item in items:
            values = item["summary"]
            content.append("<tr>" + "".join("<td>" + escape(item[key]) + "</td>" for key, _ in dimensions))
            content.extend("<td>" + escape(values[key]) + "</td>" for key in (
                "scheduled_attempts", "generated_attempts", "priced_attempts", "unavailable_generated_attempts"))
            content.append("<td>" + money(values.get("observed_sum_usd")) + "</td><td>" +
                           money(values.get("total_usd")) + "</td></tr>")
        content.append("</table>")
        return "".join(content)
    body.append(table("By model", costreport["by_model"], [("model_snapshot", "Model")]))
    body.append(table("By prompt condition", costreport["by_condition"], [("condition", "Condition")]))
    body.append("<details><summary>Costs for every question</summary>" +
                table("By question", costreport["by_question"], [("task_id", "Question")]) + "</details>")
    body.append("<details><summary>Coverage, assumptions and scope</summary><ul>" +
                "".join("<li>" + escape(note) + "</li>" for note in costreport["limitations"]) + "</ul>")
    for label, field in (("Unavailable reasons", "reason_counts"), ("Pricing assumptions", "assumption_counts")):
        body.append("<p>" + label + ": " + escape(", ".join(f"{key}: {value}" for key, value in
                    summary.get(field, {}).items()) or "none") + "</p>")
    body.append("</details><p><a href='costs.json'>Cost evidence JSON</a> · "
                "<a href='cost_attempts.csv'>Every scheduled attempt</a> · "
                "<a href='cost_summary.csv'>Question, model and condition totals</a></p></section>")
    return "".join(body)
