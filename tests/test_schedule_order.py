from pathlib import Path
import unittest

from lean_compression.io import read_json
from lean_compression.prompts import CONDITIONS
from lean_compression.store import schedule


PROJECT = Path(__file__).resolve().parents[1]


def task(name, premise_context=""):
    return {"task_id": name, "family_id": "family_" + name, "domain": "sets_logic", "split": "pilot",
            "origin": "fresh", "target_name": "target_" + name, "binders": "(P : Prop) (h : P)",
            "proposition": "P", "imports": ["Mathlib.Logic.Basic"], "premise_context": premise_context,
            "context": ""}


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(PROJECT / "configs/pilot.json")
        self.env = {"environment_hash": "fixture", "lean_toolchain": self.config["environment"]["lean_toolchain"],
                    "mathlib_commit": self.config["environment"]["mathlib_commit"]}

    def order(self, tasks, config):
        return [(slot["task_id"], slot["condition"], slot["sample_index"])
                for slot in schedule(tasks, config, self.env, "fixture")]

    def test_slot_order_is_a_seeded_shuffle_of_every_scheduled_slot(self):
        tasks = [task(f"t{i}") for i in range(4)]
        nested = [(t["task_id"], condition, sample) for t in tasks for condition in CONDITIONS
                  for sample in range(4)]
        first = self.order(tasks, self.config)
        self.assertEqual(first, self.order(tasks, self.config))
        self.assertCountEqual(first, nested)
        self.assertNotEqual(first, nested)
        reseeded = {**self.config, "schedule_seed": self.config["schedule_seed"] + 1}
        second = self.order(tasks, reseeded)
        self.assertCountEqual(second, nested)
        self.assertNotEqual(second, first)
        slots = schedule(tasks, self.config, self.env, "fixture")
        self.assertEqual([slot["schedule_index"] for slot in slots], list(range(len(nested))))

    def test_prompt_byte_cap_counts_utf8_bytes(self):
        cap = self.config["generation"]["context_byte_cap"]
        fits = task("fits", "a" * (cap // 2))
        self.assertEqual(len(self.order([fits], self.config)), 12)
        # Half the cap in characters, but the full cap in UTF-8 bytes.
        wide = task("wide", "α" * (cap // 2))
        with self.assertRaisesRegex(ValueError, "byte cap: wide"):
            schedule([fits, wide], self.config, self.env, "fixture")


if __name__ == "__main__":
    unittest.main()
