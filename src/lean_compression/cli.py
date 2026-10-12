"""Command-line reproduction entry points; no API call occurs unless generate is used."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dataset import load_tasks, readiness_errors, validate_manifest, validate_references
from .environment import doctor, prepare_environment
from .io import amend_reference_identity, append_event, digest, read_json, read_jsonl, reference_matches_task, task_input_hash, write_json
from .store import create_run, load_run, records


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Three-arm Lean compression evaluation")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local tools without revealing credentials")
    q = sub.add_parser("prepare-environment", help="Verify pins and hash source/build artifacts")
    q.add_argument("--package-root", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--metadata-only", action="store_true", help="Development only; cannot freeze a production run")
    q = sub.add_parser("validate-dataset")
    q.add_argument("--tasks", type=Path, required=True)
    q.add_argument("--references", type=Path)
    q.add_argument("--enforce-quotas", action="store_true")
    q.add_argument("--require-ready", action="store_true")
    q = sub.add_parser("readiness", help="Inspect dataset coverage and remaining audit gates without model calls")
    q.add_argument("--tasks", type=Path, required=True)
    q.add_argument("--references", type=Path, action="append", required=True)
    q.add_argument("--original-tasks", type=Path, action="append", default=[], help="Saved input snapshots for explicit identity amendments")
    q.add_argument("--config", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("verify-references", help="Kernel-check and measure private references")
    for name in ("tasks", "references", "environment", "config", "output"):
        q.add_argument(f"--{name}", type=Path, required=True)
    q.add_argument("--engine", type=Path, default=Path("lean/Evaluator.lean"))
    q.add_argument("--limit", type=int)
    q = sub.add_parser("baseline", help="Run the frozen trusted tactic portfolio once per task")
    for name in ("tasks", "environment", "config", "output"):
        q.add_argument(f"--{name}", type=Path, required=True)
    q.add_argument("--engine", type=Path, default=Path("lean/Evaluator.lean"))
    q.add_argument("--limit", type=int)
    q = sub.add_parser("index-closures", help="Create a reusable trusted environment graph/cache from checked references")
    q.add_argument("--references", type=Path, required=True)
    q.add_argument("--environment", type=Path, required=True)
    q.add_argument("--config", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("freeze", help="Freeze prompts, manifest, pins, and all scheduled attempt IDs")
    for name in ("tasks", "environment", "config", "run"):
        q.add_argument(f"--{name}", type=Path, required=True)
    q.add_argument("--split", choices=("pilot", "main"), required=True)
    q.add_argument("--model", required=True)
    q.add_argument("--development", action="store_true")
    q.add_argument("--reference-checks", type=Path, help="Required production evidence from verify-references")
    q = sub.add_parser("export-prompts")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("import-responses")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--responses", type=Path, required=True)
    q = sub.add_parser("generate", help="Make paid model calls using the frozen schedule")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--limit", type=int)
    q = sub.add_parser("evaluate")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--engine", type=Path, default=Path("lean/Evaluator.lean"))
    q.add_argument("--version", default="v1")
    q.add_argument("--limit", type=int)
    q = sub.add_parser("analyze", help="Regenerate report without model calls or Lean execution")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--references", type=Path, help="Directory produced by verify-references")
    q.add_argument("--version", default="v1")
    q.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("rescore", help="Recompute exact metrics from stored graphs in a new version")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--from-version", required=True)
    q.add_argument("--version", required=True)
    return p


def require_valid(tasks: list[dict], *, quotas: bool = False) -> None:
    errors = validate_manifest(tasks, enforce_quotas=quotas)
    if errors:
        raise ValueError("\n".join(errors))


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)


def dispatch(args: argparse.Namespace) -> dict:
    if args.command == "doctor":
        return doctor()
    if args.command == "prepare-environment":
        env = prepare_environment(args.package_root, args.output, full_hash=not args.metadata_only)
        return {"path": str(args.output), "environment_hash": env["environment_hash"], "hash_mode": env["hash_mode"]}
    if args.command == "validate-dataset":
        tasks = load_tasks(args.tasks)
        errors = validate_manifest(tasks, args.enforce_quotas)
        if args.references:
            errors.extend(validate_references(tasks, read_jsonl(args.references)))
        pending = readiness_errors(tasks)
        if args.require_ready:
            errors.extend(pending)
        if errors:
            raise ValueError("\n".join(errors))
        return {"tasks": len(tasks), "structural_status": "valid", "readiness_errors": pending,
                "ready_for_confirmatory_run": not pending}
    if args.command == "readiness":
        from .readiness import build_readiness_report, write_readiness_report
        tasks = load_tasks(args.tasks)
        snapshots = [t for path in args.original_tasks for t in load_tasks(path)]
        by_id = {}
        for task in snapshots:
            by_id.setdefault(task["task_id"], []).append(task)
        evidence, seen = [], set()
        for directory in args.references:
            for path in sorted(directory.glob("*.json")):
                record = read_json(path)
                if not record.get("task_id") or digest(record) in seen:
                    continue
                seen.add(digest(record))
                if not record.get("verification_request_hash"):
                    for original in by_id.get(record["task_id"], []):
                        try:
                            record = amend_reference_identity(record, original)
                            break
                        except ValueError:
                            continue
                evidence.append({**record, "evidence_source_path": str(path.resolve())})
        report = build_readiness_report(tasks, evidence, read_json(args.config))
        write_readiness_report(report, args.output)
        return {"output": str(args.output), "tasks": len(tasks), "reference_records": len(evidence)}
    if args.command in ("verify-references", "baseline"):
        from .runner import evaluate_proof
        from .io import file_hash
        tasks = load_tasks(args.tasks)
        require_valid(tasks)
        env, config = read_json(args.environment), read_json(args.config)
        references = (read_jsonl(args.references) if args.command == "verify-references" else
                      [{"task_id": t["task_id"], "proof": config["tactic_portfolio"]} for t in tasks])
        errors = validate_references(tasks, references)
        if errors:
            raise ValueError("\n".join(errors))
        proofs = {r["task_id"]: r["proof"] for r in references}
        policy = {"tasks_hash": digest(tasks), "references_hash": digest(references),
                  "environment_hash": env["environment_hash"], "config_hash": digest(config),
                  "engine_hash": file_hash(args.engine), "kind": args.command}
        write_json(args.output / "policy.json", policy)
        count = 0
        for task in tasks:
            dest = args.output / f"{digest(task['task_id'])}.json"
            if dest.exists():
                continue
            if args.limit is not None and count >= args.limit:
                break
            value = evaluate_proof(task, proofs[task["task_id"]], env, args.engine, config)
            value["task_id"] = task["task_id"]
            value["record_kind"] = ("trusted_tactic_baseline" if args.command == "baseline" else
                                    "verified_reference" if value["validity_status"] == "success" else "failed_reference")
            value["proof"] = proofs[task["task_id"]]
            write_json(dest, value)
            print(f"{task['task_id']}: {value['validity_status']} / {value['metric_status']}", file=sys.stderr, flush=True)
            count += 1
        all_results = [read_json(p) for p in args.output.glob("*.json") if p.name != "policy.json"]
        return {"new_checks": count, "scheduled_references": len(tasks), "checked": len(all_results),
                "verified": sum(r["validity_status"] == "success" for r in all_results),
                "measurable": sum(r["metric_status"] == "available" for r in all_results),
                "output": str(args.output)}
    if args.command == "index-closures":
        from .graph_cache import index_verified_closures, write_index
        env, config = read_json(args.environment), read_json(args.config)
        checked = [read_json(p) for p in args.references.glob("*.json") if p.name != "policy.json"]
        checked = [r for r in checked if r.get("validity_status") == "success" and r.get("metric_status") == "available"]
        if not checked:
            raise ValueError("No verified measurable reference closures are available")
        index = index_verified_closures(checked, env["environment_hash"], config["environment"]["boundary_policy"])
        write_index(index, args.output)
        return {"output": str(args.output), "verified_reference_closures": len(checked)}
    if args.command == "freeze":
        tasks, config, env = load_tasks(args.tasks), read_json(args.config), read_json(args.environment)
        require_valid(tasks, quotas=not args.development)
        if not args.development:
            errors = readiness_errors(tasks)
            if errors:
                raise ValueError("\n".join(errors))
        refs = None
        if args.reference_checks:
            refs = {r["task_id"]: r for p in args.reference_checks.glob("*.json")
                    if (r := read_json(p)).get("task_id")}
        run = create_run(args.run, tasks, config, env, split=args.split, model=args.model,
                         development=args.development, reference_records=refs)
        return {"run": str(args.run), "run_hash": run["run_hash"], "scheduled_attempts": run["scheduled_attempts"],
                "development": run["development"]}
    if args.command == "export-prompts":
        run = load_run(args.run)
        write_json(args.output, [{key: slot[key] for key in ("attempt_id", "model_snapshot", "messages", "prompt_hash",
                                                           "task_id", "condition", "sample_index")}
                                 for slot in run["slots"]])
        return {"output": str(args.output), "prompts": len(run["slots"])}
    if args.command == "import-responses":
        from .providers import import_responses
        return import_responses(args.run, read_jsonl(args.responses))
    if args.command == "generate":
        from .providers import generate_run
        return generate_run(args.run, limit=args.limit)
    if args.command == "evaluate":
        from .runner import evaluate_run
        return evaluate_run(args.run, args.engine, version=args.version, limit=args.limit)
    if args.command == "analyze":
        from .analysis import analyze, write_report
        manifest = load_run(args.run)
        reference_metrics = {}
        if args.references:
            ref_policy = read_json(args.references / "policy.json")
            eval_policy_path = args.run / "evaluations" / args.version / "policy.json"
            if ref_policy["environment_hash"] != manifest["environment"]["environment_hash"]:
                raise ValueError("Reference environment differs from the evaluated environment")
            if eval_policy_path.exists() and ref_policy["engine_hash"] != read_json(eval_policy_path)["engine_hash"]:
                raise ValueError("Reference measurement engine differs from evaluated outputs")
            if ref_policy["config_hash"] != manifest["config_hash"]:
                raise ValueError("Reference validation/measurement configuration differs from the run")
            for file in args.references.glob("*.json"):
                value = read_json(file)
                if value.get("validity_status") == "success" and value.get("metric_status") == "available":
                    task = next((t for t in manifest["tasks"] if t["task_id"] == value.get("task_id")), None)
                    if task is None:
                        continue
                    if not reference_matches_task(value, task):
                        raise ValueError("Reference target/context does not match its frozen task record")
                    if value.get("record_kind") != "verified_reference":
                        raise ValueError("Attainment anchors must be verified reference proofs, not automation baseline records")
                    if value.get("environment_hash") != manifest["environment"]["environment_hash"]:
                        raise ValueError("Reference record has incompatible environment provenance")
                    reference_metrics[value["task_id"]] = value
        rows = records(args.run, args.version)
        result = analyze(manifest["tasks"], rows, reference_metrics, manifest["config"])
        result["run_provenance"] = {"run_hash": manifest["run_hash"], "development": manifest["development"],
                                    "evaluation_version": args.version, "model_snapshot": manifest["model_snapshot"],
                                    "label": "Development fixture; not LLM evidence" if manifest["model_snapshot"] == "reference_fixture" else "Model evaluation"}
        args.output.mkdir(parents=True, exist_ok=True)
        if (args.output / "analysis.json").exists():
            raise FileExistsError("Report directory already contains an analysis; choose a new output directory")
        write_report(result, args.output)
        write_json(args.output / "all_scheduled_attempts.json", rows)
        from .audit import write_audit_bundle
        write_audit_bundle(manifest, rows, result, args.output / "audit")
        return {"output": str(args.output), "scheduled_attempts": len(rows), "development": manifest["development"]}
    if args.command == "rescore":
        from .metrics import score_graph
        if args.from_version == args.version or any("/" in v or ".." in v for v in (args.from_version, args.version)):
            raise ValueError("Use a new safe evaluation version")
        manifest = load_run(args.run)
        count = 0
        for slot in manifest["slots"]:
            source = args.run / "evaluations" / args.from_version / f"{slot['attempt_id']}.json"
            if not source.exists():
                continue
            value = read_json(source)
            extraction = value.get("extraction", {})
            if value.get("validity_status") == "success" and extraction.get("metric_status") == "available":
                tokens = extraction["source_tokens"]
                value["metrics"] = score_graph(extraction["nodes"], extraction["root"], tokens["W_S"], tokens["W_B"])
                value["metric_status"] = value["metrics"]["metric_status"]
            value["amendment"] = {"source_version": args.from_version, "source_hash": digest(read_json(source)),
                                   "kind": "metric_recomputation_from_stored_graph"}
            write_json(args.run / "evaluations" / args.version / source.name, value)
            count += 1
        append_event(args.run / "amendments.jsonl", {"kind": "rescore", "source": args.from_version,
                                                     "destination": args.version, "records": count})
        return {"rescored_records": count, "version": args.version}
    raise ValueError(f"Unhandled command: {args.command}")
