"""Freeze selected question metadata without making it eligible for model generation.

The HTML catalogue is parsed as data, never executed. Its source proofs are copied
read-only into a private reference store; they are never included in selection.jsonl.
Upstream byte verification is provenance evidence, not Lean or mathematical validation.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
from hashlib import sha256
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import urllib.request


SCHEMA_VERSION = 1
STATUS = "selected_pending_lean_preparation"
EXPECTED_IDS = [f"OM100-{i:03d}" for i in range(1, 101)]
EXPECTED_GROUPS = {k: 25 for k in ("algebra", "analysis", "combinatorics", "cs_probability")}
MAX_CANDIDATE_BYTES = 16 << 20
MAX_CATALOGUE_BYTES = 64 << 20
MAX_SOURCE_BYTES = 32 << 20
MAX_SOURCE_TOTAL_BYTES = 256 << 20
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SOURCE_PATH = re.compile(r"lean/OAI/(?:[A-Za-z_][A-Za-z0-9_]*/)*[A-Za-z_][A-Za-z0-9_]*\.lean\Z")
_PUBLIC_FIELDS = (
    "title", "question", "statement", "subject", "selection_group", "declaration_name",
    "source_path", "source_commit", "hf_commit", "source_sha256",
    "reference_declaration_sha256", "start_line", "end_line", "reference_line_count",
)
# Optional curator fields vary among the four selection groups. They remain private.
_INPUT_FIELDS = set(_PUBLIC_FIELDS) | set("""
    id task_id family family_title namespace section_context context_dependencies
    difficulty_estimate empirical_model_difficulty extraction_notes proof_summary
    significance prior_art_risk imports kernel_rechecked proof_line_count
    parent_oracle_evidence parent_oracle_result source_module_in_parent_closure
    collection_public_release_date lemma_earliest_publication training_exposure_status
    freshness_status standalone_extraction_compiled record_use open_context
    section_binders universe_declarations source_declaration_line_count
    source_proof_line_count source_span_lines novelty_status proof_nonblank_lines
    source_imports source_prefix_warning required_hidden_helpers local_commands source_url
""".split())
_BASE_FILES = {
    "selection.jsonl", "status.json", "private/candidates.jsonl",
    "private/catalogue.html", "private/references.jsonl",
}


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
                    for row in rows)


def _no_duplicate_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(data: str | bytes):
    def invalid(value):
        raise ValueError(f"Invalid JSON constant: {value}")
    try:
        return json.loads(data, object_pairs_hook=_no_duplicate_keys, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Invalid question-set JSON: {exc}") from exc


def _read(path: Path, limit: int) -> bytes:
    """Read a bounded regular file without following a final-component symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError(f"Not a regular file within the size limit: {path}")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"File exceeds size limit: {path}")
            return data
    except OSError as exc:
        raise ValueError(f"Cannot read question-set file {path}: {exc}") from exc


class _Catalogue(HTMLParser):
    """Strict structural extraction: entity decoding only, with no HTML/JS execution."""
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.cards: list[dict] = []
        self.card = None
        self.code = None

    def handle_starttag(self, tag, attrs):
        attrs = _no_duplicate_keys(attrs)
        classes = attrs.get("class", "").split()
        if self.code is not None:
            raise ValueError("Nested markup in a catalogue code block")
        if tag == "article" and "candidate" in classes:
            if self.card is not None:
                raise ValueError("Nested candidate article")
            self.card = {"attrs": attrs, "codes": {}, "reference_count": 0}
            self.cards.append(self.card)
        if self.card is not None and tag == "details" and "reference" in classes:
            self.card["reference_count"] += 1
        if tag == "code" and self.card is not None:
            kinds = {c for _, a in self.stack for c in a.get("class", "").split()}
            relevant = kinds & {"statement", "reference", "declaration", "hash"}
            if len(relevant) > 1:
                raise ValueError("Ambiguous catalogue code-block role")
            kind = next(iter(relevant), "context")
            if kind in {"statement", "reference"} and (not self.stack or self.stack[-1][0] != "pre"):
                raise ValueError("Catalogue statement/reference must be in a pre/code block")
            self.code = {"kind": kind, "text": ""}
        if tag not in self._VOID:
            self.stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise ValueError(f"Malformed catalogue HTML: unexpected closing {tag}")
        _, attrs = self.stack.pop()
        if tag == "code" and self.code is not None:
            self.card["codes"].setdefault(self.code["kind"], []).append(self.code["text"])
            self.code = None
        if tag == "article" and "candidate" in attrs.get("class", "").split():
            self.card = None

    def handle_data(self, data):
        if self.code is not None:
            self.code["text"] += data

    def finish(self, text: str) -> list[dict]:
        try:
            self.feed(text)
            self.close()
        except (AssertionError, TypeError) as exc:
            raise ValueError(f"Malformed catalogue HTML: {exc}") from exc
        if self.stack or self.card is not None or self.code is not None:
            raise ValueError("Malformed catalogue HTML: unclosed elements")
        return self.cards


def _text(row: dict, key: str, *, max_length=100_000) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or "\x00" in value:
        raise ValueError(f"{row.get('id', 'candidate')}: invalid {key}")
    return value


def _validate_inputs(candidates: bytes, catalogue: bytes) -> tuple[list, list, dict]:
    try:
        rows = [_json(line) for line in candidates.decode("utf-8").split("\n") if line.strip()]
        cards = _Catalogue().finish(catalogue.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("Question-set inputs must be UTF-8") from exc
    if len(rows) != 100 or len(cards) != 100:
        raise ValueError("Question-set adoption requires exactly 100 records and 100 catalogue candidates")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every candidate must be a JSON object")
        if set(row) - _INPUT_FIELDS:
            raise ValueError(f"Unexpected candidate fields: {sorted(set(row) - _INPUT_FIELDS)}")
    if [row.get("id") for row in rows] != EXPECTED_IDS:
        raise ValueError("Candidate IDs must be unique, contiguous OM100-001 through OM100-100 in order")
    if any(not isinstance(row.get("selection_group"), str) for row in rows):
        raise ValueError("Every selection_group must be a string")
    if Counter(row.get("selection_group") for row in rows) != EXPECTED_GROUPS:
        raise ValueError("Expected 25 candidates in each of the four selection groups")
    selections, references, declarations, spans, source_hashes = [], [], set(), set(), {}
    for row, card in zip(rows, cards):
        ident = row["id"]
        for key in _PUBLIC_FIELDS:
            if key not in {"start_line", "end_line", "reference_line_count"}:
                _text(row, key)
        family = _text(row, "family", max_length=3)
        parent = _text(row, "task_id", max_length=150)
        if not re.fullmatch(r"[0-9]{3}", family) or not re.fullmatch(r"[a-z][a-z0-9-]*", parent):
            raise ValueError(f"{ident}: invalid family or parent task ID")
        if not _SOURCE_PATH.fullmatch(row["source_path"]):
            raise ValueError(f"{ident}: unsafe source_path")
        for key in ("source_commit", "hf_commit"):
            if not _HEX40.fullmatch(row[key]):
                raise ValueError(f"{ident}: {key} must be a pinned 40-character SHA")
        for key in ("source_sha256", "reference_declaration_sha256"):
            if not _HEX64.fullmatch(row[key]):
                raise ValueError(f"{ident}: invalid {key}")
        for key in ("start_line", "end_line", "reference_line_count"):
            if type(row.get(key)) is not int or not 1 <= row[key] <= 1_000_000:
                raise ValueError(f"{ident}: invalid {key}")
        if row["end_line"] - row["start_line"] + 1 != row["reference_line_count"]:
            raise ValueError(f"{ident}: source span and reference line count disagree")
        span = (row["source_path"], row["start_line"], row["end_line"])
        if row["declaration_name"] in declarations or span in spans:
            raise ValueError(f"{ident}: duplicate declaration or source span")
        declarations.add(row["declaration_name"])
        spans.add(span)
        if source_hashes.setdefault(row["source_path"], row["source_sha256"]) != row["source_sha256"]:
            raise ValueError(f"{ident}: conflicting source hash")
        expected_url = (f"https://github.com/openai/math/blob/{row['source_commit']}/{row['source_path']}"
                        f"#L{row['start_line']}-L{row['end_line']}")
        if row.get("source_url", expected_url) != expected_url:
            raise ValueError(f"{ident}: source URL does not match pinned provenance")
        attrs, codes = card["attrs"], card["codes"]
        if (attrs.get("id") != ident.lower() or attrs.get("data-family") != family
                or attrs.get("data-subject") != row["subject"]):
            raise ValueError(f"{ident}: catalogue identity mismatch")
        for kind, expected in (("statement", row["statement"]), ("declaration", row["declaration_name"]),
                               ("hash", row["source_sha256"])):
            if codes.get(kind) != [expected]:
                raise ValueError(f"{ident}: catalogue {kind} mismatch")
        if card["reference_count"] != 1 or len(codes.get("reference", [])) != 1:
            raise ValueError(f"{ident}: expected one reference declaration")
        reference = codes["reference"][0]
        if _hash(reference.encode()) != row["reference_declaration_sha256"]:
            raise ValueError(f"{ident}: reference declaration hash mismatch")
        if len(reference.splitlines()) != row["reference_line_count"]:
            raise ValueError(f"{ident}: reference declaration line count mismatch")
        if not reference.startswith(row["statement"]) or not reference[len(row["statement"]):].lstrip().startswith(":="):
            raise ValueError(f"{ident}: statement is not the reference declaration's proof-free prefix")
        selections.append({"record_kind": "selected_question_v1", "task_id": ident,
                           "source_parent_task_id": parent, "family_id": family,
                           **{key: row[key] for key in _PUBLIC_FIELDS}})
        references.append({"task_id": ident, "declaration_name": row["declaration_name"],
                           "source_path": row["source_path"], "start_line": row["start_line"],
                           "end_line": row["end_line"], "reference_declaration": reference,
                           "reference_declaration_sha256": row["reference_declaration_sha256"]})
    pins = {}
    for key in ("source_commit", "hf_commit"):
        values = {row[key] for row in rows}
        if len(values) != 1:
            raise ValueError(f"All candidates must share one pinned {key}")
        pins[key] = values.pop()
    if len({row["family"] for row in rows}) != 19:
        raise ValueError("Expected 19 selected result families")
    counts = {"tasks": 100, "families": 19, "parent_tasks": len({row["task_id"] for row in rows}),
              "source_files": len(source_hashes), "selection_groups": EXPECTED_GROUPS.copy()}
    return selections, references, {"pins": pins, "counts": counts, "source_hashes": source_hashes}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Pinned source requests must not redirect")


def _fetch_source(commit: str, source_path: str) -> bytes:
    """Read only pinned openai/math Lean source; never interpret the retrieved file."""
    if not _HEX40.fullmatch(commit) or not _SOURCE_PATH.fullmatch(source_path):
        raise ValueError("Unsafe upstream source identity")
    url = f"https://raw.githubusercontent.com/openai/math/{commit}/{source_path}"
    try:
        with urllib.request.build_opener(_NoRedirect()).open(url, timeout=30) as response:
            if response.geturl() != url:
                raise ValueError("Unexpected upstream source URL")
            data = response.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"Cannot fetch pinned source {source_path}: {exc}") from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(f"Pinned source exceeds size limit: {source_path}")
    return data


def _verify_source(data: bytes, source_path: str, expected_hash: str, references: list[dict]) -> None:
    if _hash(data) != expected_hash:
        raise ValueError(f"Upstream whole-file hash mismatch: {source_path}")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError(f"Pinned source is not UTF-8: {source_path}") from exc
    for ref in references:
        if ref["source_path"] == source_path:
            span = "\n".join(lines[ref["start_line"] - 1:ref["end_line"]])
            if span != ref["reference_declaration"]:
                raise ValueError(f"Pinned source span differs from reference: {ref['task_id']}")


def _status(verified: bool) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": STATUS, "model_generation_enabled": False,
            "task_count": 100, "family_count": 19,
            "source_verification": "verified" if verified else "not_checked",
            "source_verification_scope": "whole-file SHA-256 and exact selected declaration spans only",
            "per_task": {ident: {"lean_compilation": "not_checked", "reference_kernel_verification": "not_checked",
                                 "answer_access_audit": "not_checked", "metric_extraction": "not_checked"}
                         for ident in EXPECTED_IDS}}


def _write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600 if relative.startswith("private/") else 0o644)
    if relative.startswith("private/"):
        for parent in path.parents:
            if parent == root:
                break
            parent.chmod(0o700)


def _inventory(root: Path) -> set[str]:
    result = set()
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if path.is_symlink():
                raise ValueError(f"Symlinks are not allowed in a frozen question set: {path}")
        result.update((Path(current) / name).relative_to(root).as_posix() for name in files)
    return result


def load_question_set(path: Path) -> dict:
    """Read a frozen selection, checking hashes and the generation-disabled status.

    Returns ``manifest``, ``status`` and public ``selection``. This reader performs
    no network requests, executes no source, and cannot certify Lean readiness.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"Question set is not a regular directory: {path}")
    manifest = _json(_read(path / "manifest.json", 2 << 20))
    if not isinstance(manifest, dict):
        raise ValueError("Invalid question-set manifest")
    identity = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    if manifest.get("manifest_sha256") != _hash(_json_bytes(identity)):
        raise ValueError("Question-set manifest integrity mismatch")
    if (manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("kind") != "openai_math_candidate_selection_v1"
            or manifest.get("status") != STATUS or manifest.get("model_generation_enabled") is not False
            or type(manifest.get("upstream_sources_verified")) is not bool):
        raise ValueError("Unsupported question-set manifest")
    verified = manifest["upstream_sources_verified"]
    files = manifest.get("files")
    if not isinstance(files, dict) or not _BASE_FILES <= set(files):
        raise ValueError("Question-set manifest lacks required files")
    actual = _inventory(path)
    if actual != set(files) | {"manifest.json"}:
        raise ValueError("Question-set file inventory differs from the frozen manifest")
    for relative, evidence in files.items():
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts or parts.as_posix() != relative:
            raise ValueError("Unsafe path in question-set manifest")
        if not isinstance(evidence, dict) or type(evidence.get("bytes")) is not int:
            raise ValueError(f"Invalid file evidence: {relative}")
        limit = MAX_CATALOGUE_BYTES if relative == "private/catalogue.html" else MAX_SOURCE_BYTES
        if not 0 <= evidence["bytes"] <= limit:
            raise ValueError(f"Invalid frozen file size: {relative}")
        data = _read(path / relative, limit)
        if len(data) != evidence["bytes"] or _hash(data) != evidence.get("sha256"):
            raise ValueError(f"Question-set file integrity mismatch: {relative}")
    raw_candidates = _read(path / "private/candidates.jsonl", MAX_CANDIDATE_BYTES)
    raw_catalogue = _read(path / "private/catalogue.html", MAX_CATALOGUE_BYTES)
    selection, references, info = _validate_inputs(raw_candidates, raw_catalogue)
    if manifest.get("input_sha256") != {"candidates": _hash(raw_candidates), "catalogue": _hash(raw_catalogue)}:
        raise ValueError("Question-set input hashes disagree")
    if (manifest.get("counts") != info["counts"] or manifest.get("pins") != info["pins"]
            or manifest.get("selected_task_ids") != EXPECTED_IDS):
        raise ValueError("Question-set manifest metadata disagrees with selected inputs")
    expected_files = _BASE_FILES | ({"private/sources/" + p for p in info["source_hashes"]} if verified else set())
    if set(files) != expected_files:
        raise ValueError("Question-set source inventory disagrees with verification status")
    for relative, expected in (("selection.jsonl", _jsonl_bytes(selection)),
                               ("private/references.jsonl", _jsonl_bytes(references)),
                               ("status.json", _json_bytes(_status(verified)))):
        if _read(path / relative, MAX_SOURCE_BYTES) != expected:
            raise ValueError(f"Question-set semantic integrity mismatch: {relative}")
    if verified:
        total = 0
        for source_path, expected_hash in info["source_hashes"].items():
            data = _read(path / "private/sources" / source_path, MAX_SOURCE_BYTES)
            total += len(data)
            if total > MAX_SOURCE_TOTAL_BYTES:
                raise ValueError("Frozen sources exceed aggregate size limit")
            _verify_source(data, source_path, expected_hash, references)
    return {"manifest": manifest, "status": _status(verified), "selection": selection}


def adopt_question_set(candidates: Path, catalogue: Path, destination: Path, *, verify_upstream: bool = False) -> dict:
    """Atomically freeze these 100 selected candidates, never start an evaluation.

    Exact input files and complete proofs stay in ``private/``. The public file is
    an allowlisted selection, not a compiled task manifest or a ready model prompt.
    Existing destinations are immutable: reuse requires matching inputs and intact
    contents. Source verification cannot be added in place to an unverified bundle.
    """
    if type(verify_upstream) is not bool:
        raise ValueError("verify_upstream must be a boolean")
    raw_candidates = _read(Path(candidates), MAX_CANDIDATE_BYTES)
    raw_catalogue = _read(Path(catalogue), MAX_CATALOGUE_BYTES)
    selection, references, info = _validate_inputs(raw_candidates, raw_catalogue)
    input_hashes = {"candidates": _hash(raw_candidates), "catalogue": _hash(raw_catalogue)}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / f".{destination.name}.adoption.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise ValueError(f"Cannot open question-set adoption lock: {lock}") from exc
    stage = None
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ValueError("Question-set adoption lock must be a regular file")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"An adoption is already active for {destination}") from exc
        if destination.exists() or destination.is_symlink():
            existing = load_question_set(destination)
            if existing["manifest"]["input_sha256"] != input_hashes:
                raise ValueError("Immutable destination already contains different inputs")
            if verify_upstream and not existing["manifest"]["upstream_sources_verified"]:
                raise ValueError("Use a new destination to add upstream verification to an immutable selection")
            return existing
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
        for relative, data in (("private/candidates.jsonl", raw_candidates), ("private/catalogue.html", raw_catalogue),
                               ("selection.jsonl", _jsonl_bytes(selection)),
                               ("private/references.jsonl", _jsonl_bytes(references)),
                               ("status.json", _json_bytes(_status(verify_upstream)))):
            _write(stage, relative, data)
        if verify_upstream:
            total = 0
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = {pool.submit(_fetch_source, info["pins"]["source_commit"], p): p
                        for p in info["source_hashes"]}
                for future in as_completed(jobs):
                    source_path = jobs[future]
                    data = future.result()
                    total += len(data)
                    if total > MAX_SOURCE_TOTAL_BYTES:
                        raise ValueError("Pinned sources exceed aggregate size limit")
                    _verify_source(data, source_path, info["source_hashes"][source_path], references)
                    _write(stage, "private/sources/" + source_path, data)
        manifest = {"schema_version": SCHEMA_VERSION, "kind": "openai_math_candidate_selection_v1",
                    "status": STATUS, "model_generation_enabled": False,
                    "input_sha256": input_hashes, "pins": info["pins"], "counts": info["counts"],
                    "selected_task_ids": EXPECTED_IDS.copy(), "upstream_sources_verified": verify_upstream,
                    "files": {}}
        for relative in sorted(_inventory(stage)):
            data = _read(stage / relative, MAX_CATALOGUE_BYTES)
            manifest["files"][relative] = {"sha256": _hash(data), "bytes": len(data)}
        manifest["manifest_sha256"] = _hash(_json_bytes(manifest))
        _write(stage, "manifest.json", _json_bytes(manifest))
        result = load_question_set(stage)
        if destination.exists() or destination.is_symlink():
            raise ValueError("Destination appeared during adoption; refusing to replace it")
        stage.rename(destination)
        stage = None
        return result
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        # Keep the lock inode: unlinking it races another adopter that already
        # opened it. The OS releases flock even if this process is killed.
        os.close(lock_fd)
