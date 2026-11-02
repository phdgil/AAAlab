"""Contract-driven nested regression search. No terminal data is accepted.

The coordinator owns independent review gates. This executable produces evidence,
never a review receipt. See references/training-runtime.md for the JSON contract.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import csv
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import time
import warnings

import joblib
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor, HistGradientBoostingRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_selection import SelectKBest, VarianceThreshold, f_regression
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.model_selection import ParameterSampler
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR


class TrainingError(ValueError):
    pass


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def binding(path):
    p = Path(path).resolve()
    with p.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(p), "sha256": sha}


def dump(path, value):
    p = Path(path)
    temporary = p.with_suffix(p.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(p)


class FitAudit(BaseEstimator, TransformerMixin):
    """Records the exact ordered IDs entering each fitted pipeline."""
    def fit(self, X, y=None, row_ids=None):
        if row_ids is None or len(row_ids) != len(X):
            raise TrainingError("Missing fit row identities")
        self.fit_row_ids_ = list(row_ids)
        return self

    def transform(self, X):
        return X


class StrictSelectKBest(SelectKBest):
    def fit(self, X, y=None):
        if self.k != "all" and self.k > X.shape[1]:
            raise TrainingError(f"Requested {self.k} features but fold has only {X.shape[1]} nonconstant features")
        return super().fit(X, y)


def estimator(name, seed):
    choices = {"RandomForest": RandomForestRegressor, "ExtraTrees": ExtraTreesRegressor,
               "Ridge": Ridge, "ElasticNet": ElasticNet, "SVR": SVR,
               "KNN": KNeighborsRegressor, "HistGradientBoosting": HistGradientBoostingRegressor}
    if name == "XGBoost":
        from xgboost import XGBRegressor
        cls = XGBRegressor
    elif name == "CatBoost":
        from catboost import CatBoostRegressor
        cls = CatBoostRegressor
    elif name in choices:
        cls = choices[name]
    else:
        raise TrainingError("Unsupported algorithm: " + name)
    model = cls()
    supported = model.get_params()
    defaults = {"random_state": seed, "n_jobs": 1}
    model.set_params(**{k: v for k, v in defaults.items() if k in supported})
    if name == "CatBoost":
        model.set_params(verbose=False, allow_writing_files=False, thread_count=1)
    return model


def pipeline(cell, parameters, seed):
    prep = cell["preprocessing"]
    pipe = Pipeline([("audit", FitAudit()),
                     ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                     ("variance", VarianceThreshold(prep["variance_threshold"])),
                     ("scale", StandardScaler() if prep["scale"] else "passthrough"),
                     ("select", StrictSelectKBest(f_regression)),
                     ("model", estimator(cell["algorithm"], seed))])
    return pipe.set_params(**parameters)


def trials(cell, seed):
    space = cell["search_space"]
    if type(cell["n_trials"]) is not int or cell["n_trials"] < 1:
        raise TrainingError("Each cell requires a positive n_trials")
    counts = space.get("select__k", [])
    if "all" not in counts or len(set(counts)) < 2 or any(k != "all" and (type(k) is not int or k < 1) for k in counts):
        raise TrainingError("Feature-count search requires all and positive integer choices")
    if cell["anchor"].get("select__k") != "all":
        raise TrainingError("Separate all-features anchor is required")
    if any(k != "select__k" and not k.startswith("model__") for k in cell["anchor"]):
        raise TrainingError("Unknown anchor parameter namespace")
    total = 1
    for key, values in space.items():
        if key != "select__k" and not key.startswith("model__"):
            raise TrainingError("Only model parameters and select__k may be searched")
        if not isinstance(values, list) or not values:
            raise TrainingError("Search dimensions must be nonempty explicit lists")
        if any(value in values[:i] for i, value in enumerate(values)):
            raise TrainingError("Duplicate search dimension values: " + key)
        total *= len(values)
    if cell["n_trials"] > total:
        raise TrainingError("Trial budget exceeds distinct Cartesian search choices")
    return [("anchor", dict(cell["anchor"]))] + [
        ("search-" + str(i), dict(p)) for i, p in enumerate(
            ParameterSampler(space, n_iter=cell["n_trials"], random_state=seed))]


def validate_partition(folds, universe, rows):
    if len(folds) < 2 or len({f["fold"] for f in folds}) != len(folds):
        raise TrainingError("At least two distinct folds required")
    observed = []
    for fold in folds:
        train, valid = fold["train_row_ids"], fold["validation_row_ids"]
        if not train or not valid or len(set(train)) != len(train) or len(set(valid)) != len(valid):
            raise TrainingError("Empty or duplicated fold rows")
        if set(train) & set(valid) or set(train) | set(valid) != set(universe):
            raise TrainingError("Fold boundary does not partition its permitted rows")
        if {rows[r]["group_id"] for r in train} & {rows[r]["group_id"] for r in valid}:
            raise TrainingError("Molecular group crosses fold boundary")
        observed.extend(valid)
    if sorted(observed) != sorted(universe):
        raise TrainingError("Validation rows must cover each permitted row exactly once")


def matrix_blocks(X):
    """Bound temporary finite masks to roughly 8 MiB; never truncate an axis."""
    row_count = max(1, (8 * 1024 * 1024) // max(1, X.shape[1]))
    for start in range(0, len(X), row_count):
        yield X[start:start + row_count]


class FeatureStore(Mapping):
    """Verified feature metadata with one resident normalized float64 family."""
    def __init__(self, metadata):
        self.metadata = metadata
        self._family = None
        self._cached = None
        self.peak_resident_bytes = 0

    def __iter__(self):
        return iter(self.metadata)

    def __len__(self):
        return len(self.metadata)

    @property
    def resident_families(self):
        return () if self._family is None else (self._family,)

    def __getitem__(self, family):
        entry = self.metadata[family]
        if family != self._family:
            self._cached = None
            self._family = None
            artifact = entry["artifact"]
            if binding(artifact["path"]) != artifact:
                raise TrainingError("Feature artifact hash mismatch before lazy load")
            with np.load(artifact["path"], allow_pickle=False) as archive:
                X = archive["X"].astype(np.float64, copy=False)
            if binding(artifact["path"]) != artifact:
                raise TrainingError("Feature artifact hash mismatch during lazy load")
            for block in matrix_blocks(X):
                block[~np.isfinite(block)] = np.nan
            self._cached = (X, entry["row_index"])
            self._family = family
            self.peak_resident_bytes = max(self.peak_resident_bytes, X.nbytes)
        return self._cached


def verify_feature_family(path, entry, rows):
    actual = binding(path)
    if actual["sha256"] != entry["artifact"]["sha256"]:
        raise TrainingError("Feature artifact hash mismatch")
    with np.load(path, allow_pickle=False) as archive:
        X = archive["X"]
        row_ids = archive["row_ids"].astype(str).tolist()
        task_ids = archive["task_ids"].astype(str).tolist()
        names = archive["feature_names"].astype(str).tolist()
    if (X.ndim != 2 or list(X.shape) != entry["shape"] or str(X.dtype) != entry["dtype"]
            or len(row_ids) != len(set(row_ids)) or set(row_ids) != set(rows) or len(task_ids) != len(row_ids)
            or names != entry["feature_names"] or len(names) != X.shape[1]
            or any(rows[r]["task_id"] != t for r, t in zip(row_ids, task_ids))):
        raise TrainingError("Feature row/task/column axis mismatch")
    if sum(int((~np.isfinite(block)).sum()) for block in matrix_blocks(X)) != entry["nonfinite_count"]:
        raise TrainingError("Feature nonfinite count mismatch")
    if binding(path) != actual:
        raise TrainingError("Feature artifact hash mismatch during validation")
    return {"artifact": actual, "row_index": {r: i for i, r in enumerate(row_ids)},
            "shape": entry["shape"], "dtype": entry["dtype"], "feature_names": names}


def read_inputs(development, folds_path, manifest_path, contract_path):
    initial_code = binding(__file__)
    initial = [binding(p) for p in (development, folds_path, manifest_path, contract_path)]
    contract, folds, manifest = load(contract_path), load(folds_path), load(manifest_path)
    split_binding = manifest.get("split_manifest")
    if not isinstance(split_binding, dict) or binding(split_binding["path"]) != split_binding:
        raise TrainingError("Missing or stale feature split_manifest binding")
    split = load(split_binding["path"])
    if (manifest.get("status") != "complete" or split.get("status") != "complete"
            or manifest.get("input") != initial[0] or manifest.get("contract") != initial[3]
            or split.get("development") != initial[0] or split.get("folds") != initial[1]
            or split.get("contract") != initial[3]):
        raise TrainingError("Frozen development/folds/contract lineage mismatch")
    initial.append(split_binding)
    spec = contract["training"]
    if spec.get("target_column") != contract["preparation"].get("target_column") or (
            "evaluation" in contract and spec.get("target_column") != contract["evaluation"].get("target_column")):
        raise TrainingError("Training target scale differs from preparation/evaluation contract")
    if spec["metric"] not in ("rmse", "mae") or type(spec["seed"]) is not int:
        raise TrainingError("Explicit integer seed and supported primary metric required")
    if spec.get("nonconvergence_policy", "fail") != "fail":
        raise TrainingError("nonconvergence_policy currently supports only fail")
    essential = ("output_row_id", "task_id", "group_id", "partition", spec["target_column"])
    rows = {}
    with Path(development).open(newline="", encoding="utf-8-sig") as stream:
        for record in csv.DictReader(stream):
            row = {key: record.get(key) for key in essential}
            identity = row["output_row_id"]
            if not identity or identity in rows:
                raise TrainingError("Missing or duplicate development identities")
            rows[identity] = row
    if not rows:
        raise TrainingError("Missing or duplicate development identities")
    if any(r.get("partition") != "development" for r in rows.values()) or len(rows) != split.get("development_rows"):
        raise TrainingError("Training requires exactly the frozen development rows")
    if set(spec["tasks"]) != set(folds["tasks"]) or set(spec["tasks"]) != {r["task_id"] for r in rows.values()}:
        raise TrainingError("Declared tasks, folds and development rows differ")
    target = {r: float(v[spec["target_column"]]) for r, v in rows.items()}
    if not all(np.isfinite(v) for v in target.values()):
        raise TrainingError("Nonfinite development target")
    features, artifacts = {}, []
    for task, task_spec in spec["tasks"].items():
        ids = folds["tasks"][task]["row_ids"]
        if len(ids) != len(set(ids)) or set(ids) != {r for r in rows if rows[r]["task_id"] == task}:
            raise TrainingError("Task row axis mismatch")
        outer = folds["tasks"][task]["outer_folds"]
        validate_partition(outer, ids, rows)
        for fold in outer:
            validate_partition(fold["inner_folds"], fold["train_row_ids"], rows)
        cells = task_spec["cells"]
        if not cells or len({c["candidate_id"] for c in cells}) != len(cells):
            raise TrainingError("Missing or duplicate candidate identities")
        for cell in cells:
            trials(cell, spec["seed"])
            prep = cell["preprocessing"]
            if type(prep["scale"]) is not bool or not isinstance(prep["variance_threshold"], (int, float)) or prep["variance_threshold"] < 0:
                raise TrainingError("Explicit boolean scaling and nonnegative variance threshold required")
            family = cell["family"]
            if family in features:
                continue
            entry = manifest["families"][family]
            path = Path(entry["artifact"]["path"])
            if not path.is_absolute():
                path = Path(manifest_path).resolve().parent / path
            features[family] = verify_feature_family(path, entry, rows)
            artifacts.append(features[family]["artifact"])
    packages = {p: importlib.metadata.version(p) for p in ("numpy", "scikit-learn", "scipy", "joblib")}
    for optional, algorithm in (("xgboost", "XGBoost"), ("catboost", "CatBoost")):
        if any(c["algorithm"] == algorithm for t in spec["tasks"].values() for c in t["cells"]):
            try:
                packages[optional] = importlib.metadata.version(optional)
            except importlib.metadata.PackageNotFoundError:
                packages[optional] = "unavailable"
    identity = {"inputs": initial + artifacts,
                "code": initial_code, "environment": {"python": platform.python_version(), **packages}}
    verify_identity(identity)
    return spec, folds, rows, target, FeatureStore(features), identity


def verify_identity(identity):
    for item in identity["inputs"] + [identity["code"]]:
        if binding(item["path"]) != item:
            raise TrainingError("Input/code hash changed during execution: " + item["path"])


def register_axis(axes, row_ids):
    axis_id = digest(row_ids)
    if axis_id not in axes:
        axes[axis_id] = {"sha256": axis_id, "count": len(row_ids), "row_ids": row_ids}
    elif axes[axis_id]["row_ids"] != row_ids:
        raise TrainingError("Fit-index axis hash collision")
    return axis_id


def frozen_fit_axes(folds):
    axes = {}
    for task in folds["tasks"].values():
        for outer in task["outer_folds"]:
            for fold in [outer, *outer["inner_folds"]]:
                for key in ("train_row_ids", "validation_row_ids"):
                    register_axis(axes, fold[key])
    return axes


def load_fit_index_registry(artifact):
    if binding(artifact["path"]) != artifact:
        raise TrainingError("Fit-index registry hash mismatch")
    registry = load(artifact["path"])
    if registry.get("schema_version") != "1.0" or not isinstance(registry.get("axes"), dict):
        raise TrainingError("Unsupported fit-index registry format")
    for axis_id, axis in registry["axes"].items():
        ids = axis.get("row_ids")
        if (not isinstance(ids, list) or not ids or any(not isinstance(r, str) for r in ids)
                or len(set(ids)) != len(ids) or axis.get("count") != len(ids)
                or axis.get("sha256") != axis_id or digest(ids) != axis_id):
            raise TrainingError("Invalid fit-index axis hash/count/row identities")
    if binding(artifact["path"]) != artifact:
        raise TrainingError("Fit-index registry changed while reading")
    return registry["axes"]


def axis_reference(axes, row_ids):
    axis_id = digest(row_ids)
    if axis_id not in axes or axes[axis_id]["row_ids"] != row_ids:
        raise TrainingError("Actual fit/transform rows differ from frozen fit-index registry")
    return axis_id


def validate_fit_contexts(records, folds, axes):
    """Bind each compact audit record to its exact frozen fitting boundary."""
    expected = {}
    for task, task_folds in folds["tasks"].items():
        for outer in task_folds["outer_folds"]:
            scope = "outer-" + str(outer["fold"])
            outer_pair = (axis_reference(axes, outer["train_row_ids"]),
                          axis_reference(axes, outer["validation_row_ids"]))
            expected[(task, scope, "outer-refit", None)] = outer_pair
            expected[(task, "final-selection", None, outer["fold"])] = outer_pair
            for inner in outer["inner_folds"]:
                expected[(task, scope, None, inner["fold"])] = (
                    axis_reference(axes, inner["train_row_ids"]),
                    axis_reference(axes, inner["validation_row_ids"]))
    for record in records:
        key = tuple(record.get(field) for field in ("task_id", "scope", "phase", "fold"))
        try:
            pair = expected.get(key)
        except TypeError:
            pair = None
        if (pair is None or pair != (record.get("fit_axis_id"), record.get("transform_axis_id"))
                or "fit_row_ids" in record or "transform_row_ids" in record):
            raise TrainingError("Checkpoint fit-index references differ from frozen task/scope/fold context")


def capture_fit_warnings(operation, context, warning_records):
    error = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            result = operation()
        except BaseException as caught_error:
            error = caught_error
    recorded = [{**context, "category": warning.category.__name__, "message": str(warning.message),
                 "nonconverged": issubclass(warning.category, ConvergenceWarning)} for warning in caught]
    warning_records.extend(recorded)
    if error is None and any(w["nonconverged"] for w in recorded):
        error = TrainingError("Nonconverged fit rejected by nonconvergence_policy=fail")
    if error is not None:
        error.fit_warnings = recorded
        raise error
    return result


def fit_predict(cell, params, train, valid, target, features, seed, evidence, context, axes, warning_records=None):
    X, index = features[cell["family"]]
    model = pipeline(cell, params, seed)
    fit_warnings = []
    def fit_and_predict():
        model.fit(X[[index[r] for r in train]], np.array([target[r] for r in train]), audit__row_ids=train)
        return model.predict(X[[index[r] for r in valid]])
    try:
        predictions = capture_fit_warnings(fit_and_predict, context, fit_warnings)
    finally:
        if warning_records is not None:
            warning_records.extend(fit_warnings)
    if not np.all(np.isfinite(predictions)):
        raise TrainingError("Nonfinite predictions")
    evidence.append({**context, "fit_axis_id": axis_reference(axes, model.named_steps["audit"].fit_row_ids_),
                     "transform_axis_id": axis_reference(axes, valid), "learned_steps": ["impute", "variance", "scale", "select", "model"],
                     "warnings": fit_warnings, "nonconverged": False,
                     "selected_feature_count": int(model.named_steps["select"].get_support().sum())})
    return model, predictions


def search_scope(task, scope, cells, cv, target, features, spec, ledger, evidence, axes):
    candidates, failed = [], False
    for cell in cells:
        for trial_id, params in trials(cell, spec["seed"]):
            start = time.monotonic()
            record = {"task_id": task, "scope": scope, "candidate_id": cell["candidate_id"],
                      "family": cell["family"], "algorithm": cell["algorithm"], "trial_id": trial_id,
                      "kind": "anchor" if trial_id == "anchor" else "search", "parameters": params,
                      "fold_scores": [], "status": "running", "failure_reason": None, "warnings": [], "nonconverged": False}
            try:
                errors = []
                for fold in cv:
                    _, predictions = fit_predict(cell, params, fold["train_row_ids"], fold["validation_row_ids"],
                                                 target, features, spec["seed"], evidence,
                                                 {"task_id": task, "scope": scope, "candidate_id": cell["candidate_id"],
                                                  "trial_id": trial_id, "fold": fold["fold"]}, axes, record["warnings"])
                    residual = predictions - np.array([target[r] for r in fold["validation_row_ids"]])
                    loss = residual ** 2 if spec["metric"] == "rmse" else np.abs(residual)
                    errors.extend(loss.tolist())
                    record["fold_scores"].append({"fold": fold["fold"], "loss": float(np.sqrt(loss.mean()) if spec["metric"] == "rmse" else loss.mean())})
                record["score"] = float(np.sqrt(np.mean(errors)) if spec["metric"] == "rmse" else np.mean(errors))
                record["status"] = "complete"
                candidates.append((record["score"], cell["candidate_id"], trial_id, cell, params))
            except Exception as error:
                record.update(status="failed", failure_reason=f"{type(error).__name__}: {error}")
                failed = True
            record["nonconverged"] = any(w["nonconverged"] for w in record["warnings"])
            record["seconds"] = time.monotonic() - start
            ledger.append(record)
    if failed or not candidates:
        raise TrainingError("Declared search contains failed trials; no substitute winner permitted")
    _, _, trial_id, cell, params = min(candidates, key=lambda value: value[:3])
    return {"candidate_id": cell["candidate_id"], "family": cell["family"], "algorithm": cell["algorithm"],
            "trial_id": trial_id, "parameters": params, "preprocessing": cell["preprocessing"]}


def validate_completed_scopes(state, spec, folds, out):
    declared = {}
    for task, task_spec in spec["tasks"].items():
        scopes = ["outer-" + str(f["fold"]) for f in folds["tasks"][task]["outer_folds"]] + ["final-selection"]
        for scope in scopes:
            declared[(task, scope)] = {(c["candidate_id"], t): (c, p)
                for c in task_spec["cells"] for t, p in trials(c, spec["seed"])}

    def check_records(records, enclosing=None):
        seen = set()
        for r in records:
            scope_key = (r.get("task_id"), r.get("scope"))
            trial_key = (r.get("candidate_id"), r.get("trial_id"))
            if (scope_key not in declared or trial_key not in declared[scope_key] or
                (enclosing is not None and scope_key != enclosing) or (scope_key, trial_key) in seen):
                raise TrainingError("Completed scope or attempt ledger identity mismatch")
            seen.add((scope_key, trial_key))
            cell, params = declared[scope_key][trial_key]
            seconds = r.get("seconds")
            if (r.get("parameters") != params or r.get("family") != cell["family"] or
                r.get("algorithm") != cell["algorithm"] or r.get("status") not in {"complete", "failed"} or
                type(seconds) not in (int, float) or not np.isfinite(seconds) or seconds < 0):
                raise TrainingError("Completed scope or attempt ledger values mismatch")

    check_records(state["ledger"])
    for attempt in state.get("attempt_history", []):
        enclosing = (attempt.get("task_id"), attempt.get("scope"))
        if enclosing not in declared:
            raise TrainingError("Completed scope or attempt history identity mismatch")
        check_records(attempt["ledger"], enclosing)
        for r in attempt["fit_evidence"]:
            if ((r.get("task_id"), r.get("scope")) != enclosing or
                (r.get("phase") != "outer-refit" and (r.get("candidate_id"), r.get("trial_id")) not in declared[enclosing])):
                raise TrainingError("Completed scope or attempt fit identity mismatch")
        validate_fit_contexts(attempt["fit_evidence"], folds, frozen_fit_axes(folds))
    for r in state["fit_evidence"]:
        if (r.get("task_id"), r.get("scope")) not in declared:
            raise TrainingError("Completed scope fit identity mismatch")
    expected_keys = set()
    expected_oof = []
    expected_models = []
    for task, task_spec in spec["tasks"].items():
        outer = folds["tasks"][task]["outer_folds"]
        for scope, cv, fold in [("outer-" + str(f["fold"]), f["inner_folds"], f) for f in outer] + [("final-selection", outer, None)]:
            key = task + ":" + scope
            expected_keys.add(key)
            if key not in state["completed"]:
                continue
            records = [r for r in state["ledger"] if r["task_id"] == task and r["scope"] == scope]
            evidence = [r for r in state["fit_evidence"] if r["task_id"] == task and r["scope"] == scope]
            expected = {(c["candidate_id"], t): (c, p) for c in task_spec["cells"] for t, p in trials(c, spec["seed"])}
            actual = [(r["candidate_id"], r["trial_id"]) for r in records]
            if len(actual) != len(expected) or set(actual) != set(expected):
                raise TrainingError("Completed scope trial coverage mismatch")
            candidates = []
            expected_fits = []
            for r in records:
                cell, params = expected[(r["candidate_id"], r["trial_id"])]
                if (r["status"] != "complete" or r["parameters"] != params or
                    r["family"] != cell["family"] or r["algorithm"] != cell["algorithm"] or
                    [f["fold"] for f in r["fold_scores"]] != [f["fold"] for f in cv] or
                    not np.isfinite(r.get("score", float("nan")))):
                    raise TrainingError("Completed scope trial evidence mismatch")
                candidates.append((r["score"], cell["candidate_id"], r["trial_id"], cell, params))
                expected_fits.extend((cell["candidate_id"], r["trial_id"], f["fold"], None) for f in cv)
            _, _, trial_id, cell, params = min(candidates, key=lambda x: x[:3])
            chosen = {k: cell[k] for k in ("candidate_id", "family", "algorithm", "preprocessing")}
            chosen.update(trial_id=trial_id, parameters=params)
            if state["completed"][key] != chosen:
                raise TrainingError("Completed scope selection mismatch")
            if fold is None:
                if state["final_selection"].get(task) != chosen:
                    raise TrainingError("Completed scope final selection mismatch")
            else:
                expected_fits.append((cell["candidate_id"], None, None, "outer-refit"))
                expected_oof.extend((task, r, fold["fold"], "selection_procedure") for r in fold["validation_row_ids"])
                expected_models.append(str(out / "outer_models" / (digest([task, scope]) + ".joblib")))
            actual_fits = [(r.get("candidate_id"), r.get("trial_id"), r.get("fold"), r.get("phase")) for r in evidence]
            if sorted(map(repr, actual_fits)) != sorted(map(repr, expected_fits)):
                raise TrainingError("Completed scope fit evidence mismatch")
    if not set(state["completed"]) <= expected_keys:
        raise TrainingError("Completed scope unknown identity")
    if sorted((r["task_id"], r["output_row_id"], r["outer_fold"], r["candidate_id"]) for r in state["oof"]) != sorted(expected_oof) or any(not np.isfinite(r["prediction"]) for r in state["oof"]):
        raise TrainingError("Completed scope OOF coverage mismatch")
    if sorted(a["path"] for a in state["artifacts"]) != sorted(expected_models):
        raise TrainingError("Completed scope model coverage mismatch")


def run_search(development, folds_path, manifest_path, contract_path, output_dir, resume=False):
    spec, folds, rows, target, features, identity = read_inputs(development, folds_path, manifest_path, contract_path)
    out = Path(output_dir).resolve()
    if any(Path(a["path"]).is_relative_to(out) for a in identity["inputs"]):
        raise TrainingError("Output overlaps protected input")
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out / "checkpoint.json"
    expected_axes = frozen_fit_axes(folds)
    if resume:
        state = load(checkpoint)
        if state.get("schema_version") != "2.0":
            raise TrainingError("Unsupported checkpoint format; expected 2.0 deduplicated fit indices. Start a new versioned search.")
        if state["identity"] != identity:
            raise TrainingError("Resume input/code/environment hash mismatch")
        axes = load_fit_index_registry(state["fit_index_registry"])
        if axes != expected_axes:
            raise TrainingError("Fit-index registry differs from frozen folds")
        validate_fit_contexts(state["fit_evidence"], folds, axes)
        validate_completed_scopes(state, spec, folds, out)
        for artifact in state.get("artifacts", []):
            if binding(artifact["path"]) != artifact:
                raise TrainingError("Resume output artifact hash mismatch")
    else:
        if any(out.iterdir()):
            raise TrainingError("New search requires an empty output directory")
        axes = expected_axes
        registry_path = out / "fit_index_registry.json"
        dump(registry_path, {"schema_version": "1.0", "axes": axes})
        state = {"schema_version": "2.0", "identity": identity, "status": "incomplete", "completed": {},
                 "fit_index_registry": binding(registry_path),
                 "ledger": [], "fit_evidence": [], "attempt_history": [], "oof": [], "final_selection": {}, "artifacts": []}
    del expected_axes
    (out / "outer_models").mkdir(exist_ok=True)
    def save():
        dump(checkpoint, state)
        dump(out / "trial_ledger.json", state["ledger"])
        dump(out / "fit_evidence.json", {"schema_version": "2.0", "fit_index_registry": state["fit_index_registry"],
                                        "records": state["fit_evidence"]})
        expected = sum((len(folds["tasks"][t]["outer_folds"]) + 1) * sum(c["n_trials"] + 1 for c in v["cells"]) for t, v in spec["tasks"].items())
        historical = [r for a in state.get("attempt_history", []) for r in a["ledger"]]
        all_attempts = historical + state["ledger"]
        dump(out / "attempt_history.json", state.get("attempt_history", []))
        coverage = []
        for task, task_spec in spec["tasks"].items():
            for scope in ["outer-" + str(f["fold"]) for f in folds["tasks"][task]["outer_folds"]] + ["final-selection"]:
                for cell in task_spec["cells"]:
                    records = [r for r in state["ledger"] if r["task_id"] == task and r["scope"] == scope and r["candidate_id"] == cell["candidate_id"]]
                    coverage.append({"task_id": task, "scope": scope, "candidate_id": cell["candidate_id"],
                                     "expected_search_trials": cell["n_trials"], "expected_anchor_trials": 1,
                                     "complete": sum(r["status"] == "complete" for r in records),
                                     "failed": sum(r["status"] == "failed" for r in records),
                                     "pending": cell["n_trials"] + 1 - len(records)})
        dump(out / "search_coverage.json", {"schema_version": "1.0", "status": state["status"], "cells": coverage,
             "attempted_trials": len(all_attempts),
             "failed_attempts": sum(r["status"] == "failed" for r in all_attempts),
             "attempt_seconds": sum(r["seconds"] for r in all_attempts),
             "expected_trials": expected, "completed_trials": sum(r["status"] == "complete" for r in state["ledger"]),
             "failed_trials": sum(r["status"] == "failed" for r in state["ledger"]),
             "completed_scopes": sorted(state["completed"]), "failure_reason": state.get("failure_reason")})
        dump(out / "final_selection.json", {"schema_version": "1.0", "status": state["status"],
             "method": "fresh full-development CV; no outer evaluation scores used", "tasks": state["final_selection"], "identity": identity})
        with (out / "selection_procedure_oof.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["task_id", "output_row_id", "outer_fold", "candidate_id", "prediction"])
            writer.writeheader()
            writer.writerows(state["oof"])
    save()
    try:
        for task, task_spec in spec["tasks"].items():
            outer = folds["tasks"][task]["outer_folds"]
            scopes = [("outer-" + str(f["fold"]), f["inner_folds"], f) for f in outer]
            scopes.append(("final-selection", outer, None))
            for scope, cv, fold in scopes:
                key = task + ":" + scope
                if key in state["completed"]:
                    continue
                # Preserve prior execution cost and fit evidence before restarting a scope.
                old_ledger = [r for r in state["ledger"] if r["task_id"] == task and r["scope"] == scope]
                old_evidence = [r for r in state["fit_evidence"] if r["task_id"] == task and r["scope"] == scope]
                if old_ledger or old_evidence:
                    state.setdefault("attempt_history", []).append({"task_id": task, "scope": scope,
                        "ledger": old_ledger, "fit_evidence": old_evidence,
                        "failure_reason": state.get("failure_reason"),
                        "failure_warnings": state.get("failure_warnings", [])})
                state["ledger"] = [r for r in state["ledger"] if not (r["task_id"] == task and r["scope"] == scope)]
                state["fit_evidence"] = [r for r in state["fit_evidence"] if not (r["task_id"] == task and r["scope"] == scope)]
                chosen = search_scope(task, scope, task_spec["cells"], cv, target, features, spec, state["ledger"], state["fit_evidence"], axes)
                if fold is None:
                    state["final_selection"][task] = chosen
                else:
                    model, predictions = fit_predict(chosen, chosen["parameters"], fold["train_row_ids"], fold["validation_row_ids"],
                                                     target, features, spec["seed"], state["fit_evidence"],
                                                     {"task_id": task, "scope": scope, "phase": "outer-refit", "candidate_id": chosen["candidate_id"]}, axes)
                    path = out / "outer_models" / (digest([task, scope]) + ".joblib")
                    joblib.dump(model, path)
                    state["artifacts"].append(binding(path))
                    state["oof"].extend({"task_id": task, "output_row_id": r, "outer_fold": fold["fold"],
                                         "candidate_id": "selection_procedure", "prediction": float(p)}
                                        for r, p in zip(fold["validation_row_ids"], predictions))
                state["completed"][key] = chosen
                state["status"] = "incomplete"
                state.pop("failure_reason", None)
                save()
        if len(state["oof"]) != len(rows):
            raise TrainingError("OOF coverage incomplete")
        validate_completed_scopes(state, spec, folds, out)
        verify_identity(identity)
        if binding(state["fit_index_registry"]["path"]) != state["fit_index_registry"]:
            raise TrainingError("Fit-index registry hash mismatch at search completion")
        state["status"] = "complete"
    except BaseException as error:
        state["status"] = "incomplete" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed"
        state["failure_reason"] = f"{type(error).__name__}: {error}"
        state["failure_warnings"] = getattr(error, "fit_warnings", [])
        save()
        raise
    save()
    return state


def final_fit(development, folds_path, manifest_path, contract_path, selection_path, lock_path, output_dir):
    """Fit the locked selection only. The runtime enforces reviewed STOP authorization."""
    spec, folds, rows, target, features, identity = read_inputs(development, folds_path, manifest_path, contract_path)
    selected_binding, lock_binding = binding(selection_path), binding(lock_path)
    selection, lock = load(selection_path), load(lock_path)
    if lock.get("decision") != "STOP" or lock.get("selection") != selected_binding:
        raise TrainingError("Final fit requires STOP with exact selection binding")
    if selection.get("status") != "complete" or selection.get("identity") != identity:
        raise TrainingError("Final selection is incomplete or stale")
    if lock.get("tasks") != {t: c["candidate_id"] for t, c in selection["tasks"].items()} or set(selection["tasks"]) != set(spec["tasks"]):
        raise TrainingError("Locked candidate task matrix mismatch")
    out = Path(output_dir).resolve()
    protected = identity["inputs"] + [binding(selection_path), binding(lock_path)]
    if any(Path(a["path"]).is_relative_to(out) for a in protected):
        raise TrainingError("Output overlaps protected input")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise TrainingError("Final fit requires empty versioned output directory")
    report = {"schema_version": "1.0", "status": "incomplete", "identity": identity,
              "selection": selected_binding, "lock": lock_binding, "models": {}, "fit_evidence": []}
    dump(out / "final_fit.json", report)
    try:
        for task, chosen in selection["tasks"].items():
            cell = next(c for c in spec["tasks"][task]["cells"] if c["candidate_id"] == chosen["candidate_id"])
            if any(chosen[k] != cell[k] for k in ("family", "algorithm", "preprocessing")):
                raise TrainingError("Locked pipeline disagrees with contract")
            if (chosen["trial_id"], chosen["parameters"]) not in trials(cell, spec["seed"]):
                raise TrainingError("Locked trial was not in declared search")
            ids = folds["tasks"][task]["row_ids"]
            X, index = features[chosen["family"]]
            model = pipeline(cell, chosen["parameters"], spec["seed"])
            fit_warnings = []
            capture_fit_warnings(lambda: model.fit(X[[index[r] for r in ids]], np.array([target[r] for r in ids]), audit__row_ids=ids),
                                 {"task_id": task, "phase": "final-fit"}, fit_warnings)
            path = out / (digest(task) + ".joblib")
            joblib.dump(model, path)
            report["models"][task] = {"artifact": binding(path), "candidate_id": chosen["candidate_id"],
                                      "family": chosen["family"], "feature_names": load(manifest_path)["families"][chosen["family"]]["feature_names"]}
            report["fit_evidence"].append({"task_id": task, "fit_row_ids": model.named_steps["audit"].fit_row_ids_,
                                           "warnings": fit_warnings, "nonconverged": False})
            del X, model
        verify_identity(identity)
        if binding(selection_path) != selected_binding or binding(lock_path) != lock_binding:
            raise TrainingError("Selection/lock changed during final fit")
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "incomplete" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "failed"
        report["failure_reason"] = f"{type(error).__name__}: {error}"
        report["failure_warnings"] = getattr(error, "fit_warnings", [])
        dump(out / "final_fit.json", report)
        raise
    dump(out / "final_fit.json", report)
    return report


def synthetic_fixture(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(910)
    X = rng.normal(size=(24, 5))
    X[1, 2] = np.nan
    rows = [{"output_row_id": f"r{i}", "task_id": "oral", "group_id": f"g{i}",
             "partition": "development", "parent_smiles": "C", "target_pLD50": float(2 * X[i, 0] + X[i, 1])} for i in range(24)]
    with (root / "development.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    ids = [r["output_row_id"] for r in rows]
    def partition(members, n):
        return [{"fold": i, "train_row_ids": [r for j, r in enumerate(members) if j % n != i],
                 "validation_row_ids": [r for j, r in enumerate(members) if j % n == i]} for i in range(n)]
    outer = partition(ids, 3)
    for fold in outer:
        fold["inner_folds"] = partition(fold["train_row_ids"], 2)
    dump(root / "folds.json", {"schema_version": "1.0", "tasks": {"oral": {"row_ids": ids, "outer_folds": outer}}})
    names = [f"f{i}" for i in range(5)]
    # Deliberately different feature order proves identity-based, not positional joining.
    reverse = list(reversed(range(24)))
    np.savez(root / "features.npz", X=X[reverse], row_ids=np.array(ids)[reverse],
             task_ids=np.array(["oral"] * 24), feature_names=np.array(names))
    dump(root / "features.json", {"families": {"synthetic": {"artifact": binding(root / "features.npz"),
               "shape": [24, 5], "dtype": "float64", "feature_names": names, "parameters": {},
               "recipe": "synthetic", "nonfinite_count": 1}}})
    cell = {"candidate_id": "ridge-synthetic", "family": "synthetic", "algorithm": "Ridge",
            "search_space": {"model__alpha": [0.1, 1.0], "select__k": [2, "all"]},
            "n_trials": 4, "anchor": {"model__alpha": 1.0, "select__k": "all"},
            "preprocessing": {"scale": True, "variance_threshold": 0.0}}
    dump(root / "contract.json", {"preparation": {"target_column": "target_pLD50"}, "training": {"target_column": "target_pLD50", "metric": "rmse",
                "seed": 71, "tasks": {"oral": {"cells": [cell]}}}})
    manifest = load(root / "features.json")
    split = {"status": "complete", "development": binding(root / "development.csv"),
             "folds": binding(root / "folds.json"), "contract": binding(root / "contract.json"),
             "development_rows": 24}
    dump(root / "split_manifest.json", split)
    manifest.update(status="complete", input=binding(root / "development.csv"),
                    contract=binding(root / "contract.json"), split_manifest=binding(root / "split_manifest.json"))
    dump(root / "features.json", manifest)
    return root


def run_preflight(output, contract_path=None):
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise TrainingError("Preflight output must be empty")
    report = {"schema_version": "1.0", "status": "incomplete", "synthetic_only": True,
              "pipeline_identity": binding(__file__), "independent_review_required": True, "checks": []}
    try:
        root = synthetic_fixture(out / "synthetic-fixture")
        paths = [root / n for n in ("development.csv", "folds.json", "features.json", "contract.json")]
        state = run_search(*paths, root / "search")
        spec, folds, rows, target, features, identity = read_inputs(*paths)
        axes = load_fit_index_registry(state["fit_index_registry"])
        if len(state["oof"]) != 24 or len(state["ledger"]) != 20:
            raise TrainingError("Synthetic search coverage failed")
        for entry in state["fit_evidence"]:
            if set(axes[entry["fit_axis_id"]]["row_ids"]) & set(axes[entry["transform_axis_id"]]["row_ids"]):
                raise TrainingError("Synthetic fit boundary overlap")
        outer = folds["tasks"]["oral"]["outer_folds"][0]
        def select(y):
            evidence, ledger = [], []
            chosen = search_scope("oral", "outer-0", spec["tasks"]["oral"]["cells"], outer["inner_folds"],
                                  y, features, spec, ledger, evidence, axes)
            model, prediction = fit_predict(chosen, chosen["parameters"], outer["train_row_ids"], outer["validation_row_ids"],
                                            y, features, spec["seed"], evidence, {"phase": "perturbation-check"}, axes)
            return chosen, prediction, model.named_steps["impute"].statistics_
        first = select(target)
        altered = {r: y + (100000 if r in outer["validation_row_ids"] else 0) for r, y in target.items()}
        second = select(altered)
        if first[0] != second[0]:
            raise TrainingError("Holdout labels changed model selection")
        np.testing.assert_array_equal(first[1], second[1])
        np.testing.assert_array_equal(first[2], second[2])
        report["checks"] = ["complete nested synthetic search", "all/search anchor coverage", "fit row disjointness",
                            "holdout-label perturbation leaves selection, preprocessing and predictions unchanged"]
        report["fixture_checkpoint"] = binding(root / "search" / "checkpoint.json")
        report["declared_cell_checks"] = []
        if contract_path is not None:
            report["contract"] = binding(contract_path)
            declared = load(contract_path)["training"]
            for task, task_spec in declared["tasks"].items():
                for cell in task_spec["cells"]:
                    check = {"task_id": task, "candidate_id": cell["candidate_id"], "status": "running", "fit_evidence": [],
                             "fit_index_axes": {}}
                    report["declared_cell_checks"].append(check)
                    choices = trials(cell, declared["seed"])
                    columns = max(k for k in cell["search_space"]["select__k"] if k != "all") + 2
                    X = np.random.default_rng(declared["seed"]).normal(size=(24, columns))
                    ids = [f"synthetic-{i}" for i in range(24)]
                    cell_axes = check["fit_index_axes"]
                    register_axis(cell_axes, ids[:16])
                    register_axis(cell_axes, ids[16:])
                    y = {r: float(X[i, 0] * 2 + X[i, 1]) for i, r in enumerate(ids)}
                    matrices = {cell["family"]: (X, {r: i for i, r in enumerate(ids)})}
                    for trial_id, parameters in (choices[0], choices[1]):
                        _, pred = fit_predict(cell, parameters, ids[:16], ids[16:], y, matrices, declared["seed"],
                                              check["fit_evidence"], {"trial_id": trial_id}, cell_axes)
                        changed = {r: value + (100000 if r in ids[16:] else 0) for r, value in y.items()}
                        _, same = fit_predict(cell, parameters, ids[:16], ids[16:], changed, matrices, declared["seed"],
                                              check["fit_evidence"], {"trial_id": trial_id, "perturbed": True}, cell_axes)
                        np.testing.assert_array_equal(pred, same)
                    check["status"] = "complete"
            if binding(contract_path) != report["contract"]:
                raise TrainingError("Contract changed during preflight")
        report["status"] = "complete"
    except BaseException as error:
        report.update(status="failed", failure_reason=f"{type(error).__name__}: {error}")
        dump(out / "preflight.json", report)
        raise
    dump(out / "preflight.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search")
    for flag in ("development", "folds", "features", "contract", "output"):
        search.add_argument("--" + flag, required=True)
    search.add_argument("--resume", action="store_true")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output", required=True)
    preflight.add_argument("--contract", help="Also smoke-fit every declared model/family on synthetic features")
    final = sub.add_parser("final-fit")
    for flag in ("development", "folds", "features", "contract", "selection", "lock", "output"):
        final.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    if args.command == "search":
        result = run_search(args.development, args.folds, args.features, args.contract, args.output, args.resume)
        print(json.dumps({"status": result["status"], "output": args.output}))
    elif args.command == "final-fit":
        result = final_fit(args.development, args.folds, args.features, args.contract, args.selection, args.lock, args.output)
        print(json.dumps({"status": result["status"], "output": args.output}))
    else:
        print(json.dumps(run_preflight(args.output, args.contract)))


if __name__ == "__main__":
    # Keep custom transformer pickle names importable by the separate evaluator.
    from qsar_train import main as entrypoint
    entrypoint()
