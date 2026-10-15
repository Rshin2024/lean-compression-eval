# Reproducing the statistical analysis

The executed notebook is `../analysis.ipynb`; its self-contained preview is `../analysis.html`. The notebook validates all 9,600 paired main-cohort design slots and 6,276 exact valid-proof ratios, independently reproduces eight standard-prompt family-bootstrap intervals, three conditional T0 matchups and two source-length sensitivity coefficients, and displays four scientific figures.

No command here requests model generations, runs Lean, contacts a provider or allocates cloud resources. Analysis is post hoc and exploratory on 100 curated questions in 19 families. Family-block resampling retains all questions in each chosen family and preserves question weighting. Question-level resampling is a sensitivity analysis; neither treats attempts or reused proof pairings as independent observations.

## Portable inputs

- `../data/attempts_normalized.csv`: 19,200 audited observations from the two main cohorts, including exact ratio strings, completion, status, source-length, token and cost fields.
- `../data/primary_attempts.csv`: the separate 9,600-row final-cohort export.
- `../data/task_metadata.*`: family and selection-group metadata; `../data/smoke_descriptive.csv` keeps the earlier ten-question smoke study separate.
- `../tables/main/` and `../tables/cross-run/`: all reviewed CSV/JSON tables and exact bootstrap-index files from the analysis.
- `../inputs/`: exact byte copies of the three final comparison DTOs and the user-supplied candidate JSONL. The latter includes the original curator metadata, including mathematical proof summaries supplied by the user; the notebook does not display or execute those fields.
- `bundled-input-manifest.json`: hashes, sizes and original provenance for 72 copied files. Copies use distinct files, never hard links.

The package excludes raw API response bodies, provider hidden reasoning, response/SSE captures and large proof graphs. Full provider/proof provenance re-auditing therefore requires the original private evaluation store. This package reproduces the statistical analysis, not the paid model experiment or the Lean verification campaign.

## Environment and execution

The recorded environment uses Python 3.12.14. Exact tested package versions are in `requirements.txt` and `environment.json`. In a compatible Python 3.12 environment, install those packages, then run from the bundle directory:

```sh
python reproducibility/execute_notebook.py
```

The helper runs every cell in order using the invoking Python interpreter, saves the executed notebook and four embedded figures, exports HTML through nbconvert, and writes `notebook-execution.json`. It does not require credentials or a live notebook service. The HTML uses embedded images and inline styling, with no external script or stylesheet dependency.

The notebook execution overwrites its derived notebook/preview/figure outputs. It does not alter the copied inputs, reviewed statistical tables or original evaluation files. `notebook-checks.json` records its numerical assertions; figure/browser inspection is recorded separately and is not inferred from execution success.

## Full statistical table replay

The original primary and cross-run analysis sources and cross-run tests are copied byte-for-byte under `main/` and `cross-run/`. `analysis-plan.md` is also unchanged. The independent joint-contest checker and its historical audit are preserved in `review/`; that contest is supplemental, not the original paper metric. The archived checker is a historical source snapshot rather than a portable entry point; use the wrapper and notebook for portable execution.

```sh
python reproducibility/reproduce.py primary
python reproducibility/reproduce.py cross-run
python -m unittest discover -s reproducibility/cross-run -p test_cross_run.py -v
```

The wrappers refuse to overwrite an existing replay directory. Use `--output /path/to/a/new/directory` to choose another output. They verify the copied-file manifest first and never modify the original source snapshots.

Primary replay changes only the input/output path globals of the copied source, using the exact comparison DTO and candidate-file copies. Its statistics, bootstrap and tests remain unchanged.

Cross-run replay uses a documented input adapter because private generation exports were intentionally excluded. It reads the exact audited normalized CSV, validates design-slot identities, completion flags, costs and exact metric strings against the copied comparison DTOs, then calls the original analysis pipeline and bootstrap functions unchanged. Token accounting is reproduced from the audited normalized values, not rederived from excluded raw provider payloads. Differences in output provenance paths/timestamps are expected; statistical tables should agree.

## Interpretation safeguards

The 128,000-token rerun also changes OpenAI processing from default to Fast/priority and uses streaming transport. Sample positions match the design, not random seeds or responses, so the contrast is not a causal token-cap estimate. Missing or truncated answers remain scheduled failures; conditional T0 excludes failures without assigning zero. Formatting adherence is part of strict-interface acceptance. Source-length coefficients are observational and change with parse-failure eligibility.

Recorded-response cost estimates exclude unknown original-request charges: seven in the baseline and 30 in the rerun. The baseline VM figure is a historical as-of estimate ($56.82), while the rerun figure is a closed-allocation estimate ($59.33). Storage, IP, network, taxes and credits are not a known invoice total.

Exact numerator/denominator and expanded-size columns must remain strings or arbitrary-precision integers, never floating-point parsing. Family IDs are zero-padded strings. Failure/missingness is explicit; no unknown field is imputed as zero.

## Replay validation in this bundle

Both portable replay commands were executed. All 14 cross-run CSV/bootstrap files reproduced byte-for-byte. Primary point estimates and interval endpoints reproduced exactly; family standard errors and associated p-values/Holm values differ only by floating-point summation order (maximum absolute difference 9.99e-16), within the declared 1e-12 comparison tolerance. The original source iterates an unordered set of family labels, so byte identity is not claimed for those specific tables. No frozen source was edited. `primary-replay-validation.json` and `cross-run-replay-validation.json` retain the complete checks.
