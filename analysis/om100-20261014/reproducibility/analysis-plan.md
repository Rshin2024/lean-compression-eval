# Analysis specification (post hoc)

Written after data collection, before fitting this analysis. This is not a
preregistration. Primary dataset: the separately frozen 128,000-token cohort;
the 8,192-token cohort is a matched-question sensitivity comparison. The
10-question smoke test is described separately and never pooled.

1. Reconcile every unique model/question/condition/sample slot and exact metric
   formula to the audited reports. Preserve hashes and all exclusions.
2. Report scheduled accepted-proof yield, completion, acceptance conditional on
   completion, and observed coverage with four samples separately. Four-sample
   coverage is not the success probability of a new independent four-call run.
3. Scientific prompt contrasts: compression vs standard, concision vs standard,
   and compression vs concision for each model. Correctness uses all questions;
   log2 compression/source-length contrasts use questions with measurable valid
   proofs in both conditions, with that selected support stated explicitly.
4. Each question receives equal weight. Resample 19 theorem-family blocks with
   replacement (10,000 draws, fixed seed), retaining all questions and attempts
   within sampled families; recompute ratio estimators. This is the principal
   uncertainty sensitivity. Also bootstrap 100 question clusters. Neither
   interval makes this purposive corpus representative of all mathematics.
5. Exploratory p-values: intercept-only paired-difference contrasts with CR1
   family-cluster standard errors, Student t with G-1 degrees of freedom, Holm
   correction within each prespecified outcome/contrast family. Sparse supports
   (<5 represented families) have no inferential p-value. Emphasize effect sizes
   and intervals, not a binary significance ranking.
6. Compare pure T0, I0 and WB within shared questions. Use exact rational
   arithmetic for T0 sample comparisons; no reference-proof normalization in
   between-model rankings. Report overlap counts. The all-eight compression
   intersection is empty; no universal pure-compression ranking is defined.
7. A new descriptive joint contest score is allowed as a supplemental,
   explicitly post-hoc social benchmark: uniformly select a question, a rival
   among the other seven models, and one of four saved responses per model.
   A valid proof beats a failed response; when both are valid, higher T0 wins;
   equal T0 and double failures split a point. Average all 16 response pairs.
   The score is roster-specific and mixes reliability with compression; 50 is
   not a passing grade. Report the exact reliability/compression decomposition,
   both-failed and both-valid contest shares, and WB sensitivity. Never treat
   the 16 correlated pairs as independent observations.
8. Examine source length across completed nonempty valid and invalid outputs.
   Main associations use log2 nonwhitespace character length, with within
   question/model/condition demeaning and family-cluster bootstrap uncertainty.
   Exclude truncations by default; provide format-failure and partial-text
   sensitivities. Interpret associations, not causal shortening effects.
9. Examine metric–correctness associations using split samples (1–2 to measure
   compression among valid outputs; 3–4 to measure held-out validity), adjusting
   for question and model/condition. These analyses remain selected on having
   a measurable early proof and cannot assign compression to invalid proofs.
10. Inspect raw metric tails, library/dependency expansion, task families,
    domain differences, costs and truncations. Retain outliers; show leave-one-
    family-out and robust/median sensitivities where useful. No model calls,
    evaluator changes, or replacement of frozen results.
