# Independent review of the candidate dataset

**Status at review: useful collected candidate pool; not ready for a confirmatory
model evaluation.** This is a pre-evaluation design review, not a report of model
effects. Structural quota checks pass, but construction-family separation,
known equivalent statements, and difficulty coverage need work before freezing.

Reviewed on 2026-10-11: `data/public/tasks.jsonl`, SHA-256
`83cb38e4f92ac8fe65148584ebdef98ce31e3ecaccf720570e7707eb002a361c`, alongside
the collected library, smoke, and fresh-main reference proofs. The dataset
author was concurrently materializing explicit signatures; this review is tied
to that snapshot. Later repairs should be recorded as amendments and rechecked.
No dataset records were edited by this review.

## What the snapshot establishes

There are 250 records: 50 pilot and 200 main tasks. Each domain has 10 pilot and
40 main tasks. Every main domain has 30 library and 10 fresh records, for the
requested main total of 150 library and 50 fresh goals. There are no duplicate
`task_id` values or exact repeated `(binders, proposition)` source pairs in the
reviewed snapshot. Its assigned family IDs do not cross the split.

Those checks are useful but limited. Family labels were assigned primarily by
source module for library records and separately for each fresh task. There are
15 pilot families (5 library modules and 10 fresh IDs) and 65 main families
(15 library modules and 50 fresh IDs). Each domain's library pilot contribution
comes from one module; its main contribution comes from three modules. These are
recorded clusters, not evidence that all 80 mathematical constructions are
independent. All 250 `family_reviewed` and `answer_access_audited` flags are false.

## Specific family and equivalent-statement findings

| Records | Evidence | Minimum pre-freeze action |
| --- | --- | --- |
| `smoke_la_02` / `fresh_linear_algebra_combinatorics_09` | Pilot asks for a zero finite sum and invariance under adding a pointwise-zero summand; main asks for an all-one finite product and invariance under multiplying by a pointwise-one factor. References use `sum_eq_zero` plus `sum_congr` versus `prod_eq_one` plus `prod_congr`, followed by pointwise simplification. These are additive/multiplicative variants of the same two-part fold construction. | Treat as one construction family and place that family entirely in one split; replace the displaced quota slot before main outputs exist. |
| `smoke_ag_02` / `fresh_algebra_groups_05` | Both start with the same group product equation, with orientation reversed, then substitute it and cancel inverses. The pilot reference is `by rw [← h]; simp [mul_assoc]`; the main reference is `by subst c; constructor <;> group`. The conclusions differ, but the proof construction overlaps directly. | Merge/review their construction family and separate it from main evaluation if already used in development. |
| `fresh_sets_logic_06` / `fresh_linear_algebra_combinatorics_10` | The first proves injectivity of `fun x => (f x, g x)` from injectivity of `f`; the second uses exactly this injectivity subproof to establish image cardinalities. Both references contain `intro x y h; exact hf (congrArg Prod.fst h)`. They are currently separate families in separate domains, both within main. | Cluster together, or document a defensible family boundary. If kept as a cross-domain family, use the analysis implementation's unstratified family-bootstrap fallback. |
| `fresh_algebra_groups_02` / `fresh_algebra_groups_04` | Both take `e * e = e` and ask for multiple consequences of idempotence. One concerns complement/orthogonality in a ring; the other higher powers in a monoid. | Review as a shared idempotence construction rather than automatically assigning each authored statement an independent family. |
| `lib_sets_logic_main_logic_relation_07` / `_08` | `_07` is `Std.Symm.flip_eq`; `_08` states `Std.Symm.swap_eq`, and its entire reference proof is `Std.Symm.flip_eq`. `_08`'s predecessor context contains `_07`, but its forbidden list contains only `Std.Symm.swap_eq`. | `_08` is a concrete known equivalent/alias case for replacement or explicit exclusion. Do not mark answer access audited merely because its own declaration name is absent. |
| `lib_sets_logic_main_logic_relation_09` / `_10` | `_09` is `flip_eq_iff`; `_10` is `swap_eq_iff` and its entire reference is `flip_eq_iff`, which is present in predecessor context. The forbidden list contains only `swap_eq_iff`. | Replace/exclude the duplicate variant before freeze, or update forbidden sets and supply an independently verified eligible reference. |

The first two rows are construction-family findings, not claims of logically
identical statements. The relation examples are stronger: the supplied reference
is exactly the earlier theorem, providing direct evidence that the new spelling
can obtain the already-available result. Even if both variants remain in one
statistical cluster, clustering does not repair answer access.

## Difficulty and domain coverage

The main snapshot contains **142 provisional short-source library records,
50 provisional easy fresh records, eight provisional medium-source records, and
no provisional long-source records**. Across the full dataset there are 179
short-source, 60 easy, 10 medium-source, and one long-source label; the one long
label is in pilot. These are provisional labels, not the frozen parser-token
and dependency-depth strata required by the plan. The main records do not yet
carry those measured stratum fields.

The library reference file has 22 literal `rfl` proofs and one `Iff.rfl` proof;
18 of those 23 belong to main. In particular, nine of the ten
`lib_algebra_groups_main_hom_basic_*` references are literally `rfl`, while five
of the eight `lib_sets_logic_pilot_set_basic_*` references are literally `rfl`.
Across all 190 library references, 112 have at most 40 source characters.
Character length is reported only as a curation diagnostic, not as W or as a
reliable measure of mathematical difficulty. A short tactic can conceal
substantial work, and a sophisticated theorem can have a short reusable proof.

The numerical domain quotas are exact, but within-domain coverage is narrow.
Examples include a main algebra block dominated by homomorphism coercion
identities and an analysis block with many real-power cast/notation variants.
Main medium-source labels occur only in analysis (six) and linear algebra (two).
Algebra, number theory, and sets/logic each have 30 short-source library and ten
easy fresh records. This supports a lightweight engineering pilot, but does not
yet establish broad easy/medium/harder mathematical coverage. One source module
per pilot domain also gives little evidence about within-domain family variance.

## Minimum release gate

Keep the corpus labeled **collected candidates / not confirmatory-ready** until
all of the following have source-backed completion evidence:

1. Resolve the concrete cross-split construction conflicts and known equivalent
   relation statements, then review related families across source modules and
   domains. Freeze replacements before evaluated model outcomes exist.
2. Finish explicit signature materialization and independently replay every
   reference under the exact allowed environment; require available metric
   closure and measured source/dependency evidence for each eligible task.
3. Measure reference W and D, define coarse difficulty strata without model
   outcomes, and curate meaningful coverage in every domain. Preserve designated
   easy controls and report them separately.
4. Audit target absence, known aliases, and forbidden dependency closure per
   reconstructed environment. Exact source-duplicate checks alone are
   insufficient, as the `flip`/`swap` examples demonstrate.
5. Recompute actual family counts after grouping, assess interval precision using
   the pilot, and freeze the final task/family manifest and exclusion reasons.

A 10-goal engineering smoke result can verify the pipeline even while these
corpus gates remain open. This review does not establish absence from model
pretraining, prove novelty of fresh statements, exhaust all equivalent theorems,
or claim that every provisional short/easy record is mathematically trivial.

## Follow-up after the targeted amendments

The reviewed repairs are now saved in public manifest SHA-256
`372118415c4454e3e7b87d8afdaa2467c858e68ddc36fc496cd7dddd23b363a4`.
The amendment log is `data/private/dataset_amendments.jsonl`, SHA-256
`c00468798aef07061519ad2665d059d9a78ade2f48d71a39ad891d670b5bd986`.
That hash covers the log's first 226 rows, as reviewed here; with the 315 rows
appended on 2026-10-12, the current file's SHA-256 is
`17e6dfaa7759d387a13868533e07981f710f0a23508de8d10f014f81e62691d3`.
An independent read of that patched manifest confirmed 250 public tasks,
250 one-to-one private reference records, 74 provisional families, and passing
structural quota, exact signature-duplicate, and reference-mapping checks.

The old inverse-cancellation pilot task `smoke_ag_02` was replaced by a
two-condition monoid case split and grouped with its related pilot branching
family. The old sum-perturbation pilot `smoke_la_02` was replaced by nonempty
Cartesian-product/cardinality reasoning. A fuller pass also identified the
absolute-value triangle construction shared by `smoke_an_02` and the former
`fresh_analysis_inequalities_08`; that main task was replaced by continuity of
an affine-precomposed continuous function and a pointwise square.

The two relation alias slots now contain `Equivalence.comap` and
`Relation.comp_eq_fun`. The injective-first-coordinate pair and idempotence pair
now share their respective construction families. The discrete two-step-gap
pair `fresh_number_theory_06` / `fresh_linear_algebra_combinatorics_04` is grouped
across domains. The inverse-consequence tasks `fresh_algebra_groups_03`, `_05`,
and `_07` are grouped conservatively. Family resampling must retain these
cross-domain families; the analysis implementation reports the corresponding
unstratified cluster-bootstrap fallback.

The dataset author reports successful Lean source verification of all 250
explicit signatures/reference proofs. That report is not substituted for the
independent engine evidence: at this follow-up, full library closure extraction
and the amended fresh reference check were still running. The offline readiness
report distinguishes these stages and checks each reference's input identity.

These targeted repairs address the concrete findings listed here. They do not
complete an exhaustive equivalence/alias search or establish independent
construction families throughout the corpus. Difficulty coverage, final
reference length/depth strata, and full manual answer-access/family review
remain open gates. The confirmatory-ready status has not been granted.
