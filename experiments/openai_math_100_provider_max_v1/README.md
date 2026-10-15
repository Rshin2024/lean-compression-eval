# Provider-maximum rerun

The rerun is complete: all **9,600 logical attempts** have saved generation and
terminal verification records. The final validation accepted **3,831** proofs
and recorded **123 native-limit truncations**. The recorded-response token
price estimate is **$4,369.71831081**; **30 original failed requests have unknown
charges**, so this is not a complete API invoice total. The closed Azure compute
estimate is **$59.32855721094778**, excluding invoice adjustments and other
non-compute charges. Six VMs were deallocated, and the task resource group was
confirmed absent at **2026-10-15 04:56:17 UTC**.

The all-eight-model intersection contains **1** measurable question for the
standard condition, **0** for mathematical compression and **3** for source
concision. Accordingly, the all-eight compression ranking is undefined.
Changing the selected cohort or coverage threshold changes the population
being compared; conditional ranks should be read alongside validity and
shared-question coverage.

T0 is `(U_S + U_B) / (W_S + W_B)`: expanded dependency references divided by
source tokens, including the fixed statement contribution. Higher T0 need not
mean a shorter or more factored proof, or better mathematical reasoning.
The evaluation retains the frozen metric, all records and all scores.

Completion evidence is archived as small hash-bound receipts in
`amendments/final_validation_v1`; cloud closure and confirmed resource deletion
are documented in `amendments/closed-cost-amendment-v1` and
`amendments/operational-archive-v1`. These receipts reference private evidence
that is deliberately omitted from Git. The full validation predates the
separately reviewed cloud-cost-only display amendment; its historical hashes
are retained unchanged. Final browser QA checked the amended report.

This separate experiment repeats the supplied 100 questions, eight model IDs,
three prompt conditions and four samples: 9,600 new logical attempts. Its
generation manifests have fresh attempt IDs and byte-identical messages to
the completed 8,192-token run. High effort and the Lean evaluator are unchanged.

The new generation ceiling is **128,000 tokens**, the documented synchronous
maximum for these model IDs. This is a provider-maximum run, not an unlimited
run. Reasoning consumes the generation budget. Truncation can still occur.
The documentation evidence is archived in
`workspace/work/unlimited-eval-100-20261014/provider-limits/protocol-note.md`.

OpenAI requests priority processing, with costs determined from the delivered
tier. Streaming, rather than a single buffered response, preserves received
bytes while long generations are running. These are explicit operational
differences from the earlier run and should accompany any budget comparison.
The experiment cannot isolate the causal effect of the output ceiling alone.

Generation ran locally. API keys are inherited in process memory and are not
sent to verification VMs. Six Azure VMs provided 384 vCPUs and 3 TiB memory;
independent Lean checkers retain the same per-attempt CPU, memory, PID and time
limits. All checks use the existing `v2_threads1` runtime. The 100 verified
reference records are reused with checked hashes; they are not fresh model
generations. Compression rankings compare model outputs on shared questions.

The fleet includes AMD EPYC 7763 and Intel Xeon 8573C hosts. Equal nominal CPU
quotas do not make their physical hardware identical. Report checker timeouts
separately and retain host metadata; this is not a controlled timing benchmark.

Incomplete streams and terminal provider errors retain their original bytes
and unknown billing. A saved completed answer is never requested again.
Separately reviewed, exact-attempt recovery amendments may authorize one
replacement for a terminal provider failure; any such amendments and their
effect on cost coverage must be disclosed in the final report. Text-only
terminal responses mislabeled as tool stops can be reconciled from complete
saved evidence without a new call or executing any returned text.

The source manifest alone is not a completion certificate. Runtime results, full proof
graphs, API captures, credentials and account access material remain outside
Git. `source-manifest.json` hashes the allowlisted operational sources. The
original package and Lean sources remain in their normal repository locations.
The archived paths preserve the original workspace structure; they rely on
the original run manifests, data, runtime image and reference evidence.

The original `cloud_report_job.py` and `report_fleet.py` are retained as
historical sources. Their `_v2` replacements correct mutable operational status
writes and detect stale report jobs; the original jobs are retired. Generation
and verification were not affected by that reporting supervision fix.

Three Claude report jobs used a separately versioned 16-process
record reader. It preserves the pinned per-record checks and canonical order;
all 1,200 Luna records and the full analysis matched the original reader
exactly. Original report assets are retained in `report-assets.tar.gz`, including
the earlier viewer route allowlist used by cloud jobs. The current local viewer
additionally serves the explicit operational disclosure files.

An equivalence-test fixture initially used hard links to live manifests. This
changed inode metadata and triggered the generation supervisor's integrity
guard, while file bytes stayed unchanged and dispatched requests continued to
finish. The incident and original equivalence receipts are preserved. Future
fixtures use byte copies. No provider request was terminated; reviewed retries
start only after the original generation child exits naturally and every other
saved response has been hash-checked against its frozen slot.

The retained local work directory is `work/unlimited-eval-100-20261014`; run
directories are `runs/om100-max-{model}-high-20261014`, and the new combined
report is `runs/om100-max-suite-high-20261014`. The earlier completed suite
remains `runs/om100-suite-high-20261013`.

The separate comparison uses this run-directory selection (rebuilding also
requires the archived adapters, pinned inputs and operational disclosures):

```sh
python3 reporting/build_comparison.py \
  --suite runs/om100-max-suite-high-20261014 \
  --runs-root runs \
  --run-directory-template 'om100-max-{model}-high-20261014'
```

The retained reporting handoff adds its cloud-cost ledger and any transport
charge disclosure to this command. Report generation must use the archived
priority-cost adapter before importing the core analysis module. Completion
validation checked all 9,600 new generation and verification records, report
consistency, streaming audits, and cloud/local result hash verification.

The source-only `display-assertion-fix-v1` amendment preserves the old and new
combined-report builder and their diff. It corrects a disclosure-insertion
assertion that counted repeated text instead of the specific script element;
it does not change measurements. `viewer-amendment-v1` preserves the explicit
local disclosure routes. Operational recovery v1 and v2 remain separate
historical archives; v2 records the final 30 exact-attempt recoveries.

`refresh_snapshot.py` copies the existing nonrecursive source allowlist.
`archive_completion_sources.py` copies only the named completion helpers and
small receipts; it excludes response/proof bodies, graphs, private archive
payloads, logs, credentials and before/after report snapshots. The only
secret-pattern exemptions are hash-bound source lines containing literal
detector markers or a synthetic test fixture. All copies use independent bytes,
not hard links. The original evaluator and Lean sources were not edited.
