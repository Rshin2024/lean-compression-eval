#!/usr/bin/env python3
"""Prepare only the pinned OAI import closure needed by selected source prefixes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

COMMIT = "adc7f1241b42e322a6451854ab7e4b4c146bf78a"
MATHLIB = "d13f23b723b8a846827a245b89c10fc7d3f11612"


def without_comments(text):
    out, depth, quote, i = [], 0, False, 0
    while i < len(text):
        c, pair = text[i], text[i:i + 2]
        if depth:
            if pair == "/-": depth += 1; out.extend("  "); i += 2; continue
            if pair == "-/": depth -= 1; out.extend("  "); i += 2; continue
            out.append("\n" if c == "\n" else " ")
        elif quote:
            if c == "\\":
                out.extend("  "); i += 2; continue
            if c == '"': quote = False
            out.append("\n" if c == "\n" else " ")
        elif pair == "/-":
            depth = 1; out.extend("  "); i += 2; continue
        elif pair == "--":
            end = text.find("\n", i)
            if end < 0: end = len(text)
            out.extend(" " * (end - i)); i = end; continue
        elif c == '"': quote = True; out.append(" ")
        else: out.append(c)
        i += 1
    if depth or quote:
        raise ValueError("Unclosed comment/string in Lean source")
    return "".join(out)


def imports(text):
    result = []
    atom = r"(?:«[^»]+»|[^\W\d]\w*)"
    module = re.compile(atom + r"(?:\." + atom + r")*")
    for line in without_comments(text).splitlines():
        match = re.match(r"^\s*(?:(?:public|private|meta)\s+)*import\s+(.*)$", line)
        if not match: continue
        value = match[1].strip()
        if value.startswith("all "): value = value[4:].strip()
        while value:
            name = module.match(value)
            if name is None:
                raise ValueError(f"Unparsed import header: {line}")
            result.append(name[0]); value = value[name.end():].strip()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(["git", "-C", str(args.upstream), "rev-parse", "HEAD"], text=True).strip()
    if actual != COMMIT: raise ValueError("Wrong upstream commit")
    if args.output.exists(): raise ValueError("Build context already exists")
    rows = [json.loads(line) for line in args.selection.read_text().split("\n") if line.strip()]
    roots, per_task = set(), {}
    for row in rows:
        raw = (args.upstream / row["source_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["source_sha256"]:
            raise ValueError(f"Selected source changed: {row['task_id']}")
        prefix = "\n".join(raw.decode().splitlines()[:row["start_line"] - 1])
        dependencies = imports(prefix)
        per_task[row["task_id"]] = dependencies
        roots.update(dependencies)
    pending = list(roots)
    files, graph, externals = {}, {}, set()
    while pending:
        name = pending.pop()
        if name in graph or name in externals: continue
        if not name.startswith("OAI."):
            externals.add(name); continue
        path = args.upstream / "lean" / (name.replace(".", "/") + ".lean")
        if not path.is_file(): raise ValueError(f"Missing OAI module: {name}")
        data = path.read_bytes()
        graph[name] = imports(data.decode())
        files[name] = {"source_path": path.relative_to(args.upstream).as_posix(),
                       "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        pending.extend(graph[name])
    known = {"Mathlib", "Lean", "Init", "Std", "Batteries", "Aesop", "Qq", "ProofWidgets",
             "Plausible", "LeanSearchClient", "ImportGraph", "Lake", "Cli"}
    unknown = sorted(name for name in externals if name.split(".")[0] not in known)
    manifest = {"source_commit": COMMIT, "mathlib_commit": MATHLIB, "lean_toolchain": "leanprover/lean4:v4.34.1",
                "selected_task_count": len(rows), "selected_module_count": len({r['source_path'] for r in rows}),
                "direct_import_roots": sorted(roots), "per_task_direct_imports": per_task,
                "oai_module_count": len(graph), "oai_source_bytes": sum(x['bytes'] for x in files.values()),
                "external_imports": sorted(externals), "unknown_external_imports": unknown,
                "files": files, "import_graph": graph}
    if unknown: raise ValueError(f"Additional packages require explicit preparation: {unknown}")
    args.output.mkdir(parents=True)
    package = args.output / "package"
    package.mkdir()
    for name, info in files.items():
        relative = Path(info["source_path"]).relative_to("lean")
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.upstream / info["source_path"], target)
    (package / "lean-toolchain").write_text("leanprover/lean4:v4.34.1\n")
    (package / "lakefile.lean").write_text('import Lake\nopen Lake DSL\npackage om100Runtime where\n  leanOptions := #[⟨`autoImplicit, false⟩]\nrequire mathlib from git "https://github.com/leanprover-community/mathlib4.git" @ "' + MATHLIB + '"\nlean_lib OAI\n@[default_target] lean_lib RequiredImports\n')
    (package / "RequiredImports.lean").write_text("\n".join("import " + name for name in sorted(roots)) + "\n")
    (args.output / "closure-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k:v for k,v in manifest.items() if k not in ['files','import_graph','per_task_direct_imports','direct_import_roots','external_imports']},indent=2))


if __name__ == "__main__": main()
