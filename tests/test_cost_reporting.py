import csv
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.analysis import analyze, write_report
from lean_compression.cost_reporting import build_cost_report, cost_html, write_cost_report
from lean_compression.io import digest


def pricing():
    return {"schema_version": 1, "provider": "openai", "model": "model-a", "currency": "USD",
            "as_of": "2026-10-13", "service_tier": "default", "region": "global",
            "sources": ["https://developers.openai.com/api/docs/pricing"],
            "rates_per_million": {"input": "2", "cache_read": "0.2", "cache_write": "2.5", "output": "10"}}


def generation(input_tokens=10, output_tokens=5, read=2, write=3, status="completed"):
    return {"generation_status": status, "raw_response": "SECRET_REFERENCE_PROOF",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "input_tokens_details": {"cached_tokens": read, "cache_write_tokens": write},
                      "output_tokens_details": {"reasoning_tokens": output_tokens}},
            "provider_response": {"service_tier": "default", "output": "SECRET_PROVIDER_RESPONSE"}}


def row(task="q1", model="model-a", condition="standard", sample=0, gen=None, status="success"):
    return {"task_id": task, "model_snapshot": model, "condition": condition,
            "sample_index": sample, "generation": gen, "validity_status": status,
            "proof_summary": "SECRET_CURATOR_HINT"}


class CostReportingTests(unittest.TestCase):
    def records(self):
        return [row(gen=generation()),
                row(condition="mathematical_compression", gen=generation(4, 0, 0, 0, "truncated_output"),
                    status="truncated_output"),
                row(task="q2", gen=generation(10, 1, 0, 0), status="elaboration_failure"),
                row(task="q2", model="model-b", gen=generation()),
                row(task="q2", condition="source_concision", gen=None, status="missing_record")]

    def test_counts_all_slots_and_aggregates_exactly_across_questions_models_conditions(self):
        report = build_cost_report(self.records(), pricing(), provider="openai")
        summary = report["summary"]
        self.assertEqual(summary["scheduled_attempts"], 5)
        self.assertEqual(summary["generated_attempts"], 4)
        self.assertEqual(summary["priced_attempts"], 3)
        self.assertEqual(summary["not_generated_attempts"], 1)
        self.assertEqual(summary["unavailable_generated_attempts"], 1)
        self.assertEqual(summary["observed_sum_usd"], "0.0001059")
        self.assertIsNone(summary["total_usd"])
        self.assertIsNone(summary["generated_total_usd"])
        by_question = {r["task_id"]: r["summary"] for r in report["by_question"]}
        self.assertEqual(by_question["q1"]["total_usd"], "0.0000759")
        self.assertEqual(by_question["q2"]["observed_sum_usd"], "0.00003")
        by_condition = {r["condition"]: r["summary"] for r in report["by_condition"]}
        self.assertEqual(by_condition["standard"]["observed_sum_usd"], "0.0000979")
        self.assertEqual(by_condition["mathematical_compression"]["total_usd"], "0.000008")
        by_model = {r["model_snapshot"]: r["summary"] for r in report["by_model"]}
        self.assertEqual(by_model["model-a"]["observed_sum_usd"], "0.0001059")
        self.assertIsNone(by_model["model-b"]["observed_sum_usd"])
        self.assertEqual(len(report["by_question_model_condition"]), 5)
        self.assertEqual(report["attempts"][1]["generation_status"], "truncated_output")
        self.assertEqual(report["attempts"][2]["cost"]["status"], "estimated")

    def test_legacy_missing_pricing_and_missing_usage_stay_unknown(self):
        for price, gen in ((None, generation()), (pricing(), {}), (pricing(), None)):
            report = build_cost_report([row(gen=gen)], price)
            self.assertEqual(report["attempts"][0]["cost"]["status"], "unavailable")
            self.assertIsNone(report["summary"]["total_usd"])
            self.assertIsNone(report["summary"]["observed_sum_usd"])
            self.assertNotIn("$0", cost_html(report))

    def test_explicit_zero_usage_is_a_valid_zero_cost(self):
        report = build_cost_report([row(gen=generation(0, 0, 0, 0))], pricing())
        self.assertEqual(report["summary"]["total_usd"], "0")
        self.assertEqual(report["summary"]["observed_sum_usd"], "0")
        self.assertEqual(report["summary"]["priced_attempts"], 1)

    def test_cost_artifacts_export_evidence_and_exclude_proofs_and_hints(self):
        price = pricing()
        report = build_cost_report(self.records(), price)
        price["rates_per_million"]["input"] = "999"
        self.assertEqual(report["pricing_schedule"]["rates_per_million"]["input"], "2")
        self.assertEqual(report["pricing_hash"], digest(pricing()))
        self.assertEqual(report["pricing_schedule"]["sources"], pricing()["sources"])
        for forbidden in ("SECRET_REFERENCE_PROOF", "SECRET_PROVIDER_RESPONSE", "SECRET_CURATOR_HINT", "raw_response"):
            self.assertNotIn(forbidden, json.dumps(report))
        with tempfile.TemporaryDirectory() as folder:
            paths = write_cost_report(report, folder)
            self.assertEqual(json.loads(Path(paths["costs"]).read_text()), report)
            with Path(paths["cost_attempts"]).open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 5)
            self.assertEqual(rows[0]["total_usd"], "0.0000679")
            self.assertEqual(rows[0]["uncached_input_tokens"], "5")
            self.assertEqual(rows[0]["output_tokens"], "5")
            self.assertEqual(rows[3]["total_usd"], "")
            self.assertEqual(rows[4]["generated"], "False")
            self.assertEqual(rows[0]["usage_hash"], digest(self.records()[0]["generation"]["usage"]))
            with Path(paths["cost_summary"]).open() as stream:
                summaries = list(csv.DictReader(stream))
            self.assertEqual(summaries[0]["group_type"], "all")
            self.assertEqual(summaries[0]["observed_sum_usd"], "0.0001059")
            self.assertEqual({r["group_type"] for r in summaries},
                             {"all", "by_question", "by_model", "by_condition", "by_question_model_condition"})

    def test_html_and_csv_escape_untrusted_identity_labels(self):
        records = [row(task="<script>alert(1)</script>", gen=generation()),
                   row(task="=HYPERLINK(\"bad\")", gen=generation())]
        report = build_cost_report(records, pricing())
        report["pricing_basis"] = "explicit_retrospective_pricing"
        report["transport_coverage"] = {"scope": "Historical events <not invoices>",
                                        "outcome_unknown_events": 2, "invoice_reconciled": False}
        rendered = cost_html(report)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("Unknown retry and transport charges", rendered)
        self.assertIn("not an invoice", rendered)
        self.assertIn("Explicit retrospective pricing", rendered)
        self.assertIn("Historical events &lt;not invoices&gt;", rendered)
        self.assertIn("Outcome unknown events</td><td>2", rendered)
        with tempfile.TemporaryDirectory() as folder:
            paths = write_cost_report(report, folder)
            with Path(paths["cost_attempts"]).open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertTrue(rows[1]["task_id"].startswith("'=HYPERLINK"))

    def test_duplicate_slot_and_duplicate_attempt_id_are_rejected(self):
        duplicate = row(gen=generation())
        with self.assertRaisesRegex(ValueError, "duplicate scheduled"):
            build_cost_report([duplicate, duplicate], pricing())
        a, b = row(task="q1"), row(task="q2")
        a["attempt_id"] = b["attempt_id"] = "same"
        with self.assertRaisesRegex(ValueError, "duplicate scheduled"):
            build_cost_report([a, b], pricing())

    def test_analyze_includes_missing_slots_and_writes_cost_exports(self):
        task = {"task_id": "q1", "family_id": "f1", "domain": "algebra", "origin": "fresh"}
        record = row(gen=generation(), status="elaboration_failure")
        report = analyze([task], [record], {}, {"bootstrap_replicates": 0, "pricing": pricing(),
                                               "generation": {"provider": "openai"}})
        summary = report["costs"]["summary"]
        self.assertEqual(summary["scheduled_attempts"], 12)
        self.assertEqual(summary["generated_attempts"], 1)
        self.assertEqual(summary["not_generated_attempts"], 11)
        self.assertEqual(summary["observed_sum_usd"], "0.0000679")
        self.assertEqual(len(report["costs"]["attempts"]), 12)
        with tempfile.TemporaryDirectory() as folder:
            paths = write_report(report, folder)
            for key in ("costs", "cost_attempts", "cost_summary"):
                self.assertTrue(Path(paths[key]).is_file())
            rendered = Path(paths["html"]).read_text()
            self.assertIn("Estimated API token costs", rendered)
            self.assertNotIn("No API cost is inferred", rendered)
            self.assertIn("cost_attempts.csv", rendered)
            stored = json.loads(Path(paths["json"]).read_text())
            self.assertEqual(stored["costs"], report["costs"])

    def test_empty_schedule_exports_and_does_not_invent_total(self):
        report = build_cost_report([], None)
        self.assertIsNone(report["summary"]["total_usd"])
        self.assertIsNone(report["summary"]["observed_sum_usd"])
        with tempfile.TemporaryDirectory() as folder:
            paths = write_cost_report(report, folder)
            self.assertIn("cost_status", Path(paths["cost_attempts"]).read_text())


if __name__ == "__main__":
    unittest.main()
