"""Retrying rejected prompts: generate --retry-rejected stops on prompts rejected again and moves on from one command
to the next, a prompt taken after its rejection is no longer passed over, and the shipped configs freeze the
Retry-After budget.

The provider (HTTP) and the docker preflight are mocked; no network, model, Docker daemon or Lean runtime is used.
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_rejection_retries

import test_provider_followups as provider_fixtures
import test_review_followups as followups

from lean_compression.io import append_event, digest, read_json, read_jsonl, write_json
from lean_compression.providers import import_responses
from lean_compression.store import create_run, generation_path, logged_outages, record_generation, records, rejected_prompts

PROJECT = Path(__file__).resolve().parents[1]
reply, http_error, fail = provider_fixtures.reply, provider_fixtures.http_error, provider_fixtures.fail
EVERY = object()  # rejects every prompt


def prompt_of(request):
    return digest(json.loads(request.data)["input"])


def flagged_400():
    raise http_error(400, provider_fixtures.FLAGGED)


def flagged_failed():
    return reply("failed", error={"code": "invalid_prompt", "message": "Invalid prompt: flagged"})


class RetryFixture(provider_fixtures.RunFixture):
    """The fixture's schedule shows its six prompts first at slots 0, 3, 4, 5, 7 and 10 (self.order)."""

    def setUp(self):
        super().setUp()
        self.order = list(dict.fromkeys(slot["prompt_hash"] for slot in self.slots))

    def refreeze(self, name, **retry):
        """The same tasks frozen into a new run, with retry settings changed."""
        manifest = read_json(self.run / "manifest.json")
        generation = manifest["config"]["generation"]
        config = {**manifest["config"], "generation": {**generation, "retry": {**generation["retry"], **retry}}}
        self.run = self.run.parent / name
        self.manifest = create_run(self.run, manifest["tasks"], config, manifest["environment"], split="pilot",
                                   model=provider_fixtures.MODEL, development=True)
        self.slots = self.manifest["slots"]

    def rejecting(self, prompts, rejection=flagged_400):
        """The provider rejects these prompts (or EVERY one) and answers the others."""
        def answer(request):
            return rejection() if prompts is EVERY or prompt_of(request) in prompts else reply()
        return answer

    def command(self, answer, *options):
        """generate through the CLI: (parsed stdout or exit code, stderr)."""
        with self.provider(answer):
            return followups.quiet(["generate", "--run", self.run, *options])

    def asked(self):
        return [prompt_of(request) for request in self.requests]

    def pending(self):
        return {row["attempt_id"] for row in records(self.run) if row["validity_status"] == "not_generated"}

    def slots_of(self, prompt):
        return [slot for slot in self.slots if slot["prompt_hash"] == prompt]

    def remember_every_prompt(self, rejection=flagged_400):
        """Plain commands under a systemic rejection until one sends nothing; the prompts in the order rejected."""
        seen = []
        for _ in range(len(self.order) + 1):
            result, log = self.command(self.rejecting(EVERY, rejection))
            if not self.requests:
                break
            seen += self.asked()
        self.assertEqual(sorted(seen), sorted(self.order))  # each prompt was asked once
        return seen


class RetryStopTests(RetryFixture):
    """Issue 1: --retry-rejected stops after max_consecutive_prompt_rejections rejections in a row, prompts rejected
    again included, and sends those prompts after every other pending slot, the one rejected longest ago first, so
    limited retries make progress."""

    def test_a_systemic_rejection_stops_a_retry_at_the_cap(self):
        for cap, rejection in ((1, flagged_400), (2, flagged_400), (3, flagged_400), (3, flagged_failed)):
            with self.subTest(cap=cap, rejection=rejection.__name__):
                self.refreeze(f"cap-{cap}-{rejection.__name__}", max_consecutive_prompt_rejections=cap)
                seen = self.remember_every_prompt(rejection)
                code, log = self.command(self.rejecting(EVERY, rejection), "--retry-rejected")
                self.assertEqual((code, self.asked()), (2, seen[:cap]))
                count = "a prompt again" if cap == 1 else f"{cap} different prompts in a row ({cap} of them again)"
                self.assertIn(f"Provider rejected {count} as invalid_prompt", log)
                self.assertIn("pass --retry-rejected to send them again, those rejected longest ago first", log)
                # Nothing is recorded: every slot stays pending, and analyze still counts each one as rejected.
                self.assertEqual((self.generations(), self.markers(), len(self.pending())), ([], [], 24))
                self.assertEqual(logged_outages(self.run)["provider_outages"]["prompt_rejected_attempts"], 24)

    def test_limited_retries_move_on_to_the_prompts_rejected_longest_ago(self):
        seen = self.remember_every_prompt()
        for expected in (seen[0:2], seen[2:4], seen[4:6], seen[0:2]):
            # Two rejected again, below the cap of 3: the limit ends the command, and the next one asks two others.
            result, log = self.command(self.rejecting(EVERY), "--retry-rejected", "--limit", "2")
            self.assertEqual(result, {"new_generation_records": 0, "prompt_rejections": 2,
                                      "skipped_rejected_prompt_slots": 3, "scheduled_attempts": 24}, log)
            self.assertEqual(self.asked(), expected)
        self.assertEqual(list(rejected_prompts(self.run, self.slots)), seen[2:] + seen[:2])
        # Once the provider takes the prompt next in turn, the limited retry records generations of it.
        result, log = self.command(lambda request: reply(), "--retry-rejected", "--limit", "2")
        self.assertEqual((result["new_generation_records"], self.asked()), (2, [seen[2]] * 2))

    def test_retries_follow_the_other_pending_slots_which_keep_their_order(self):
        flagged = self.order[3]  # at slots 5, 11, 12 and 23
        result, log = self.command(self.rejecting({flagged}), "--limit", "6")
        self.assertEqual((result["new_generation_records"], result["prompt_rejections"]), (5, 1), log)
        # Still rejected, the prompt never holds up the rest: a limited retry records the next slots of the schedule.
        result, log = self.command(self.rejecting({flagged}), "--retry-rejected", "--limit", "4")
        self.assertEqual(result, {"new_generation_records": 4, "prompt_rejections": 0,
                                  "skipped_rejected_prompt_slots": 0, "scheduled_attempts": 24}, log)
        self.assertNotIn(flagged, self.asked())
        logged = len(read_jsonl(self.run / "transport.jsonl"))
        result, log = self.command(lambda request: reply(), "--retry-rejected")
        self.assertEqual((result["new_generation_records"], result["prompt_rejections"]), (15, 0), log)
        started = [event["attempt_id"] for event in read_jsonl(self.run / "transport.jsonl")[logged:]
                   if event["event"] == "request_started"]
        self.assertEqual(started, [slot["attempt_id"] for slot in self.slots[10:] if slot["prompt_hash"] != flagged] +
                         [slot["attempt_id"] for slot in self.slots_of(flagged)])

    def test_new_prompts_and_prompts_rejected_again_count_together(self):
        result, log = self.command(self.rejecting(set(self.order[:2])), "--limit", "2")
        self.assertEqual((result["new_generation_records"], result["prompt_rejections"]), (0, 2), log)
        # Every slot but those of the two rejected prompts and of one new prompt (first at slot 10) has a generation.
        import_responses(self.run, [{key: slot[key] for key in ("attempt_id", "prompt_hash", "model_snapshot")} |
                                    {"raw_response": "by exact h"} for slot in self.slots
                                    if slot["prompt_hash"] in self.order[2:5]])
        # The new prompt goes first and is rejected, then the two are sent again and rejected: three in a row.
        code, log = self.command(self.rejecting({*self.order[:2], self.order[5]}), "--retry-rejected")
        self.assertEqual((code, self.asked()), (2, [self.order[5], *self.order[:2]]))
        self.assertIn("Provider rejected 3 different prompts in a row (2 of them again) as invalid_prompt", log)

    def test_a_generation_between_rejections_starts_the_count_again(self):
        seen = self.remember_every_prompt()
        # Rejected, rejected, taken, rejected, rejected, taken: never three in a row, so the retry goes through.
        result, log = self.command(self.rejecting({seen[0], seen[1], seen[3], seen[4]}), "--retry-rejected")
        self.assertEqual(result, {"new_generation_records": 8, "prompt_rejections": 4,
                                  "skipped_rejected_prompt_slots": 12, "scheduled_attempts": 24}, log)
        self.assertEqual(self.asked(), [seen[0], seen[1], *[seen[2]] * 4, seen[3], seen[4], *[seen[5]] * 4])

    def test_a_lost_retry_keeps_its_marker_and_stops_every_later_command(self):
        a, b = self.order[:2]
        self.command(self.rejecting({a, b}))
        def lost(request):
            if prompt_of(request) == b:
                raise TimeoutError("The read operation timed out")
            return self.rejecting({a})(request)
        outcome = self.generate(lost, retry_rejected=True)
        self.assertRegex(str(outcome), "outcome is unknown")
        self.assertEqual((self.asked(), len(self.markers())), ([a, b], 1))
        # b is now the prompt rejected longest ago, so a retry would send it first: its retained marker stops that.
        for options in (("--retry-rejected",), ()):
            with self.subTest(options=options):
                code, log = self.command(lambda request: reply(), *options)
                self.assertEqual((code, self.requests), (2, []))
                self.assertIn("Unresolved in-flight call", log)
        self.assertEqual(len(self.markers()), 1)


class ClearedRejectionTests(RetryFixture):
    """Issue 2: a generation of any slot of a rejected prompt, recorded after its latest rejection, ends the rejection,
    so generate sends its other slots without --retry-rejected and analyze no longer counts them as rejected."""

    def setUp(self):
        super().setUp()
        self.flagged = self.order[3]  # at slots 5, 11, 12 and 23

    def test_a_sample_taken_on_retry_clears_the_rejection(self):
        result, log = self.command(self.rejecting({self.flagged}))
        self.assertEqual(result, {"new_generation_records": 20, "prompt_rejections": 1,
                                  "skipped_rejected_prompt_slots": 3, "scheduled_attempts": 24})
        result, log = self.command(lambda request: reply(), "--retry-rejected", "--limit", "1")
        self.assertEqual((result["new_generation_records"], self.asked()), (1, [self.flagged]))
        self.assertEqual(rejected_prompts(self.run, self.slots), set())
        outages = logged_outages(self.run)["provider_outages"]
        self.assertEqual((outages["unresolved_attempts"], outages["prompt_rejected_attempts"]), (0, 0))
        result, log = followups.quiet(["analyze", "--run", self.run, "--output", self.run.parent / "report"])
        self.assertIsInstance(result, dict, log)
        self.assertNotIn("invalid_prompt", (self.run.parent / "report/report.html").read_text(encoding="utf-8"))
        result, log = self.command(lambda request: reply())
        self.assertEqual(result, {"new_generation_records": 3, "prompt_rejections": 0,
                                  "skipped_rejected_prompt_slots": 0, "scheduled_attempts": 24})
        self.assertEqual((self.asked(), self.pending()), ([self.flagged] * 3, set()))

    def test_any_slot_and_any_source_of_generation_clears_it(self):
        self.command(self.rejecting({self.flagged}))
        slot = self.slots_of(self.flagged)[2]  # never sent; imported after the rejection
        import_responses(self.run, [{key: slot[key] for key in ("attempt_id", "prompt_hash", "model_snapshot")}
                                    | {"raw_response": "by exact h"}])
        result, log = self.command(lambda request: reply())
        self.assertEqual((result["new_generation_records"], result["skipped_rejected_prompt_slots"]), (3, 0))
        self.assertEqual(self.pending(), set())

    def test_a_generation_before_the_rejection_does_not_clear_it(self):
        taken = []
        def first_then_rejected(request):
            if prompt_of(request) != self.flagged:
                return reply()
            taken.append(request)
            return reply() if len(taken) == 1 else flagged_400()
        result, log = self.command(first_then_rejected)
        self.assertEqual(result, {"new_generation_records": 21, "prompt_rejections": 1,
                                  "skipped_rejected_prompt_slots": 2, "scheduled_attempts": 24})
        result, log = self.command(lambda request: reply())
        self.assertEqual((result["skipped_rejected_prompt_slots"], self.requests), (3, []))
        self.assertEqual(logged_outages(self.run)["provider_outages"]["prompt_rejected_attempts"], 3)

    def test_a_rejection_after_the_clearing_generation_holds_again(self):
        self.command(self.rejecting({self.flagged}))
        self.command(lambda request: reply(), "--retry-rejected", "--limit", "1")
        result, log = self.command(self.rejecting({self.flagged}))
        self.assertEqual(result, {"new_generation_records": 0, "prompt_rejections": 1,
                                  "skipped_rejected_prompt_slots": 2, "scheduled_attempts": 24})
        result, log = self.command(lambda request: reply())
        self.assertEqual((result["skipped_rejected_prompt_slots"], self.requests), (3, []))

    def test_times_that_cannot_be_compared_never_clear_a_rejection(self):
        # A generation after the rejection, as far as anything shows, yet with a time missing or not comparable.
        for label, untimed, created in (("rejection without recorded_at", True, None),
                                        ("unreadable created_at", False, "after the rejection"),
                                        ("created_at without a time zone", False, "2999-01-01T00:00:00"),
                                        ("no created_at", False, "missing")):
            with self.subTest(label):
                self.refreeze(label.replace(" ", "-"))
                rejected, generated = self.slots_of(self.flagged)[:2]
                event = {"attempt_id": rejected["attempt_id"], "event": "no_model_outcome", "prompt_rejected": True}
                if untimed:
                    with (self.run / "transport.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps(event) + "\n")
                else:
                    append_event(self.run / "transport.jsonl", event)
                if created is None:
                    record_generation(self.run, generated, "by exact h", generation_status="completed")
                elif created == "missing":
                    write_json(generation_path(self.run, generated["attempt_id"]), {
                        "attempt_id": generated["attempt_id"], "prompt_hash": generated["prompt_hash"],
                        "raw_response": "by exact h", "raw_response_hash": digest("by exact h")})
                else:
                    with patch("lean_compression.store.now", return_value=created):
                        record_generation(self.run, generated, "by exact h", generation_status="completed")
                self.assertEqual(rejected_prompts(self.run, self.slots), {self.flagged})
                result, log = self.command(lambda request: reply())
                self.assertEqual((result["new_generation_records"], result["skipped_rejected_prompt_slots"]), (20, 3))
                self.assertNotIn(self.flagged, self.asked())


class RetryAfterBudgetTests(RetryFixture):
    """Issue 3: the shipped configs carry retry.max_retry_after_seconds, so a frozen run records the budget in force."""

    def test_the_shipped_configs_freeze_the_retry_after_budget(self):
        for name in ("configs/pilot.json", "configs/development.json"):
            self.assertEqual(read_json(PROJECT / name)["generation"]["retry"]["max_retry_after_seconds"], 60, name)
        self.assertEqual(read_json(self.run / "manifest.json")["config"]["generation"]["retry"]["max_retry_after_seconds"], 60)
        throttled = lambda request: fail(http_error(429, headers={"Retry-After": "30"}))
        outcome = self.generate(throttled)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual((len(self.requests), self.sleeps), (3, [30, 30]))
        # The frozen value is the one in force.
        self.refreeze("short-budget", max_retry_after_seconds=5)
        outcome = self.generate(throttled)
        self.assertIsInstance(outcome, RuntimeError)
        self.assertEqual((len(self.requests), self.sleeps), (1, []))


if __name__ == "__main__":
    unittest.main()
