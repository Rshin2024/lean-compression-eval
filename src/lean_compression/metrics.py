"""Exact weighted dependency expansion for the paper-aligned acyclic profile.

The graph is evidence supplied by a trusted extractor. This module never guesses
whether a missing declaration is primitive and never constructs expanded terms.
"""
from __future__ import annotations

from collections import deque
import math
from typing import Any, Mapping

PROFILE = "paper_aligned_v1"


def exact_int(value: Any, label: str = "count") -> int:
    """Read an exact nonnegative integer, accepting JSON decimal strings."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label} must be an integer or decimal string")
    if isinstance(value, str) and (not value or not value.isascii() or not value.isdigit()):
        raise ValueError(f"{label} must be a nonnegative decimal integer")
    # Chunked parsing also works beyond Python's decimal conversion safety limit.
    if isinstance(value, str):
        number = 0
        for start in range(0, len(value), 9):
            chunk = value[start:start + 9]
            number = number * (10 ** len(chunk)) + int(chunk)
    else:
        number = value
    if number < 0:
        raise ValueError(f"{label} must be nonnegative")
    return number


def decimal(value: int) -> str:
    """Serialize arbitrarily large integers without changing process limits."""
    if value < 0:
        return "-" + decimal(-value)
    chunks = []
    while value >= 1_000_000_000:
        value, remainder = divmod(value, 1_000_000_000)
        chunks.append(f"{remainder:09d}")
    return str(value) + "".join(reversed(chunks))


def log2_int(value: int) -> float | None:
    """Logarithm from leading bits; no conversion of huge integers to float."""
    if value <= 0:
        return None
    shift = max(0, value.bit_length() - 53)
    return math.log2(value >> shift) + shift


def ratio(numerator: int, denominator: int) -> dict:
    result = {"numerator_decimal": decimal(numerator),
              "denominator_decimal": decimal(denominator), "log2": None}
    if denominator == 0:
        result["reason"] = "zero_denominator"
    elif numerator == 0:
        result["reason"] = "zero_numerator_log_undefined"
    else:
        result["log2"] = log2_int(numerator) - log2_int(denominator)
    return result


def _nodes(graph: Any) -> dict:
    if (isinstance(graph, Mapping) and "nodes" in graph
            and not (isinstance(graph["nodes"], Mapping)
                     and type(graph["nodes"].get("primitive")) is bool)):
        graph = graph["nodes"]
    if isinstance(graph, list):
        result = {}
        for node in graph:
            if not isinstance(node, Mapping) or not isinstance(node.get("name"), str):
                raise ValueError("list graph nodes must have a string name")
            if node["name"] in result:
                raise ValueError(f"duplicate graph node: {node['name']}")
            result[node["name"]] = node
        return result
    if not isinstance(graph, Mapping):
        raise ValueError("graph must be a mapping or list of named nodes")
    return dict(graph)


def score_graph(graph: Any, root: str, W_S: int | str, W_B: int | str) -> dict:
    """Score one source-backed nonprimitive root, with explicit availability.

    Each node requires ``primitive: bool``. Nonprimitives also require a
    ``signature`` histogram and ``body`` histogram or explicit None. A primitive
    is an atomic boundary regardless of its own stored dependencies. Missing
    nodes, malformed evidence, cycles and zero-reference nonprimitives make the
    expansion unavailable without changing independent proof validity.
    """
    result = {"metric_profile": PROFILE, "metric_status": "unavailable",
              "W_S": None, "W_B": None, "U_S_decimal": None,
              "U_B_decimal": None, "U_decimal": None, "D": None,
              "T0": None, "I0": None, "log2_T0": None, "log2_I0": None,
              "log2_U_S": None, "log2_U_B": None, "log2_U": None,
              "log2_W": None, "body_state": None, "reasons": []}

    def unavailable(reason, **details):
        result["reasons"].append({"code": reason, **details})
        result["reason"] = reason
        return result

    try:
        ws, wb = exact_int(W_S, "W_S"), exact_int(W_B, "W_B")
        result.update(W_S=ws, W_B=wb)
        result["I0"] = ratio(wb, ws) if ws else None
        result["log2_I0"] = result["I0"]["log2"] if ws else None
        result["log2_W"] = log2_int(ws + wb)
        if not ws:
            return unavailable("zero_signature_source_count")
        nodes = _nodes(graph)
    except (ValueError, TypeError) as exc:
        return unavailable("invalid_evidence", detail=str(exc))
    if root not in nodes:
        return unavailable("missing_root", node=root)

    # Traverse just the reachable closure. Explicit primitive flags, rather
    # than module/name heuristics, are the frozen boundary-policy evidence.
    reachable, dependencies, histograms, parents = set(), {}, {}, {}
    pending = [root]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        if name not in nodes:
            return unavailable("missing_dependency", node=name)
        node = nodes[name]
        if not isinstance(node, Mapping) or type(node.get("primitive")) is not bool:
            return unavailable("missing_primitive_classification", node=name)
        reachable.add(name)
        if node["primitive"]:
            dependencies[name] = set()
            histograms[name] = ({}, None)
            continue
        if "signature" not in node or "body" not in node:
            return unavailable("missing_histogram", node=name)
        sig, body = node["signature"], node["body"]
        if not isinstance(sig, Mapping) or (body is not None and not isinstance(body, Mapping)):
            return unavailable("invalid_histogram", node=name)
        try:
            sig = {dep: exact_int(count, f"{name} signature multiplicity") for dep, count in sig.items()}
            body = None if body is None else {
                dep: exact_int(count, f"{name} body multiplicity") for dep, count in body.items()}
            if any(not isinstance(dep, str) for dep in set(sig) | set(body or {})):
                raise ValueError("dependency names must be strings")
        except ValueError as exc:
            return unavailable("invalid_histogram", node=name, detail=str(exc))
        deps = {dep for dep, count in sig.items() if count}
        deps.update(dep for dep, count in (body or {}).items() if count)
        dependencies[name], histograms[name] = deps, (sig, body)
        for dep in deps:
            parents.setdefault(dep, []).append(name)
            pending.append(dep)

    result["distinct_dependency_count"] = len(reachable) - 1
    result["reachable_node_count"] = len(reachable)
    result["primitive_count"] = sum(nodes[name]["primitive"] for name in reachable)
    if nodes[root]["primitive"]:
        return unavailable("primitive_root_has_no_signature_body_decomposition")
    result["body_state"] = "absent" if histograms[root][1] is None else "present"
    degrees = {name: len(dependencies[name]) for name in reachable}
    ready = deque(sorted(name for name, degree in degrees.items() if not degree))
    order = []
    while ready:
        name = ready.popleft()
        order.append(name)
        for parent in parents.get(name, []):
            degrees[parent] -= 1
            if degrees[parent] == 0:
                ready.append(parent)
    if len(order) != len(reachable):
        return unavailable("dependency_cycle", blocked_nodes=sorted(set(reachable) - set(order)))

    totals, depths, signature_counts, body_counts = {}, {}, {}, {}
    zero_nodes = []
    for name in order:
        if nodes[name]["primitive"]:
            totals[name], depths[name] = 1, 0
            continue
        sig, body = histograms[name]
        us = sum(count * totals[dep] for dep, count in sig.items() if count)
        ub = None if body is None else sum(count * totals[dep] for dep, count in body.items() if count)
        signature_counts[name], body_counts[name] = us, ub
        totals[name] = us + (ub or 0)
        depths[name] = 1 + max((depths[dep] for dep in dependencies[name]), default=-1)
        if totals[name] == 0:
            zero_nodes.append(name)
    if zero_nodes:
        return unavailable("zero_reference_nonprimitive", nodes=sorted(zero_nodes))
    us, ub, total = signature_counts[root], body_counts[root], totals[root]
    result.update(U_S_decimal=decimal(us), U_B_decimal=None if ub is None else decimal(ub),
                  U_decimal=decimal(total), D=depths[root], log2_U_S=log2_int(us),
                  log2_U_B=None if ub is None else log2_int(ub), log2_U=log2_int(total))
    if ub is None:
        # The total expansion remains informative, but a submitted theorem's
        # absent body cannot be silently interpreted as a measured zero body.
        return unavailable("root_body_absent")
    result["T0"] = ratio(total, ws + wb)
    result["log2_T0"] = result["T0"]["log2"]
    result["metric_status"] = "available"
    return result
