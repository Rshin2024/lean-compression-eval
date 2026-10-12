"""The exact three prompt conditions in the supplied protocol."""
from .io import digest

SYSTEM = '''You are completing a Lean theorem in the supplied fixed environment.
Return exactly one Lean proof term that fills <PROOF> in the target.
A tactic proof beginning with "by" is allowed.
Prove the exact target using its stated assumptions and the supplied imports.
Do not return Markdown fences, prose, a replacement declaration, imports,
top-level commands, new axioms, sorry, admit, or custom metaprogramming.
Ordinary local have and let expressions are allowed.'''

CONDITIONS = {
    "standard": "Produce a correct Lean proof of the target.",
    "mathematical_compression": "Produce a correct Lean proof in the most mathematically compressed form you can. Use suitable existing definitions and lemmas, and factor repeated reasoning locally, to express the argument compactly. Avoid irrelevant reasoning or artificial expansion.",
    "source_concision": "Produce a correct Lean proof using as few Lean source tokens as possible.",
}


def messages(task: dict, condition: str, toolchain: str) -> list[dict]:
    instruction = CONDITIONS[condition]
    if task.get("import_specs"):
        imports = "\n".join(
            ("public " if spec.get("is_exported") else "") +
            ("meta " if spec.get("is_meta") else "") + "import " +
            ("all " if spec.get("import_all") else "") + spec["module"]
            for spec in task["import_specs"])
    else:
        imports = "\n".join(f"import {name}" for name in task["imports"])
    user = (f"Lean toolchain: {toolchain}\n\nApproved imports:\n{imports}\n\n"
            f"Relevant context:\n{task.get('premise_context', '')}\n\nTask:\n{instruction}\n\n"
            f"theorem {task['target_name']} {task['binders']} :\n"
            f"  {task['proposition']} := <PROOF>")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def prompt_policy_hash() -> str:
    return digest({"version": "exact_plan_v1", "system": SYSTEM, "conditions": CONDITIONS})


def adapt_response(raw: str, policy: str = "identity_v1") -> str:
    if policy == "identity_v1":
        return raw
    if policy == "single_fence_v1":
        import re
        match = re.fullmatch(r"\s*```(?:lean|lean4)?\s*\n(.*?)\n```\s*", raw, re.DOTALL)
        # An optional mechanical wrapper removal only; never choose a proof.
        if match and "```" not in match.group(1):
            return match.group(1)
        return raw
    raise ValueError(f"Unknown response adapter: {policy}")
