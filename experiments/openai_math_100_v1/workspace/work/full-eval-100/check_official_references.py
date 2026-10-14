"""Run claim-aware reference workers; collect aggregate outcomes and resources."""
import argparse
from collections import Counter
import os
from pathlib import Path
import statistics
import subprocess
import time
import materialize as m


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--input", type=Path, default=m.HERE / "materialized_final_v1")
    parser.add_argument("--output", type=Path, default=m.HERE / "reference-checks-v1")
    parser.add_argument("--logs", type=Path, default=m.HERE / "reference-check-workers-v1")
    args = parser.parse_args()
    if not 1 <= args.workers <= 48:
        parser.error("--workers must be between 1 and 48")
    args.logs.mkdir(mode=0o700, parents=True, exist_ok=True)
    command = [str(m.REPO / "run-eval"), "verify-references", "--tasks", str(args.input / "prepared_tasks.jsonl"),
               "--references", str(args.input / "prepared_references.jsonl"),
               "--environment", str(m.HERE / "environment.azure.json"),
               "--config", str(m.HERE / "config.reference-final.json"),
               "--engine", str(m.REPO / "lean/Evaluator.lean"), "--output", str(args.output), "--limit", "8"]
    started = time.monotonic()
    processes = []
    run_name = str(time.time_ns())
    for index in range(args.workers):
        path = args.logs / f"worker-{run_name}-{index:02}.log"
        with path.open("w") as stream:
            path.chmod(0o600)
            process = subprocess.Popen(command, cwd=m.REPO, stdin=subprocess.DEVNULL,
                                       stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append((process, path))
    exits = [{"pid": process.pid, "returncode": process.wait(), "log": str(path)} for process, path in processes]
    records = []
    for path in args.output.glob("*.json"):
        if path.name == "policy.json":
            continue
        result = m.read_json(path)
        records.append({"task_id": result["task_id"], "validity_status": result["validity_status"],
                        "metric_status": result["metric_status"],
                        "metric_unavailable_reason": result.get("metric_unavailable_reason"),
                        "validation_resources": result.get("validation", {}).get("resources", {}),
                        "extraction_resources": result.get("extraction", {}).get("resources", {})})
    resource_summary = {}
    for phase in ("validation", "extraction"):
        for metric in ("elapsed_seconds", "peak_memory_mib"):
            values = [record[phase + "_resources"].get(metric) for record in records]
            observed = [value for value in values if isinstance(value, (int, float))]
            resource_summary[phase + "_" + metric] = {"observed_count": len(observed), "missing_count": len(values) - len(observed),
                "min": min(observed) if observed else None, "median": statistics.median(observed) if observed else None,
                "max": max(observed) if observed else None, "sum": sum(observed) if observed else None}
    summary = {"completed_at": m.now(), "elapsed_seconds": time.monotonic() - started,
               "workers": args.workers, "scheduled": len(m.jsonl(args.input / "prepared_tasks.jsonl")),
               "checked": len(records), "validity": dict(Counter(record["validity_status"] for record in records)),
               "measurement": dict(Counter(record["metric_status"] for record in records)),
               "worker_exits": exits, "resources": resource_summary, "tasks": sorted(records, key=lambda record: record["task_id"])}
    m.write_private(args.logs / "summary.json", m.json_text(summary))
    print(m.json_text({key: value for key, value in summary.items() if key not in {"tasks", "worker_exits"}}), flush=True)
    return 0 if summary["checked"] == summary["scheduled"] == summary["validity"].get("success") == summary["measurement"].get("available") else 2


if __name__ == "__main__":
    raise SystemExit(main())
