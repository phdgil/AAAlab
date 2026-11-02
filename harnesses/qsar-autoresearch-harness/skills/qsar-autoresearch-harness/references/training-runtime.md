# Training runtime

`scripts/qsar_train.py` runs regression search on development artifacts only.
The coordinator must use `qsar_runtime.py` to require accepted upstream reviews
and an independent review of the actual preflight before scientific training.
The standalone numerical APIs do not authenticate reviews or provide a sandbox.
No terminal labels, terminal metrics or terminal files are inputs to these APIs.

## Contract

The research contract contains this explicit `training` object. These values are
an illustrative fixture, not a recommended scientific model list or budget.

```json
{
  "training": {
    "target_column": "target_pLD50",
    "metric": "rmse",
    "seed": 71,
    "nonconvergence_policy": "fail",
    "tasks": {
      "oral": {
        "cells": [{
          "candidate_id": "ridge-descriptors",
          "family": "RDKit2D",
          "algorithm": "Ridge",
          "preprocessing": {"scale": true, "variance_threshold": 0.0},
          "search_space": {"model__alpha": [0.1, 1.0], "select__k": [32, "all"]},
          "n_trials": 4,
          "anchor": {"model__alpha": 1.0, "select__k": "all"}
        }]
      }
    }
  }
}
```

Metrics are `rmse` or `mae`, minimized over pooled inner-validation row losses.
Each task requires a nonempty candidate matrix with unique candidate IDs.
Supported algorithm identifiers are `RandomForest`, `ExtraTrees`, `Ridge`,
`ElasticNet`, `SVR`, `KNN`, `HistGradientBoosting`, `XGBoost`, and `CatBoost`.
The last two require their real optional dependencies; there are no substitutes
and no installation side effects. Unspecified estimator parameters use the
installed library defaults, bound by the environment version record. Declare
scientifically important parameters explicitly in the contract.

Search dimensions are explicit nonempty lists, using `model__<parameter>` and
`select__k`. The feature-count dimension includes `all` and at least one positive
integer. `ParameterSampler` samples distinct Cartesian combinations using the
declared seed. `n_trials` cannot exceed the combination count. The separate
all-features anchor adds one trial per cell and scope; it never consumes or
masquerades as the declared search budget. All denotes all features surviving
the fold-local variance filter. An integer exceeding that fold's available
features fails explicitly instead of silently selecting fewer features.

Preprocessing uses median imputation with empty columns retained, variance
filtering, optional standard scaling, and supervised univariate F-regression
selection. Every learned transform and estimator is fitted inside the same
pipeline on only the current training rows. No rows or input features are capped.
Python warnings are captured per trial/fold with category, message, fit context,
and a `nonconverged` flag. `nonconvergence_policy` defaults to `fail`, currently
the only accepted value: a scikit-learn `ConvergenceWarning` (including subclasses)
fails that trial and therefore blocks complete search coverage. Ordinary warnings
remain in the ledger even when fitting succeeds. The same rule applies to outer
refits and locked final fits; their failure reports retain the warnings.
`nonconverged: false` means no convergence warning was observed, not a guarantee
of mathematical convergence. Native-library stdout/stderr messages that are not
Python warnings still require review of execution logs.
This implementation does not include pruning, time-budget termination,
classification, learned neural representations, or arbitrary distribution-valued
search spaces. Such requirements need an explicit implementation extension.

Each search dimension must contain distinct values. Repeated choices, including
numerically equivalent values such as `1` and `1.0`, are rejected rather than
silently deduplicated: changing the space would change the declared budget.
The separate anchor may intentionally match one searched configuration; it is
reported as an anchor and does not increase distinct search coverage.

## Inputs and fold semantics

`development.csv` supplies immutable `output_row_id`, `task_id`, `group_id`,
`partition=development`, and the configured target. All declared task rows must occur exactly once.
`folds.json` follows the preparation runtime contract: each task contains ordered
`row_ids` and `outer_folds`; each outer fold contains `fold`, `train_row_ids`,
`validation_row_ids`, and `inner_folds` with the same membership fields.
Partitions must cover all permitted rows exactly once on validation, and
molecular groups cannot cross any train/validation boundary.

`feature_manifest.json` contains `families[NAME]` with `artifact` (`path`,
`sha256`), `shape`, `dtype`, `feature_names`, and `nonfinite_count`. NPZ files
contain `X`, `row_ids`, `task_ids`, `feature_names`. The runner checks hashes,
row/task/feature axes, shape, dtype and nonfinite counts before fitting. It joins
by identity even when NPZ rows are reordered. Nonfinite feature values become
missing values for fold-local imputation. Targets must be finite.
The complete feature manifest must bind `input`, `contract`, and `split_manifest`.
The complete split manifest must bind the exact development, folds, and contract
files and declare `development_rows`. Missing or stale frozen lineage blocks
training. All consumed files are hashed before reading and reverified before
completion; this never reads the sealed-terminal artifact listed in a split manifest.

Feature validation loads one NPZ family at a time in its stored dtype, verifies
every requested family's hash, shape, dtype, row/task/feature axes and nonfinite
count before fitting, then discards the matrix and retains metadata/row indices.
The returned mapping-compatible `FeatureStore` starts with no resident matrix.
On demand it evicts the preceding family, rechecks the next file's hash, loads
the full matrix and converts to float64 without reducing precision, rows or
features. Nonfinite checks/normalization use bounded row blocks; this changes
temporary mask size only. Repeated access to the same family reuses its matrix.
Cross-family access reloads from the hash-bound artifact. Development CSV reading
retains only row ID, task, group, partition and target; full provenance remains
in the immutable source CSV.

The cache bounds retained feature storage to one float64 family, not total process
memory. Decompression/casting can temporarily retain the native matrix too;
fold indexing, imputation, scaling, selection, estimator state and model artifacts
also require memory. A family plus those costs can still exceed available RAM.
The resource gate must account for these costs before real training; the cache
is not proof that a particular workload fits. `peak_resident_bytes` measures
cached matrix bytes only, not process peak RSS.

For each outer fold, the runner searches every cell and all its trials on the
frozen inner folds, selects the entire family/model/parameter pipeline using
inner losses, refits only on outer training rows, and predicts outer validation
rows. The OOF candidate identity is `selection_procedure`, because the selected
model may differ between outer folds. Actual selected candidates remain in
`checkpoint.json.completed` and fit evidence. Outer scores never select a model.

Final selection is a separate search with fresh fits using the frozen outer
train/validation membership as full-development CV. It does not reuse outer
evaluation scores or outer-fitted models. Its losses are selection evidence,
not another unbiased nested estimate. `final_selection.json` records the
winning configuration; it does not fit any model on all development data.

## Commands and Python APIs

```text
python qsar_train.py preflight --output PREFLIGHT --contract research.json
python qsar_train.py search --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --output SEARCH
python qsar_train.py search --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --output SEARCH --resume
python qsar_train.py final-fit --development development.csv --folds folds.json --features feature_manifest.json --contract research.json --selection SEARCH/final_selection.json --lock lock.json --output FINAL
python scripts/test_qsar_train.py -v
```

`run_search(development, folds_path, manifest_path, contract_path, output_dir,
resume=False)` returns the checkpoint state. `final_fit(development, folds_path,
manifest_path, contract_path, selection_path, lock_path, output_dir)` returns its
fit manifest. Exceptions are failures; callers must not treat an existing output
directory as successful completion.

Preflight executes deterministic synthetic checks through those same public
APIs, retains a synthetic fixture and its actual fit evidence, and writes
`preflight.json`. It is self-contained in the installed runtime and does not
import repository test files. With `--contract`, it additionally fits
every declared cell's all-features anchor and first sampled configuration on
synthetic matrices, checking holdout-label perturbation invariance. This checks
actual imports and parameter compatibility. It is deliberately not full HPO or
a claim that arbitrary real feature schemas are valid. Review the bound contract,
code and preflight artifacts independently before real training.

## Artifacts and completion

- `trial_ledger.json`: task, scope, candidate, family, algorithm, anchor/search
  identity, exact parameters, fold losses, pooled score, timing, status and
  failure reason for trials in the current scope attempts, plus per-fold warnings
  and explicit nonconvergence flags. Superseded attempts remain separately below.
- `attempt_history.json`: ordered superseded scope attempts, preserving their
  task/scope, ledger, fit evidence, failure reason and warnings. The checkpoint
  retains the same history. A successful retry does not erase an earlier failure.
- `fit_index_registry.json`: immutable `schema_version: "1.0"` with
  `axes: {AXIS_SHA256: {sha256, count, row_ids}}`. Each distinct ordered row axis
  from the frozen inner/outer folds is stored once; SHA-256 uses the runtime's
  canonical `digest(row_ids)` JSON representation. The registry is written once,
  not embedded into or rewritten with every checkpoint.
- `fit_evidence.json`: `schema_version: "2.0"`, the registry's exact file binding,
  and `records` containing `fit_axis_id`, `transform_axis_id`, learned steps,
  selected feature count, and fit context. Resolve each axis ID through the
  registry to recover every actual ordered fitting/prediction row ID. There is
  no row cap or loss of row-level evidence. Pipeline artifacts also retain their
  actual fit IDs in the audit step.
- `selection_procedure_oof.csv`: `task_id,output_row_id,outer_fold,candidate_id,
  prediction`; one prediction per development row when complete.
- `outer_models/*.joblib`: fitted outer-training pipelines. These are trusted
  local Python serialization artifacts; load only artifacts from this run.
- `search_coverage.json`: aggregate and per-task/scope/cell expected anchor/search
  counts, completed, failed and pending counts. Failed trials block selection;
  a successful neighboring cell cannot silently substitute. `attempted_trials`,
  `failed_attempts` and `attempt_seconds` include recorded superseded attempts;
  active `completed_trials` and `failed_trials` describe current coverage only.
  Trial timing excludes outer refits and unrecorded work lost to hard termination;
  it is not total process wall time or a guarantee of resource-budget enforcement.
- `final_selection.json`: `schema_version,status,method,tasks,identity`.
  Each task has `candidate_id,family,algorithm,trial_id,parameters,preprocessing`.
- `checkpoint.json`: `schema_version: "2.0"`, compact fit evidence, a
  `fit_index_registry` file binding, completed scopes, fitted artifact hashes and
  the complete input/code/environment identity. Full row axes are not duplicated
  in this file. The separate final-fit manifest still stores one complete fit-row
  list per selected final model.

Checkpoints commit after a complete outer scope or final-selection scope. Resume
requires identical data, splits, feature files, contract, training source and
package versions, and verifies saved model hashes and the fit-index registry's
file hash, internal axis hashes/counts, frozen-fold equality and evidence
references. Before skipping a completed scope, it reconciles exact declared trial
identities, parameters, fold evidence, selected candidate, OOF row/fold membership
and required model bindings. Missing or substituted records cannot be replaced by
a completion flag or matching aggregate count. Inconsistent checkpoints reject
before rewriting persisted outputs; reconciliation also runs before completion.
Legacy checkpoint formats are rejected explicitly; they require a
new versioned search and are never silently reinterpreted. Interrupted scopes restart;
completed scopes are reused. Restarted scope evidence is retained in attempt
history before active records are replaced. Failed scopes can be retried only unchanged; repairs
requiring changed code/configuration need a new versioned output directory.
Caught interruptions are `incomplete`; hard termination leaves the last durable
incomplete checkpoint. A trial failure is `failed`, never complete scientific
coverage. Persisted final selection and coverage carry the overall status.

## Locked final fitting

Only the reviewed improvement STOP permits a separate final-fit runtime stage.
The lock JSON must contain `decision: "STOP"`, `selection: {path, sha256}` bound
to the exact complete final-selection file, and `tasks: {TASK: candidate_id}`.
There is no self-asserted `reviewed` boolean. Runtime stage reviews supply that
authorization; the numerical API verifies the lock's consistency and input
identity. It rechecks that each selected trial belongs to the declared search,
then fits only those selected pipelines on all permitted development rows.

`final_fit.json` records `schema_version,status,identity,selection,lock,models,
fit_evidence`. Each task's model has `artifact:{path,sha256}`, `candidate_id`,
`family`, and `feature_names`. Any partial final fit remains failed/incomplete.
Terminal inference and terminal evaluation belong to another execution.
