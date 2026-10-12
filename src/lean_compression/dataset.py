"""Task loading and structural checks; structural validity is not proof verification.

Public records deliberately contain no reference proof.  Audit fields distinguish
collected candidates from tasks cleared for a frozen evaluation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

DOMAINS = (
    "algebra_groups", "number_theory", "analysis_inequalities", "sets_logic",
    "linear_algebra_combinatorics",
)
REQUIRED_FIELDS = (
    "task_id", "family_id", "domain", "split", "origin", "stratum", "imports",
    "context", "binders", "proposition", "target_name", "premise_context",
    "forbidden_declarations",
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]*\Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_tasks(path: str | Path) -> list[dict[str, Any]]:
    """Read a JSON array (or {tasks: [...]}) or newline-delimited JSON records."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    if path.suffix == ".json":
        value = json.loads(text)
        if isinstance(value, dict):
            value = value.get("tasks")
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            raise ValueError("JSON manifest must contain a list of task objects")
        return value
    result = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_no}: expected a task object")
        result.append(value)
    return result


def validate_manifest(tasks: Iterable[dict[str, Any]], enforce_quotas: bool = False) -> list[str]:
    """Return every structural error, without claiming Lean/audit readiness.

    With enforce_quotas, check the proposed complete 50/200 design.  A smoke
    subset should be checked without quotas.  Call readiness_errors separately
    before a confirmatory run.
    """
    tasks = list(tasks)
    errors: list[str] = []
    ids: set[str] = set()
    targets: set[str] = set()
    family_splits: dict[str, set[str]] = defaultdict(set)
    signatures: dict[tuple[str, str], str] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            errors.append(f"row {index + 1}: expected a task object")
            continue
        label = str(task.get("task_id", f"row {index + 1}"))
        for field in REQUIRED_FIELDS:
            if field not in task:
                errors.append(f"{label}: missing {field}")
        if any(field not in task for field in REQUIRED_FIELDS):
            continue
        for field in REQUIRED_FIELDS:
            if field in ("imports", "forbidden_declarations"):
                if not isinstance(task[field], list) or not all(isinstance(v, str) for v in task[field]):
                    errors.append(f"{label}: {field} must be a list of strings")
            elif not isinstance(task[field], str):
                errors.append(f"{label}: {field} must be a string")
        if not isinstance(task["task_id"], str):
            continue
        if task["task_id"] in ids:
            errors.append(f"{label}: duplicate task_id")
        ids.add(task["task_id"])
        if not task["task_id"] or not isinstance(task["family_id"], str) or not task["family_id"]:
            errors.append(f"{label}: task_id and family_id must be nonempty")
        if task["domain"] not in DOMAINS:
            errors.append(f"{label}: unknown domain")
        if task["split"] not in ("pilot", "main"):
            errors.append(f"{label}: split must be pilot or main")
        if task["origin"] not in ("library", "fresh"):
            errors.append(f"{label}: origin must be library or fresh")
        if isinstance(task["family_id"], str) and isinstance(task["split"], str):
            family_splits[task["family_id"]].add(task["split"])
        if not isinstance(task["target_name"], str) or not _IDENTIFIER.fullmatch(task["target_name"]):
            errors.append(f"{label}: unsafe target_name")
        elif task["target_name"] in targets:
            errors.append(f"{label}: duplicate target_name")
        else:
            targets.add(task["target_name"])
        if isinstance(task["imports"], list):
            if any(not isinstance(m, str) or not _IDENTIFIER.fullmatch(m) for m in task["imports"]):
                errors.append(f"{label}: invalid module name")
            provenance = task.get("provenance", {})
            if not isinstance(provenance, dict):
                errors.append(f"{label}: provenance must be an object")
                provenance = {}
            source_module = provenance.get("module")
            if source_module and source_module in task["imports"]:
                errors.append(f"{label}: target module is imported")
            specs = task.get("import_specs")
            if specs is not None:
                if not isinstance(specs, list) or not all(isinstance(s, dict) for s in specs):
                    errors.append(f"{label}: import_specs must be a list of objects")
                else:
                    if [s.get("module") for s in specs] != task["imports"]:
                        errors.append(f"{label}: import_specs module order must match imports")
                    for spec in specs:
                        if any(k not in ("module", "import_all", "is_exported", "is_meta") for k in spec):
                            errors.append(f"{label}: import_specs contains an unknown field")
                        for flag in ("import_all", "is_exported", "is_meta"):
                            if flag in spec and not isinstance(spec[flag], bool):
                                errors.append(f"{label}: import_specs {flag} must be a boolean")
        if any(key in task for key in ("proof", "reference_proof", "private_reference")):
            errors.append(f"{label}: private reference proof in public manifest")
        if isinstance(task["proposition"], str) and not task["proposition"].strip():
            errors.append(f"{label}: empty proposition")
        if isinstance(task["binders"], str) and isinstance(task["proposition"], str):
            key = (task["binders"].strip(), task["proposition"].strip())
            if key in signatures:
                errors.append(f"{label}: exact source signature duplicate of {signatures[key]}")
            signatures[key] = label
        hashes = task.get("hashes", {})
        if not isinstance(hashes, dict):
            errors.append(f"{label}: hashes must be an object")
            hashes = {}
        for field in ("context", "premise_context", "binders", "proposition"):
            digest = hashes.get(field)
            if digest and isinstance(task[field], str) and digest != sha256_text(task[field]):
                errors.append(f"{label}: stale {field} hash")
    for family, splits in family_splits.items():
        if len(splits) > 1:
            errors.append(f"family {family}: crosses pilot/main split")
    if enforce_quotas:
        countable = [t for t in tasks if isinstance(t, dict)
                     and isinstance(t.get("split"), str)
                     and isinstance(t.get("domain"), str)
                     and isinstance(t.get("origin"), str)]
        counts = Counter((t.get("split"), t.get("domain")) for t in countable)
        for domain in DOMAINS:
            for split, expected in (("pilot", 10), ("main", 40)):
                if counts[split, domain] != expected:
                    errors.append(f"{split}/{domain}: expected {expected}, found {counts[split, domain]}")
            main_origins = Counter(t.get("origin") for t in countable if t.get("split") == "main" and t.get("domain") == domain)
            for origin, expected in (("library", 30), ("fresh", 10)):
                if main_origins[origin] != expected:
                    errors.append(f"main/{domain}/{origin}: expected {expected}, found {main_origins[origin]}")
        if len(tasks) != 250:
            errors.append(f"complete manifest: expected 250 tasks, found {len(tasks)}")
    return errors


def readiness_errors(tasks: Iterable[dict[str, Any]]) -> list[str]:
    """Fail closed for the preregistered run; collection and smoke have lower gates."""
    result = []
    for task in tasks:
        audit = task.get("audit", {})
        for field in ("reference_verified", "explicit_signature_frozen", "answer_access_audited", "family_reviewed", "metric_closure_verified"):
            if audit.get(field) is not True:
                result.append(f"{task.get('task_id', '?')}: {field} is not true")
    return result


def validate_references(tasks: Iterable[dict[str, Any]], references: Iterable[dict[str, Any]]) -> list[str]:
    """Check one nonempty private proof per task; Lean checking is separate."""
    task_ids = {t["task_id"] for t in tasks}
    refs = list(references)
    counts = Counter(r.get("task_id") for r in refs)
    errors = []
    for task_id in sorted(task_ids):
        if counts[task_id] != 1:
            errors.append(f"{task_id}: expected one private reference, found {counts[task_id]}")
    for ref in refs:
        if ref.get("task_id") not in task_ids:
            errors.append(f"{ref.get('task_id')}: orphan private reference")
        if not isinstance(ref.get("proof"), str) or not ref["proof"].strip():
            errors.append(f"{ref.get('task_id')}: empty private reference")
    return errors
