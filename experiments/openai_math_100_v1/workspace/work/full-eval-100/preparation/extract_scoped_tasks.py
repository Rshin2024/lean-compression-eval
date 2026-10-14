"""Prepare source-scoped OM100 drafts; never run Lean, models, or curator prose.

The lexical scanner is deliberately not represented as a Lean parser. Every
output still requires Lean signature materialization and answer-access auditing.
Compiler prefixes (which include preceding proofs) and reference bodies remain
private. Public interfaces do not contain theorem or lemma proof bodies.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import tempfile


HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
DEFAULT_BUNDLE = BASE / "outputs/lean-compression-eval/data/question_sets/openai_math_100_v1"
VERSION = "om100_source_scoped_draft_v1"
TOOLCHAIN = "leanprover/lean4:v4.34.1"
MATHLIB = "d13f23b723b8a846827a245b89c10fc7d3f11612"
AUDITS = ("reference_verified", "explicit_signature_frozen", "answer_access_audited",
          "family_reviewed", "metric_closure_verified")
PAIRS = {")": "(", "]": "[", "}": "{", "⟩": "⟨", "⦄": "⦃"}
OPEN = set(PAIRS.values())
KEYWORDS = ("theorem", "lemma", "def", "abbrev", "opaque", "instance", "inductive", "structure", "class",
            "namespace", "section", "end", "variable", "include", "omit", "open", "universe", "universes",
            "attribute", "alias", "export", "notation", "infix", "infixl", "infixr", "prefix", "postfix",
            "set_option", "assert_not_exists", "example", "macro", "elab", "syntax", "initialize",
            "simproc", "builtin_simproc", "add_decl_doc", "run_cmd", "import", "module")
MODIFIERS = r"(?:(?:public|protected|private|noncomputable|nonrec|unsafe|partial|local|scoped|meta)\s+)*"
DECL_WORDS = ("theorem", "lemma", "def", "abbrev", "opaque", "instance", "inductive", "structure", "class")
COMMAND = re.compile(r"^(?:[ \t]*(?:@\[|" + MODIFIERS + r"(?:" + "|".join(DECL_WORDS)
                     + r")\b)|" + MODIFIERS + r"(?:" + "|".join(KEYWORDS) + r")\b|#[A-Za-z_]+)", re.M)
DECL = re.compile(r"\A\s*" + MODIFIERS + r"(?P<kind>theorem|lemma|def|abbrev|opaque|instance|inductive|structure|class)\s+")
IMPORT = re.compile(r"^(?P<public>public\s+)?(?P<meta>meta\s+)?import\s+(?P<all>all\s+)?(?P<modules>[^\n]+)", re.M)
MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")
SCOPE_WORDS = {"open", "variable", "include", "omit", "universe", "universes", "notation", "infix", "infixl", "infixr", "prefix", "postfix"}


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


def mask_lean(source: str) -> str:
    """Mask comments/string contents/quoted identifiers, retaining offsets/newlines.

    Nested Lean comments, escaped ordinary strings, raw strings and character
    literals cannot contribute fake declaration keywords or binder delimiters.
    """
    out = list(source)

    def blank(a, b):
        for j in range(a, b):
            if source[j] not in "\r\n":
                out[j] = " "

    i = 0
    while i < len(source):
        if source.startswith("/-", i):
            start, depth = i, 1
            i += 2
            while i < len(source) and depth:
                if source.startswith("/-", i):
                    depth += 1
                    i += 2
                elif source.startswith("-/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise ValueError("unterminated_block_comment")
            blank(start, i)
        elif source.startswith("--", i):
            end = source.find("\n", i)
            end = len(source) if end < 0 else end
            blank(i, end)
            i = end
        elif source[i] == "«":
            end = source.find("»", i + 1)
            if end < 0:
                raise ValueError("unterminated_quoted_identifier")
            blank(i + 1, end)
            i = end + 1
        else:
            raw = re.match(r'r(#+)"', source[i:]) if source[i] == "r" else None
            if raw and (i == 0 or not (source[i - 1].isalnum() or source[i - 1] == "_")):
                start = i
                terminator = '"' + raw.group(1)
                end = source.find(terminator, i + len(raw.group(0)))
                if end < 0:
                    raise ValueError("unterminated_raw_string")
                i = end + len(terminator)
                blank(start, i)
            elif source[i] == '"':
                start = i
                i += 1
                closed = False
                while i < len(source):
                    if source[i] == "\\":
                        i += 2
                    elif source[i] == '"':
                        i += 1
                        closed = True
                        break
                    else:
                        i += 1
                if not closed:
                    raise ValueError("unterminated_string")
                blank(start, i)
            elif source[i] == "'" and (i == 0 or not (source[i - 1].isalnum() or source[i - 1] in "_'")):
                char = re.match(r"'(?:\\(?:u\{[0-9A-Fa-f]+\}|.)|[^'\\\n])'", source[i:])
                if char:
                    blank(i, i + len(char.group(0)))
                    i += len(char.group(0))
                else:
                    i += 1
            else:
                i += 1
    return "".join(out)


def top_tokens(masked: str, token: str) -> list[int]:
    stack, found = [], []
    i = 0
    while i < len(masked):
        ch = masked[i]
        if not stack and masked.startswith(token, i):
            if token != ":" or not masked.startswith((":=", "::"), i):
                found.append(i)
                i += len(token)
                continue
        if ch in OPEN:
            stack.append(ch)
        elif ch in PAIRS:
            if not stack or stack.pop() != PAIRS[ch]:
                raise ValueError(f"unbalanced_delimiter_at:{i}")
        i += 1
    if stack:
        raise ValueError("unclosed_delimiter")
    return found


def interface_end(masked: str) -> int:
    """Find the start of a declaration value without scanning its proof syntax."""
    stack = []
    for i, ch in enumerate(masked):
        if not stack:
            if masked.startswith(":=", i):
                return i
            if masked.startswith("where", i) and (i == 0 or not masked[i - 1].isalnum()) and (i + 5 == len(masked) or not masked[i + 5].isalnum()):
                return i
            if ch == "|" and not masked[masked.rfind("\n", 0, i) + 1:i].strip():
                return i
        if ch in OPEN:
            stack.append(ch)
        elif ch in PAIRS:
            if not stack or stack.pop() != PAIRS[ch]:
                raise ValueError(f"unbalanced_interface_delimiter_at:{i}")
    if stack:
        raise ValueError("unclosed_interface_delimiter")
    return len(masked)


def without_leading_attributes(text: str) -> str:
    masked = mask_lean(text)
    offset = len(masked) - len(masked.lstrip())
    while masked.startswith("@[", offset):
        depth = 0
        end = None
        for i in range(offset + 1, len(masked)):
            if masked[i] == "[":
                depth += 1
            elif masked[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            raise ValueError("unclosed_attribute")
        offset = end + len(masked[end:]) - len(masked[end:].lstrip())
    return text[offset:]


def read_name(text: str, start: int) -> tuple[str, int]:
    """Read a declaration identifier, including quoted components."""
    i = start
    while i < len(text):
        if text[i] == "«":
            end = text.find("»", i + 1)
            if end < 0:
                raise ValueError("unterminated_declaration_name")
            i = end + 1
        elif text[i].isspace() or text[i] in "([{⦃:":
            break
        else:
            i += 1
    if i == start:
        raise ValueError("missing_declaration_name")
    return text[start:i], i


def split_reference(reference: str, expected_statement: str | None = None) -> dict:
    masked = mask_lean(reference)
    match = DECL.match(masked)
    if not match or match.group("kind") not in {"lemma", "theorem"}:
        raise ValueError("target_is_not_a_named_theorem_or_lemma")
    name, name_end = read_name(reference, match.end())
    if expected_statement is not None:
        if not reference.startswith(expected_statement):
            raise ValueError("statement_prefix_mismatch")
        rest = masked[len(expected_statement):]
        proof_separator = len(expected_statement) + len(rest) - len(rest.lstrip())
        if not masked.startswith(":=", proof_separator):
            raise ValueError("statement_not_followed_by_proof_assignment")
        # Use the hash-bound statement boundary; this also handles a `let x :=`
        # inside the proposition without mistaking it for the proof assignment.
        if proof_separator not in top_tokens(masked[:proof_separator + 2], ":="):
            raise ValueError("proof_assignment_not_at_balanced_top_level")
    else:
        assignments = top_tokens(masked, ":=")
        if not assignments:
            raise ValueError("missing_proof_assignment")
        proof_separator = assignments[0]
    colons = top_tokens(masked[name_end:proof_separator], ":")
    if not colons:
        raise ValueError("missing_target_type_colon")
    colon = name_end + colons[0]
    binders = reference[name_end:colon].strip()
    proposition = reference[colon + 1:proof_separator].strip()
    proof = reference[proof_separator + 2:].strip()
    if not proposition or not proof:
        raise ValueError("empty_target_type_or_proof")
    return {"source_kind": match.group("kind"), "source_name": name, "binders": binders,
            "proposition": proposition, "proof": proof,
            "proof_assignment_character": proof_separator}


def imports_and_context(prefix: str) -> tuple[list[dict], str, list[str]]:
    """Remove only source import/module commands, preserving all compiler scopes."""
    masked = mask_lean(prefix)
    specs, removals, diagnostics = [], [], []
    for match in IMPORT.finditer(masked):
        modules = match.group("modules").split()
        if not modules or any(not MODULE_NAME.fullmatch(name) for name in modules):
            raise ValueError("unsupported_import_syntax")
        specs.extend({"module": name, "import_all": bool(match.group("all")),
                      "is_exported": bool(match.group("public")), "is_meta": bool(match.group("meta"))}
                     for name in modules)
        removals.append((match.start(), match.end()))
    for match in re.finditer(r"^module\s*$", masked, re.M):
        removals.append((match.start(), match.end()))
    # Trailing comments are target documentation. A trailing @[...] applies to
    # the target, not its predecessors, and must not become a stray command.
    cut = len(masked.rstrip())
    while cut and masked[:cut].endswith("]"):
        attr = masked.rfind("@[", 0, cut)
        if attr < 0:
            break
        try:
            balanced = top_tokens(masked[attr + 1:cut], "__never__")
        except ValueError:
            break
        # No preceding command after the attribute; nested square brackets are allowed.
        depth, end = 0, None
        for j in range(attr + 1, cut):
            if masked[j] == "[":
                depth += 1
            elif masked[j] == "]":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end != cut:
            break
        cut = len(masked[:attr].rstrip())
        diagnostics.append("target_attribute_removed")
    context = list(prefix[:cut])
    for start, end in removals:
        for i in range(start, min(end, cut)):
            if context[i] not in "\r\n":
                context[i] = " "
    return specs, "".join(context).strip(), diagnostics


def commands(context: str) -> list[dict]:
    masked = mask_lean(context)
    matches = list(COMMAND.finditer(masked))
    out = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(context)
        # Trailing source comments must not become informal proof-plan hints.
        stop = len(masked[:end].rstrip())
        out.append({"start": match.start(), "end": stop, "source": context[match.start():stop],
                    "masked": masked[match.start():stop]})
    return out


def _uncomment(text: str) -> str:
    """Remove comments without erasing string literals used by definitions."""
    masked = mask_lean(text)
    # Public interface blocks conservatively use the masked form. Quoted syntax
    # definitions are omitted instead of publishing a semantically altered body.
    return "\n".join(line.rstrip() for line in masked.split("\n")).strip()


def public_interfaces(context: str, target_statement: str, *, max_bytes=24_000) -> tuple[str, dict]:
    """Bounded, whole-block interfaces; no theorem/lemma/instance proof bodies.

    Pure computational def/abbrev bodies may be shown. Tactic bodies, structure
    literals and definitions using `where` are reduced to their typed header.
    Imported-module interfaces still need a trusted Lean exporter.
    """
    active = [{"kind": "root", "name": "", "commands": []}]
    blocks, omitted, unsupported = [], [], []
    pending_wrapper = []
    for command in commands(context):
        text, masked = command["source"], command["masked"]
        clean = _uncomment(text)
        scope = re.match(r"\s*(?:(?:noncomputable|public)\s+)?(namespace|section|end)\b([^\n]*)", masked)
        if scope:
            kind, argument = scope.group(1), scope.group(2).strip()
            if kind == "end":
                if len(active) > 1:
                    active.pop()
                else:
                    unsupported.append("unmatched_end")
            else:
                active.append({"kind": kind, "name": argument.split()[0] if argument else "", "commands": []})
            continue
        text = without_leading_attributes(text)
        masked = mask_lean(text)
        decl = DECL.match(masked)
        if decl:
            kind = decl.group("kind")
            try:
                name, _ = read_name(text, decl.end())
                cut = interface_end(masked)
                has_assignment = masked.startswith(":=", cut)
                header = _uncomment(text[:cut])
                namespace = ".".join(s["name"] for s in active if s["kind"] == "namespace" and s["name"])
                qualified = name.removeprefix("_root_.") if name.startswith("_root_.") else ".".join(x for x in (namespace, name) if x)
                safe_definition = (kind in {"def", "abbrev"} and has_assignment
                                   and not re.search(r"\b(?:by|where|termination_by|decreasing_by|sorry|admit)\b", masked)
                                   and "⟨" not in masked and "⟩" not in masked
                                   and '"' not in text and "«" not in text)
                structural = (kind in {"structure", "class", "inductive"}
                              and ":=" not in masked and not re.search(r"\b(?:by|sorry|admit)\b", masked))
                body = clean if safe_definition or structural else header
                # Even a type may contain a proof term, e.g. Fin indices with
                # `by omega`. Do not leak or alter it: omit that interface and
                # request a proof-free pretty-printed type from Lean later.
                if re.search(r"\b(?:by|sorry|admit)\b", mask_lean(body)):
                    omitted.append(qualified)
                    unsupported.append(f"interface_contains_embedded_proof:{qualified}")
                    pending_wrapper = []
                    continue
                if not safe_definition and kind in {"def", "abbrev", "opaque", "instance"}:
                    omitted.append(qualified)
                # Source declaration names are informational and not executable prompts.
                parameters = [c for scope in active for c in scope["commands"]
                              if re.match(r"(?:variable|include|omit|universe|universes)\b", c)]
                parameter_note = ("\n-- Source parameters: " + " ; ".join(" ".join(c.split()) for c in parameters)) if parameters else ""
                block = f"-- Available in namespace {namespace or '_root_'}{parameter_note}\n{body}"
                blocks.append({"name": qualified, "short_name": name.split(".")[-1], "kind": kind,
                               "text": block, "source_start": command["start"]})
            except ValueError as exc:
                unsupported.append(f"interface_scan:{command['start']}:{exc}")
            pending_wrapper = []
            continue
        word = re.match(r"\s*(?:(?:local|scoped)\s+)?([A-Za-z_]+)", masked)
        if word and word.group(1) in SCOPE_WORDS:
            if re.search(r"\b(?:by|sorry|admit|theorem|lemma)\b", masked):
                unsupported.append(f"unsafe_scope_interface_omitted:{command['start']}")
                pending_wrapper = []
                continue
            if re.search(r"\bin\s*$", masked):
                pending_wrapper.append(clean)
            else:
                active[-1]["commands"].append(clean)
            continue
        if clean and not clean.startswith("@["):
            unsupported.append(f"omitted_command:{clean.splitlines()[0][:100]}")
    namespaces = [s["name"] for s in active if s["kind"] == "namespace" and s["name"]]
    scope_blocks = ["Source namespace: " + (".".join(namespaces) or "_root_")]
    scope_blocks += [c for s in active for c in s["commands"]]
    if pending_wrapper:
        scope_blocks += ["Target-local source command:\n" + c for c in pending_wrapper]
    intro = ("Local mathematical interfaces from the source preceding the target.\n"
             "Theorem and lemma proof bodies are omitted. Imported declarations are available through the listed imports.\n")
    # Select interfaces using target-statement names and source position only;
    # never inspect the hidden reference proof to choose model-facing premises.
    tokens = set(re.findall(r"[\w']+", target_statement))
    ranked = sorted(enumerate(blocks), key=lambda pair: (
        pair[1]["short_name"] in tokens,
        pair[1]["kind"] in {"def", "abbrev", "structure", "class", "inductive"}, pair[0]), reverse=True)
    mandatory = intro + "\n\n".join(scope_blocks) + "\n\nAvailable preceding declarations:\n"
    if len(mandatory.encode()) > max_bytes:
        raise ValueError("active_scope_context_exceeds_premise_limit")
    remaining = max_bytes - len(mandatory.encode())
    chosen = set()
    for i, block in ranked:
        size = len((block["text"] + "\n\n").encode())
        if size <= remaining:
            chosen.add(i)
            remaining -= size
    selected = [b for i, b in enumerate(blocks) if i in chosen]
    premise = mandatory + "\n\n".join(b["text"] for b in selected)
    return premise, {"available_local_interface_count": len(blocks), "included_local_interface_count": len(selected),
                     "omitted_local_interface_count": len(blocks) - len(selected),
                     "definitions_with_omitted_bodies": omitted, "omitted_or_unsupported_commands": unsupported,
                     "target_local_wrappers": pending_wrapper, "source_namespace": ".".join(namespaces),
                     "premise_bytes": len(premise.encode()), "premise_limit_bytes": max_bytes,
                     "imported_interface_export": "pending", "included_interfaces": [b["name"] for b in selected]}


def extract_one(row: dict, reference: dict, bundle: Path, *, max_premise_bytes=24_000) -> tuple[dict, dict, dict]:
    ident = row["task_id"]
    source_path = bundle / "private/sources" / row["source_path"]
    raw = source_path.read_bytes()
    if sha(raw) != row["source_sha256"]:
        raise ValueError("whole_source_hash_mismatch")
    source = raw.decode("utf-8")
    lines = source.split("\n")
    start, end = row["start_line"] - 1, row["end_line"]
    selected_source = "\n".join(lines[start:end])
    if selected_source != reference["reference_declaration"] or sha(selected_source) != row["reference_declaration_sha256"]:
        raise ValueError("reference_span_hash_mismatch")
    parts = split_reference(selected_source, row["statement"])
    expected_short = row["declaration_name"].split(".")[-1]
    if parts["source_name"].split(".")[-1] != expected_short:
        raise ValueError("declaration_name_mismatch")
    raw_prefix = "\n".join(lines[:start]) + ("\n" if start else "")
    specs, context, context_notes = imports_and_context(raw_prefix)
    # This is the upstream lakefile's package option, not inferred implicit
    # binders. Keep it before all predecessor commands and the target wrapper.
    context = "set_option autoImplicit false\n\n" + context
    premise, interface_info = public_interfaces(context, row["statement"], max_bytes=max_premise_bytes)
    source_module = row["source_path"].removeprefix("lean/").removesuffix(".lean").replace("/", ".")
    if source_module in [s["module"] for s in specs]:
        raise ValueError("target_module_imported")
    task = {"task_id": ident, "family_id": row["family_id"], "domain": row["selection_group"],
            "selection_group": row["selection_group"], "split": "main", "origin": "library",
            "stratum": "unmeasured_selected_intermediate", "imports": [s["module"] for s in specs],
            "import_specs": specs, "context": context, "binders": parts["binders"],
            "proposition": parts["proposition"], "target_name": "_root_.CompressionEval." + ident.replace("-", "_"),
            "premise_context": premise, "forbidden_declarations": [row["declaration_name"]],
            "smoke": False, "record_kind": "source_scoped_task_draft_v1", "model_generation_enabled": False,
            "audit": {name: False for name in AUDITS}, "preparation_status": "awaiting_lean_signature_materialization",
            "environment": {"lean_toolchain": TOOLCHAIN, "mathlib_commit": MATHLIB},
            "provenance": {"kind": VERSION, "module": source_module, "source_path": row["source_path"],
                           "declaration": row["declaration_name"], "source_line": row["start_line"],
                           "source_end_line": row["end_line"], "source_commit": row["source_commit"],
                           "source_sha256": row["source_sha256"], "raw_prefix_sha256": sha(raw_prefix),
                           "raw_prefix_bytes": len(raw_prefix.encode()), "reference_declaration_sha256": row["reference_declaration_sha256"],
                           "source_parent_task_id": row["source_parent_task_id"],
                           "context_policy": "Upstream autoImplicit=false prelude plus exact source predecessor commands minus imports/module and target documentation/attributes; target-local wrappers retained",
                           "premise_policy": "Bounded local definition/interface excerpts; no theorem/lemma proof bodies; imported-interface export pending",
                           "signature_policy": "Written source binders and proposition; implicit section parameters require trusted Lean materialization",
                           "family_assignment": "Supplied result family; dependency and alias audit pending"}}
    task["hashes"] = {key: sha(task[key]) for key in ("context", "premise_context", "binders", "proposition")}
    ref = {"task_id": ident, "proof": parts["proof"], "proof_sha256": sha(parts["proof"]),
           "verification_status": "not_checked", "source_declaration": row["declaration_name"],
           "reference_declaration_sha256": row["reference_declaration_sha256"]}
    diag = {"task_id": ident, "status": "source_scoped_draft_created", "source_hash_verified": True,
            "reference_span_verified": True, "source_prefix_bytes": len(raw_prefix.encode()),
            "compiler_context_bytes": len(context.encode()), "explicit_binders_materialized": False,
            "target_body_removed_from_context": True, "later_source_commands_excluded": True,
            "imported_target_alias_audit": "pending", "context_notes": context_notes,
            "source_kind": parts["source_kind"], "source_written_name": parts["source_name"], **interface_info}
    return task, ref, {**diag, "_raw_prefix": raw_prefix}


def _write(path: Path, data: str, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    temp.chmod(0o600 if private else 0o644)
    temp.replace(path)


def _json(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def _jsonl(rows):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for r in rows)


def prepare(bundle: Path, output: Path, *, max_premise_bytes=24_000) -> dict:
    output = output.resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        raise ValueError("Output must be a new preparation subdirectory")
    selections = read_jsonl(bundle / "selection.jsonl")
    references = {r["task_id"]: r for r in read_jsonl(bundle / "private/references.jsonl")}
    expected = [f"OM100-{i:03d}" for i in range(1, 101)]
    if [r["task_id"] for r in selections] != expected or set(references) != set(expected):
        raise ValueError("Selected IDs must be exactly OM100-001 through OM100-100")
    tasks, refs, public, diagnostics = [], [], [], []
    for row in selections:
        ident = row["task_id"]
        try:
            task, ref, diag = extract_one(row, references[ident], bundle, max_premise_bytes=max_premise_bytes)
            raw_prefix = diag.pop("_raw_prefix")
            tasks.append(task)
            refs.append(ref)
            context_path = f"private/compiler_context/{ident}.lean"
            _write(output / context_path, task["context"] + "\n", private=True)
            _write(output / f"private/source_prefix/{ident}.lean", raw_prefix, private=True)
            public.append({**task, "context": "", "compiler_context_ref": context_path,
                           "record_kind": "public_question_draft_v1",
                           "preparation_status": "unready_public_interface_only",
                           "hashes": {**task["hashes"], "context": sha("")}})
            diagnostics.append(diag)
        except (ValueError, UnicodeError, OSError) as exc:
            diagnostics.append({"task_id": ident, "status": "extraction_failed", "reason": str(exc)})
            public.append({"task_id": ident, "family_id": row["family_id"], "selection_group": row["selection_group"],
                           "record_kind": "public_question_draft_v1", "model_generation_enabled": False,
                           "preparation_status": "extraction_failed", "audit": {name: False for name in AUDITS},
                           "source_path": row["source_path"], "source_commit": row["source_commit"]})
    report = {"schema_version": 1, "preparation_version": VERSION, "selected_questions": len(selections),
              "draft_tasks": len(tasks), "private_reference_bodies": len(refs), "model_generation_enabled": False,
              "lean_checks_performed": 0, "model_calls_performed": 0,
              "counts": dict(Counter(d["status"] for d in diagnostics)), "per_question": diagnostics,
              "remaining_gates": ["trusted Lean signature materialization", "reference compilation and kernel replay",
                                  "imported-interface and answer-access audit", "OAI dependency boundary and metric validation",
                                  "matching runtime verification", "family dependency review"]}
    _write(output / "private/scoped_tasks.jsonl", _jsonl(tasks), private=True)
    _write(output / "private/references.jsonl", _jsonl(refs), private=True)
    _write(output / "public/questions.jsonl", _jsonl(public))
    _write(output / "diagnostics.json", _json(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--output", type=Path, default=HERE / "draft_v1")
    parser.add_argument("--max-premise-bytes", type=int, default=24_000)
    args = parser.parse_args()
    report = prepare(args.bundle, args.output, max_premise_bytes=args.max_premise_bytes)
    print(json.dumps({k: v for k, v in report.items() if k != "per_question"}, indent=2))


if __name__ == "__main__":
    main()
