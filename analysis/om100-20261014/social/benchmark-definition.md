# Social benchmark definitions and provenance

These are draft static cards, not published posts. They describe this fixed, curated corpus of 100 public Lean lemmas. They do not establish unseen training data, mathematical generality, a model's internal reasoning quality, or a universal compression winner. The primary report contains all models, prompt conditions, failure outcomes and sensitivity analyses.

## Card 1: questions solved in four attempts

For model m and question i, let V(m,i,s) be 1 when the saved standard-prompt sample s passes the frozen Lean validity gate, and 0 otherwise. Four samples were scheduled for each question.

`coverage(m) = (1/100) × sum_i 1[sum_{s=1}^4 V(m,i,s) >= 1]`.

The bar label is the integer numerator out of 100. Every question remains in the denominator, including provider truncations and checker failures. This is observed four-sample question coverage, not single-attempt accuracy or a guaranteed success probability for a future batch. Models are displayed in descending observed coverage without a significance-based winner claim. The plot uses the standard prompt, high effort and the frozen 128,000-token provider ceiling.

| Model | Questions solved | 95% family interval, expressed per 100 |
|---|---:|---:|
| Claude Fable 5.1 | 96/100 | 91.261284–100.000000 |
| GPT-6 Astra | 89/100 | 80.808081–95.876289 |
| GPT-6.1 Sol | 84/100 | 73.737374–92.857143 |
| Claude Opus 5.5 | 81/100 | 72.277228–89.216477 |
| GPT-6 Sol | 67/100 | 56.435644–76.923077 |
| GPT-6 Luna | 30/100 | 22.916667–37.255348 |
| Claude Haiku 5.5 | 19/100 | 11.111111–27.722772 |
| Claude Sonnet 5.5 | 16/100 | 9.473684–23.424573 |

## Card 2: compression head-to-head when both solve

These are three explicitly selected descriptive pairs: Claude Fable 5.1 versus GPT-6 Astra; Claude Fable 5.1 versus Claude Opus 5.5; and Claude Opus 5.5 versus GPT-6 Astra. They are not an exhaustive ranking or a prespecified three-model championship. Each pair has its own successful-question support.

Use the mathematical-compression prompt. Let A_i and B_i be the successful, measurable responses for models A and B on question i, and S_AB the questions where both sets are nonempty. Compare all |A_i|×|B_i| successful response pairs, from the four originally scheduled responses per model. Define exact win credit w(a,b) as 1 if T0(a)>T0(b), 0 if T0(a)<T0(b), and 1/2 for an exact tie.

`q_i(A,B) = sum_{a in A_i, b in B_i} w(a,b) / (|A_i| × |B_i|)`.

`H(A,B) = 100 × sum_{i in S_AB} q_i(A,B) / |S_AB|`.

Thus every shared question has equal weight, regardless of whether it has one or four successful samples. Failed responses have no numerical compression value and are absent from this conditional comparison. Reliability remains visible in Card 1 and the full report. This conditions on different successful outputs and questions; it does not remove selection bias. The sixteen possible response pairs reuse four samples and are not independent observations.

The fixed metric is `T0 = (U_S + U_B)/(W_S + W_B)`, where U counts weighted expanded primitive/dependency references and W counts Lean source-token spans. S is the fixed statement and B the submitted proof body. It includes statement contributions. Exact integer fractions determine wins/ties; displayed percentages alone are rounded. T0 is not a source-length-only measure, compressed byte count or internal-reasoning measure.

| First model versus second | Exact mean win credit | Displayed percentage | Shared questions | 95% family interval |
|---|---|---:|---:|---:|
| Fable vs Astra | 2251/3696 | 60.903680% | 77 | 53.406271–69.047709% |
| Fable vs Opus | 5471/11232 | 48.709046% | 78 | 40.322546–57.165872% |
| Opus vs Astra | 47/70 | 67.142857% | 70 | 60.351563–73.762388% |

When reversing a saved comparison, the score becomes 1−p and the interval becomes [1−upper, 1−lower]. All three selected pairs have support in 18 source families. The independent renderer recomputes these scores from exact stored ratios rather than relying only on reversing report values.

## Intervals and limitations

Intervals are percentile 95% family-cluster bootstrap intervals from the new post-hoc analysis: 10,000 draws, NumPy random seed 20261014, resampling 19 theorem families with replacement and retaining all questions/models/conditions/samples within each selected family. Point estimates weight questions equally; repeated family draws give their questions the corresponding multiplicity. Numerators and denominators are recomputed in each draw. The parent analysis draws its question-bootstrap matrix before the family matrix; the renderer reproduces that RNG sequence. No selected displayed endpoint has an empty family replicate.

This bootstrap is unstratified and differs from the historical domain-stratified report bootstrap. It describes resampling uncertainty/sensitivity within the curated case set. It does not make these public tasks representative of all mathematics. Card intervals are marginal and are not simultaneous multiple-comparison guarantees; no binary winner declaration is made. Pair supports differ, and there are zero questions measurable for all eight models under the compression prompt, so these pairwise displays cannot support a universal eight-model pure-compression ranking.

The final cards use a white background, a single blue mark color, dark labels, direct values and 0–100 axes. Card 1 bars begin at zero; Card 2 shows a neutral 50% guide and numerical intervals. There is no reference-proof normalization and no combination of correctness and T0 in Card 2.

## Inputs and reproduction

All points and intervals were independently checked against the saved comparison rows by `build_cards.py`. It never executes the main analysis, a model, Lean or a cloud action. Source data remain unchanged. The exact row values and question IDs are saved in `card-data.json`.

Run from the workspace root:

```sh
PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  work/paper-analysis-20261014/.venv/bin/python \
  outputs/lean-compression-paper-20261014/social/build_cards.py
```

Input SHA-256 hashes:

- `outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json`: `4ddf80101317bb6158bd13a435eccae899684479dfd74b91c2047c81c550f264`
- `outputs/lean-compression-eval/data/prepared/openai_math_100_v1/index.json`: `cfde6d4075d9d63dc3d1513eb98b7d0550ce5c8e6a87b9cf95e54e312d7846be`
- `work/paper-analysis-20261014/main/results/rates.json`: `fd1ed525d4ad240a136ee968f8b5740066957e273ee9cca56f83e273ecdeef29`
- `work/paper-analysis-20261014/main/results/pairwise_contests.json`: `4472bf124652d1bbc34ff26cb37f05d4e78fd523ee28078bfbfce54497bc1c80`
- `work/paper-analysis-20261014/main/results/analysis_manifest.json`: `786be0f376ffa68690683d0d51c1f11aa2cfa2f23593d4ad9d34c50ec5890f86`

Draft post lengths are 234, 276 characters. No posts have been sent or published.
