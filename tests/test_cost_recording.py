import copy
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.cli import dispatch, parser
from lean_compression.io import read_json
from lean_compression.providers import response_payload
from lean_compression.store import create_run, record_generation

ROOT = Path(__file__).resolve().parents[1]


class CostRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / "run"
        self.config = read_json(ROOT / "configs/pilot.json")
        self.config["validation"]["isolation"] = "macos_sandbox"
        self.config["generation"]["provider"] = "openai"
        self.config["generation"]["model_snapshot"] = "gpt-6-sol"
        self.config["generation"]["service_tier"] = "default"
        self.config["cost_tracking_required"] = True
        self.config["pricing"] = read_json(ROOT / "configs/pricing/2026-10-13.json")["models"]["gpt-6-sol"]
        self.env = {"environment_hash": "fixture", "hash_mode": "development_metadata_only",
                    "lean_toolchain": self.config["environment"]["lean_toolchain"],
                    "mathlib_commit": self.config["environment"]["mathlib_commit"]}
        self.tasks = [{"task_id": "one", "family_id": "family", "domain": "sets_logic", "split": "pilot",
                       "origin": "fresh", "target_name": "testTarget", "binders": "(P : Prop) (h : P)",
                       "proposition": "P", "imports": ["Mathlib.Logic.Basic"], "premise_context": "", "context": ""}]
        self.usage = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300},
                      "output_tokens": 100, "output_tokens_details": {"reasoning_tokens": 50}}

    def freeze(self):
        return create_run(self.run, self.tasks, self.config, self.env,
                          split="pilot", model="gpt-6-sol", development=True)

    def test_recorded_cost_uses_frozen_rates_and_resume_does_not_rewrite(self):
        run = self.freeze()
        self.config["pricing"]["rates_per_million"]["output"] = "999"
        slot = run["slots"][0]
        metadata = {"generation_status": "truncated_output", "usage": self.usage,
                    "returned_model": "gpt-6-sol", "provider_response": {"service_tier": "default"}}
        first = record_generation(self.run, slot, "partial", **metadata)
        self.assertEqual(first["cost_estimate"]["total_usd"], "0.00279")
        self.assertEqual(first["cost_estimate"]["pricing_as_of"], "2026-10-13")
        path = self.run / "generations" / (slot["attempt_id"] + ".json")
        before = path.read_bytes()
        self.assertEqual(record_generation(self.run, slot, "partial", **metadata), first)
        self.assertEqual(path.read_bytes(), before)

    def test_required_or_invalid_prices_fail_before_freeze(self):
        self.config.pop("pricing")
        with self.assertRaisesRegex(ValueError, "pricing snapshot"):
            self.freeze()
        self.assertFalse((self.run / "manifest.json").exists())

    def test_legacy_run_does_not_gain_invented_cost_record(self):
        self.config.pop("pricing")
        self.config.pop("cost_tracking_required")
        run = self.freeze()
        record = record_generation(self.run, run["slots"][0], "by exact h", generation_status="completed")
        self.assertNotIn("cost_estimate", record)

    def test_missing_usage_stays_unavailable(self):
        run = self.freeze()
        record = record_generation(self.run, run["slots"][0], "", generation_status="refusal")
        self.assertEqual(record["cost_estimate"]["status"], "unavailable")
        self.assertIsNone(record["cost_estimate"]["total_usd"])

    def test_explicit_service_tier_and_legacy_payload_compatibility(self):
        run = self.freeze()
        slot = run["slots"][0]
        self.assertEqual(response_payload(slot, self.config["generation"])["service_tier"], "default")
        old = copy.deepcopy(self.config["generation"])
        old.pop("service_tier")
        self.assertNotIn("service_tier", response_payload(slot, old))

    def test_costs_command_exports_without_generation_or_verification(self):
        run = self.freeze()
        record_generation(self.run, run["slots"][0], "SECRET_PROOF", generation_status="completed", usage=self.usage)
        out = Path(self.tmp.name) / "costs"
        result = dispatch(parser().parse_args(["costs", "--run", str(self.run), "--output", str(out)]))
        self.assertEqual(result["pricing_basis"], "frozen_run_pricing")
        report = json.loads((out / "costs.json").read_text())
        self.assertEqual(report["run_hash"], run["run_hash"])
        self.assertFalse(report["transport_coverage"]["invoice_reconciled"])
        self.assertNotIn("SECRET_PROOF", (out / "costs.json").read_text())
        self.assertTrue((out / "cost_attempts.csv").exists())
        self.assertTrue((out / "cost_summary.csv").exists())
        self.assertTrue((out / "costs.html").exists())
        with self.assertRaisesRegex(FileExistsError, "new or empty"):
            dispatch(parser().parse_args(["costs", "--run", str(self.run), "--output", str(out)]))


if __name__ == "__main__":
    unittest.main()
