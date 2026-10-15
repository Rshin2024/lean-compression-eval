# Compression explorer: design and metric review

**Recommended default:** show a compact podium plus the complete six-model ranking first. Use the heading **Compression rank points**, and keep **6 models · 23 shared questions · Compression prompt** immediately beside it. A small **Conditional on valid proofs** badge is enough; put derivation details behind an information control. Add a conspicuous **All 9,600 outputs** action. Preserve the existing report as supporting analysis rather than making users traverse it before seeing scores.

The frozen ≥25-question coverage rule admits Fable, Opus, Astra, GPT-6.1 Sol, GPT-6 Sol and Luna. Their rank points are 75.65, 73.04, 68.70, 49.57, 27.83 and 5.22. Show Haiku (20/100) and Sonnet (11/100) visibly as outside this aggregate's coverage rule. Do not remove them from question details or the output inspector. These are descriptive ranks on a selected support, not a statistically resolved podium of general ability.

## Distinct labels and units

| Display | Label | Meaning |
|---|---|---|
| Per-output raw value | **T₀ · expanded refs/source token** | Exact (U_S+U_B)/(W_S+W_B), with scientific notation for display. |
| Model on one question | **Geometric mean T₀** and **Measured k/4** | Mean of logs over successful measurable samples, exponentiated; no best-of-four selection. |
| Within-question relative result | **Rank** or **Rank points** | Descending T₀ midrank; ties share the average rank. |
| Across shared questions | **Average rank points** | Equal-question mean, fixed participants and fixed common support. |
| Source length | **Proof-body tokens W_B** | Lean parser tokens, separate from T₀ and provider billing tokens. |

Rank points are `100×(M−rank)/(M−1)` for M≥2. Never append a percent sign or call them a compression percentage. They are relative to the participant set. All participants tying gives 50; a single measured participant has no competitive points. For a fixed roster, averaging these points is exactly the same ordering as averaging midranks.

Order geometric means with exact rational products and sample-count powers, not rounded scientific notation or floats. Valid sample counts may differ from 1 to 4; display that support. The mean is conditional on successful samples and is not an estimate of an invalid output's missing compression. A high raw T₀ can reflect larger dependency expansion, so do not label it shorter proof or reasoning quality.

## Navigation with little prose

1. The leaderboard row opens that model's per-question ranking table on the same 23-question support.
2. A question row or **View 4 outputs** opens the inspector filtered to model + question + condition.
3. A separate question selector covers all 100 questions, in stable ID order. Its comparison lists all eight models, including **N/A — no measured proof** and k/4 counts. Mark whether the selected question belongs to the default 23.
4. The inspector retains all 9,600 rows, including failures. Show active filter chips, **shown / matching / 9,600** counts, and **Clear filters**. A context click may show four rows; the dedicated all-outputs action should clear those context filters.

Avoid silently substituting an eight-model question ranking for a six-model aggregate rank. Either retain the six-model rank column and display the other models' raw values without a rank, or offer a clearly named **Rank among measured models on this question** mode. The latter's M can change by question and its points must not feed the fixed six-model aggregate.

**All eight models** must produce an honest aggregate empty state: **0 shared compression questions — aggregate ranking unavailable**. Keep per-question and raw-output views usable. Changing condition, coverage threshold or participant roster must visibly update both M and the shared-question count; no stale podium from the previous selection.

## Raw-output handling and verification checklist

Expose model, question, condition, sample, verdict, raw T₀, W_S/W_B, U_S/U_B, depth and attempt ID. Large counts remain decimal strings; retain exact numerator/denominator in expandable detail or copy action. Scientific notation is presentation only. Optional log₂ T₀ is a secondary column with its own label. Failed/unmeasured rows show N/A; nulls sort after measured rows in either direction and never appear as zero-height scored outputs.

The independent expected values are in `expected-default.json`, bound to the frozen comparison SHA. Later checks should cover all 138 default model/question ranks; participant and question identities; exact ties; k=1 versus k=4 geometric means; empty all-eight support; one-participant and all-tie states; all 9,600 unique attempt IDs; filters/pagination; direct row-to-four-output drill-down; and N/A behavior. Search must not hide which filters remain active. On narrow screens, stack podium cards and retain model labels, score units and coverage before secondary diagnostics.

This review creates only notes and expected-value metadata. It does not change the app, analysis data, evaluator or original results.
