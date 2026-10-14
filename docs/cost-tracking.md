# API cost tracking

The eight model profiles now require an explicit USD pricing snapshot. Each new
run freezes its rate card inside its immutable configuration. New generation
records store `cost_estimate` alongside the original provider usage and response
IDs. Resuming a run uses its frozen rates; it does not read today's model-profile
prices or rewrite earlier responses.

The published first-party, standard-speed, global rates were checked on
**October 13, 2026** and are recorded in
[`configs/pricing/2026-10-13.json`](../configs/pricing/2026-10-13.json). The model
profiles embed their own complete entries, including source URLs and the date.
OpenAI profiles explicitly request the `default` service tier. A response that
reports an unsupported tier, region or model is unpriced rather than silently
assigned the standard rate. If a response omits tier/region metadata, any use of
the explicitly configured standard/global scope is disclosed as an assumption.

## Accounting

- OpenAI ordinary input is `input_tokens - cached_tokens - cache_write_tokens`.
  Cache reads and cache writes are charged separately at their own rates.
- Anthropic `input_tokens` excludes cache reads and writes. Five-minute and
  one-hour cache writes are charged separately; their sum must match the reported
  cache-creation total.
- Provider `output_tokens` already includes reasoning/thinking tokens. The
  reasoning-detail counter is never charged a second time.
- The card applies the documented whole-request long-context multipliers:
  over 272,000 input tokens for these OpenAI models, and over 100,000 total
  input tokens (including caching) for Haiku 5.5.
- Decimal arithmetic preserves dollar amounts. Failed, refused, truncated and
  invalid-proof attempts are included when generation usage is available.
- Missing usage or cache counters remain unknown, not zero. Partial observed
  sums are labeled separately from complete totals and retain coverage counts.

These amounts are **API token-cost estimates, not provider invoices**. They
exclude taxes, credits, negotiated discounts, local/cloud verification compute,
and potentially billable transport requests that have no recorded generation.
Raw transport events and captures remain available for reconciliation; an
unknown charge is not assumed to be free. The standalone cost report includes
historical transport-event counts to make this limitation visible.

## Reports

New analysis reports include per-attempt costs, per-question/model/condition
breakdowns, coverage, and the pricing basis. The export files are `costs.json`,
`cost_attempts.csv`, and `cost_summary.csv`. Cost export does not expose model
proof text or credentials.

Costs can also be inspected before Lean verification finishes, without making
model calls:

```sh
./run-eval costs --run runs/NEW-RUN --output runs/NEW-RUN-costs
```

Completed legacy runs have no frozen pricing schedule and are not rewritten.
To produce a separately labeled retrospective estimate using the dated card:

```sh
./run-eval costs \
  --run runs/smoke-gpt-6.1-sol-high \
  --pricing configs/pricing/2026-10-13.json \
  --output runs/smoke-gpt-6.1-sol-high-costs-20261013
```

This reads existing responses only. An output directory must be new or empty.
The report identifies explicit retrospective pricing and preserves the run
hash, original run timestamp, card date and pricing hash.

## Sources

- [OpenAI model pricing](https://developers.openai.com/api/docs/models)
- [OpenAI cache accounting](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Anthropic cache accounting](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
