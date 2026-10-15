# Provider maximum rerun: evidence and execution guidance

Checked against official provider documentation on October 14, 2026 (America/Los_Angeles). This is a separate rerun of the existing 100 questions × 3 conditions × 4 samples × 8 models. No old run, prompt, response, evaluator, or verification record was edited by this research lane. No authenticated request was made.

## Token ceilings

All eight exact model IDs are documented; no model substitution is needed. The synchronous provider ceiling is **128,000 generated tokens** for each:

| Provider | Model | Ceiling | Evidence |
| --- | --- | ---: | --- |
| OpenAI | gpt-6.1-sol | 128,000 | [Model specification](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| OpenAI | gpt-6-sol | 128,000 | [Model specification](https://developers.openai.com/api/docs/models/gpt-6-sol) |
| OpenAI | gpt-6-luna | 128,000 | [Model specification](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| OpenAI | gpt-6-astra | 128,000 | [Model specification](https://developers.openai.com/api/docs/models/gpt-6-astra) |
| Anthropic | claude-fable-5-1 | 128,000 | [Model comparison](https://platform.claude.com/docs/en/models/overview) |
| Anthropic | claude-opus-5-5 | 128,000 | [Model comparison](https://platform.claude.com/docs/en/models/overview) |
| Anthropic | claude-sonnet-5-5 | 128,000 | [Model comparison](https://platform.claude.com/docs/en/models/overview) |
| Anthropic | claude-haiku-5-5 | 128,000 | [Model comparison](https://platform.claude.com/docs/en/models/overview) |

OpenAI Responses accepts omission of `max_output_tokens`; it is an optional parameter and includes reasoning as well as visible text. Omission removes the client's explicit ceiling, but cannot remove the model's own maximum. [Responses schema](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

Anthropic Messages requires `max_tokens`; it cannot simply be omitted. Thinking consumes this budget. The required value should be 128000 for this synchronous rerun. Three of these Claude models have a 300K Message Batches beta ceiling, but Fable does not; batches do not provide an hour-completion guarantee and would add another protocol difference. [Messages schema](https://platform.claude.com/docs/en/api/messages/create), [thinking limits](https://platform.claude.com/docs/en/build-with-claude/thinking).

Recommended protocol label: **provider-maximum budget**, not unlimited. A uniform integer 128000 also fits the existing core's mandatory `generation.max_output_tokens` field without modifying the frozen package. An OpenAI-omission variant is possible with a separately versioned transport adapter and explicit request provenance.

## Keep the scientific comparison interpretable

Copy the old frozen prompts byte-for-byte, with fresh run/attempt identities. Keep high effort, model IDs, 3 conditions, 4 samples, single-shot generation, omitted temperature, no tools, parsing policy, and v2_threads1 verification identical. Do not insert new budget-awareness text: that would change two experimental variables. Save and validate old/new prompt hashes. Preserve generation and verification provenance separately.

Transport changes required to avoid disconnects are operational amendments, and should be recorded before launch. If a faster paid service tier is selected, record requested and delivered tier and use tier-appropriate prices. Do not carry old standard-tier prices into Fast responses. More parallel workers must not change the per-proof CPU/memory/time limits, and resource starvation must not be scored as incorrectness.

## Transport and recovery

The existing provider adapter uses a single nonstreaming `urllib.request.urlopen(..., timeout=600)` and captures the response only after `reply.read()` finishes. Its 600-second network timeout and lack of incremental capture are unsuitable for long 128K generations. Do not change the frozen core; use a separately versioned adapter.

Anthropic documents SDK-required streaming above 21,333 tokens and warns that long thinking requests can hit connection/time limits. Stream and append raw SSE evidence durably, then assemble the final message only when terminal evidence exists. Keep read-idle timeout separate from total generation time. A practical network policy is 30 seconds to connect and 600 seconds without any bytes; no client total generation deadline. This is an implementation recommendation, not a provider guarantee. [Long requests](https://platform.claude.com/docs/en/build-with-claude/thinking).

OpenAI background mode returns a response ID early and allows polling. Persist the ID and request evidence before polling; retrieval retries do not create a replacement generation. The current docs describe temporary retention for polling, including `store:false`; continuous archival of received responses remains necessary. [Background mode](https://developers.openai.com/api/docs/guides/background).

Set SDK automatic generation retries to zero and let the audited scheduler handle only explicit no-generation errors. A disconnected POST after possible acceptance remains uncertain; retrying can duplicate billing and must not be hidden. Never repeat a saved response. Preserve partial streams, request IDs, error bodies without secrets, stop reasons, usage, and unknown billing. No tools should be executed from returned model text.

## Throughput and the hour target

9,600 attempts in 60 minutes requires **160 completions/minute overall, 20/model/minute**, before setup and final report time. If a model averages 40K generated tokens, its output requirement alone is 800K tokens/minute; at 128K it is 2.56M/minute. These are arithmetic scenarios, not predicted consumption. Cloud VMs accelerate Lean verification; they cannot raise provider account limits or shorten a single provider generation.

Anthropic rate limits use RPM, input TPM, and output TPM. Output accounting is based on actual generated tokens in real time; setting `max_tokens=128000` does not itself reserve 128K OTPM. Public Fable limits are 100K/300K/800K OTPM at Start/Build/Scale, with custom limits separate. The other three current Claude models list 400K/1M/2M OTPM. Account-specific response headers/console are authoritative; tiers have not been read for this rerun. Sudden traffic growth can hit acceleration limits. [Rate limits](https://platform.claude.com/docs/en/api/rate-limits).

OpenAI's guide describes reservation based on the larger of requested maximum tokens and estimated input. It does not establish the reservation behavior when the Responses output cap is omitted. Do not assume omission permits unlimited parallelism. At a 128K reservation, the published 1M-TPM Build level would permit only about 7.8 starts/minute per such bucket, before input/account sharing. Capture rate-limit remaining/reset/request/project headers and adapt concurrency to them. [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits).

Use an adaptive centralized limiter shared by all generation workers for each account/model limit bucket: start conservatively, increase on successful capacity observations, reduce and honor Retry-After on 429, and distinguish rate errors from spend/access failures. Avoid independent VMs each assuming they own the whole quota. A target of 64–128 in-flight requests/model may be useful where observed quotas and connection limits permit, but is not itself a promise of throughput. Launch verification as responses arrive so its work overlaps generation. Report achieved rates early; never promise an hour solely from the VM count.

Historical response latency distributions from all 9,600 received responses are in `historical-generation-latencies.json`. Means ranged from 35.0 to 112.3 seconds under the old 8,192 budget. They are censored by that budget and must not be used as predictions for the larger-budget run.

## Faster service options

OpenAI Fast/priority is available for these four models at the documented premium (their model pages show 2× Standard). Standard and Fast share model rate limits; rapid ramp-up can deliver Standard instead, so record the response's actual tier. Ultrafast is access-controlled and only relevant to Astra/6.1 Sol, so availability cannot be assumed. [Fast mode](https://developers.openai.com/api/docs/guides/fast-mode).

Anthropic Fast is documented for Opus 5.5 only among this cohort, requires preview access, and uses `speed:fast` plus `fast-mode-2026-02-01`. It has a separate quota and costs $8 input/$40 output per million tokens. It uses the same model weights. Do not request it for Haiku, Sonnet, or Fable. Without verified access, use standard streaming immediately rather than waiting for provisioning. [Anthropic Fast](https://platform.claude.com/docs/en/build-with-claude/fast-mode).

No capability or throughput value above establishes actual account access. The one-hour completion target remains dependent on provider quotas, generation duration, and cloud startup/verification capacity.
