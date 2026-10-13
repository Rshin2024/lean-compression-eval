"""Task loading and structural checks; structural validity is not proof verification.

Public records deliberately contain no reference proof.  Audit fields distinguish
collected candidates from tasks cleared for a frozen evaluation.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import json
import math
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
    if path.is_dir():
        raise ValueError(f"{path}: expected a task manifest file, found a directory")
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
    # read_text turns "\r\n" and "\r" into "\n"; records end there only: splitlines()
    # also splits U+2028/U+2029/U+0085, which JSON allows raw inside strings.
    for line_no, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_no}: {error}") from error
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
    tasks = list(tasks)
    if not tasks:
        return ["manifest contains no tasks"]
    result = []
    for index, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            result.append(f"row {index}: expected a task object")
            continue
        audit = task.get("audit", {})
        if not isinstance(audit, dict):
            result.append(f"{task.get('task_id', '?')}: audit must be an object")
            continue
        for field in ("reference_verified", "explicit_signature_frozen", "answer_access_audited", "family_reviewed", "metric_closure_verified"):
            if audit.get(field) is not True:
                result.append(f"{task.get('task_id', '?')}: {field} is not true")
    return result


def validate_references(tasks: Iterable[dict[str, Any]], references: Iterable[dict[str, Any]]) -> list[str]:
    """Check one nonempty private proof per task; Lean checking is separate.

    Task rows without a string task_id cannot be mapped; validate_manifest reports them.
    Reference rows without one are reported here.
    """
    task_ids = {t["task_id"] for t in tasks if isinstance(t, dict) and isinstance(t.get("task_id"), str)}
    refs, errors = [], []
    for index, ref in enumerate(references, 1):
        if isinstance(ref, dict) and isinstance(ref.get("task_id"), str):
            refs.append(ref)
        else:
            errors.append(f"reference row {index}: expected an object with a string task_id")
    counts = Counter(r["task_id"] for r in refs)
    for task_id in sorted(task_ids):
        if counts[task_id] != 1:
            errors.append(f"{task_id}: expected one private reference, found {counts[task_id]}")
    for ref in refs:
        if ref.get("task_id") not in task_ids:
            errors.append(f"{ref.get('task_id')}: orphan private reference")
        if not isinstance(ref.get("proof"), str) or not ref["proof"].strip():
            errors.append(f"{ref.get('task_id')}: empty private reference")
    return errors


# Answer-access screen: a private proof against its own task's model-visible prompt.
#
# Exact matches.  A match of at least ANSWER_ACCESS_MIN_CHARS whitespace-normalized characters is
# answer access: the rule of the first answer-access amendment (dataset_amendments.jsonl rows
# 417-424), which on its pre-amendment rows flags exactly the 8 tasks it amended.  From
# SHORT_EXACT_MATCH_MIN_CHARS (9) characters a match is answer access too unless the proof is a
# generic shape that names nothing specific to the task: a literal reflexivity proof
# (LITERAL_REFLEXIVITY); "by" and one identifier with no arguments (SINGLE_TACTIC, such as
# "by positivity"), where Lean can only parse the identifier as a tactic name (a term after "by"
# needs "exact"), so the proof names no lemma, hypothesis or term; or a name bound by the target's
# own binders, which the statement shows anyway.  Below 9 characters the committed matches are such
# generic shapes and exact matches are not screened.
#
# The examples in these comments are invented, not private proofs.  A match reports the premise-context
# text it found, its line, its edit counts and the proof's token and component counts, never the
# proof's own text or tokens.
#
# Near-verbatim copies, for proofs of at least SHORT_EXACT_MATCH_MIN_CHARS characters that are not a
# generic shape.  A proof component of the premise context (proof_components: a declaration value, or
# a term inside one that can stand as a proof by itself, such as an anonymous-constructor component,
# a fun body, a rewrite rule or a tactic's term) copies the proof near-verbatim when few token edits
# turn it into the proof and those edits change few of the proof's name components:
# - tokens (proof_tokens) are maximal runs of characters other than whitespace, brackets, commas and
#   ";", so a name ("foo_bar_left"), a projection ("h.1") or a notation ("ℝ≥0", "→*₀") is one token;
# - an edit inserts, deletes or substitutes one token or swaps two adjacent tokens, so an argument
#   swap "b c" -> "c b" is one edit;
# - at most NEAR_VERBATIM_MAX_TOKEN_EDITS (3) edits, or one per NEAR_VERBATIM_TOKENS_PER_EDIT (6)
#   tokens of the proof where that allows more: a left/right dual of a short proof changes up to
#   three tokens, and a long proof may carry as many local changes per token;
# - the same comparison over name components (name_components: tokens split at "." and "_")
#   changes at most NEAR_VERBATIM_MAX_COMPONENT_FRACTION (1/3) of the proof's components, rounded
#   to the nearest whole component, so that the copy keeps the proof's content: "lefty_qux" for
#   "righty_qux" changes 1 of 2 and "foo_ne_left h" for "foo_ne_right h" 1 of 4, while "Qux.corge _ _"
#   for "Qux.grault_waldo_grault _ _" changes 3 of 6 and "rfl" for "foo_ne_right h" 4 of 4.
# A declaration header (the statement of a lemma that the proof may apply, which the prompt shows by
# design) and a parenthesized single term (an argument or a grouped function) are not proof
# components.  On the rows saved before the near-verbatim amendment
# (data/private/tasks-before-near-verbatim-amendment.json) these rules flag exactly the tasks it
# amended.
ANSWER_ACCESS_MIN_CHARS = 15
SHORT_EXACT_MATCH_MIN_CHARS = 9
LITERAL_REFLEXIVITY = ("rfl", "Iff.rfl", "by rfl", "by exact rfl", "by exact Iff.rfl")
SINGLE_TACTIC = re.compile(r"by ([A-Za-z_][A-Za-z0-9_]*[?!]?)")
NEAR_VERBATIM_MAX_TOKEN_EDITS = 3
NEAR_VERBATIM_TOKENS_PER_EDIT = 6
NEAR_VERBATIM_MAX_COMPONENT_FRACTION = Fraction(1, 3)
# Proof components (see proof_components) start after an opening bracket of COMPONENT_BRACKETS, a
# comma or a TERM_STARTS_AFTER token, and end before a closing bracket, a comma or a
# TERM_ENDS_BEFORE token.  A TACTIC_BLOCK_OPENERS token opens a tactic block whose tactics are
# divided by TACTIC_SEPARATORS and by lines at the column of its first tactic; several of them form
# a component only as the whole block.
COMPONENT_BRACKETS = "⟨[{⦃"
TERM_STARTS_AFTER = ("=>", "↦", ":=", "<|", "$", "by", "·", ";", "<;>", "from", "using", "exact", "exacts",
                     "apply", "refine", "refine'", "use", "then", "else")
TERM_ENDS_BEFORE = (";", "<;>", "then", "else", "|")
TACTIC_SEPARATORS = (";", "<;>")
TACTIC_BLOCK_OPENERS = ("by", "·")

_OPENERS, _CLOSERS = "([{⟨⦃", ")]}⟩⦄"
_BRACKET = re.compile(r"[([{⟨⦃)\]}⟩⦄]")
_LEXICAL = re.compile(r'/-|--|"')
_BLOCK_DELIMITER = re.compile(r"/-|-/")
_LEAN_TOKEN = re.compile(r"<;>|[()\[\]{}⟨⟩⦃⦄,;]|(?:«[^»\n]*»|(?!<;>)[^\s()\[\]{}⟨⟩⦃⦄,;«])+")
_ASSIGN = re.compile(r":=")
_WHERE = re.compile(r"(?<![\w'.])where(?![\w'])")
_BINDER_GROUP = re.compile(r"[(\[{⦃]\s*([^:()\[\]{}⦃⦄]+?)\s*:(?!=)")


def reference_in_prompt(task: dict[str, Any], proof: Any) -> bool | None:
    """Whether a private proof's whitespace-normalized text occurs in the task's prompt, at any length.

    The prompt is every message prompts.messages() renders for each condition.  None
    means the check could not run: no proof text, or a task that cannot be rendered.
    screen_answer_access decides which matches are answer access.
    """
    from .prompts import CONDITIONS, messages
    compact = " ".join(proof.split()) if isinstance(proof, str) else ""
    if not compact:
        return None
    try:
        shown = [message["content"] for condition in CONDITIONS for message in messages(task, condition, "")]
    except (KeyError, TypeError, AttributeError):
        return None
    return any(compact in " ".join(text.split()) for text in shown)


def _blank_comments(text: str) -> str:
    """The text with comments and string contents replaced by spaces; offsets and line breaks are kept."""
    spans, position, depth, opened = [], 0, 0, 0
    while True:
        # Block comments nest; inside one only their delimiters matter.
        match = (_BLOCK_DELIMITER if depth else _LEXICAL).search(text, position)
        if not match:
            if depth:
                spans.append((opened, len(text)))
            break
        token, start = match.group(), match.start()
        position = match.end()
        if depth:
            depth += 1 if token == "/-" else -1
            if not depth:
                spans.append((opened, position))
        elif token == "/-":
            depth, opened = 1, start
        elif token == "--":
            end = text.find("\n", start)
            position = len(text) if end < 0 else end
            spans.append((start, position))
        elif token == '"':
            end = start + 1
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            spans.append((start + 1, min(end, len(text))))
            position = end + 1
    pieces, kept = [], 0
    for start, end in spans:
        pieces += [text[kept:start], re.sub(r"[^\n]", " ", text[start:end])]
        kept = end
    return "".join(pieces) + text[kept:]


def _first_at_depth_zero(pattern: re.Pattern, text: str, start: int, end: int) -> re.Match | None:
    brackets = [(m.start(), 1 if m.group() in _OPENERS else -1) for m in _BRACKET.finditer(text, start, end)]
    for match in pattern.finditer(text, start, end):
        depth = 0
        for position, step in brackets:
            if position >= match.start():
                break
            depth = max(0, depth + step)
        if depth == 0:
            return match
    return None


def _where_fields(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Field spans of a where block: a field starts at each line of the block's least indentation."""
    entries, line_start, first = [], start, True
    while line_start < end:
        line_end = text.find("\n", line_start, end)
        line_end = end if line_end < 0 else line_end
        line = text[line_start:line_end]
        if line.strip():
            # Text after "where" on its own line is a field by itself.
            entries.append((line_start, -1 if first else len(line) - len(line.lstrip())))
        first, line_start = False, line_end + 1
    base = min((indent for _, indent in entries if indent >= 0), default=None)
    starts = [position for position, indent in entries if indent < 0 or indent == base]
    return list(zip(starts, starts[1:] + [end]))


def declaration_values(source: str) -> list[tuple[int, int]]:
    """Spans of the declaration values in Lean source, in source order.

    A top-level command starts at each line that begins in column 0 once comments are blanked.
    Its value follows its first ":=" outside brackets; a command with a "where" before any such
    ":=" has one value per field instead, after the field's first ":=" outside brackets.  Arms of
    equation-compiler definitions ("| pattern => value") are not separated.  Values that are
    empty once comments are blanked, such as "/- proof omitted -/", are omitted.
    """
    blanked = _blank_comments(source)
    line_starts = [0] + [match.end() for match in re.finditer("\n", blanked)]
    commands = [start for start in line_starts if start < len(blanked) and not blanked[start].isspace()]
    spans = []
    for start, end in zip(commands, commands[1:] + [len(blanked)]):
        assign = _first_at_depth_zero(_ASSIGN, blanked, start, end)
        where = _first_at_depth_zero(_WHERE, blanked, start, end)
        if where and (not assign or where.start() < assign.start()):
            for field_start, field_end in _where_fields(blanked, where.end(), end):
                field_assign = _first_at_depth_zero(_ASSIGN, blanked, field_start, field_end)
                if field_assign:
                    spans.append((field_assign.end(), field_end))
        elif assign:
            spans.append((assign.end(), end))
    return [(start, end) for start, end in spans if blanked[start:end].strip()]


def proof_tokens(text: str) -> list[str]:
    """Lean-like tokens of source text without its comments: each bracket, comma, ";" and "<;>" is one
    token, and every other maximal run of non-whitespace characters is one, so that a name
    ("foo_bar_left"), a projection ("h.1") or a notation ("ℝ≥0", "→*₀", "a⁻¹") is one token."""
    return _LEAN_TOKEN.findall(_blank_comments(text))


def name_components(tokens: Iterable[str]) -> list[str]:
    """The tokens with each split at "." and "_" into its nonempty parts; a token without any ("_") stays."""
    parts: list[str] = []
    for token in tokens:
        parts.extend([piece for piece in re.split(r"[._]", token) if piece] or [token])
    return parts


def _edit_distance(left: list[str], right: list[str]) -> int:
    """Edits turning one sequence into the other: an insertion, deletion or substitution of one item,
    or a swap of two adjacent items, is one edit (optimal string alignment distance)."""
    before, previous = [], list(range(len(right) + 1))
    for i, item in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            cost = min(previous[j - 1] + (item != other), previous[j] + 1, current[j - 1] + 1)
            if i > 1 and j > 1 and item == right[j - 2] and left[i - 2] == other and item != other:
                cost = min(cost, before[j - 2] + 1)
            current.append(cost)
        before, previous = previous, current
    return previous[-1]


def _tactic_blocks(blanked: str, tokens: list[tuple[int, int, str]]) -> dict[int, tuple[int, list[int]]]:
    """The tactic blocks among tokens: opener index ("by" or "·") -> (index of the block's last token,
    indices of the first tokens of its later tactic lines).  A block's tactics start at the column of
    its first token; it ends before a line that starts left of that column or at its group's closing
    bracket."""
    def column(position: int) -> int:
        return position - blanked.rfind("\n", 0, position) - 1

    blocks = {}
    for opener, (_, _, text) in enumerate(tokens):
        if text not in TACTIC_BLOCK_OPENERS or opener + 1 >= len(tokens):
            continue
        base, depth, last, lines = column(tokens[opener + 1][0]), 0, opener + 1, []
        for index in range(opener + 1, len(tokens)):
            start, _, token = tokens[index]
            if index > opener + 1 and depth == 0 and "\n" in blanked[tokens[index - 1][1]:start]:
                if column(start) < base:
                    break
                if column(start) == base:
                    lines.append(index)
            if token in _OPENERS:
                depth += 1
            elif token in _CLOSERS:
                depth -= 1
                if depth < 0:
                    break
            last = index
        blocks[opener] = (last, lines)
    return blocks


def _value_components(blanked: str, start: int, end: int) -> set[tuple[int, int]]:
    """The proof-component spans of one declaration value blanked[start:end] (see proof_components)."""
    tokens = [(match.start(), match.end(), match.group()) for match in _LEAN_TOKEN.finditer(blanked, start, end)]
    if not tokens:
        return set()
    last, closing, stack, tuples = len(tokens) - 1, {}, [], set()
    for index, (_, _, token) in enumerate(tokens):
        if token in _OPENERS:
            stack.append(index)
        elif token in _CLOSERS and stack:
            closing[stack.pop()] = index
        elif token == "," and stack and tokens[stack[-1]][2] == "(":
            tuples.add(stack[-1])  # a comma inside parentheses makes them a tuple, a constructor
    blocks = _tactic_blocks(blanked, tokens)
    inside = {}  # token index -> opener of the innermost tactic block that contains it
    for opener, (block_last, _) in sorted(blocks.items()):
        inside.update(dict.fromkeys(range(opener + 1, block_last + 1), opener))
    # Tactic boundaries (a later tactic line or a separator) -> the opener of the block they divide.
    boundaries = {index: inside.get(index, -2) for index, (_, _, token) in enumerate(tokens)
                  if token in TACTIC_SEPARATORS}
    for opener, (_, lines) in blocks.items():
        boundaries.update(dict.fromkeys(lines, opener))
    starts, ends = {0}, {last}
    for index, (_, _, token) in enumerate(tokens):
        if index in closing:
            ends.add(closing[index] - 1)
            if token in COMPONENT_BRACKETS or index in tuples:
                starts.add(index + 1)
        if token == "," or token in TERM_STARTS_AFTER:
            starts.add(index + 1)
        if token == "," or token in TERM_ENDS_BEFORE:
            ends.add(index - 1)
    for _, lines in blocks.values():
        starts.update(lines)
        ends.update(line - 1 for line in lines)
    spans = {(tokens[0][0], tokens[last][1])}
    for first in sorted(index for index in starts if index <= last):
        # A component that starts inside a tactic block stays inside it, and one that opens a block holds
        # all of it.  Several tactics of a block form a component only as the whole block: crossing a
        # tactic boundary needs a component that starts at or before the block's first tactic and holds
        # the block to its end.
        own = inside.get(first)
        limit = blocks[own][0] if own is not None else last
        depth, needed = 0, first
        for index in range(first, limit + 1):
            token = tokens[index][2]
            if depth == 0 and index > first and index in boundaries:
                opener = boundaries[index]
                if first > opener + 1 or opener not in blocks:
                    break
                needed = max(needed, blocks[opener][0])
            if token in _OPENERS:
                depth += 1
            elif token in _CLOSERS:
                depth -= 1
                if depth < 0:
                    break
            elif depth == 0 and token == ",":
                break
            if index in blocks:
                needed = max(needed, blocks[index][0])
            if depth == 0 and index in ends and index >= needed:
                spans.add((tokens[first][0], tokens[index][1]))
    return spans


def proof_components(source: str) -> list[tuple[int, int]]:
    """Spans of the proof components of Lean source, in source order: each declaration value and each
    term inside one that can stand as a proof by itself.

    Inside a value, a component is a bracket-balanced run of tokens from a term start to a term end
    that contains no comma of its own bracket level.  Terms start at the value's start, after an
    opening bracket of COMPONENT_BRACKETS or of a parenthesized tuple (anonymous-constructor
    components, rewrite and simp rules, structure-instance fields), after a comma, after a
    TERM_STARTS_AFTER token (a fun body, a field, "have", calc-step or named-argument value, the
    argument after "<|", a tactic block and each tactic in it, a tactic's term, an if-branch) and at
    each later line of a tactic block that starts at the column of its first tactic.  They end at the
    value's end, before a closing bracket, a comma, a TERM_ENDS_BEFORE token or such a tactic line.
    A component that starts inside a tactic block stays inside it, one that contains a block's
    opener contains the whole block, and one that crosses a tactic boundary (a separator or a later
    tactic line) of a block holds the block from its first tactic to its end: several tactics form a
    component only as their whole block.  A parenthesized single term is an argument or a grouped
    function, not a component, though a fun body or tactic block inside it ends at its parenthesis.
    """
    blanked = _blank_comments(source)
    spans: set[tuple[int, int]] = set()
    for start, end in declaration_values(source):
        spans |= _value_components(blanked, start, end)
    return sorted(spans)


def near_verbatim_matches(task: dict[str, Any], proof: Any) -> list[dict[str, Any]] | None:
    """The premise-context proof components that copy a private proof near-verbatim, closest first.

    Each match records the component's whitespace-normalized premise-context text (snippet), its line
    in premise_context, whether it is a whole declaration value, both edit counts and the numbers of
    the proof's tokens and name components (reference_token_count, reference_component_count); it
    never holds the proof's own text or tokens.  Proofs shorter than SHORT_EXACT_MATCH_MIN_CHARS
    normalized characters, and generic shapes (generic_proof_shape), have no matches; None means no
    proof text.
    """
    compact = " ".join(proof.split()) if isinstance(proof, str) else ""
    if not compact:
        return None
    context = task.get("premise_context") if isinstance(task, dict) else None
    if len(compact) < SHORT_EXACT_MATCH_MIN_CHARS or generic_proof_shape(task, proof) or not isinstance(context, str):
        return []
    reference = proof_tokens(proof)
    parts = name_components(reference)
    max_token_edits = max(NEAR_VERBATIM_MAX_TOKEN_EDITS, math.ceil(len(reference) / NEAR_VERBATIM_TOKENS_PER_EDIT))
    max_component_edits = round(NEAR_VERBATIM_MAX_COMPONENT_FRACTION * len(parts))
    blanked = _blank_comments(context)
    values = {(start + len(blanked[start:end]) - len(blanked[start:end].lstrip()), len(blanked[:end].rstrip()))
              for start, end in declaration_values(context)}
    found = []
    for start, end in proof_components(context):
        component = _LEAN_TOKEN.findall(blanked, start, end)
        if abs(len(component) - len(reference)) > max_token_edits:
            continue
        token_edits = _edit_distance(reference, component)
        if token_edits > max_token_edits:
            continue
        component_edits = _edit_distance(parts, name_components(component))
        if component_edits > max_component_edits:
            continue
        found.append({"snippet": " ".join(context[start:end].split()),
                      "premise_context_line": context.count("\n", 0, start) + 1,
                      "declaration_value": (start, end) in values, "token_edits": token_edits,
                      "component_edits": component_edits, "reference_token_count": len(reference),
                      "reference_component_count": len(parts)})
    return sorted(found, key=lambda m: (Fraction(m["component_edits"], m["reference_component_count"]),
                                        m["token_edits"], m["premise_context_line"]))


def binder_names(binders: str) -> set[str]:
    """Names bound by the outermost binder groups, such as h, a and b in "(h : P) {a b : α}"."""
    names = set()
    for match in _BINDER_GROUP.finditer(binders):
        before = binders[:match.start()]
        if sum(before.count(c) for c in _OPENERS) == sum(before.count(c) for c in _CLOSERS):
            names.update(name.strip("«»") for name in match.group(1).split())
    return names


def generic_proof_shape(task: dict[str, Any], proof: Any) -> str | None:
    """Name the generic shape of a proof that names nothing specific to its task, else None."""
    compact = " ".join(proof.split()) if isinstance(proof, str) else ""
    binders = task.get("binders") if isinstance(task, dict) else None
    if compact in LITERAL_REFLEXIVITY:
        return "literal_reflexivity"
    if SINGLE_TACTIC.fullmatch(compact):
        return "single_tactic"
    if compact and isinstance(binders, str) and compact in binder_names(binders):
        return "binder_name"
    return None


def screen_answer_access(task: dict[str, Any], proof: Any) -> dict[str, Any]:
    """Screen one private proof against its task's prompt by the rules above.

    exact_match is reference_in_prompt (None: unchecked).  gated_exact_match is an exact match of
    at least ANSWER_ACCESS_MIN_CHARS characters, or of at least SHORT_EXACT_MATCH_MIN_CHARS that is
    not a generic shape.  near_verbatim lists near_verbatim_matches (None: unchecked).
    """
    exact = reference_in_prompt(task, proof)
    chars = len(" ".join(proof.split())) if exact else None
    shape = generic_proof_shape(task, proof) if exact else None
    gated = bool(exact) and (chars >= ANSWER_ACCESS_MIN_CHARS or (chars >= SHORT_EXACT_MATCH_MIN_CHARS and shape is None))
    return {"exact_match": exact, "exact_match_chars": chars, "generic_shape": shape, "gated_exact_match": gated,
            "near_verbatim": None if exact is None else near_verbatim_matches(task, proof)}


def answer_access_errors(tasks: Iterable[dict[str, Any]], references: Iterable[dict[str, Any]]) -> list[str]:
    """Flag tasks whose private reference proof is copyable from their own prompt: a gated exact
    match, or else a near-verbatim premise-context proof component (see screen_answer_access)."""
    proofs: dict[str, list[Any]] = defaultdict(list)
    for ref in references:
        if isinstance(ref, dict) and isinstance(ref.get("task_id"), str):
            proofs[ref["task_id"]].append(ref.get("proof"))
    errors = []
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            continue
        screens = [screen_answer_access(task, proof) for proof in proofs[task["task_id"]] if isinstance(proof, str)]
        near = [screen["near_verbatim"][0] for screen in screens if screen["near_verbatim"]]
        if any(screen["gated_exact_match"] for screen in screens):
            errors.append(f"{task['task_id']}: private reference proof occurs in the model-visible prompt (answer access)")
        elif near:
            edits = near[0]["token_edits"]
            errors.append(f"{task['task_id']}: premise_context line {near[0]['premise_context_line']} is a near-verbatim "
                          f"copy of the private reference proof ({edits} token edit{'' if edits == 1 else 's'}; answer access)")
    return errors
