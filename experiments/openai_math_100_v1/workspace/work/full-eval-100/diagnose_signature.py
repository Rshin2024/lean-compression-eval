"""Inspect mismatched type metadata; this helper cannot produce reference anchors."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import materialize as m
import repair_materialization as r

base = Path(__file__).resolve().parent
out = base / "signature-diagnostics"
out.mkdir(mode=0o700, exist_ok=True)
engine = out / "engine/EvaluatorDiagnostic.lean"
source = (m.REPO / "lean/Evaluator.lean").read_text()
old = 'if !(Kernel.isDefEqGuarded comparable {} target.type trusted.type) then return err "target_mismatch" "elaborated target differs from trusted signature"'
new = '''if !(Kernel.isDefEqGuarded comparable {} target.type trusted.type) then return obj [
    ("validity_status", toJson "target_mismatch"), ("metric_status", toJson "unavailable"),
    ("failure_reason", toJson "elaborated target differs from trusted signature"),
    ("diagnostic_only", toJson true),
    ("proved_levels", toJson (target.levelParams.map Name.toString)),
    ("trusted_levels", toJson (trusted.levelParams.map Name.toString)),
    ("proved_type_expression", toJson (toString target.type)),
    ("trusted_type_expression", toJson (toString trusted.type))]'''
assert source.count(old) == 1
m.write_private(engine, source.replace(old, new))
engine.parent.chmod(0o755)
engine.chmod(0o644)
environment = m.load_environment(base / "environment.azure.json")
config = m.read_json(base / "config.preparation.json")
tasks = {t["task_id"]: t for t in m.jsonl(m.DEFAULT_DRAFT / "scoped_tasks.jsonl")}
refs = {t["task_id"]: t for t in m.jsonl(m.DEFAULT_DRAFT / "references.jsonl")}

def check(tid):
    previous = m.read_json(base / "materialized_v1/repairs_v2/records" / tid / "bootstrap.json")["result"]
    before = json.loads(next(line.split("OM100_BOOTSTRAP ", 1)[1] for line in previous["failure_reason"].splitlines()
                             if "OM100_BOOTSTRAP " in line))
    scoped, _ = r.open_source_namespace(tasks[tid])
    candidate, _ = r.materialized_candidate(scoped, before)
    request = {key: candidate[key] for key in ("imports", "import_specs", "context", "binders", "proposition",
                                               "target_name", "forbidden_declarations")}
    request.update(proof=refs[tid]["proof"], max_heartbeats=config["validation"]["max_heartbeats"],
                   permitted_axioms=config["validation"]["permitted_axioms"])
    result = m.execute_engine(request, environment, engine, config["validation"], mode="validate",
                              wall_seconds=config["validation"]["wall_seconds"])
    m.write_private(out / f"{tid}.json", m.json_text({"task_id": tid, "candidate": candidate, "result": result,
                                                    "diagnostic_engine_hash": m.file_hash(engine),
                                                    "not_reference_verification": True}))
    print(tid, result["validity_status"], result.get("proved_levels"), result.get("trusted_levels"), flush=True)

with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(check, ["OM100-048", "OM100-078"]))
