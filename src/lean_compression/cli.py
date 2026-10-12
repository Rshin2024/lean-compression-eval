"""Command-line reproduction entry points; no API call occurs unless generate is used."""
from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import json
import os
import shutil
import socket
import sys
import time
from pathlib import Path

from .dataset import load_tasks, readiness_errors, validate_manifest, validate_references
from .environment import doctor, load_environment, prepare_environment
from .integrity import is_content_digest, portfolio_config, require_implemented_policy, require_reference_directory
from .io import amend_reference_identity, append_event, canonical, digest, measurement_hash, read_json, read_jsonl, reference_matches_task, same_measurement, task_input_hash, write_json
from .outcomes import outcome_class
from .store import create_run, evaluation_directory, load_reference_checks, load_run, logged_outages, records, without_graph


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
    q.add_argument("--references", type=Path, action="append", required=True,
                   help="verify-references output directory (repeatable); validate-dataset takes the references JSONL instead")
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
    q.add_argument("--reference-checks", type=Path,
                   help="verify-references output with a verified record for every task; required in production")
    q.add_argument("--engine", type=Path, default=Path("lean/Evaluator.lean"),
                   help="The Lean engine evaluate must use; with --reference-checks, the one that verified them")
    q.add_argument("--pilot-report", type=Path, help="Production main runs: analyze output of the complete pilot that "
                                                    "preregistration.pilot_report_hash names")
    q = sub.add_parser("export-prompts")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--output", type=Path, required=True)
    q = sub.add_parser("import-responses")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--responses", type=Path, required=True)
    q = sub.add_parser("generate", help="Make paid model calls using the frozen schedule")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--limit", type=int)
    q.add_argument("--retry-rejected", action="store_true",
                   help="Also send slots whose prompt the provider already rejected as invalid_prompt; skipped by default")
    q = sub.add_parser("evaluate")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--engine", type=Path, default=Path("lean/Evaluator.lean"))
    q.add_argument("--version", default="v1")
    q.add_argument("--limit", type=int)
    q.add_argument("--reference-checks", type=Path,
                   help="After a checker or extractor fix: the frozen reference proofs re-checked by this engine")
    q = sub.add_parser("analyze", help="Regenerate report without model calls or Lean execution")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--references", type=Path, help="Directory produced by verify-references")
    q.add_argument("--version", default="v1")
    q.add_argument("--output", type=Path, required=True)
    q.add_argument("--baseline", type=Path, help="Directory produced by the baseline command")
    q = sub.add_parser("rescore", help="Recompute exact metrics from stored graphs in a new version")
    q.add_argument("--run", type=Path, required=True)
    q.add_argument("--from-version", required=True)
    q.add_argument("--version", required=True)
    return p


def require_valid(tasks: list[dict], *, quotas: bool = False) -> None:
    errors = validate_manifest(tasks, enforce_quotas=quotas)
    if errors:
        raise ValueError("\n".join(errors))


def rescored(value: dict) -> dict:
    """Recompute a verified record's metrics from its stored graph with the current scorer.

    Metrics the evaluator withheld, with a metric_unavailable_reason or none at all, stay withheld: older code
    kept the graph of an engine edited during extraction, which may not be the engine the record names.
    """
    from .metrics import score_graph
    extraction = value.get("extraction") or {}
    if value.get("validity_status") != "success" or extraction.get("metric_status") != "available":
        return value
    if value.get("metric_unavailable_reason") is not None or value.get("metrics") is None:
        return value
    tokens = extraction["source_tokens"]
    metrics = score_graph(extraction["nodes"], extraction["root"], tokens["W_S"], tokens["W_B"])
    return {**value, "metrics": metrics, "metric_status": metrics["metric_status"]}


def write_rows(path: Path, rows: list[dict]) -> None:
    """Write rows as one indented JSON array, a row at a time instead of one document string.

    Rows pair conditions with raw responses, so the file is private (0600) like write_json's.
    """
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as out:
        out.write("[")
        for index, row in enumerate(rows):
            text = json.dumps(row, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
            out.write((",\n  " if index else "\n  ") + text.replace("\n", "\n  "))
        out.write("\n]\n" if rows else "]\n")


STAGING_MARKER = ".analyze-staging.json"


def staging_prefix(target: Path) -> str:
    return f".{target.name[:40]}.analyze-"


@contextlib.contextmanager
def staged_report(target: Path):
    """Yield a hidden directory to build a report in, publish it when complete, and remove it on failure.

    A new destination is staged beside it and published with one rename. An existing empty directory is
    never replaced: the report is staged inside it and its entries are moved in.
    """
    parent = target if target.is_dir() else target.parent
    staging = parent / f"{staging_prefix(target)}{os.getpid()}-{time.time_ns()}.partial"
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        # Lets a later analyze recognize this directory as its own if this process is killed.
        write_json(staging / STAGING_MARKER, {"host": socket.gethostname(), "pid": os.getpid(), "target": str(target)})
    except OSError as error:
        if staging.exists():
            shutil.rmtree(staging)
        raise RuntimeError(f"Cannot write a report to {target}: {error}") from None
    try:
        yield staging
        publish(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def publish(staging: Path, target: Path) -> None:
    """Move a complete staged report into place without replacing anything; analysis.json goes last."""
    try:
        if staging.parent != target:
            (staging / STAGING_MARKER).unlink()
            staging.rename(target)
            return
        moved = []
        try:
            for entry in sorted(staging.iterdir(), key=lambda path: (path.name == "analysis.json", path.name)):
                if entry.name == STAGING_MARKER:
                    continue
                destination = target / entry.name
                if entry.is_dir():
                    if os.path.lexists(destination):
                        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
                    entry.rename(destination)
                else:
                    os.link(entry, destination)  # unlike rename, never replaces an existing file
                    entry.unlink()
                moved.append(entry.name)
        except OSError:
            for name in reversed(moved):
                (target / name).rename(staging / name)
            raise
    except OSError as error:
        if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError("Report directory already contains an analysis; choose a new output directory") from None
        raise RuntimeError(f"Cannot publish the report to {target}: {error}") from None


def stale_stagings(target: Path) -> list[Path]:
    """Staging directories of this destination that an analyze on this host left when it was killed."""
    def running(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OverflowError):
            return True
        return True
    stale = []
    for parent in (target.parent, target):
        try:
            entries = [path for path in parent.iterdir() if path.name.startswith(staging_prefix(target))
                       and path.name.endswith(".partial") and path.is_dir() and not path.is_symlink()]
        except OSError:
            continue
        for path in entries:
            try:
                owner = read_json(path / STAGING_MARKER)
            except (OSError, ValueError):
                continue
            pid = owner.get("pid") if isinstance(owner, dict) else None
            if (type(pid) is int and pid > 0 and owner.get("host") == socket.gethostname()
                    and owner.get("target") == str(target) and not running(pid)):
                stale.append(path)
    return stale


def set_digest(files: dict) -> str:
    """digest() of {task_id: record}, reading each record file again so that no graph is kept in memory.

    files maps each task to its record file and that file's SHA-256 when first read; a changed file is refused.
    """
    total = hashlib.sha256(b"{")
    for index, (task_id, (path, sha256)) in enumerate(sorted(files.items())):
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"Reference record changed while it was read: {path}")
        total.update((b"," if index else b"") + canonical(task_id) + b":" + canonical(json.loads(data.decode("utf-8"))))
    total.update(b"}")
    return total.hexdigest()


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        if os.environ.get("LEAN_COMPRESSION_DEBUG") == "1":
            raise
        print(error_message(error), file=sys.stderr)
        raise SystemExit(2)


def error_message(error: Exception) -> str:
    """The stderr text: a KeyError names its field; an OSError's text names its path."""
    if isinstance(error, KeyError):
        field = f" {error.args[0]!r}" if error.args else ""
        return f"Missing required field{field} in an input record; set LEAN_COMPRESSION_DEBUG=1 for a traceback"
    return str(error) or type(error).__name__


def dispatch(args: argparse.Namespace) -> dict:
    if args.command == "doctor":
        return doctor()
    if args.command == "prepare-environment":
        env = prepare_environment(args.package_root, args.output, full_hash=not args.metadata_only)
        return {"path": str(args.output), "environment_hash": env["environment_hash"], "hash_mode": env["hash_mode"]}
    if args.command == "validate-dataset":
        from .dataset import answer_access_errors
        tasks = load_tasks(args.tasks)
        errors = validate_manifest(tasks, args.enforce_quotas)
        pending = readiness_errors(tasks)
        if args.references:
            references = read_jsonl(args.references)
            errors.extend(validate_references(tasks, references))
            pending.extend(answer_access_errors(tasks, references))
        if args.require_ready:
            errors.extend(pending)
        if errors:
            raise ValueError("\n".join(errors))
        return {"tasks": len(tasks), "structural_status": "valid", "readiness_errors": pending,
                "ready_for_confirmatory_run": not pending}
    if args.command == "readiness":
        from .io import file_hash
        from .readiness import build_readiness_report, write_readiness_report
        for directory in args.references:
            if not directory.is_dir():
                raise ValueError(f"readiness --references must be an existing verify-references output directory: {directory}")
        tasks = load_tasks(args.tasks)
        snapshots = [t for path in args.original_tasks for t in load_tasks(path)]
        by_id = {}
        for task in snapshots:
            by_id.setdefault(task["task_id"], []).append(task)
        evidence, seen, ignored = [], set(), []
        for directory in args.references:
            for path in sorted(directory.glob("*.json")):
                record = read_json(path)
                if not isinstance(record, dict) or not isinstance(record.get("task_id", ""), str):
                    ignored.append({"evidence_source_path": str(path.resolve()), "reason": "not_a_task_reference_record"})
                    continue
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
        report = build_readiness_report(tasks, evidence, read_json(args.config), task_file_sha256=file_hash(args.tasks),
                                        ignored_records=ignored)
        write_readiness_report(report, args.output)
        return {"output": str(args.output), "tasks": len(tasks), "reference_records": len(evidence)}
    if args.command in ("verify-references", "baseline"):
        from .runner import evaluate_proof
        from .io import claim, file_hash, remove_abandoned_claim
        from .metrics import SCORER_HASH
        tasks = load_tasks(args.tasks)
        require_valid(tasks)
        env, config = load_environment(args.environment), read_json(args.config)
        references = (read_jsonl(args.references) if args.command == "verify-references" else
                      [{"task_id": t["task_id"], "proof": config["tactic_portfolio"]} for t in tasks])
        errors = validate_references(tasks, references)
        if errors:
            raise ValueError("\n".join(errors))
        require_implemented_policy(config)
        # The record must describe the runtime that runs Lean; the runner defaults to Docker.
        isolation, image = config["validation"].get("isolation", "docker"), config["validation"].get("docker_image")
        if (env.get("runtime_backend") == "docker") != (isolation == "docker"):
            raise ValueError(f"Environment runtime_backend {env.get('runtime_backend')!r} does not match validation.isolation {isolation!r}: "
                             "use a record collected inside the Docker image exactly when Lean runs in Docker")
        if isolation == "docker" and env.get("docker_image") != image:
            raise ValueError(f"Environment docker_image {env.get('docker_image')!r} differs from validation.docker_image "
                             f"{image!r}: use the record collected inside the image that runs Lean")
        if isolation == "docker" and not is_content_digest(image):
            # The runner refuses it too, but only after policy.json is written.
            raise ValueError(f"validation.docker_image {image!r} is not an immutable content digest "
                             "(sha256: and 64 lowercase hex digits, optionally after name@)")
        checker_config = portfolio_config(config) if args.command == "baseline" else config
        proofs = {r["task_id"]: r["proof"] for r in references}
        policy = {"tasks_hash": digest(tasks), "references_hash": digest(references),
                  "environment_hash": env["environment_hash"], "config_hash": digest(config),
                  "measurement_hash": measurement_hash(config), "scorer_hash": SCORER_HASH,
                  "engine_hash": file_hash(args.engine), "kind": args.command}
        if args.command == "baseline":
            policy["portfolio_validation_wall_seconds"] = checker_config["validation"]["wall_seconds"]
        if (args.output / "policy.json").exists() and read_json(args.output / "policy.json") != policy:
            raise FileExistsError(f"{args.output} was checked with other tasks, references, engine, settings or "
                                  "metric code; use a new output directory")
        write_json(args.output / "policy.json", policy)
        count = incidents = 0
        for task in tasks:
            dest = args.output / f"{digest(task['task_id'])}.json"
            if dest.exists():
                # A worker killed after writing this record leaves its claim; remove it unless a process holds it.
                remove_abandoned_claim(dest)
                continue
            if args.limit is not None and count + incidents >= args.limit:
                break
            with claim(dest) as held:
                # Another worker is checking this task, or recorded it after the check above.
                if not held or dest.exists():
                    continue
                value = evaluate_proof(task, proofs[task["task_id"]], env, args.engine, checker_config)
                if value["engine_hash"] != policy["engine_hash"]:
                    raise RuntimeError("Engine changed during this run; use a new output directory")
                value["task_id"] = task["task_id"]
                value["proof"] = proofs[task["task_id"]]
                if outcome_class(value["validity_status"]) == "incomplete":
                    # A checker failure is not the proof's verdict. Keep its evidence and leave the task pending.
                    append_event(args.output / "incidents.jsonl", {"event": "checker_incident", **value})
                    phase = " during extraction" if value.get("failure_phase") == "extraction" else ""
                    print(f"{task['task_id']}: {value['validity_status']}{phase} (checker incident; task left pending)", file=sys.stderr, flush=True)
                    incidents += 1
                    continue
                value["record_kind"] = ("trusted_tactic_baseline" if args.command == "baseline" else
                                        "verified_reference" if value["validity_status"] == "success" else "failed_reference")
                write_json(dest, value)
                print(f"{task['task_id']}: {value['validity_status']} / {value['metric_status']}", file=sys.stderr, flush=True)
                count += 1
        all_results = [read_json(p) for p in args.output.glob("*.json") if p.name != "policy.json"]
        return {"new_checks": count, "scheduled_references": len(tasks), "checked": len(all_results),
                "verified": sum(r["validity_status"] == "success" for r in all_results),
                "measurable": sum(r["metric_status"] == "available" for r in all_results),
                "checker_incidents": incidents, "output": str(args.output)}
    if args.command == "index-closures":
        from .graph_cache import index_verified_closures, write_index
        env, config = load_environment(args.environment), read_json(args.config)
        require_reference_directory(args.references)
        checked = [read_json(p) for p in args.references.glob("*.json") if p.name != "policy.json"]
        checked = [r for r in checked if r.get("validity_status") == "success" and r.get("metric_status") == "available"]
        if not checked:
            raise ValueError("No verified measurable reference closures are available")
        require_implemented_policy(config)
        index = index_verified_closures(checked, env["environment_hash"], config["environment"]["boundary_policy"])
        write_index(index, args.output)
        return {"output": str(args.output), "verified_reference_closures": len(checked)}
    if args.command == "freeze":
        from .integrity import pilot_report
        from .io import file_hash
        tasks, config, env = load_tasks(args.tasks), read_json(args.config), load_environment(args.environment)
        require_valid(tasks, quotas=not args.development)
        if not args.development:
            errors = readiness_errors(tasks)
            if errors:
                raise ValueError("\n".join(errors))
        refs, ref_policy = load_reference_checks(args.reference_checks) if args.reference_checks else (None, None)
        run = create_run(args.run, tasks, config, env, split=args.split, model=args.model,
                         development=args.development, reference_records=refs, reference_policy=ref_policy,
                         engine_hash=file_hash(args.engine),
                         pilot_report=pilot_report(args.pilot_report) if args.pilot_report else None)
        return {"run": str(args.run), "run_hash": run["run_hash"], "scheduled_attempts": run["scheduled_attempts"],
                "development": run["development"], "engine_hash": run["engine_hash"]}
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
        return generate_run(args.run, limit=args.limit, retry_rejected=args.retry_rejected)
    if args.command == "evaluate":
        from .environment import verify_environment
        from .runner import evaluate_run
        # Refuse an edited or stale frozen environment record before any Lean process runs.
        verify_environment(load_run(args.run)["environment"])
        return evaluate_run(args.run, args.engine, version=args.version, limit=args.limit,
                            reference_checks=args.reference_checks)
    if args.command == "analyze":
        from .analysis import analyze, scorer_identity, write_report
        from .metrics import SCORER_HASH
        target = args.output.resolve()
        for stale in stale_stagings(target):
            shutil.rmtree(stale)
            print(f"Removed {stale}, a partial report that a killed analyze left", file=sys.stderr, flush=True)
        if (args.output / "analysis.json").exists():
            raise FileExistsError("Report directory already contains an analysis; choose a new output directory")
        if args.output.exists() and (not args.output.is_dir() or any(args.output.iterdir())):
            raise FileExistsError(f"{args.output} is not an empty directory; choose a new output directory")
        manifest = load_run(args.run)
        # Staged before the analysis, so a destination that cannot take the report fails at once.
        with staged_report(target) as staging:
            # Rows keep every status, metric and resource; dependency graphs stay in the evaluation files.
            rows = records(args.run, args.version, graphs=False)
            # A scoring fix applies to the frozen anchors too: rescore their stored graphs, never swap them.
            rescore_anchors = {scorer_identity(row) for row in rows if row.get("validity_status") == "success"
                               and row.get("metric_status") == "available"} == {SCORER_HASH}
            reference_metrics, anchor_digests, recomputed, ref_policy, binding = {}, {}, {}, None, None
            eval_policy_path = evaluation_directory(args.run, args.version) / "policy.json"
            eval_policy = read_json(eval_policy_path) if eval_policy_path.exists() else {}
            # Tasks whose frozen proof this version's engine rejected or could not measure: no anchor, no substitute.
            unanchored = eval_policy.get("unanchored_reference_tasks") or {}
            if args.references:
                require_reference_directory(args.references)
                ref_policy = read_json(args.references / "policy.json")
                if ref_policy["environment_hash"] != manifest["environment"]["environment_hash"]:
                    raise ValueError("Reference environment differs from the evaluated environment")
                if eval_policy and ref_policy["engine_hash"] != eval_policy["engine_hash"]:
                    raise ValueError("Reference measurement engine differs from evaluated outputs")
                if not same_measurement(ref_policy, manifest):
                    raise ValueError("Reference validation/measurement configuration differs from the run")
                # Freezing binds the anchors: per task in current manifests, as one set in older ones. A version
                # evaluated with --reference-checks after a checker fix is bound to the proofs its engine re-checked.
                amended = eval_policy.get("reference_anchors") is not None
                bound = eval_policy if amended else manifest
                frozen = "bound to this evaluation version" if amended else "frozen with the run"
                if bound.get("reference_policy_hash") not in (None, digest(ref_policy)):
                    raise ValueError(f"Reference policy differs from the one {frozen}")
                anchors, checked = bound.get("reference_anchors"), {}
                legacy = anchors is None and manifest.get("reference_records_hash")
                for file in sorted(args.references.glob("*.json")):
                    data = file.read_bytes()
                    value = json.loads(data.decode("utf-8"))
                    if not value.get("task_id"):
                        continue
                    if legacy:
                        # The set's digest is computed below by reading the file again, so no graph is kept.
                        checked[value["task_id"]] = file, hashlib.sha256(data).hexdigest()
                    task = next((t for t in manifest["tasks"] if t["task_id"] == value["task_id"]), None)
                    if task is None:
                        continue
                    record_digest = digest(value)
                    if amended and task["task_id"] in unanchored:
                        if unanchored[task["task_id"]].get("record_digest") != record_digest:
                            raise ValueError(f"Reference record for {task['task_id']} is not the one {frozen}")
                        continue
                    if anchors is not None and anchors.get(task["task_id"]) != record_digest:
                        raise ValueError(f"Reference record for {task['task_id']} is not the one {frozen}"
                                         if task["task_id"] in anchors else
                                         f"No reference record for {task['task_id']} was frozen with the run; "
                                         "analyze without --references, or freeze a new run with complete reference checks")
                    if rescore_anchors and scorer_identity(value) != SCORER_HASH:
                        # As rescore does for attempts: any verified anchor with an extracted graph, even one the
                        # older code left unavailable.
                        updated = rescored(value)
                        recomputed[task["task_id"]] = updated is not value
                        value = updated
                    if value.get("validity_status") == "success" and value.get("metric_status") == "available":
                        if not reference_matches_task(value, task):
                            raise ValueError("Reference target/context does not match its frozen task record")
                        if value.get("record_kind") != "verified_reference":
                            raise ValueError("Attainment anchors must be verified reference proofs, not automation baseline records")
                        if value.get("environment_hash") != manifest["environment"]["environment_hash"]:
                            raise ValueError("Reference record has incompatible environment provenance")
                        anchor_digests[value["task_id"]] = record_digest
                        # Analysis reads only metrics and provenance; the graph stays in the reference file.
                        reference_metrics[value["task_id"]] = without_graph(value)
                if legacy and set_digest(checked) != manifest["reference_records_hash"]:
                    raise ValueError("Reference records differ from the set frozen with the run")
                binding = ("amended_per_task" if amended else "frozen_per_task" if anchors is not None else
                           "frozen_set" if legacy else "not_frozen")
            result = analyze(manifest["tasks"], rows, reference_metrics, manifest["config"])
            result["run_provenance"] = {"run_hash": manifest["run_hash"], "development": manifest["development"],
                                        # A main freeze checks these when this report is its preregistered pilot report.
                                        "split": manifest["split"], "experiment": manifest["config"]["experiment"],
                                        "protocol_version": manifest["config"]["protocol_version"],
                                        "evaluation_version": args.version, "model_snapshot": manifest["model_snapshot"],
                                        "label": "Development fixture; not LLM evidence" if manifest["model_snapshot"] == "reference_fixture" else "Model evaluation",
                                        "reference_policy_hash": None if ref_policy is None else digest(ref_policy),
                                        "reference_anchor_binding": binding,
                                        "reference_anchor_digests": dict(sorted(anchor_digests.items())),
                                        "unanchored_reference_tasks": dict(sorted(unanchored.items())),
                                        "reference_metrics_recomputed": sorted(t for t, done in recomputed.items() if done),
                                        # The logs that say why slots are still pending.
                                        **logged_outages(args.run, args.version)}
            from .baseline import automation_baseline
            result["automation_baseline"] = automation_baseline(args.baseline, manifest, result, rows,
                                                                evaluation_directory(args.run, args.version) / "policy.json")
            write_report(result, staging)
            write_rows(staging / "all_scheduled_attempts.json", rows)
            from .audit import write_audit_bundle
            write_audit_bundle(manifest, rows, result, staging / "audit", run=args.run)
        return {"output": str(args.output), "scheduled_attempts": len(rows), "development": manifest["development"]}
    if args.command == "rescore":
        from .io import evaluator_hash
        from .metrics import SCORER_HASH
        manifest = load_run(args.run)
        source, destination = evaluation_directory(args.run, args.from_version), evaluation_directory(args.run, args.version)
        if not (source / "policy.json").exists():
            raise ValueError(f"Evaluation version {args.from_version} does not exist or has no policy.json")
        source_policy = read_json(source / "policy.json")
        if source_policy.get("version") != args.from_version or source_policy.get("run_hash") != manifest["run_hash"]:
            raise ValueError(f"{source / 'policy.json'} does not describe version {args.from_version} of this run")
        if destination.exists():
            raise FileExistsError(f"Evaluation version {args.version} already exists; rescore into a new version")
        paths = [path for slot in manifest["slots"] if (path := source / f"{slot['attempt_id']}.json").exists()]
        if not paths:
            raise ValueError(f"Evaluation version {args.from_version} has no records to rescore")
        for path in paths:
            if read_json(path).get("engine_hash") not in (None, source_policy["engine_hash"]):
                raise ValueError(f"{path} was not produced by the engine named in its version policy")
        # The new version records which engine produced its graphs and which code scored them.
        write_json(destination / "policy.json", {
            "kind": "rescore", "version": args.version, "source_version": args.from_version,
            "source_policy_hash": digest(source_policy), "engine_hash": source_policy["engine_hash"],
            "scorer_hash": SCORER_HASH, "evaluator_source_hash": evaluator_hash(),
            "config_hash": manifest["config_hash"], "run_hash": manifest["run_hash"],
            # Graphs from re-checked references' engine keep those references as their anchors, the unanchored
            # tasks, and the record of the frozen engine that engine replaced.
            **{key: source_policy[key] for key in ("reference_policy_hash", "reference_anchors",
                                                   "unanchored_reference_tasks", "frozen_engine_hash")
               if key in source_policy}})
        for path in paths:
            original = read_json(path)
            write_json(destination / path.name, {**rescored(original), "amendment": {
                "source_version": args.from_version, "source_hash": digest(original),
                "kind": "metric_recomputation_from_stored_graph"}})
        append_event(args.run / "amendments.jsonl", {"kind": "rescore", "source": args.from_version,
                                                     "destination": args.version, "records": len(paths),
                                                     "scorer_hash": SCORER_HASH})
        return {"rescored_records": len(paths), "version": args.version}
    raise ValueError(f"Unhandled command: {args.command}")
