"""The trusted tactic portfolio reaches every alternative.

`first` commits to the first alternative that does not fail. A simp_all that only
rewrites hypotheses therefore ended the old portfolio with unsolved goals, and
omega and aesop never ran. The Lean class runs the real `baseline` command and
skips without the pinned local runtime and the macOS development sandbox.
"""
import contextlib
import io
import json
from pathlib import Path
import platform
import tempfile
import unittest

from lean_compression import cli
from lean_compression.io import read_json, write_json

try:
    from lean_runtime import manifest_path, pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_baseline_portfolio, run from the repository root
    from tests.lean_runtime import manifest_path, pinned_runtime

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
PORTFOLIO = "by first | rfl | assumption | (simp_all; done) | omega | aesop"
COMMITTING = "by first | rfl | assumption | simp_all | omega | aesop"
FIXTURE = {"split": "pilot", "origin": "fresh", "stratum": "provisional_easy", "imports": ["Aesop"],
           "context": "", "premise_context": "", "forbidden_declarations": []}
# simp_all rewrites a hypothesis without closing either goal; omega proves the first, aesop the second.
TASKS = [{**FIXTURE, "task_id": "portfolio_omega", "family_id": "portfolio_omega", "domain": "number_theory",
          "target_name": "portfolio_omega", "binders": "(x y : Nat) (h1 : x + y = 10) (h2 : x = y + 2)",
          "proposition": "x = 6"},
         {**FIXTURE, "task_id": "portfolio_aesop", "family_id": "portfolio_aesop", "domain": "sets_logic",
          "target_name": "portfolio_aesop", "binders": "(P Q R : Prop) (h : True ∧ P ∨ Q) (hp : P → R) (hq : Q → R)",
          "proposition": "R"}]


class PortfolioConfigTests(unittest.TestCase):
    def test_shipped_portfolios_make_every_alternative_terminal(self):
        for name in ("configs/development.json", "configs/pilot.json"):
            self.assertEqual(read_json(PROJECT / name)["tactic_portfolio"], PORTFOLIO, name)


class PortfolioLeanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if platform.system() != "Darwin":
            raise unittest.SkipTest("Needs the macOS development sandbox")
        cls.manifest = manifest_path()
        pinned_runtime(cls.manifest)

    def baseline(self, root, config, name):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["baseline", "--tasks", str(root / "tasks.jsonl"), "--config", str(config),
                      "--environment", str(self.manifest),
                      "--engine", str(ENGINE), "--output", str(root / name)])
        return {record["task_id"]: record for path in (root / name).glob("*.json")
                if (record := read_json(path)).get("task_id")}

    def test_later_alternatives_prove_what_simp_all_only_simplifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tasks.jsonl").write_text("".join(json.dumps(task) + "\n" for task in TASKS))
            write_json(root / "committing.json", {**read_json(PROJECT / "configs/development.json"),
                                                  "tactic_portfolio": COMMITTING})
            before = self.baseline(root, root / "committing.json", "committing")
            after = self.baseline(root, PROJECT / "configs/development.json", "portfolio")
        for task in TASKS:
            with self.subTest(task["task_id"]):
                old, new = before[task["task_id"]], after[task["task_id"]]
                self.assertEqual(old["validity_status"], "elaboration_failure", old["validation"])
                self.assertIn("unsolved goals", old["validation"]["failure_reason"])
                self.assertEqual((new["validity_status"], new["record_kind"], new["proof"]),
                                 ("success", "trusted_tactic_baseline", PORTFOLIO), new["validation"])
                self.assertTrue(new["validation"]["kernel_replay"])


if __name__ == "__main__":
    unittest.main()
