"""Offline token-accounting tests; all prices below are synthetic fixtures."""
from copy import deepcopy
from decimal import Decimal, localcontext
import unittest

from lean_compression.costs import estimate_cost, summarize_costs, validate_pricing
from lean_compression.io import digest


def pricing(provider="openai", **updates):
    result = {"schema_version": 1, "currency": "USD", "as_of": "2026-10-13", "provider": provider,
              "model": "gpt-test" if provider == "openai" else "claude-test",
              "sources": ["https://openai.com/api/pricing/" if provider == "openai"
                          else "https://platform.claude.com/docs/en/about-claude/pricing"],
              "rates_per_million": {"input": "2", "cache_read": "0.5", "cache_write": "3", "output": "10"},
              "service_tier": "default" if provider == "openai" else "standard", "region": "global"}
    if provider == "anthropic":
        result["rates_per_million"]["cache_write_1h"] = "4"
    result.update(updates)
    return result


def openai_usage(input=1000, read=200, write=100, output=100):
    return {"provider": "openai", "returned_model": "gpt-test",
            "provider_response": {"service_tier": "default"},
            "usage": {"input_tokens": input, "output_tokens": output,
                      "input_tokens_details": {"cached_tokens": read, "cache_write_tokens": write},
                      "output_tokens_details": {"reasoning_tokens": 90}}}


def anthropic_usage(input=1000, read=200, five=100, hour=50, output=100):
    return {"provider": "anthropic", "returned_model": "claude-test",
            "usage": {"input_tokens": input, "output_tokens": output, "service_tier": "standard",
                      "cache_read_input_tokens": read, "cache_creation_input_tokens": five + hour,
                      "cache_creation": {"ephemeral_5m_input_tokens": five, "ephemeral_1h_input_tokens": hour}}}


class CostTests(unittest.TestCase):
    def assert_unavailable(self, gen, price, reason, **kwargs):
        result = estimate_cost(gen, price, **kwargs)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["total_usd"])
        self.assertIn(reason, result["reason"])
        return result

    def test_openai_cache_read_and_write_are_subtracted_once(self):
        gen, price = openai_usage(), pricing()
        result = estimate_cost(gen, price)
        self.assertEqual(result["status"], "estimated")
        self.assertEqual(result["total_usd"], "0.0028")
        self.assertEqual({k: v["tokens"] for k, v in result["components"].items()},
                         {"input": 700, "cache_read": 200, "cache_write": 100, "output": 100})
        self.assertEqual(result["components"]["cache_write"]["cost_usd"], "0.0003")
        self.assertEqual(result["input_total_tokens"], 1000)
        self.assertEqual(result["pricing_hash"], digest(price))
        self.assertEqual(result["usage_hash"], digest(gen["usage"]))
        self.assertEqual(result["pricing_as_of"], "2026-10-13")

    def test_anthropic_cache_ttls_are_added_to_ordinary_input(self):
        result = estimate_cost(anthropic_usage(), pricing("anthropic"))
        self.assertEqual(result["total_usd"], "0.0036")
        self.assertEqual(result["input_total_tokens"], 1350)
        self.assertEqual(result["components"]["input"]["tokens"], 1000)
        self.assertEqual(result["components"]["cache_write"]["tokens"], 100)
        self.assertEqual(result["components"]["cache_write_1h"]["tokens"], 50)

    def test_output_already_includes_reasoning_tokens(self):
        gen = openai_usage()
        ordinary = estimate_cost(gen, pricing())["total_usd"]
        gen["usage"]["output_tokens_details"]["reasoning_tokens"] = 10**9
        self.assertEqual(estimate_cost(gen, pricing())["total_usd"], ordinary)

    def test_exact_threshold_and_full_request_multipliers(self):
        price = pricing(long_context={"threshold_input_tokens": 1000,
                                      "input_multiplier": "2", "output_multiplier": "1.5"})
        at = estimate_cost(openai_usage(), price)
        over = estimate_cost(openai_usage(input=1001), price)
        self.assertFalse(at["long_context_applied"])
        self.assertEqual(at["total_usd"], "0.0028")
        self.assertTrue(over["long_context_applied"])
        self.assertEqual(over["total_usd"], "0.005104")
        self.assertEqual(over["components"]["cache_read"]["multiplier"], "2")
        self.assertEqual(over["components"]["output"]["multiplier"], "1.5")

    def test_anthropic_long_context_counts_cache_categories_once(self):
        price = pricing("anthropic", long_context={"threshold_input_tokens": 1350,
                                                  "input_multiplier": "2", "output_multiplier": "2"})
        self.assertFalse(estimate_cost(anthropic_usage(), price)["long_context_applied"])
        result = estimate_cost(anthropic_usage(read=201), price)
        self.assertTrue(result["long_context_applied"])
        self.assertEqual(result["total_usd"], "0.007201")

    def test_openai_missing_cache_usage_is_unavailable_even_when_input_is_zero(self):
        for key in ("cached_tokens", "cache_write_tokens"):
            gen = openai_usage(input=0, read=0, write=0, output=0)
            del gen["usage"]["input_tokens_details"][key]
            self.assert_unavailable(gen, pricing(), f"missing_usage:usage.input_tokens_details.{key}")

    def test_anthropic_missing_cache_totals_and_ttl_breakdown(self):
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens", "cache_creation"):
            gen = anthropic_usage()
            del gen["usage"][key]
            self.assert_unavailable(gen, pricing("anthropic"), "missing_usage")
        gen = anthropic_usage()
        gen["usage"]["cache_creation"]["ephemeral_1h_input_tokens"] += 1
        self.assert_unavailable(gen, pricing("anthropic"), "cache_creation_ttl_total_mismatch")
        price = pricing("anthropic")
        del price["rates_per_million"]["cache_write_1h"]
        self.assert_unavailable(anthropic_usage(), price, "missing_pricing:cache_write_1h")
        self.assertEqual(estimate_cost(anthropic_usage(hour=0), price)["status"], "estimated")

    def test_zero_creation_allows_absent_ttl_and_zero_cost_is_observed(self):
        gen = anthropic_usage(input=0, read=0, five=0, hour=0, output=0)
        del gen["usage"]["cache_creation"]
        self.assertEqual(estimate_cost(gen, pricing("anthropic"))["total_usd"], "0")
        self.assertEqual(estimate_cost(openai_usage(0, 0, 0, 0), pricing())["total_usd"], "0")
        bad = deepcopy(gen)
        bad["usage"]["cache_creation"] = {"ephemeral_5m_input_tokens": 1, "ephemeral_1h_input_tokens": 0}
        self.assert_unavailable(bad, pricing("anthropic"), "cache_creation_ttl_total_mismatch")

    def test_invalid_counts_and_inconsistent_openai_cache(self):
        for field in ("input_tokens", "output_tokens"):
            for value in (-1, True, "100", 1.0, None):
                gen = openai_usage()
                gen["usage"][field] = value
                self.assert_unavailable(gen, pricing(), "invalid_token_count")
        gen = openai_usage(input=299)
        self.assert_unavailable(gen, pricing(), "cache_tokens_exceed_total_input")
        for provider, gen in (("openai", openai_usage()), ("anthropic", anthropic_usage())):
            del gen["usage"]["output_tokens"]
            self.assert_unavailable(gen, pricing(provider), "missing_usage:usage.output_tokens")

    def test_reported_tiers_regions_and_identity_must_match(self):
        for tier in ("priority", "flex", "batch", "auto", ""):
            gen = openai_usage()
            gen["provider_response"]["service_tier"] = tier
            self.assert_unavailable(gen, pricing(), "unsupported_service_tier")
        gen = anthropic_usage()
        gen["usage"]["service_tier"] = "fast"
        self.assert_unavailable(gen, pricing("anthropic"), "unsupported_service_tier")
        gen = anthropic_usage()
        gen["usage"]["inference_geo"] = "us"
        self.assert_unavailable(gen, pricing("anthropic"), "unsupported_region")
        for field in ("returned_model", "model_snapshot"):
            gen = openai_usage()
            gen[field] = "other-model"
            self.assert_unavailable(gen, pricing(), "model_mismatch")
        gen = openai_usage()
        gen["provider_response"]["model"] = "other-model"
        self.assert_unavailable(gen, pricing(), "model_mismatch")
        gen = openai_usage()
        gen["provider"] = "anthropic"
        self.assert_unavailable(gen, pricing(), "generation_provider_mismatch")

    def test_missing_reported_tier_has_explicit_assumption(self):
        gen = openai_usage()
        del gen["provider_response"]["service_tier"]
        result = estimate_cost(gen, pricing())
        self.assertEqual(result["status"], "estimated")
        self.assertIn("service_tier_not_reported_using_explicit_pricing_tier", result["assumptions"])

    def test_missing_usage_generation_or_legacy_pricing_never_becomes_zero(self):
        for gen in ({}, {"usage": None}, {"usage": []}):
            self.assert_unavailable(gen, pricing(), "missing_usage")
        self.assert_unavailable(None, pricing(), "generation_not_recorded")
        self.assert_unavailable(openai_usage(), None, "pricing_not_configured")

    def test_pricing_rejects_invalid_values_and_identity(self):
        validate_pricing(pricing(), "openai", "gpt-test")
        for value in (True, 2, 0.1, "NaN", "Infinity", "-0.01", "1_0", " 2"):
            price = pricing()
            price["rates_per_million"]["input"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_pricing(price, "openai", "gpt-test")
        for update in ({"provider": "anthropic"}, {"model": "other"}, {"currency": "EUR"},
                       {"schema_version": True}, {"as_of": "2026-02-30"}, {"as_of": "20261013"},
                       {"sources": ["http://openai.com/pricing"]}, {"sources": ["https://openai.com.evil.test/"]},
                       {"service_tier": "priority"}, {"region": "us"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_pricing(pricing(**update), "openai", "gpt-test")
        price = pricing()
        price["rates_per_million"]["input"] = "0"
        validate_pricing(price, "openai", "gpt-test")

    def test_decimal_results_are_exact_under_low_ambient_precision(self):
        price = pricing()
        price["rates_per_million"] = {"input": "0.123456789123456789", "cache_read": "0", "cache_write": "0", "output": "0"}
        with localcontext() as ctx:
            ctx.prec = 3
            result = estimate_cost(openai_usage(input=1_000_000, read=0, write=0, output=0), price)
        self.assertEqual(result["total_usd"], "0.123456789123456789")

    def test_summary_counts_failed_attempts_and_partial_observed_cost(self):
        valid = {"generation": openai_usage(), "status": "success"}
        failed = {"generation": openai_usage(), "status": "elaboration_failure"}
        incomplete = {"generation": {"usage": {"input_tokens": 10}}, "status": "provider_failure"}
        pending = {"generation": None, "status": "pending"}
        full = summarize_costs([valid, failed], pricing())
        self.assertEqual(full["total_usd"], "0.0056")
        self.assertEqual(full["priced_attempts"], 2)
        partial = summarize_costs([valid, failed, incomplete, pending], pricing())
        self.assertEqual(partial["observed_sum_usd"], "0.0056")
        self.assertIsNone(partial["total_usd"])
        self.assertIsNone(partial["generated_total_usd"])
        self.assertEqual(partial["generated_attempts"], 3)
        self.assertEqual(partial["unavailable_generated_attempts"], 1)
        self.assertEqual(partial["not_generated_attempts"], 1)
        partial = summarize_costs([valid, pending], pricing())
        self.assertEqual(partial["generated_total_usd"], "0.0028")
        self.assertIsNone(partial["total_usd"])

    def test_summary_unknown_and_observed_zero_are_distinct(self):
        for records in ([], [{"generation": None}], [{"generation": {}}]):
            result = summarize_costs(records, pricing())
            self.assertIsNone(result["total_usd"])
            self.assertIsNone(result["observed_sum_usd"])
            self.assertEqual(result["pricing_hash"], digest(pricing()))
        result = summarize_costs([{"generation": openai_usage(0, 0, 0, 0)}], pricing())
        self.assertTrue(result["complete"])
        self.assertEqual(result["total_usd"], "0")

    def test_legacy_summary_and_explicit_provider_model_validation(self):
        result = summarize_costs([{"generation": openai_usage()}], None)
        self.assertIsNone(result["observed_sum_usd"])
        self.assertEqual(result["reason_counts"], {"pricing_not_configured": 1})
        self.assert_unavailable(openai_usage(), pricing(), "invalid_pricing", model="other")
        self.assert_unavailable(openai_usage(), pricing(), "invalid_pricing", provider="anthropic")

    def test_summary_respects_scheduled_identity_without_returned_model(self):
        gen = openai_usage()
        del gen["returned_model"]
        records = [{"model_snapshot": "gpt-test", "generation": gen},
                   {"model": "different-model", "generation": gen}]
        result = summarize_costs(records, pricing(), provider="openai", model="gpt-test")
        self.assertEqual(result["priced_attempts"], 1)
        self.assertEqual(result["unavailable_generated_attempts"], 1)
        self.assertEqual(result["observed_sum_usd"], "0.0028")
        self.assertIsNone(result["total_usd"])
        self.assertEqual(summarize_costs([{"model_snapshot": None, "model": None,
                                         "provider": None, "generation": gen}], pricing(),
                                        provider="openai", model="gpt-test")["total_usd"], "0.0028")


if __name__ == "__main__":
    unittest.main()
