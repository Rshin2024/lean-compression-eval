"""Round 4 regressions: a rejected prompt never blocks the rest of the run, and the import nesting check ends on
cyclic values.

The provider (HTTP) and the docker preflight are mocked; no network, model, Docker daemon or Lean runtime is used.
The checks on cyclic values run in a child interpreter under a time bound, so a regression fails instead of
hanging the suite.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures, also when run as tests.test_round4

import test_provider_followups as provider_fixtures
import test_review_followups as followups

from lean_compression.io import append_event, digest, read_json, read_jsonl
from lean_compression.providers import nesting_depth
from lean_compression.store import create_run, logged_outages, records, rejected_prompts

SRC = Path(__file__).resolve().parents[1] / "src"
FLAGGED = provider_fixtures.FLAGGED
reply, http_error = provider_fixtures.reply, provider_fixtures.http_error
EVERY = object()  # rejects every prompt


def prompt_of(request):
    return digest(json.loads(request.data)["input"])


class RejectedPromptTests(provider_fixtures.RunFixture):
    """R2-providers-1, as regressed in round 3: a rejected prompt is remembered per prompt hash, so it never blocks
    the slots behind it."""

    def setUp(self):
        super().setUp()
        self.flag(next(slot["prompt_hash"] for slot in self.slots
                       if slot["task_id"] == "two" and slot["condition"] == "standard"))

    def flag(self, prompt):
        self.prompt = prompt
        self.flagged = {slot["attempt_id"] for slot in self.slots if slot["prompt_hash"] == prompt}
        self.positions = [index for index, slot in enumerate(self.slots) if slot["prompt_hash"] == prompt]
        self.assertEqual(len(self.positions), 4)

    def refreeze(self, name, **retry):
        """The same tasks frozen into a new run, with retry settings changed."""
        manifest = read_json(self.run / "manifest.json")
        generation = manifest["config"]["generation"]
        config = {**manifest["config"], "generation": {**generation, "retry": {**generation["retry"], **retry}}}
        self.run = self.run.parent / name
        self.manifest = create_run(self.run, manifest["tasks"], config, manifest["environment"], split="pilot",
                                   model=provider_fixtures.MODEL, development=True)
        self.slots = self.manifest["slots"]
        self.flag(self.prompt)

    def rejecting(self, prompts=None):
        """The provider rejects the flagged prompt (or the given ones, or EVERY one) and answers the others."""
        prompts = {self.prompt} if prompts is None else prompts
        def answer(request):
            if prompts is EVERY or prompt_of(request) in prompts:
                raise http_error(400, FLAGGED)
            return reply()
        return answer

    def command(self, answer, *options):
        """generate through the CLI: (parsed stdout or exit code, stderr)."""
        with self.provider(answer):
            return followups.quiet(["generate", "--run", self.run, *options])

    def asked(self):
        return [prompt_of(request) for request in self.requests]

    def pending(self):
        return {row["attempt_id"] for row in records(self.run) if row["validity_status"] == "not_generated"}

    def test_limit_batches_reach_every_slot_of_the_other_prompts(self):
        # The reviewer's case: README-style --limit batches, the first of which reaches a third sample of one flagged
        # prompt. Each prompt in turn is the flagged one, so its samples sit at different schedule positions.
        for index, prompt in enumerate(sorted({slot["prompt_hash"] for slot in self.slots})):
            with self.subTest(prompt=index):
                self.prompt = prompt
                self.refreeze(f"batches-{index}")
                limit, requests = self.positions[2] + 1, 0
                for _ in range(30):
                    result, log = self.command(self.rejecting(), "--limit", str(limit))
                    self.assertIsInstance(result, dict, log)
                    requests += len(self.requests)
                    if not self.requests:
                        break
                self.assertEqual(result, {"new_generation_records": 0, "prompt_rejections": 0,
                                          "skipped_rejected_prompt_slots": 4, "scheduled_attempts": 24})
                self.assertEqual(self.pending(), self.flagged)
                self.assertEqual(len(self.generations()), 20)
                # The flagged prompt reached the provider once in all those commands.
                self.assertEqual((requests, len(self.outcomes()), self.markers()), (21, 1, []))

    def test_an_unlimited_rerun_after_a_batch_reaches_every_slot(self):
        self.generate(self.rejecting(), limit=self.positions[2] + 1)
        outcome = self.generate(self.rejecting())
        self.assertEqual((outcome["prompt_rejections"], self.asked().count(self.prompt)), (0, 0))
        self.assertEqual(self.pending(), self.flagged)

    def test_one_command_sends_a_rejected_prompt_once(self):
        outcome = self.generate(self.rejecting())
        self.assertEqual(outcome, {"new_generation_records": 20, "prompt_rejections": 1,
                                   "skipped_rejected_prompt_slots": 3, "scheduled_attempts": 24})
        self.assertEqual((len(self.requests), self.asked().count(self.prompt), self.markers()), (21, 1, []))
        first = self.slots[self.positions[0]]["attempt_id"]
        logged = {event["attempt_id"] for event in read_jsonl(self.run / "transport.jsonl")}
        # The other three samples were passed over: no request, no event, still pending.
        self.assertEqual(self.flagged & logged, {first})
        self.assertEqual(self.pending(), self.flagged)

    def test_later_commands_pass_over_the_rejected_prompt_and_say_so(self):
        self.generate(self.rejecting())
        # Even a provider that would now answer is not asked again without --retry-rejected.
        result, log = self.command(lambda request: reply())
        self.assertEqual(result, {"new_generation_records": 0, "prompt_rejections": 0,
                                  "skipped_rejected_prompt_slots": 4, "scheduled_attempts": 24})
        self.assertEqual((self.requests, log), ([], ""))
        self.assertEqual(self.pending(), self.flagged)

    def test_retry_rejected_sends_rejected_prompts_again(self):
        self.refreeze("strict", max_consecutive_prompt_rejections=1)
        code, log = self.command(self.rejecting())
        self.assertEqual((code, len(self.requests)), (2, self.positions[0] + 1))
        self.assertIn("Provider rejected a prompt as invalid_prompt", log)
        result, log = self.command(self.rejecting())
        self.assertEqual(result, {"new_generation_records": 20 - self.positions[0], "prompt_rejections": 0,
                                  "skipped_rejected_prompt_slots": 4, "scheduled_attempts": 24})
        # Rejected again: one request for that prompt, and no stop even at a cap of 1, because it is no news.
        result, log = self.command(self.rejecting(), "--retry-rejected")
        self.assertEqual(result, {"new_generation_records": 0, "prompt_rejections": 1,
                                  "skipped_rejected_prompt_slots": 3, "scheduled_attempts": 24})
        self.assertEqual(self.asked(), [self.prompt])
        # Accepted at last. Without the option, the remembered rejection still holds for the rest.
        result, log = self.command(lambda request: reply(), "--retry-rejected", "--limit", "1")
        self.assertEqual((result["new_generation_records"], len(self.requests)), (1, 1))
        result, log = self.command(lambda request: reply())
        self.assertEqual((result["skipped_rejected_prompt_slots"], self.requests), (3, []))
        result, log = self.command(lambda request: reply(), "--retry-rejected")
        self.assertEqual((result["new_generation_records"], len(self.requests)), (3, 3))
        self.assertEqual((self.pending(), len(self.generations())), (set(), 24))

    def test_the_breaker_counts_different_new_prompts_only(self):
        seen = []
        for _ in range(2):
            code, log = self.command(self.rejecting(EVERY))
            self.assertEqual((code, len(self.requests), len(set(self.asked()))), (2, 3, 3))
            self.assertIn("Provider rejected 3 different prompts in a row as invalid_prompt", log)
            self.assertIn("pass --retry-rejected to send them again", log)
            seen += self.asked()
        self.assertEqual(len(set(seen)), 6)  # the second command moved on to prompts not yet asked
        result, log = self.command(self.rejecting(EVERY))
        self.assertEqual((result["skipped_rejected_prompt_slots"], self.requests), (24, []))
        # Rejected again, the known prompts never stop the command: one request each, none of them news.
        result, log = self.command(self.rejecting(EVERY), "--retry-rejected")
        self.assertEqual(result, {"new_generation_records": 0, "prompt_rejections": 6,
                                  "skipped_rejected_prompt_slots": 18, "scheduled_attempts": 24})
        self.assertEqual(sorted(self.asked()), sorted(set(seen)))
        self.assertEqual(self.markers(), [])

    def test_rejections_use_up_the_limit_and_passed_over_slots_do_not(self):
        first = self.positions[0]
        outcome = self.generate(self.rejecting(), limit=first + 1)
        self.assertEqual((outcome["new_generation_records"], outcome["prompt_rejections"], len(self.requests)),
                         (first, 1, first + 1))
        rest = 20 - first
        last = max(index for index, slot in enumerate(self.slots) if slot["attempt_id"] not in self.flagged)
        outcome = self.generate(self.rejecting(), limit=rest)
        self.assertEqual(outcome, {"new_generation_records": rest, "prompt_rejections": 0, "scheduled_attempts": 24,
                                   "skipped_rejected_prompt_slots": sum(index < last for index in self.positions)})
        self.assertEqual(len(self.requests), rest)
        outcome = self.generate(self.rejecting(), limit=1, retry_rejected=True)
        self.assertEqual(outcome, {"new_generation_records": 0, "prompt_rejections": 1,
                                   "skipped_rejected_prompt_slots": 0, "scheduled_attempts": 24})
        self.assertEqual(self.asked(), [self.prompt])

    def test_ambiguous_calls_are_never_resent(self):
        self.generate(self.rejecting())
        def lost(request):
            raise TimeoutError("The read operation timed out")
        outcome = self.generate(lost, retry_rejected=True)
        self.assertRegex(str(outcome), "outcome is unknown")
        self.assertEqual(len(self.markers()), 1)
        # Its prompt was rejected before, yet the retained marker still stops every later command unsent.
        for options in ((), ("--retry-rejected",)):
            with self.subTest(options=options):
                code, log = self.command(lambda request: reply(), *options)
                self.assertEqual((code, self.requests), (2, []))
                self.assertIn("Unresolved in-flight call", log)
        self.assertEqual(len(self.markers()), 1)

    def test_only_logged_invalid_prompt_rejections_are_remembered(self):
        transport = self.run / "transport.jsonl"
        by_prompt = {}
        for slot in self.slots:
            by_prompt.setdefault(slot["prompt_hash"], slot["attempt_id"])
        rejected, failed, unknown, sloppy = list(by_prompt)[:4]
        for prompt, event in ((rejected, {"event": "no_model_outcome", "prompt_rejected": True}),
                              (failed, {"event": "no_model_outcome", "prompt_rejected": False}),
                              (unknown, {"event": "outcome_unknown"}),
                              (sloppy, {"event": "no_model_outcome", "prompt_rejected": "true"})):
            append_event(transport, {"attempt_id": by_prompt[prompt], **event})
        append_event(transport, {"attempt_id": "unscheduled", "event": "no_model_outcome", "prompt_rejected": True})
        append_event(transport, {"attempt_id": ["not", "a", "string"], "event": "no_model_outcome", "prompt_rejected": True})
        with transport.open("a") as log:
            log.write("{torn\n")
        self.assertEqual(rejected_prompts(self.run, self.slots), {rejected})
        outcome = self.generate(lambda request: reply())
        self.assertEqual((outcome["new_generation_records"], outcome["skipped_rejected_prompt_slots"]), (20, 4))
        pending = self.pending()
        self.assertEqual({slot["prompt_hash"] for slot in self.slots if slot["attempt_id"] in pending}, {rejected})

    def test_analyze_counts_every_slot_of_a_rejected_prompt_as_a_provider_outage(self):
        self.generate(self.rejecting())
        expected = {"log": "transport.jsonl", "no_model_outcome_events": 1, "outcome_unknown_events": 0,
                    "unreadable_lines": 0, "unresolved_attempts": 4, "prompt_rejected_attempts": 4, "in_flight_markers": 0}
        self.assertEqual(logged_outages(self.run)["provider_outages"], expected)
        result, log = followups.quiet(["analyze", "--run", self.run, "--output", self.run.parent / "first"])
        self.assertIsInstance(result, dict, log)
        report = self.run.parent / "first"
        self.assertEqual(read_json(report / "analysis.json")["run_provenance"]["provider_outages"], expected)
        page = (report / "report.html").read_text(encoding="utf-8")
        self.assertIn("Attempts whose provider calls have no recorded model outcome: 4, logged in <code>transport.jsonl"
                      "</code>. Of these, 4 have a prompt the provider rejected as <code>invalid_prompt</code>; "
                      "<code>generate</code> sends them again only with <code>--retry-rejected</code>.", page)
        self.generate(lambda request: reply(), retry_rejected=True)
        self.assertEqual({key: logged_outages(self.run)["provider_outages"][key]
                          for key in ("unresolved_attempts", "prompt_rejected_attempts")},
                         {"unresolved_attempts": 0, "prompt_rejected_attempts": 0})
        result, log = followups.quiet(["analyze", "--run", self.run, "--output", self.run.parent / "second"])
        self.assertNotIn("invalid_prompt", (self.run.parent / "second/report.html").read_text(encoding="utf-8"))


# A regressed walk loops while its memory grows, so the child ends itself with status 3 after 10 s.
WATCHDOG = r"""
import json, os, sys, threading
sys.path.insert(0, sys.argv[1])
watchdog = threading.Timer(10, os._exit, (3,))
watchdog.daemon = True
watchdog.start()
"""

CYCLES = WATCHDOG + r"""
from lean_compression.providers import nesting_depth
d = {"a": 1}; d["self"] = d
l = []; l.append(l)
a = {}; b = [a]; a["b"] = b
t = ([],); t[0].append(t)
head = ring = {}
for _ in range(199):
    ring["next"] = {}; ring = ring["next"]
ring["next"] = head  # closes 200 levels down, past the cap
wide = {"many": list(range(100000))}; wide["self"] = wide
out = {}
for name, value in (("self dict", d), ("self list", l), ("mutual", a), ("tuple", t), ("ring", head), ("wide", wide)):
    out[name] = []
    for limit in (None, 64):
        try:
            out[name].append(nesting_depth(value, limit))
        except ValueError as error:
            out[name].append(str(error))
print(json.dumps(out))
"""

CYCLIC_IMPORT = WATCHDOG + r"""
from pathlib import Path
from lean_compression.providers import import_responses
from lean_compression.store import load_run
run = Path(sys.argv[2])
slots = load_run(run)["slots"]
def row(slot, **extra):
    return {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
            "model_snapshot": slot["model_snapshot"], "raw_response": "by exact h", **extra}
out = {}
for field in ("usage", "provenance", "generation_settings"):
    value = {"tokens": 3}; value["again"] = value
    try:
        out[field] = import_responses(run, [row(slots[0]), row(slots[1]), row(slots[2], **{field: value})])
    except ValueError as error:
        out[field] = str(error)
out["written"] = len(list((run / "generations").glob("*.json")))
shared = {"tokens": 3}
out["shared"] = import_responses(run, [row(slots[0], usage={"input": shared, "output": [shared, shared]})])
print(json.dumps(out))
"""


class ImportCycleTests(provider_fixtures.RunFixture):
    """R2-providers-2, as regressed in round 3: the nesting check ends on a cyclic value from the Python API and refuses
    it as before round 3."""

    def child(self, program, *args):
        done = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(SRC), *map(str, args)],
                              capture_output=True, text=True, timeout=120)
        self.assertNotEqual(done.returncode, 3, "the nesting check did not end within 10 s")
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_nesting_depth_ends_on_cyclic_values(self):
        circular = "Circular reference detected"
        self.assertEqual(self.child(CYCLES), {"self dict": [circular] * 2, "self list": [circular] * 2,
                                              "mutual": [circular] * 2, "tuple": [circular] * 2,
                                              "ring": [circular, 65], "wide": [circular] * 2})

    def test_shared_values_tuples_and_the_bound(self):
        shared = {"k": [1]}
        self.assertEqual(nesting_depth({"a": shared, "b": [shared, shared]}), 4)  # shared, not cyclic
        self.assertEqual([nesting_depth(value) for value in ((), ((),), {"a": (1, [2])})], [1, 2, 3])
        deep = {}
        for _ in range(99999):
            deep = {"k": deep}
        self.assertEqual((nesting_depth(deep), nesting_depth(deep, 64), nesting_depth(deep, 100000)), (100000, 65, 100000))

    def test_cyclic_rows_are_refused_before_anything_is_written(self):
        out = self.child(CYCLIC_IMPORT, self.run)
        refusal = f"Import row 3 cannot be stored as JSON evidence (Circular reference detected): {self.slots[2]['attempt_id']}"
        self.assertEqual({field: out[field] for field in ("usage", "provenance", "generation_settings", "written")},
                         {"usage": refusal, "provenance": refusal, "generation_settings": refusal, "written": 0})
        self.assertEqual(out["shared"], {"imported": 1, "already_imported": 0})


if __name__ == "__main__":
    unittest.main()
