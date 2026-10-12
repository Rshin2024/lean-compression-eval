"""Frozen qualitative review samples and condition-blinded proof evidence.

This is an evidence packet, not a subjective score or an exclusion mechanism.
The unblinding key is deliberately outside the reviewer-facing directory, as is
the random per-bundle secret that keys every task and proof code.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import hmac
import html
import json
from pathlib import Path
import random
import re
import secrets

from .io import digest, read_json, write_json
from .store import with_graph

MECHANISMS = ("ordinary brevity", "useful existing-lemma reuse", "local factoring",
              "automation", "irrelevant dependency growth", "suspected answer access")
KEY_FILE = "private/code_key.json"
CODE_SCHEME = "hmac-sha256(key, run_hash|seed|kind|identity)[:16]"


def _opaque_id(kind: str, run_hash: str, identity: str, seed: int, key: bytes | None = None) -> str:
    # Run hash, seed and attempt IDs are public; only the private bundle key blinds codes.
    if not key:
        raise ValueError("Blinded audit codes require the bundle's private code key")
    return kind + "-" + hmac.new(key, f"{run_hash}|{seed}|{kind}|{identity}".encode(), hashlib.sha256).hexdigest()[:16]


def _code_key(directory: Path) -> bytes:
    """Reuse the bundle's random code key; create one only for a new bundle."""
    path = directory / KEY_FILE
    if not path.exists():
        if any((directory / name).exists() for name in ("audit.json", "review", "private")):
            raise FileExistsError("Audit bundle has no private code key; write a new directory: " + str(directory))
        (directory / "private").mkdir(mode=0o700, parents=True)
        write_json(path, {"scheme": CODE_SCHEME, "key": secrets.token_hex(32)})
    value = read_json(path)
    if not (isinstance(value, dict) and value.get("scheme") == CODE_SCHEME and isinstance(value.get("key"), str)
            and re.fullmatch("[0-9a-f]{64}", value["key"])):
        raise ValueError("Unrecognized audit code key: " + str(path))
    return bytes.fromhex(value["key"])


def _import_lines(task: dict) -> str:
    # Mirrors prompts.messages(): target.lean shows the import lines the model saw.
    if task.get("import_specs"):
        return "\n".join(("public " if spec.get("is_exported") else "") +
                         ("meta " if spec.get("is_meta") else "") + "import " +
                         ("all " if spec.get("import_all") else "") + spec["module"]
                         for spec in task["import_specs"])
    return "\n".join("import " + name for name in task.get("imports", []))


def _write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != text.encode("utf-8"):
            raise FileExistsError("Refusing to overwrite differing audit evidence: " + str(path))
        return
    with path.open("xb") as handle:
        handle.write(text.encode("utf-8"))


def write_audit_bundle(manifest: dict, rows: list[dict], result: dict,
                       path: str | Path, run: str | Path | None = None) -> dict:
    """Package selected tasks with every available condition/sample outcome.

    Selection comes from paired task effects: the ten largest strictly positive
    and ten most negative changes, plus twenty uniformly sampled paired tasks
    per model/contrast, using the frozen audit seed. Tasks selected more than
    once are reviewed once, while all selection reasons remain recorded.
    Rows read without graphs reload them, one exported attempt at a time, from
    the run directory ``run``.
    """
    directory = Path(path)
    config = manifest.get("config", {})
    settings = {**config, **config.get("analysis", {})}
    seed = settings.get("audit_seed", settings.get("bootstrap_seed", 20261011))
    run_hash = manifest.get("run_hash", digest(manifest))
    tasks = {task["task_id"]: task for task in manifest["tasks"]}
    selected = defaultdict(list)
    selection_by_contrast = {}
    for model, model_result in sorted(result["models"].items()):
        paired = model_result.get("paired_tasks", [])
        for contrast in sorted({row["contrast"] for row in paired}):
            ordered = sorted((row for row in paired if row["contrast"] == contrast),
                             key=lambda row: (row["mean_log2_T0_difference"], row["task_id"]))
            positive = [row["task_id"] for row in reversed(ordered)
                        if row["mean_log2_T0_difference"] > 0][:10]
            negative = [row["task_id"] for row in ordered
                        if row["mean_log2_T0_difference"] < 0][:10]
            paired_ids = sorted(row["task_id"] for row in ordered)
            # Prefer the analysis's already-published frozen sample. Fallback
            # supports older saved analysis records without regenerating data.
            saved = model_result.get("audit_sample", {}).get(contrast, {}).get("random_paired")
            random_ids = saved if saved is not None else random.Random(
                f"{seed}|{model}|{contrast}").sample(paired_ids, min(20, len(paired_ids)))
            if len(set(random_ids)) != len(random_ids) or not set(random_ids).issubset(paired_ids):
                raise ValueError("Published random audit sample is not a subset of paired tasks")
            groups = {"largest_positive": positive, "largest_negative": negative, "random_paired": random_ids}
            selection_by_contrast[f"{model}/{contrast}"] = groups
            for reason, ids in groups.items():
                for task_id in ids:
                    selected[task_id].append({"model_snapshot": model, "contrast": contrast, "reason": reason})
    unknown = set(selected) - set(tasks)
    if unknown:
        raise ValueError("Audit contains tasks absent from frozen manifest: " + repr(sorted(unknown)))

    rows_by_task, identity_seen = defaultdict(list), set()
    for row in rows:
        identity = (row.get("model_snapshot"), row["task_id"], row.get("condition"), row.get("sample_index"))
        if identity in identity_seen:
            raise ValueError("Duplicate attempt identity in audit: " + repr(identity))
        identity_seen.add(identity)
        rows_by_task[row["task_id"]].append(row)
    # Materialize any absent scheduled rows from the manifest as explicit gaps.
    for slot in manifest.get("slots", []):
        identity = (slot.get("model_snapshot"), slot["task_id"], slot.get("condition"), slot.get("sample_index"))
        if identity not in identity_seen:
            rows_by_task[slot["task_id"]].append({**{k: v for k, v in slot.items() if k != "messages"},
                "validity_status": "missing_record", "metric_status": "unavailable", "metrics": None})
            identity_seen.add(identity)

    code_key = _code_key(directory)
    # Check an existing bundle before writing: a replaced key, run hash or seed would otherwise add review/ directories.
    labels_file = directory / "private" / "labels.json"
    for code, label in (read_json(labels_file) if labels_file.exists() else {}).items():
        if (code, label["task_code"]) != (_opaque_id("proof", run_hash, label["attempt_id"], seed, code_key),
                                          _opaque_id("task", run_hash, label["task_id"], seed, code_key)):
            raise FileExistsError("Audit codes do not match this bundle's key, run hash and seed: " + str(directory))
    index = {"audit_version": "frozen_blinded_v2", "run_hash": run_hash, "seed": seed,
             "selection_rule": "10 strictly positive, 10 strictly negative, 20 random paired tasks per model/contrast",
             "selection_by_contrast": selection_by_contrast, "selected_tasks": [],
             "mechanism_labels": list(MECHANISMS),
             "policy": "Manual labels explain metrics; they never override scores or remove valid outliers.",
             "blinding_limit": "Model and condition labels are hidden in review/. Proof text and mathematical task are preserved verbatim; they may reveal clues.",
             "unblinding_key": "private/labels.json", "code_key": KEY_FILE,
             "reviewer_scope": "Give reviewers review/ only; audit.json, private/ and the enclosing report reveal models and conditions.",
             "review_index": "review/index.html"}
    labels, review_tasks, annotation_rows = {}, [], []
    for task_id in sorted(selected):
        task = tasks[task_id]
        task_code = _opaque_id("task", run_hash, task_id, seed, code_key)
        task_path = directory / "review" / task_code
        ordered_rows = sorted(rows_by_task[task_id], key=lambda row: _opaque_id(
            "proof", run_hash, row.get("attempt_id", digest({key: row.get(key) for key in
                ("model_snapshot", "task_id", "condition", "sample_index")})), seed, code_key))
        proof_items = []
        # This context is the same frozen context for all condition outputs.
        context = {key: task.get(key) for key in
                   ("imports", "import_specs", "context", "premise_context", "binders", "proposition", "target_name")}
        write_json(task_path / "target.json", context)
        # Lean accepts public/meta import flags only after a `module` header; the import lines stay as prompted.
        target_text = (("module\n\n" if task.get("import_specs") else "") + _import_lines(task) + "\n\n" +
                       task.get("context", "") + "\n\n" +
                       f"theorem {task.get('target_name', 'evaluation_target')} {task.get('binders', '')} :\n"
                       f"  {task.get('proposition', '')} := <PROOF>\n")
        _write_text(task_path / "target.lean", target_text)
        for row in ordered_rows:
            row = with_graph(run, row)
            attempt_identity = row.get("attempt_id", digest({key: row.get(key) for key in
                ("model_snapshot", "task_id", "condition", "sample_index")}))
            code = _opaque_id("proof", run_hash, attempt_identity, seed, code_key)
            generation = row.get("generation") or {}
            # A malformed response's text is the provider's entire reply, which can name the model; only private/ keeps it.
            raw = (None if generation.get("generation_status") == "malformed_response"
                   else generation.get("raw_response", row.get("raw_response")))
            adapted = row.get("adapted_response", raw)
            extraction = row.get("extraction") or {}
            evidence = task_path / code
            links = {}
            if raw is not None:
                _write_text(evidence / "original.txt", raw)
                links["original_response"] = "original.txt"
            if adapted is not None:
                _write_text(evidence / "proof.lean", adapted)
                links["submitted_proof"] = "proof.lean"
            if "nodes" in extraction:
                write_json(evidence / "dependencies.json", {"root": extraction.get("root"),
                                                            "nodes": extraction["nodes"]})
                links["dependency_graph"] = "dependencies.json"
            if "source_tokens" in extraction:
                write_json(evidence / "source_tokens.json", extraction["source_tokens"])
                links["source_token_evidence"] = "source_tokens.json"
            write_json(evidence / "metrics.json", row.get("metrics"))
            links["metrics"] = "metrics.json"
            # Logs retain the full evidence separately; the reviewer-facing
            # summary only exposes checking status and source/graph evidence.
            private_attempt = directory / "private" / "attempts" / f"{code}.json"
            write_json(private_attempt, row)
            labels[code] = {"task_code": task_code, "task_id": task_id,
                            "attempt_id": attempt_identity, "model_snapshot": row.get("model_snapshot"),
                            "condition": row.get("condition"), "sample_index": row.get("sample_index"),
                            "complete_record": f"attempts/{code}.json"}
            item = {"proof_code": code, "validity_status": row.get("validity_status", "missing_status"),
                    "metric_status": row.get("metric_status", "unavailable"),
                    "source_available": raw is not None, "links": links}
            write_json(evidence / "summary.json", item)
            proof_items.append(item)
            annotation_rows.append({"task_code": task_code, "proof_code": code,
                                    "mechanism": "", "reviewer_notes": "", "suspected_harness_issue": ""})
        write_json(task_path / "proofs.json", proof_items)
        review_tasks.append({"task_code": task_code, "proofs": proof_items})
        paired_context = {model: [row for row in model_result.get("paired_tasks", []) if row["task_id"] == task_id]
                          for model, model_result in result["models"].items()}
        index["selected_tasks"].append({"task_id": task_id, "task_code": task_code,
            "family_id": task.get("family_id"), "domain": task.get("domain"), "origin": task.get("origin"),
            "selection_reasons": selected[task_id], "paired_context": paired_context,
            "attempt_count": len(proof_items), "evidence_directory": f"review/{task_code}"})
    write_json(directory / "private" / "labels.json", labels)
    write_json(directory / "audit.json", index)
    # This CSV is intentionally blank. Reviewers should make an annotated copy.
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, ["task_code", "proof_code", "mechanism", "reviewer_notes", "suspected_harness_issue"])
    writer.writeheader()
    writer.writerows(annotation_rows)
    _write_text(directory / "review" / "annotations.csv", buffer.getvalue())
    page = ["<!doctype html><html><meta charset='utf-8'><title>Blinded proof audit</title>",
            "<style>body{max-width:1000px;margin:3rem auto;padding:0 1rem;font:16px system-ui;color:#17212b}"
            "article{padding:1rem;background:#f0f4f6;margin:1rem 0}pre{white-space:pre-wrap}"
            ".notice{background:#fff4d8;padding:1rem}a{color:#145a88}li{margin:.5rem 0}</style>",
            "<h1>Blinded proof audit</h1><p class='notice'>Manual review explains measurements; it does not override scores "
            "or remove valid outliers. Model and condition labels are in the separate private unblinding key.</p>",
            "<p>Read all available proofs for each selected task. Record an apparent mechanism and notes in a copy of "
            "<a href='annotations.csv'>annotations.csv</a>. Mechanisms: " + html.escape(", ".join(MECHANISMS)) + ".</p>"]
    if result.get("run_provenance", {}).get("label"):
        page.append("<p class='notice'>" + html.escape(result["run_provenance"]["label"]) + "</p>")
    for task_item in review_tasks:
        task_code = task_item["task_code"]
        page.append(f"<h2>{task_code}</h2><p><a href='{task_code}/target.lean'>Target template</a> · "
                    f"<a href='{task_code}/target.json'>Fixed context and import flags</a></p>")
        for proof in task_item["proofs"]:
            code = proof["proof_code"]
            page.append(f"<article><h3>{code}</h3><p>Validity: {html.escape(proof['validity_status'])}; "
                        f"metrics: {html.escape(proof['metric_status'])}.</p><ul>")
            for name, filename in proof["links"].items():
                page.append(f"<li><a href='{task_code}/{code}/{filename}'>{html.escape(name.replace('_', ' '))}</a></li>")
            if not proof["source_available"]:
                page.append("<li>Original response unavailable for this scheduled outcome.</li>")
            page.append("</ul></article>")
    page.append("</html>")
    _write_text(directory / "review" / "index.html", "\n".join(page))
    return {"selected_tasks": len(index["selected_tasks"]), "attempts": len(annotation_rows),
            "audit_manifest": str(directory / "audit.json"),
            "blinded_review": str(directory / "review" / "index.html")}
