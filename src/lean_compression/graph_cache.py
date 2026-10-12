"""Content-addressed imported-environment index from verified closures.

Only imported declarations enter the reusable index. Evaluator-owned context,
candidate roots and auxiliaries stay private to their individual extraction;
a merge takes from the index only the names a candidate's closure imports.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path

from .io import digest, write_json
from .metrics import decimal, exact_int, log2_int

INDEX_VERSION = "verified_imported_closure_index_v3"


def _normalize_nodes(value) -> dict:
    if isinstance(value, list):
        named = []
        for node in value:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                raise ValueError("each listed graph node requires a name")
            named.append((node["name"], node))
    elif isinstance(value, dict):
        named = list(value.items())
    else:
        raise ValueError("nodes must be a mapping or list")
    result = {}
    for name, node in named:
        if name in result:
            raise ValueError("duplicate graph node: " + name)
        if not isinstance(name, str) or not isinstance(node, dict):
            raise ValueError("node names must be strings and nodes must be mappings")
        if node.get("name", name) != name:
            raise ValueError("node name disagrees with mapping key: " + name)
        for field in ("owner_module", "type_hash", "value_hash", "primitive", "signature", "body"):
            if field not in node:
                raise ValueError(f"node {name} lacks {field}")
        if not isinstance(node["owner_module"], str) or not node["owner_module"]:
            raise ValueError("missing module ownership for " + name)
        if type(node["primitive"]) is not bool:
            raise ValueError("primitive classification must be Boolean: " + name)
        if node["type_hash"] is None:
            raise ValueError("missing declaration type hash: " + name)
        if not isinstance(node["signature"], dict) or (node["body"] is not None and not isinstance(node["body"], dict)):
            raise ValueError("invalid reference histograms for " + name)
        histograms = {}
        for field in ("signature", "body"):
            hist = node[field]
            if hist is None:
                histograms[field] = None
                continue
            if any(not isinstance(dependency, str) for dependency in hist):
                raise ValueError("dependency names must be strings: " + name)
            histograms[field] = {dependency: decimal(exact_int(count)) for dependency, count in sorted(hist.items())}
        result[name] = {**node, "name": name, **histograms}
    return result


def _node_key(node: dict, environment_hash: str, policy_hash: str, engine_hash: str) -> str:
    # Lean's Expr hash alone is not a cryptographic identity. Include every
    # weighted reference, both value/type hashes, the boundary flag and the
    # extractor. Ownership is not content: Lean attributes on-demand auxiliaries
    # (e.g. equation lemmas) to whichever imported module first realized them.
    return digest({"environment_hash": environment_hash, "boundary_policy_hash": policy_hash,
                   "engine_hash": engine_hash,
                   **{key: node[key] for key in ("name", "primitive", "type_hash",
                                                "value_hash", "signature", "body")},
                   "histogram_status": node.get("histogram_status"),
                   "body_present": node.get("body_present")})


def _evaluator_owned(node: dict) -> bool:
    owner = node["owner_module"]
    return owner == "EvaluatorCandidate" or owner.startswith("EvaluatorCandidate.")


def _body_state(node):
    if node["primitive"] and node.get("histogram_status") == "atomic_boundary_not_expanded":
        return "unmeasured_atomic"
    return "absent" if node["body"] is None else "present"


def _require_provenance(record: dict, environment_hash: str, policy_hash: str, engine_hash: str):
    if record.get("environment_hash") != environment_hash:
        raise ValueError("record environment_hash does not match the trusted index")
    supplied_hash = record.get("boundary_policy_hash")
    if supplied_hash is None and "boundary_policy" in record:
        supplied_hash = digest(record["boundary_policy"])
    if supplied_hash != policy_hash:
        raise ValueError("record boundary_policy_hash does not match the trusted index")
    if "boundary_policy" in record and digest(record["boundary_policy"]) != policy_hash:
        raise ValueError("record boundary policy and its digest disagree")
    if record.get("engine_hash") != engine_hash:
        raise ValueError("record engine_hash does not match the trusted index")


def _base_metrics(nodes: dict) -> dict:
    """One iterative pass computes each imported node at most once."""
    deps, missing, parents = {}, {}, {}
    for name, node in nodes.items():
        refs = set() if node["primitive"] else {
            dep for histogram in (node["signature"], node["body"] or {})
            for dep, count in histogram.items() if exact_int(count)}
        deps[name] = refs
        missing[name] = sorted(refs - nodes.keys())
        for dependency in refs & nodes.keys():
            parents.setdefault(dependency, []).append(name)
    degree = {name: len(refs & nodes.keys()) for name, refs in deps.items()}
    ready = deque(sorted(name for name, count in degree.items() if not count))
    result, totals, depths = {}, {}, {}
    while ready:
        name = ready.popleft()
        node = nodes[name]
        item = {"metric_status": "unavailable", "U_decimal": None, "U_S_decimal": None,
                "U_B_decimal": None, "D": None, "log2_U": None,
                "body_state": _body_state(node)}
        if node["primitive"]:
            totals[name], depths[name] = 1, 0
            item.update(metric_status="available", U_decimal="1", D=0, log2_U=0.0)
        elif missing[name]:
            item.update(reason="missing_dependency", missing_dependencies=missing[name])
        else:
            unavailable = sorted(dep for dep in deps[name] if result[dep]["metric_status"] != "available")
            if unavailable:
                item.update(reason="dependency_metrics_unavailable", unavailable_dependencies=unavailable)
            else:
                us = sum(exact_int(count) * totals[dep] for dep, count in node["signature"].items() if exact_int(count))
                ub = None if node["body"] is None else sum(
                    exact_int(count) * totals[dep] for dep, count in node["body"].items() if exact_int(count))
                total = us + (ub or 0)
                if total == 0:
                    item.update(reason="zero_reference_nonprimitive")
                else:
                    totals[name], depths[name] = total, 1 + max((depths[dep] for dep in deps[name]), default=-1)
                    item.update(metric_status="available", U_decimal=decimal(total), U_S_decimal=decimal(us),
                                U_B_decimal=None if ub is None else decimal(ub), D=depths[name], log2_U=log2_int(total))
        result[name] = item
        for parent in parents.get(name, []):
            degree[parent] -= 1
            if degree[parent] == 0:
                ready.append(parent)
    for name in nodes.keys() - result.keys():
        result[name] = {"metric_status": "unavailable", "reason": "dependency_cycle",
                        "detail": "Node belongs to or depends on a reachable cycle.",
                        "U_decimal": None, "U_S_decimal": None, "U_B_decimal": None,
                        "D": None, "log2_U": None,
                        "body_state": _body_state(nodes[name])}
    return result


def index_verified_closures(records: list[dict], environment_hash: str,
                            boundary_policy: dict | str, engine_hash: str | None = None) -> dict:
    """Merge imported nodes from matching, independently verified references.

    A reference may have unavailable root metrics (e.g. cycle) as long as its
    proof is verified and its extractor supplied the graph. Such base states
    remain unavailable. No source costs are synthesized for indexed nodes.
    One extractor engine produced every indexed histogram; without an explicit
    engine_hash, the records must agree on one.
    """
    if (not isinstance(environment_hash, str) or not environment_hash
            or not isinstance(boundary_policy, (dict, str)) or not boundary_policy):
        raise ValueError("a nonempty environment hash and explicit boundary policy object or version string are required")
    policy_hash = digest(boundary_policy)
    records = list(records)
    if engine_hash is None:
        engines = {record.get("engine_hash") for record in records}
        engine_hash = engines.pop() if len(engines) == 1 else None
    if not isinstance(engine_hash, str) or not engine_hash:
        raise ValueError("verified closures must share one nonempty extractor engine_hash")
    nodes, keys, owners, sources = {}, {}, {}, []
    excluded = set()
    for record in records:
        if record.get("validity_status") != "success":
            raise ValueError("environment indexing requires a verified reference record")
        _require_provenance(record, environment_hash, policy_hash, engine_hash)
        extraction = record.get("extraction", record)
        if extraction.get("metric_status") != "available" or "nodes" not in extraction:
            raise ValueError("verified reference has no successfully extracted dependency graph")
        for name, node in _normalize_nodes(extraction["nodes"]).items():
            if _evaluator_owned(node):
                excluded.add(name)
                continue
            key = _node_key(node, environment_hash, policy_hash, engine_hash)
            if name in keys and keys[name] != key:
                raise ValueError("conflicting imported declaration in verified closures: " + name)
            nodes[name], keys[name] = node, key
            owners.setdefault(name, set()).add(node["owner_module"])
        sources.append({"task_id": record.get("task_id"), "source_hash": record.get("source_hash"),
                        "engine_hash": record.get("engine_hash"), "graph_hash": digest(extraction["nodes"])})
    # Module ownership depends on each task's imports; keep it as metadata.
    nodes = {name: {**{key: value for key, value in node.items() if key != "owner_module"},
                    "observed_owner_modules": sorted(owners[name])} for name, node in sorted(nodes.items())}
    computed = _base_metrics(nodes)
    cache = {name: {"cache_key": keys[name], "metrics": computed[name]} for name in nodes}
    primitives = sorted(name for name, node in nodes.items() if node["primitive"])
    index = {"index_version": INDEX_VERSION, "environment_hash": environment_hash,
             "boundary_policy": boundary_policy, "boundary_policy_hash": policy_hash,
             "engine_hash": engine_hash, "nodes": nodes, "base_cache": cache,
             "primitive_manifest": primitives, "primitive_manifest_hash": digest(primitives),
             "primitive_manifest_scope": "union of imported reachable reference closures; not a full-corpus manifest",
             "excluded_evaluator_node_names": sorted(excluded), "sources": sorted(sources, key=digest),
             "node_count": len(nodes), "available_node_count": sum(
                 item["metrics"]["metric_status"] == "available" for item in cache.values()),
             "source_cost_policy": "Source counts are absent; no source costs were invented or charged."}
    index["index_hash"] = digest(index)
    return index


def _verify_index(index):
    if index.get("index_version") != INDEX_VERSION:
        raise ValueError("unsupported environment index version")
    if index.get("index_hash") != digest({key: value for key, value in index.items() if key != "index_hash"}):
        raise ValueError("environment index content hash mismatch")
    if index.get("boundary_policy_hash") != digest(index.get("boundary_policy")):
        raise ValueError("environment index boundary policy mismatch")
    if not isinstance(index.get("engine_hash"), str) or not index["engine_hash"]:
        raise ValueError("environment index lacks its extractor engine_hash")


def write_index(index: dict, path: str | Path) -> None:
    """Write one complete immutable JSON index at the supplied file path."""
    _verify_index(index)
    write_json(path, index)


def merge_candidate(index: dict, candidate_nodes: dict) -> dict:
    """Return a merged graph after matching a provenance-bearing envelope.

    The envelope must contain environment_hash, boundary_policy_hash (or the
    exact boundary_policy), engine_hash, nodes and imported_node_names. Nodes
    must include every evaluator-owned declaration of the candidate's closure;
    these stay private, so a task's context may redeclare a name that another
    task imports. Only the listed imported names are taken from the index, and
    supplied imported nodes must match it. Bare node lists intentionally lack
    enough provenance to enter the cache. The immutable index itself is never
    changed.
    """
    _verify_index(index)
    if not isinstance(candidate_nodes, dict) or "nodes" not in candidate_nodes:
        raise ValueError("candidate nodes require an environment/boundary provenance envelope")
    _require_provenance(candidate_nodes, index["environment_hash"], index["boundary_policy_hash"],
                        index["engine_hash"])
    imported = candidate_nodes.get("imported_node_names")
    if not isinstance(imported, list) or any(not isinstance(name, str) for name in imported):
        raise ValueError("candidate envelope requires the imported_node_names of its closure")
    imported = set(imported)
    candidate = _normalize_nodes(candidate_nodes["nodes"])
    merged = {name: index["nodes"][name] for name in sorted(imported & index["nodes"].keys())}
    for name, node in candidate.items():
        if _evaluator_owned(node):
            if name in imported:
                raise ValueError("candidate lists its own declaration as imported: " + name)
        elif name in index["nodes"]:
            key = _node_key(node, index["environment_hash"], index["boundary_policy_hash"], index["engine_hash"])
            if key != index["base_cache"][name]["cache_key"]:
                raise ValueError("candidate conflicts with trusted imported declaration: " + name)
        merged[name] = node
    return merged
