import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE / "outputs/lean-compression-eval/src"))
import priority_costs as p


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.price = json.loads((BASE / "outputs/lean-compression-eval/configs/models/gpt-6-astra.json").read_text())["pricing"]
        rates = self.price["rates_per_million"]
        from decimal import Decimal
        self.price["pricing_by_delivered_tier"] = {
            "version": p.VERSION, "region": "global", "missing_delivered_tier": "unavailable",
            "rates_per_million": {"default": rates.copy(),
              "priority": {k: str(Decimal(v)*2) for k,v in rates.items()}}}
        self.gen = {"provider":"openai", "returned_model":"gpt-6-astra",
            "provider_response":{"model":"gpt-6-astra","service_tier":"priority"},
            "usage":{"input_tokens":1000000,"output_tokens":1000000,
                "input_tokens_details":{"cached_tokens":100000,"cache_write_tokens":100000}}}
    def calc(self):
        return p.estimate_cost(self.gen,self.price,provider="openai",model="gpt-6-astra")
    def test_priority_and_no_mutation(self):
        before=copy.deepcopy((self.gen,self.price));r=self.calc()
        self.assertEqual(r["status"],"estimated")
        self.assertEqual(r["total_usd"],"187.4")
        self.assertEqual(r["service_tier"],"priority")
        self.assertEqual((self.gen,self.price),before)
    def test_default_fallback(self):
        self.gen["provider_response"]["service_tier"]="default"
        self.assertEqual(self.calc()["total_usd"],"93.7")
    def test_fast_alias(self):
        self.gen["provider_response"]["service_tier"]="fast"
        self.assertEqual(self.calc()["total_usd"],"187.4")
    def test_missing(self):
        del self.gen["provider_response"]["service_tier"]
        self.assertEqual(self.calc()["reason"],"delivered_service_tier_not_reported")
    def test_unknown(self):
        self.gen["provider_response"]["service_tier"]="ultrafast"
        self.assertEqual(self.calc()["status"],"unavailable")
    def test_conflict(self):
        self.gen["usage"]["service_tier"]="default"
        self.assertEqual(self.calc()["reason"],"conflicting_delivered_service_tiers")
    def test_bad_usage_not_priced(self):
        del self.gen["usage"]["input_tokens_details"]["cache_write_tokens"]
        self.assertEqual(self.calc()["status"],"unavailable")
    def test_install_idempotent(self):
        p.install();p.install()
        self.assertIs(p.costs.estimate_cost,p.estimate_cost)


if __name__ == "__main__":
    unittest.main()
