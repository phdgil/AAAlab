"""Contract-driven nested regression/classification search. No terminal data is accepted.

The coordinator owns independent review gates. This executable produces evidence,
never a review receipt. See references/training-runtime.md for the JSON contract.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import tempfile
import time
import warnings

import joblib
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import (ExtraTreesClassifier, ExtraTreesRegressor, RandomForestClassifier,
                              RandomForestRegressor, HistGradientBoostingClassifier, HistGradientBoostingRegressor)
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_selection import SelectKBest, VarianceThreshold, f_classif, f_regression
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.metrics import average_precision_score, balanced_accuracy_score, matthews_corrcoef, roc_auc_score
from sklearn.model_selection import ParameterSampler
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC, SVR
from threadpoolctl import threadpool_limits

import qsar_calibration
import qsar_domain
import qsar_models
import qsar_search


class TrainingError(ValueError):
    pass


class TrainingBudgetExhausted(TrainingError):
    """A start-boundary budget check stopped work; no in-fit cancellation."""


def resource_controls(spec):
    value = spec.get("resources", {})
    if not isinstance(value, dict) or set(value) - {"threads", "concurrent_fits", "deadline"}:
        raise TrainingError("resources accepts only threads, concurrent_fits and deadline")
    result = {"threads": value.get("threads", 1), "concurrent_fits": value.get("concurrent_fits", 1),
              "deadline": value.get("deadline")}
    for name in ("threads", "concurrent_fits"):
        if type(result[name]) is not int or result[name] < 1:
            raise TrainingError("resources." + name + " must be a positive integer")
    if result["deadline"] is not None:
        qsar_search.validate_resource_policy({"maximum_trials": 1, "deadline": result["deadline"]})
    return result


def check_deadline(spec, cell=None, context=None):
    deadlines = [resource_controls(spec)["deadline"]]
    if cell is not None:
        deadlines.append(cell.get("resource_policy", {}).get("deadline"))
    now = datetime.now(timezone.utc)
    for deadline in deadlines:
        if deadline is not None and now >= datetime.fromisoformat(deadline):
            raise TrainingBudgetExhausted("Resource deadline reached before " + str(context or "work start"))


@contextmanager
def fit_resources(spec):
    threads = resource_controls(spec)["threads"]
    # threadpoolctl covers BLAS/OpenMP; torch uses its own explicit intra-op pool.
    import sys
    torch = sys.modules.get("torch")
    previous = None if torch is None else torch.get_num_threads()
    if torch is not None:
        torch.set_num_threads(threads)
    try:
        with threadpool_limits(limits=threads):
            yield
    finally:
        if torch is not None:
            torch.set_num_threads(previous)


def backend(cell):
    value = cell.get("backend", "parameter_sampler")
    if value not in ("parameter_sampler", "optuna"):
        raise TrainingError("backend must be parameter_sampler or optuna")
    return value


def validate_cell_controls(cell, kind):
    backend(cell)
    calibration = cell.get("calibration")
    sampling = cell.get("sampling")
    if kind != "classification" and (calibration is not None or sampling is not None):
        raise TrainingError("calibration and sampling require task_type=classification")
    if calibration is not None and (not isinstance(calibration, dict)
            or set(calibration) != {"method", "n_splits"}
            or calibration["method"] not in ("sigmoid", "isotonic")
            or type(calibration["n_splits"]) is not int or calibration["n_splits"] < 2):
        raise TrainingError("calibration requires method=sigmoid|isotonic and integer n_splits >= 2")
    if sampling is not None:
        if (not isinstance(sampling, dict) or set(sampling) - {"method", "k_neighbors", "sampling_strategy"}
                or sampling.get("method") != "smote" or feature_route(cell) != "numeric"):
            raise TrainingError("sampling requires real SMOTE on a numeric candidate only")
        if type(sampling.get("k_neighbors", 5)) is not int or sampling.get("k_neighbors", 5) < 1:
            raise TrainingError("SMOTE k_neighbors must be a positive integer")
    if "resource_policy" in cell and "n_trials" in cell:
        qsar_search.validate_resource_policy(cell["resource_policy"], n_trials=cell["n_trials"])


def task_type(spec):
    value = spec.get("task_type", "regression")
    if value not in ("regression", "classification"):
        raise TrainingError("task_type must be regression or classification")
    return value


def valid_threshold(value):
    return type(value) in (int, float) and np.isfinite(value) and 0 <= value <= 1


def thresholds(spec):
    fixed = spec.get("threshold", 0.5)
    if not valid_threshold(fixed):
        raise TrainingError("Classification threshold must be a finite number in [0, 1]")
    grid = spec.get("threshold_grid", [fixed])
    if (not isinstance(grid, list) or not grid or any(not valid_threshold(t) for t in grid)
            or len(set(grid)) != len(grid)):
        raise TrainingError("threshold_grid requires distinct finite numbers in [0, 1]")
    if "threshold_grid" in spec and spec["metric"] not in ("mcc", "balanced_accuracy"):
        raise TrainingError("threshold_grid is supported only for mcc and balanced_accuracy")
    return grid


def validate_training_spec(spec):
    kind = task_type(spec)
    resource_controls(spec)
    metrics = ("rmse", "mae") if kind == "regression" else ("mcc", "balanced_accuracy", "roc_auc", "average_precision")
    if spec["metric"] not in metrics or type(spec["seed"]) is not int:
        raise TrainingError("Explicit integer seed and supported primary metric required for " + kind)
    if spec.get("nonconvergence_policy", "fail") != "fail":
        raise TrainingError("nonconvergence_policy currently supports only fail")
    if kind == "classification":
        if type(spec.get("positive_class", 1)) is not int or spec.get("positive_class", 1) != 1:
            raise TrainingError("Binary classification positive_class must be 1")
        thresholds(spec)
        if "selection_constraints" in spec:
            constraints = spec["selection_constraints"]
            allowed = {"min_sensitivity", "min_specificity", "max_sensitivity_specificity_gap"}
            if (not isinstance(constraints, dict) or not constraints or set(constraints) - allowed
                    or any(not valid_threshold(value) for value in constraints.values())):
                raise TrainingError("selection_constraints requires declared sensitivity/specificity bounds in [0, 1]")
    elif any(key in spec for key in ("threshold", "threshold_grid", "positive_class", "selection_constraints")):
        raise TrainingError("Classification threshold/positive_class options require task_type=classification")


def require_binary_labels(values, context):
    values = np.asarray(values)
    if values.ndim != 1 or not len(values) or values.dtype.kind not in "iuf" or not np.all(np.isin(values, [0, 1])):
        raise TrainingError("Binary classification requires explicit finite labels 0/1: " + context)
    if set(values.tolist()) != {0, 1}:
        raise TrainingError("Classification fold feasibility requires both classes 0/1: " + context)


def classification_score(metric, labels, probabilities, threshold):
    require_binary_labels(labels, "metric validation rows")
    if not valid_threshold(threshold):
        raise TrainingError("Classification threshold must be a finite number in [0, 1]")
    probabilities = np.asarray(probabilities, dtype=float)
    if (probabilities.shape != np.asarray(labels).shape or not np.all(np.isfinite(probabilities))
            or np.any((probabilities < 0) | (probabilities > 1))):
        raise TrainingError("Classification predictions must be row-aligned positive-class probabilities in [0, 1]")
    if metric == "mcc":
        return float(matthews_corrcoef(labels, probabilities >= threshold))
    if metric == "balanced_accuracy":
        return float(balanced_accuracy_score(labels, probabilities >= threshold))
    if metric == "roc_auc":
        return float(roc_auc_score(labels, probabilities))
    if metric == "average_precision":
        return float(average_precision_score(labels, probabilities))
    raise TrainingError("Unsupported classification metric: " + metric)


def classification_summary(record, cv, target, spec):
    saved = record.get("fold_predictions", [])
    if [f.get("fold") for f in saved] != [f["fold"] for f in cv]:
        raise TrainingError("Completed scope classification prediction folds mismatch")
    labels, probabilities = [], []
    for fold, prediction in zip(cv, saved):
        y = [target[r] for r in fold["validation_row_ids"]]
        classification_score(spec["metric"], y, prediction["prediction"], spec.get("threshold", 0.5))
        labels.extend(y)
        probabilities.extend(prediction["prediction"])
    scores = [{"threshold": float(t), "score": classification_score(spec["metric"], labels, probabilities, t)}
              for t in thresholds(spec)]
    constraints = spec.get("selection_constraints")
    if constraints:
        y, p = np.asarray(labels), np.asarray(probabilities)
        for row in scores:
            predicted = p >= row["threshold"]
            sensitivity = float(predicted[y == 1].mean())
            specificity = float((~predicted[y == 0]).mean())
            row.update(sensitivity=sensitivity, specificity=specificity,
                       selection_eligible=(
                           sensitivity >= constraints.get("min_sensitivity", 0)
                           and specificity >= constraints.get("min_specificity", 0)
                           and abs(sensitivity - specificity) <= constraints.get("max_sensitivity_specificity_gap", 1)))
    eligible = [row for row in scores if row.get("selection_eligible", True)]
    best = max(eligible or scores, key=lambda r: r["score"])
    result = {"task_type": "classification", "positive_class": 1, "threshold": best["threshold"],
            "threshold_scores": scores, "score": best["score"],
            "fold_scores": [{"fold": f["fold"], "score": classification_score(
                spec["metric"], [target[r] for r in f["validation_row_ids"]], p["prediction"], best["threshold"])}
                for f, p in zip(cv, saved)]}
    if constraints:
        result.update(selection_eligible=bool(eligible), selection_constraints=dict(constraints))
    return result


def selection(cell, trial_id, params, spec, record):
    chosen = candidate_contract(cell)
    chosen.update(trial_id=trial_id, parameters=params, task_type=task_type(spec))
    if task_type(spec) == "classification":
        chosen.update(positive_class=1, threshold=record["threshold"])
    if backend(cell) == "optuna" and trial_id != "anchor":
        chosen["trial_evidence_sha256"] = record["evidence_sha256"]
    return chosen


def score_order(score, spec):
    return -score if task_type(spec) == "classification" else score


def set_task_attributes(model, spec):
    model.task_type_ = task_type(spec)
    model.positive_class_ = 1 if model.task_type_ == "classification" else None
    model.threshold_ = float(spec.get("threshold", 0.5)) if model.task_type_ == "classification" else None
    if isinstance(model.named_steps["model"], qsar_models.ChemicalRuleClassifier):
        model.named_steps["model"].threshold_ = model.threshold_


def feature_route(cell):
    route = cell.get("feature_route", "numeric")
    if route not in ("numeric", "smiles"):
        raise TrainingError("feature_route must be numeric or smiles")
    if (cell["family"] == "SMILES") != (route == "smiles"):
        raise TrainingError("SMILES family requires the explicit feature_route=smiles")
    chemical = cell["algorithm"] in ("ChemicalRuleRegressor", "ChemicalRuleClassifier", "FrozenSmilesEncoder")
    if chemical != (route == "smiles"):
        raise TrainingError("SMILES route requires a chemical rule model or FrozenSmilesEncoder")
    return route


def rule_candidate(cell):
    return cell["algorithm"] in ("ChemicalRuleRegressor", "ChemicalRuleClassifier")


def prepare_checkpoint(cell, base_directory):
    encoder = dict(cell.get("encoder", {}))
    checkpoint_dir = Path(encoder.get("checkpoint_dir", ""))
    if not checkpoint_dir.is_absolute():
        checkpoint_dir = Path(base_directory) / checkpoint_dir
    root, inventory = qsar_models._checkpoint_files(checkpoint_dir, encoder.get("checkpoint_inventory"))
    encoder.update(checkpoint_dir=str(root), checkpoint_inventory=inventory)
    cell["encoder"] = encoder
    return {"checkpoint_dir": str(root), "checkpoint_inventory": inventory}


def candidate_contract(cell):
    return {k: cell[k] for k in ("candidate_id", "family", "algorithm", "preprocessing",
                                "feature_route", "rule", "encoder", "head", "calibration", "sampling",
                                "backend", "resource_policy") if k in cell}


def learned_steps(cell):
    if rule_candidate(cell):
        steps = ["chemical_basis", "model"]
    else:
        steps = (["encoder"] if feature_route(cell) == "smiles" else []) + ["impute", "variance", "scale", "select"]
        steps += (["smote"] if cell.get("sampling") else []) + ["model"]
    return steps + (["calibration"] if cell.get("calibration") else [])


def model_provenance(model, cell):
    result = {"feature_route": feature_route(cell), "learned_steps": learned_steps(cell)}
    if cell.get("calibration"):
        result["calibration"] = model.calibration_audit_
    if cell.get("sampling"):
        result["sampling"] = qsar_calibration._sampler_audit(model.base_estimator_ if cell.get("calibration") else model,
                                                          model.named_steps["audit"].fit_row_ids_)
    if rule_candidate(cell):
        rule = model.named_steps["model"]
        basis = {"descriptor_ranges": rule.descriptor_ranges_,
                 "descriptor_bin_edges": {k: v.tolist() for k, v in rule.descriptor_bin_edges_.items()},
                 "scaffold_vocabulary": rule.scaffold_vocabulary_,
                 "scaffold_frequencies": rule.scaffold_frequencies_}
        result.update(selected_feature_count=len(rule.basis_.get_feature_names_out()),
                      chemical_basis_sha256=digest(basis), scoring_method=rule.scoring_method())
    else:
        result["selected_feature_count"] = int(model.named_steps["select"].get_support().sum())
        if feature_route(cell) == "smiles":
            result["encoder"] = model.named_steps["encoder"].encoding_method()
            result["encoder"]["truncation"] = "forbidden"
            result["encoder"]["mean_pooling_tokens"] = "all_nonpadding_including_special_tokens"
    return result


def frozen_recipe(cell, params, spec):
    return digest({"candidate": candidate_contract(cell), "parameters": params, "seed": spec["seed"],
                   "task_type": task_type(spec), "threshold": spec.get("threshold", 0.5) if task_type(spec) == "classification" else None,
                   "threads": resource_controls(spec)["threads"]})


def validate_fitted_identity(model, chosen, spec, task, identity):
    if not isinstance(chosen, dict) or not isinstance(chosen.get("parameters"), dict):
        raise TrainingError("Fitted pipeline requires its selected recipe and parameters")
    cell = next((c for c in spec["tasks"][task]["cells"] if c["candidate_id"] == chosen.get("candidate_id")), None)
    if cell is None or candidate_contract(chosen) != candidate_contract(cell):
        raise TrainingError("Fitted pipeline selection differs from declared candidate contract")
    if not isinstance(chosen.get("trial_id"), str) or not chosen["trial_id"]:
        raise TrainingError("Fitted pipeline selection lacks its chosen trial")
    if backend(cell) != "optuna" or chosen["trial_id"] == "anchor":
        if (chosen["trial_id"], chosen["parameters"]) not in trials(cell, spec["seed"]):
            raise TrainingError("Fitted pipeline parameters differ from the declared search trial")
    elif set(chosen["parameters"]) != set(cell["search_space"]):
        raise TrainingError("Fitted pipeline parameters differ from the declared adaptive search dimensions")
    if getattr(model, "training_identity_sha256_", None) != digest(identity):
        raise TrainingError("Fitted pipeline training identity differs from frozen training inputs")
    recipe_spec = {**spec, "threshold": chosen.get("threshold", spec.get("threshold", 0.5))}
    if getattr(model, "frozen_recipe_sha256_", None) != frozen_recipe(cell, chosen["parameters"], recipe_spec):
        raise TrainingError("Fitted pipeline recipe differs from locked training selection")
    calibrated = isinstance(model, qsar_calibration.CrossFittedCalibratedClassifier)
    if calibrated != bool(cell.get("calibration")):
        raise TrainingError("Fitted pipeline calibration wrapper differs from declared candidate contract")
    if (getattr(model, "task_type_", None) != task_type(spec)
            or chosen.get("task_type") != task_type(spec)
            or getattr(model, "feature_route_", None) != feature_route(cell)):
        raise TrainingError("Fitted pipeline task/feature route differs from declared candidate contract")


def attach_fit_metadata(model, cell, row_ids, features, spec, fit_domain=False):
    set_task_attributes(model, spec)
    model.feature_route_ = feature_route(cell)
    model.fit_training_row_ids_ = list(row_ids)
    model.applicability_domain_ = None
    model.applicability_domain_sha256_ = None
    if fit_domain and "applicability_domain" in spec:
        parents = getattr(features, "parent_smiles", None)
        if parents is None or any(not parents.get(r) for r in row_ids):
            raise TrainingError("Applicability domain requires development parent_smiles for every training row")
        model.applicability_domain_ = qsar_domain.fit([parents[r] for r in row_ids], row_ids, spec["applicability_domain"])
        model.applicability_domain_sha256_ = model.applicability_domain_.to_dict()["artifact_sha256"]


class LabelFreeFrozenSmilesEncoder(qsar_models.FrozenSmilesEncoder):
    """The pipeline may pass y; the feature-stage encoder never receives it."""
    def fit(self, X, y=None):
        return super().fit(X)


def positive_probability(model, X):
    if not callable(getattr(model, "predict_proba", None)):
        raise TrainingError("Classification requires predict_proba; score-only estimators are unsupported")
    classes = np.asarray(model.classes_)
    if classes.ndim != 1 or len(classes) != 2 or set(classes.tolist()) != {0, 1}:
        raise TrainingError("Fitted classifier must expose exactly classes 0/1")
    probabilities = np.asarray(model.predict_proba(X), dtype=float)
    if (probabilities.shape != (len(X), 2) or not np.all(np.isfinite(probabilities))
            or np.any((probabilities < 0) | (probabilities > 1))
            or not np.allclose(probabilities.sum(axis=1), 1, rtol=1e-7, atol=1e-7)):
        raise TrainingError("Classifier predict_proba returned invalid row-aligned probabilities")
    return probabilities[:, np.flatnonzero(classes == 1)[0]]


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
    payload = json.dumps(value, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent,
                                         prefix=p.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(8):
            try:
                temporary.replace(p)
                return
            except PermissionError as error:
                # Windows scanners/readers can briefly deny an atomic replace.
                # Retry only committing these exact bytes, never a scientific fit.
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 7:
                    raise
                time.sleep(.01 * 2 ** attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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


def estimator(name, seed, kind="regression"):
    choices = ({"RandomForest": RandomForestRegressor, "ExtraTrees": ExtraTreesRegressor,
                "Ridge": Ridge, "ElasticNet": ElasticNet, "SVR": SVR,
                "KNN": KNeighborsRegressor, "HistGradientBoosting": HistGradientBoostingRegressor}
               if kind == "regression" else
               {"LogisticRegression": LogisticRegression, "SVC": SVC, "RandomForest": RandomForestClassifier,
                "ExtraTrees": ExtraTreesClassifier, "HistGradientBoosting": HistGradientBoostingClassifier,
                "KNN": KNeighborsClassifier, "MLP": MLPClassifier, "GaussianNB": GaussianNB})
    if name == "XGBoost":
        from xgboost import XGBClassifier, XGBRegressor
        cls = XGBClassifier if kind == "classification" else XGBRegressor
    elif name == "CatBoost":
        from catboost import CatBoostClassifier, CatBoostRegressor
        cls = CatBoostClassifier if kind == "classification" else CatBoostRegressor
    elif name == "LightGBM" and kind == "classification":
        from lightgbm import LGBMClassifier
        cls = LGBMClassifier
    elif name in choices:
        cls = choices[name]
    else:
        raise TrainingError("Unsupported algorithm: " + name)
    model = cls()
    supported = model.get_params()
    defaults = {"random_state": seed, "n_jobs": 1}
    model.set_params(**{k: v for k, v in defaults.items() if k in supported})
    if name == "SVC":
        model.set_params(probability=True)
    if name == "CatBoost":
        model.set_params(verbose=False, allow_writing_files=False, thread_count=1)
        if kind == "classification":
            model.set_params(random_seed=seed)
    return model


def pipeline(cell, parameters, seed, kind="regression", threads=1):
    validate_cell_controls(cell, kind)
    route = feature_route(cell)
    prep = cell["preprocessing"]
    if rule_candidate(cell):
        expected = "ChemicalRuleClassifier" if kind == "classification" else "ChemicalRuleRegressor"
        if cell["algorithm"] != expected or prep != {}:
            raise TrainingError("Chemical rule task type must match training; preprocessing must be {}")
        options = cell.get("rule", {})
        if not isinstance(options, dict) or set(options) - {"descriptor_names", "smarts_rules", "fit_intercept", "max_iter"}:
            raise TrainingError("Unsupported chemical rule configuration")
        model_class = getattr(qsar_models, expected)
        defaults = {"random_state": seed} if kind == "classification" else {}
        model = model_class(**defaults, **options)
        steps = [("audit", FitAudit()), ("model", model)]
    else:
        if (not isinstance(prep, dict) or set(prep) != {"scale", "variance_threshold"}
                or type(prep["scale"]) is not bool or type(prep["variance_threshold"]) not in (int, float)
                or not np.isfinite(prep["variance_threshold"]) or prep["variance_threshold"] < 0):
            raise TrainingError("Explicit boolean scaling and finite nonnegative variance threshold required")
        steps = [("audit", FitAudit())]
        algorithm = cell["algorithm"]
        if route == "smiles":
            encoder = cell.get("encoder")
            head = cell.get("head")
            if (not isinstance(encoder, dict) or not {"checkpoint_dir", "checkpoint_inventory", "pooling"} <= set(encoder)
                    or set(encoder) - {"checkpoint_dir", "checkpoint_inventory", "pooling", "batch_size", "device", "max_length"}
                    or encoder["pooling"] not in ("mean", "cls")):
                raise TrainingError("FrozenSmilesEncoder requires a pinned local checkpoint and explicit mean/cls pooling")
            if not isinstance(head, dict) or set(head) != {"algorithm"}:
                raise TrainingError("FrozenSmilesEncoder requires a declared sklearn head.algorithm")
            algorithm = head["algorithm"]
            if algorithm in ("XGBoost", "CatBoost", "LightGBM"):
                raise TrainingError("FrozenSmilesEncoder head must be a supported sklearn estimator")
            steps.append(("encoder", LabelFreeFrozenSmilesEncoder(**encoder)))
        steps.extend([("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                      ("variance", VarianceThreshold(prep["variance_threshold"])),
                      ("scale", StandardScaler() if prep["scale"] else "passthrough"),
                      ("select", StrictSelectKBest(f_classif if kind == "classification" else f_regression)),
                      ("model", estimator(algorithm, seed, kind))])
    pipe = Pipeline(steps)
    if cell.get("calibration") and isinstance(pipe.named_steps["model"], SVC):
        pipe.set_params(model__probability=False)
    pipe.set_params(**parameters)
    thread_parameters = {name: threads for name in pipe.get_params(deep=True)
                         if name.rsplit("__", 1)[-1] in ("n_jobs", "nthread", "thread_count", "num_threads")}
    pipe.set_params(**thread_parameters)
    if cell.get("calibration") and isinstance(pipe.named_steps["model"], SVC) and pipe.named_steps["model"].probability:
        raise TrainingError("Explicit calibration requires SVC probability=False")
    if cell.get("sampling"):
        sampling = cell["sampling"]
        pipe = qsar_calibration.with_smote(pipe, representation=route, random_state=seed,
                    k_neighbors=sampling.get("k_neighbors", 5), sampling_strategy=sampling.get("sampling_strategy", "auto"))
    if cell.get("calibration"):
        return qsar_calibration.CrossFittedCalibratedClassifier(pipe, random_state=seed, **cell["calibration"])
    if kind == "classification" and not callable(getattr(pipe, "predict_proba", None)):
        raise TrainingError("Classification requires predict_proba; score-only estimators are unsupported")
    return pipe


def trials(cell, seed):
    feature_route(cell)
    space = cell["search_space"]
    if not isinstance(space, dict) or not space or not isinstance(cell["anchor"], dict):
        raise TrainingError("Each cell requires explicit search dimensions and an anchor")
    if type(cell["n_trials"]) is not int or cell["n_trials"] < 1:
        raise TrainingError("Each cell requires a positive n_trials")
    adaptive = backend(cell) == "optuna"
    if adaptive:
        qsar_search.validate_search_spec(cell, seed, resource_policy=cell.get("resource_policy"))
    def choices(value):
        return value.get("choices", []) if isinstance(value, dict) else value
    def numeric_dimension(value, integer=False, positive=False):
        values = choices(value)
        if isinstance(value, dict) and value.get("type") in ("int", "float"):
            if integer and value["type"] != "int":
                return False
            values = [value["low"], value["high"]]
        return (isinstance(values, list) and len(values) >= 2 and all(
            (type(v) is int if integer else type(v) in (int, float)) and np.isfinite(v)
            and (v > 0 if positive else v >= 0) for v in values))
    if rule_candidate(cell):
        regularizer = "model__C" if cell["algorithm"] == "ChemicalRuleClassifier" else "model__alpha"
        if "select__k" in space or "select__k" in cell["anchor"]:
            raise TrainingError("Chemical rules search bins/regularization, not numeric select__k")
        for name in ("model__n_bins", regularizer):
            values = space.get(name)
            if not numeric_dimension(values, name == "model__n_bins", name == "model__n_bins" or regularizer == "model__C") or name not in cell["anchor"]:
                raise TrainingError("Chemical rules require explicit n_bins and regularization search plus anchor")
        if type(cell["anchor"]["model__n_bins"]) is not int or cell["anchor"]["model__n_bins"] < 1:
            raise TrainingError("Chemical rule n_bins choices must be positive integers")
        if any(type(v) not in (int, float) or not np.isfinite(v) or v < 0 or (regularizer == "model__C" and v == 0)
               for v in [cell["anchor"][regularizer]]):
            raise TrainingError("Chemical rule regularization choices must be finite and valid")
    else:
        counts = choices(space.get("select__k", []))
        if "all" not in counts or len(set(counts)) < 2 or any(k != "all" and (type(k) is not int or k < 1) for k in counts):
            raise TrainingError("Feature-count search requires all and positive integer choices")
        if cell["anchor"].get("select__k") != "all":
            raise TrainingError("Separate all-features anchor is required")
    allowed = lambda k: (k.startswith("model__") or (not rule_candidate(cell) and k == "select__k")) and k.rsplit("__", 1)[-1] not in (
        "n_jobs", "nthread", "thread_count", "num_threads")
    if any(not allowed(k) or k in ("model__threshold", "model__smiles_column") for k in cell["anchor"]):
        raise TrainingError("Unknown anchor parameter namespace")
    total = 1
    for key, values in space.items():
        if not allowed(key) or key in ("model__threshold", "model__smiles_column"):
            raise TrainingError("Only model parameters and select__k may be searched")
        if adaptive:
            continue
        if not isinstance(values, list) or not values:
            raise TrainingError("Search dimensions must be nonempty explicit lists")
        if any(value in values[:i] for i, value in enumerate(values)):
            raise TrainingError("Duplicate search dimension values: " + key)
        total *= len(values)
    if adaptive:
        # Suggestions must be asked only after preceding CV outcomes were told.
        return [("anchor", dict(cell["anchor"]))]
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
    """One verified resident numeric family or label-free parent-SMILES axis."""
    def __init__(self, metadata, parent_smiles=None, group_ids=None):
        self.metadata = metadata
        self.parent_smiles = {} if parent_smiles is None else parent_smiles
        self.group_ids = {} if group_ids is None else group_ids
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
            if entry.get("feature_route") == "smiles":
                X = np.asarray([self.parent_smiles[r] for r in entry["row_index"]], dtype=object)
            else:
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
    source_hashes = [binding(module.__file__) for module in (qsar_models, qsar_domain, qsar_calibration, qsar_search)]
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
    validate_training_spec(spec)
    essential = ("output_row_id", "task_id", "group_id", "partition", "parent_smiles", spec["target_column"])
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
    try:
        target = {r: float(v[spec["target_column"]]) for r, v in rows.items()}
    except (TypeError, ValueError) as error:
        raise TrainingError("Development targets must be explicit numeric values") from error
    if not all(np.isfinite(v) for v in target.values()):
        raise TrainingError("Nonfinite development target")
    if task_type(spec) == "classification":
        require_binary_labels(list(target.values()), "development rows")
    parents = {r: row["parent_smiles"] for r, row in rows.items()}
    needs_chemistry = "applicability_domain" in spec or any(
        feature_route(c) == "smiles" for t in spec["tasks"].values() for c in t["cells"])
    if needs_chemistry and any(not isinstance(s, str) or not s.strip() for s in parents.values()):
        raise TrainingError("SMILES/domain training requires development parent_smiles for every row")
    if not needs_chemistry:
        parents = {}
    for row in rows.values():
        row.pop("parent_smiles")
    if "applicability_domain" in spec:
        qsar_domain._recipe(spec["applicability_domain"])
    features, artifacts, checkpoints = {}, [], []
    for task, task_spec in spec["tasks"].items():
        ids = folds["tasks"][task]["row_ids"]
        if len(ids) != len(set(ids)) or set(ids) != {r for r in rows if rows[r]["task_id"] == task}:
            raise TrainingError("Task row axis mismatch")
        outer = folds["tasks"][task]["outer_folds"]
        validate_partition(outer, ids, rows)
        for fold in outer:
            validate_partition(fold["inner_folds"], fold["train_row_ids"], rows)
            if task_type(spec) == "classification":
                for boundary in [fold, *fold["inner_folds"]]:
                    for side in ("train_row_ids", "validation_row_ids"):
                        require_binary_labels([target[r] for r in boundary[side]],
                                              f"task={task}, outer={fold['fold']}, fold={boundary['fold']}, {side}")
        cells = task_spec["cells"]
        if not cells or len({c["candidate_id"] for c in cells}) != len(cells):
            raise TrainingError("Missing or duplicate candidate identities")
        for cell in cells:
            validate_cell_controls(cell, task_type(spec))
            trials(cell, spec["seed"])
            if cell["algorithm"] == "FrozenSmilesEncoder":
                checkpoint = prepare_checkpoint(cell, Path(contract_path).resolve().parent)
                if checkpoint not in checkpoints:
                    checkpoints.append(checkpoint)
                    artifacts.extend(binding(Path(checkpoint["checkpoint_dir"]) / name) for name in checkpoint["checkpoint_inventory"])
            prep = cell["preprocessing"]
            if rule_candidate(cell):
                if prep != {}:
                    raise TrainingError("Chemical rule preprocessing must be {}; its basis is fitted inside the model")
            elif (not isinstance(prep, dict) or set(prep) != {"scale", "variance_threshold"}
                    or type(prep["scale"]) is not bool or type(prep["variance_threshold"]) not in (int, float)
                    or not np.isfinite(prep["variance_threshold"]) or prep["variance_threshold"] < 0):
                raise TrainingError("Explicit boolean scaling and finite nonnegative variance threshold required")
            family = cell["family"]
            if family in features:
                continue
            if feature_route(cell) == "smiles":
                features[family] = {"feature_route": "smiles", "row_index": {r: i for i, r in enumerate(rows)},
                                    "feature_names": ["parent_smiles"]}
                continue
            entry = manifest["families"][family]
            path = Path(entry["artifact"]["path"])
            if not path.is_absolute():
                path = Path(manifest_path).resolve().parent / path
            features[family] = verify_feature_family(path, entry, rows)
            artifacts.append(features[family]["artifact"])
    packages = {p: importlib.metadata.version(p) for p in ("numpy", "scikit-learn", "scipy", "joblib", "threadpoolctl")}
    all_cells = [cell for task in spec["tasks"].values() for cell in task["cells"]]
    for package, requested, error_type in (
            ("optuna", any(backend(c) == "optuna" for c in all_cells), qsar_search.SearchDependencyError),
            ("imbalanced-learn", any(c.get("sampling") for c in all_cells), qsar_calibration.CalibrationDependencyError)):
        if requested:
            try:
                packages[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError as error:
                raise error_type("Requested training dependency unavailable: " + package + "; no fallback") from error
    required = {"rdkit"} if needs_chemistry else set()
    if checkpoints:
        required.update(("torch", "transformers", "tokenizers", "huggingface-hub"))
    for package in sorted(required):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise TrainingError("Requested SMILES/domain dependency unavailable: " + package) from error
    if checkpoints:
        try:
            packages["safetensors"] = importlib.metadata.version("safetensors")
        except importlib.metadata.PackageNotFoundError:
            packages["safetensors"] = "unavailable"
    for optional, algorithm in (("xgboost", "XGBoost"), ("catboost", "CatBoost"), ("lightgbm", "LightGBM")):
        if any(c["algorithm"] == algorithm for t in spec["tasks"].values() for c in t["cells"]):
            try:
                packages[optional] = importlib.metadata.version(optional)
            except importlib.metadata.PackageNotFoundError:
                packages[optional] = "unavailable"
    identity = {"inputs": initial + artifacts,
                "code": initial_code, "source_hashes": source_hashes, "checkpoints": checkpoints,
                "environment": {"python": platform.python_version(), **packages}}
    verify_identity(identity)
    return spec, folds, rows, target, FeatureStore(features, parents, {r: row["group_id"] for r, row in rows.items()}), identity


def verify_identity(identity):
    for item in identity["inputs"] + [identity["code"]] + identity["source_hashes"]:
        if binding(item["path"]) != item:
            raise TrainingError("Input/code hash changed during execution: " + item["path"])
    for checkpoint in identity["checkpoints"]:
        qsar_models._checkpoint_files(checkpoint["checkpoint_dir"], checkpoint["checkpoint_inventory"])


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


def fit_training_model(model, cell, X, labels, row_ids, spec, groups=None):
    def before_fit(context):
        check_deadline(spec, cell, context)
    if cell["algorithm"] == "FrozenSmilesEncoder":
        __import__("torch")
    with fit_resources(spec):
        if cell.get("calibration"):
            if groups is None or any(r not in groups for r in row_ids):
                raise TrainingError("Calibration requires explicit molecular groups for all current training rows")
            return model.fit(X, labels, row_ids=row_ids, groups=[groups[r] for r in row_ids], before_fit=before_fit)
        before_fit({"phase": "pipeline-fit"})
        return model.fit(X, labels, audit__row_ids=row_ids)


def fit_predict(cell, params, train, valid, target, features, seed, evidence, context, axes, warning_records=None, task_spec=None, groups=None):
    task_spec = cell if task_spec is None else task_spec
    kind = task_type(task_spec)
    if kind == "classification":
        require_binary_labels([target[r] for r in train], "pipeline training rows")
    check_deadline(task_spec, cell, context)
    X, index = features[cell["family"]]
    model = pipeline(cell, params, seed, kind, resource_controls(task_spec)["threads"])
    fit_warnings = []
    def fit_and_predict():
        fit_training_model(model, cell, X[[index[r] for r in train]], np.array([target[r] for r in train]), train,
                           task_spec, getattr(features, "group_ids", None) if groups is None else groups)
        model.frozen_recipe_sha256_ = frozen_recipe(cell, params, {**task_spec, "seed": seed})
        attach_fit_metadata(model, cell, train, features, task_spec, context.get("phase") == "outer-refit")
        if kind == "classification":
            return positive_probability(model, X[[index[r] for r in valid]])
        return model.predict(X[[index[r] for r in valid]])
    try:
        predictions = capture_fit_warnings(fit_and_predict, context, fit_warnings)
    except BaseException as error:
        if hasattr(model, "calibration_audit_"):
            error.calibration_audit = model.calibration_audit_
        raise
    finally:
        if warning_records is not None:
            warning_records.extend(fit_warnings)
    if not np.all(np.isfinite(predictions)):
        raise TrainingError("Nonfinite predictions")
    evidence.append({**context, "fit_axis_id": axis_reference(axes, model.named_steps["audit"].fit_row_ids_),
                     "transform_axis_id": axis_reference(axes, valid), **model_provenance(model, cell),
                     "warnings": fit_warnings, "nonconverged": False})
    if model.applicability_domain_ is not None:
        domain = model.applicability_domain_.to_dict()
        evidence[-1]["applicability_domain"] = {k: domain[k] for k in (
            "artifact_sha256", "recipe_sha256", "training_sha256", "references_sha256", "calibration_sha256", "cutoff")}
    return model, predictions


def study_key(task, scope, cell):
    return digest([task, scope, cell["candidate_id"]])


def study_spec(cell, spec):
    return qsar_search.validate_search_spec(cell, spec["seed"],
        direction="maximize" if task_type(spec) == "classification" else "minimize",
        resource_policy=cell.get("resource_policy"))


def trial_receipt(record, evidence):
    keys = ("task_id", "scope", "candidate_id", "trial_id")
    fits = [fit for fit in evidence if all(fit.get(key) == record[key] for key in keys)]
    return digest({"trial": {k: v for k, v in record.items() if k != "evidence_sha256"}, "fits": fits})


def open_study(task, scope, cell, spec, study_context):
    if study_context is None:
        raise TrainingError("Optuna requires a persisted study context; suggestions cannot be approximated")
    key = study_key(task, scope, cell)
    saved = study_context["state"].setdefault("studies", {}).get(key)
    path = Path(study_context["out"]) / "studies" / (key + ".sqlite3")
    if saved is not None and (saved.get("artifact") != binding(path)
            or saved.get("snapshot_artifact") != binding(path.with_suffix(".snapshot.json"))
            or load(path.with_suffix(".snapshot.json")) != saved.get("snapshot")):
        raise TrainingError("Optuna study artifact/snapshot hash mismatch")
    return qsar_search.OptunaStudyCoordinator(path, study_spec(cell, spec),
        {"training_identity": study_context["identity"], "task_id": task, "scope": scope, "candidate_id": cell["candidate_id"]},
        study_name=key, resume=saved is not None, expected_snapshot=None if saved is None else saved["snapshot"])


def save_study(coordinator, study_context, checkpoint):
    snapshot = coordinator.snapshot()
    path = coordinator.path.with_suffix(".snapshot.json")
    dump(path, snapshot)
    study_context["state"].setdefault("studies", {})[coordinator.study_name] = {
        "artifact": binding(coordinator.path), "snapshot_artifact": binding(path), "snapshot": snapshot}
    if checkpoint is not None:
        checkpoint()


def validate_studies(state, spec, out, folds):
    expected = {}
    for task, task_spec in spec["tasks"].items():
        scopes = {r["scope"] for r in state["ledger"] if r["task_id"] == task}
        scopes.update(key[len(task) + 1:] for key in state["completed"] if key.startswith(task + ":"))
        for scope in scopes:
            for cell in task_spec["cells"]:
                if backend(cell) == "optuna":
                    expected[study_key(task, scope, cell)] = (task, scope, cell)
    for key, saved in state.get("studies", {}).items():
        if key not in expected:
            # A study can be initialized before the first anchor starts.
            definition = saved["snapshot"].get("input_identity", {})
            task, scope, candidate = (definition.get(k) for k in ("task_id", "scope", "candidate_id"))
            cell = next((c for c in spec["tasks"].get(task, {}).get("cells", []) if c["candidate_id"] == candidate), None)
            permitted = ["final-selection"] + ["outer-" + str(f["fold"]) for f in folds["tasks"].get(task, {}).get("outer_folds", [])]
            if cell is None or scope not in permitted or backend(cell) != "optuna" or study_key(task, scope, cell) != key:
                raise TrainingError("Optuna unknown study identity")
            expected[key] = (task, scope, cell)
        task, scope, cell = expected[key]
        with open_study(task, scope, cell, spec, {"out": out, "identity": state["identity"], "state": state}) as coordinator:
            snapshot = coordinator.snapshot()
        records = {r["trial_id"]: r for r in state["ledger"] if r["task_id"] == task
                   and r["scope"] == scope and r["candidate_id"] == cell["candidate_id"] and r["trial_id"] != "anchor"}
        trials_by_id = {trial["trial_id"]: trial for trial in snapshot["trials"]}
        if set(records) - set(trials_by_id):
            raise TrainingError("Optuna ledger trial missing from persisted study")
        for trial in snapshot["trials"]:
            record = records.get(trial["trial_id"])
            if record is None:
                if trial["status"] == "running":
                    continue
                raise TrainingError("Optuna persisted outcome lacks CV evidence")
            if record["parameters"] != trial["parameters"] or (trial["status"] == "running" and record["status"] != "incomplete"):
                raise TrainingError("Optuna trial parameters/status differ from persisted study")
            if trial["status"] != "running":
                receipt = trial_receipt(record, state["fit_evidence"])
                if (record["status"] != trial["status"] or record.get("evidence_sha256") != receipt
                        or trial["user_attributes"].get("qsar_fit_evidence_sha256") != receipt
                        or (trial["status"] == "complete" and trial["objective_value"] != record.get("score"))):
                    raise TrainingError("Optuna persisted CV evidence digest/outcome mismatch")
    for key, (task, scope, cell) in expected.items():
        if key not in state.get("studies", {}) and any(r["task_id"] == task and r["scope"] == scope
                and r["candidate_id"] == cell["candidate_id"] for r in state["ledger"]):
            raise TrainingError("Optuna ledger lacks its persisted study")


def declared_trials(cell, seed, records=()):
    return trials(cell, seed) + ([(r["trial_id"], r["parameters"]) for r in records
            if r["candidate_id"] == cell["candidate_id"] and r["trial_id"] != "anchor"] if backend(cell) == "optuna" else [])


def validate_fit_controls(record, cell, spec, axes, groups, target):
    ids = axes[record["fit_axis_id"]]["row_ids"]
    if cell.get("calibration"):
        audit = record.get("calibration")
        config = cell["calibration"]
        expected_groups = [groups[r] for r in ids]
        if (not isinstance(audit, dict) or audit.get("method") != config["method"]
                or audit.get("n_splits") != config["n_splits"] or audit.get("random_state") != spec["seed"]
                or audit.get("fold_models_retained") is not False or audit.get("base_clone_id") != "calibration-base-refit"
                or audit.get("calibrator_row_ids") != ids or audit.get("calibrator_groups") != expected_groups
                or audit.get("base_fit_row_ids") != ids or len(audit.get("folds", [])) != config["n_splits"]):
            raise TrainingError("Completed scope calibration row/group/clone evidence mismatch")
        splits = qsar_calibration._partition(None, np.asarray([target[r] for r in ids]), expected_groups,
                                             config["n_splits"], spec["seed"])
        for index, ((train, valid), fold) in enumerate(zip(splits, audit["folds"])):
            train_ids, valid_ids = ([ids[i] for i in indices] for indices in (train, valid))
            if (fold.get("fold") != index or fold.get("clone_id") != "calibration-fold-" + str(index)
                    or fold.get("independent_pipeline_clone") is not True or not isinstance(fold.get("preprocessing"), dict)
                    or fold.get("train_row_ids") != train_ids or fold.get("audit_fit_row_ids") != train_ids
                    or fold.get("validation_row_ids") != valid_ids
                    or fold.get("train_groups") != list(dict.fromkeys(groups[r] for r in train_ids))
                    or fold.get("validation_groups") != list(dict.fromkeys(groups[r] for r in valid_ids))):
                raise TrainingError("Completed scope calibration fold leakage/clone evidence mismatch")
        base = audit.get("base_fit", {})
        if base.get("audit_fit_row_ids") != ids or base.get("independent_pipeline_clone") is not True:
            raise TrainingError("Completed scope calibration final base evidence mismatch")
        sampler_fits = [(fold["train_row_ids"], fold.get("samplers")) for fold in audit["folds"]] + [(ids, base.get("samplers"))]
    else:
        if "calibration" in record:
            raise TrainingError("Completed scope unexpected calibration evidence")
        sampler_fits = []
    if cell.get("sampling"):
        sampler_fits.append((ids, record.get("sampling")))
    elif "sampling" in record:
        raise TrainingError("Completed scope unexpected sampling evidence")
    for original_ids, samplers in sampler_fits:
        if not cell.get("sampling"):
            if samplers != []:
                raise TrainingError("Completed scope unexpected calibration sampler")
            continue
        if not isinstance(samplers, list) or len(samplers) != 1:
            raise TrainingError("Completed scope missing real SMOTE evidence")
        sampler = samplers[0]
        if (sampler.get("step") != "smote" or "SMOTE" not in sampler.get("class", "")
                or sampler.get("original_fit_row_ids") != original_ids
                or sampler.get("row_identity_scope") != "original_rows_before_resampling"
                or sampler.get("synthetic_row_ids") is not None or sampler.get("synthetic_parent_row_ids") is not None
                or not isinstance(sampler.get("synthetic_counts"), list)
                or any(type(row.get("count")) is not int or row["count"] < 0 for row in sampler["synthetic_counts"])):
            raise TrainingError("Completed scope SMOTE original/synthetic provenance mismatch")


def search_scope(task, scope, cells, cv, target, features, spec, ledger, evidence, axes, checkpoint=None, study_context=None):
    candidates, failed = [], False
    kind = task_type(spec)

    def evaluate(cell, trial_id, params):
            nonlocal failed
            check_deadline(spec, cell, {"scope": scope, "trial_id": trial_id})
            start = time.monotonic()
            record = {"task_id": task, "scope": scope, "candidate_id": cell["candidate_id"],
                      "family": cell["family"], "algorithm": cell["algorithm"], "trial_id": trial_id,
                      "kind": "anchor" if trial_id == "anchor" else "search", "parameters": params,
                      "fold_scores": [], "status": "incomplete", "failure_reason": None,
                      "warnings": [], "nonconverged": False, "seconds": 0.0}
            if kind == "classification":
                record.update(task_type=kind, positive_class=1, threshold=float(spec.get("threshold", 0.5)), fold_predictions=[])
            ledger.append(record)
            try:
                errors = []
                for fold in cv:
                    _, predictions = fit_predict(cell, params, fold["train_row_ids"], fold["validation_row_ids"],
                                                 target, features, spec["seed"], evidence,
                                                 {"task_id": task, "scope": scope, "candidate_id": cell["candidate_id"],
                                                  "trial_id": trial_id, "fold": fold["fold"]}, axes, record["warnings"], spec)
                    if kind == "classification":
                        score = classification_score(spec["metric"], [target[r] for r in fold["validation_row_ids"]],
                                                     predictions, spec.get("threshold", 0.5))
                        record["fold_predictions"].append({"fold": fold["fold"], "prediction": predictions.tolist()})
                        record["fold_scores"].append({"fold": fold["fold"], "score": score})
                    else:
                        residual = predictions - np.array([target[r] for r in fold["validation_row_ids"]])
                        loss = residual ** 2 if spec["metric"] == "rmse" else np.abs(residual)
                        errors.extend(loss.tolist())
                        record["fold_scores"].append({"fold": fold["fold"], "loss": float(np.sqrt(loss.mean()) if spec["metric"] == "rmse" else loss.mean())})
                    record["seconds"] = time.monotonic() - start
                    record["nonconverged"] = any(w["nonconverged"] for w in record["warnings"])
                    if checkpoint is not None:
                        checkpoint()
                if kind == "classification":
                    record.update(classification_summary(record, cv, target, spec))
                else:
                    record["score"] = float(np.sqrt(np.mean(errors)) if spec["metric"] == "rmse" else np.mean(errors))
                record["status"] = "complete"
                if record.get("selection_eligible", True):
                    candidates.append((score_order(record["score"], spec), cell["candidate_id"], trial_id, cell, params, record))
            except Exception as error:
                if hasattr(error, "calibration_audit"):
                    record["partial_calibration"] = error.calibration_audit
                if isinstance(error, TrainingBudgetExhausted):
                    record["failure_reason"] = str(error)
                    raise
                record.update(status="failed", failure_reason=f"{type(error).__name__}: {error}")
                failed = True
            finally:
                record["nonconverged"] = any(w["nonconverged"] for w in record["warnings"])
                record["seconds"] = time.monotonic() - start
                if checkpoint is not None:
                    checkpoint()
            return record

    for cell in cells:
        if backend(cell) == "parameter_sampler":
            policy = qsar_search.validate_resource_policy(cell.get("resource_policy"), n_trials=cell["n_trials"])
            for trial_id, params in trials(cell, spec["seed"]):
                check_deadline(spec, cell, {"scope": scope, "trial_id": trial_id})
                if trial_id != "anchor":
                    try:
                        policy.check_start(int(trial_id.split("-")[1]), 0)
                    except qsar_search.SearchBudgetError as error:
                        raise TrainingBudgetExhausted(str(error)) from error
                evaluate(cell, trial_id, params)
            continue
        check_deadline(spec, cell, {"scope": scope, "phase": "study-open"})
        with open_study(task, scope, cell, spec, study_context) as coordinator:
            save_study(coordinator, study_context, checkpoint)
            evaluate(cell, "anchor", dict(cell["anchor"]))
            previous = [r for r in ledger if r["task_id"] == task and r["scope"] == scope
                        and r["candidate_id"] == cell["candidate_id"] and r["trial_id"] != "anchor"]
            for record in previous:
                if record["status"] == "complete":
                    if record.get("selection_eligible", True):
                        candidates.append((score_order(record["score"], spec), cell["candidate_id"], record["trial_id"],
                                           cell, record["parameters"], record))
                else:
                    failed = True
            pending = coordinator.pending_trials()
            while pending or coordinator.snapshot()["coverage"]["attempted_search_trials"] < cell["n_trials"]:
                check_deadline(spec, cell, {"scope": scope, "phase": "trial-start"})
                if pending:
                    handle = pending.pop(0)
                else:
                    snapshot = coordinator.snapshot()
                    try:
                        coordinator.spec.resource_policy.check_start(snapshot["coverage"]["attempted_search_trials"], 0)
                    except qsar_search.SearchBudgetError as error:
                        raise TrainingBudgetExhausted(str(error)) from error
                    handle = coordinator.ask()
                    save_study(coordinator, study_context, checkpoint)
                record = evaluate(cell, handle["trial_id"], handle["parameters"])
                record["evidence_sha256"] = trial_receipt(record, evidence)
                if record["status"] == "complete":
                    coordinator.tell(handle, record["score"], evidence_sha256=record["evidence_sha256"])
                else:
                    coordinator.tell(handle, failure=record["failure_reason"], evidence_sha256=record["evidence_sha256"])
                save_study(coordinator, study_context, checkpoint)
    if failed:
        raise TrainingError("Declared search contains failed trials; no substitute winner permitted")
    if not candidates:
        raise TrainingError("No completed candidate meets the declared selection constraints")
    _, _, trial_id, cell, params, record = min(candidates, key=lambda value: value[:3])
    return selection(cell, trial_id, params, spec, record)


def validate_completed_scopes(state, spec, folds, out, target):
    validate_studies(state, spec, out, folds)
    declared, scope_folds = {}, {}
    axes = frozen_fit_axes(folds)
    groups = {}
    if any(c.get("calibration") for task in spec["tasks"].values() for c in task["cells"]):
        with Path(state["identity"]["inputs"][0]["path"]).open(newline="", encoding="utf-8-sig") as stream:
            groups = {row["output_row_id"]: row["group_id"] for row in csv.DictReader(stream)}
    all_records = state["ledger"] + [r for attempt in state.get("attempt_history", []) for r in attempt["ledger"]]
    for task, task_spec in spec["tasks"].items():
        outer = folds["tasks"][task]["outer_folds"]
        for scope, cv in [("outer-" + str(f["fold"]), f["inner_folds"]) for f in outer] + [("final-selection", outer)]:
            declared[(task, scope)] = {(c["candidate_id"], t): (c, p)
                for c in task_spec["cells"] for t, p in declared_trials(c, spec["seed"],
                    [r for r in all_records if r["task_id"] == task and r["scope"] == scope])}
            scope_folds[(task, scope)] = cv

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
                r.get("algorithm") != cell["algorithm"] or r.get("kind") != ("anchor" if r["trial_id"] == "anchor" else "search") or
                r.get("status") not in {"complete", "failed", "incomplete", "pruned"} or
                type(seconds) not in (int, float) or not np.isfinite(seconds) or seconds < 0):
                raise TrainingError("Completed scope or attempt ledger values mismatch")
            cv = scope_folds[scope_key]
            scores = r.get("fold_scores", [])
            if not isinstance(scores, list) or [f.get("fold") for f in scores] != [f["fold"] for f in cv[:len(scores)]]:
                raise TrainingError("Completed scope or attempt fold progress mismatch")
            if r["status"] == "complete" and len(scores) != len(cv):
                raise TrainingError("Completed scope or attempt trial fold coverage mismatch")
            if task_type(spec) == "classification":
                saved = r.get("fold_predictions", [])
                if (r.get("task_type") != "classification" or type(r.get("positive_class")) is not int or r.get("positive_class") != 1
                        or not valid_threshold(r.get("threshold"))
                        or not isinstance(saved, list) or len(saved) != len(scores)
                        or [f.get("fold") for f in saved] != [f["fold"] for f in cv[:len(scores)]]):
                    raise TrainingError("Completed scope classification probability evidence mismatch")
                if r["status"] == "complete":
                    expected = classification_summary(r, cv, target, spec)
                    if any(r.get(k) != value for k, value in expected.items()):
                        raise TrainingError("Completed scope classification metric/threshold evidence mismatch")
                else:
                    if r.get("threshold") != spec.get("threshold", 0.5):
                        raise TrainingError("Completed scope classification progress threshold mismatch")
                    for fold, prediction, score in zip(cv, saved, scores):
                        actual = classification_score(spec["metric"], [target[r] for r in fold["validation_row_ids"]],
                                                      prediction["prediction"], spec.get("threshold", 0.5))
                        if score != {"fold": fold["fold"], "score": actual}:
                            raise TrainingError("Completed scope classification fold progress score mismatch")
            elif any(type(f.get("loss")) not in (int, float) or not np.isfinite(f["loss"]) or f["loss"] < 0 for f in scores):
                raise TrainingError("Completed scope or attempt fold loss mismatch")

    def check_fits(records, ledger, enclosing=None):
        identities = set()
        trials_by_key = {(r["task_id"], r["scope"], r["candidate_id"], r["trial_id"]): r for r in ledger}
        for r in records:
            scope_key = (r.get("task_id"), r.get("scope"))
            trial_key = (r.get("candidate_id"), r.get("trial_id"))
            key = tuple(r.get(k) for k in ("task_id", "scope", "candidate_id", "trial_id", "fold", "phase"))
            if (scope_key not in declared or (enclosing is not None and scope_key != enclosing) or key in identities
                    or (r.get("phase") != "outer-refit" and (trial_key not in declared[scope_key]
                        or key[:4] not in trials_by_key))):
                raise TrainingError("Completed scope or attempt fit identity mismatch")
            cell = next((c for c in spec["tasks"][scope_key[0]]["cells"] if c["candidate_id"] == trial_key[0]), None)
            if (cell is None or r.get("feature_route") != feature_route(cell) or r.get("learned_steps") != learned_steps(cell)
                    or type(r.get("selected_feature_count")) is not int or r["selected_feature_count"] < 1
                    or not isinstance(r.get("warnings"), list) or r.get("nonconverged") is not False):
                raise TrainingError("Completed scope candidate-specific pipeline evidence mismatch")
            validate_fit_controls(r, cell, spec, axes, groups, target)
            def sha256(value):
                return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
            if rule_candidate(cell):
                method = r.get("scoring_method")
                if (not sha256(r.get("chemical_basis_sha256")) or not isinstance(method, dict)
                        or method.get("method") != "fitted_rule_derived_additive_scorer"
                        or method.get("task_type") != task_type(spec)):
                    raise TrainingError("Completed scope chemical rule basis evidence mismatch")
            if cell["algorithm"] == "FrozenSmilesEncoder":
                encoder = r.get("encoder", {})
                config = cell["encoder"]
                fingerprint = hashlib.sha256(json.dumps(config["checkpoint_inventory"], sort_keys=True).encode()).hexdigest()
                if (not isinstance(encoder, dict) or encoder.get("method") != "frozen_pretrained_smiles_encoder" or encoder.get("encoder_fine_tuned") is not False
                        or encoder.get("pooling") != config["pooling"] or encoder.get("truncation") != "forbidden"
                        or encoder.get("mean_pooling_tokens") != "all_nonpadding_including_special_tokens"
                        or encoder.get("checkpoint_fingerprint") != fingerprint
                        or encoder.get("batch_size") != config.get("batch_size", 32)
                        or encoder.get("device") != config.get("device", "cpu")
                        or type(encoder.get("max_length")) is not int or encoder["max_length"] < 1
                        or (config.get("max_length") is not None and encoder["max_length"] != config["max_length"])):
                    raise TrainingError("Completed scope frozen encoder provenance mismatch")
            expects_domain = r.get("phase") == "outer-refit" and "applicability_domain" in spec
            domain = r.get("applicability_domain")
            if expects_domain:
                if (not isinstance(domain, dict) or set(domain) != {"artifact_sha256", "recipe_sha256", "training_sha256",
                        "references_sha256", "calibration_sha256", "cutoff"}
                        or any(not sha256(domain[k]) for k in domain if k != "cutoff") or not valid_threshold(domain["cutoff"])):
                    raise TrainingError("Completed scope applicability domain evidence mismatch")
            elif domain is not None:
                raise TrainingError("Completed scope unexpected applicability domain evidence")
            identities.add(key)
        for r in ledger:
            for fold in r.get("fold_scores", []):
                key = (r["task_id"], r["scope"], r["candidate_id"], r["trial_id"], fold["fold"], None)
                if key not in identities:
                    raise TrainingError("Completed scope or attempt fold progress lacks fit evidence")

    check_records(state["ledger"])
    check_fits(state["fit_evidence"], state["ledger"])
    for attempt in state.get("attempt_history", []):
        enclosing = (attempt.get("task_id"), attempt.get("scope"))
        if enclosing not in declared:
            raise TrainingError("Completed scope or attempt history identity mismatch")
        check_records(attempt["ledger"], enclosing)
        check_fits(attempt["fit_evidence"], attempt["ledger"], enclosing)
        validate_fit_contexts(attempt["fit_evidence"], folds, frozen_fit_axes(folds))
    expected_keys = set()
    expected_oof = []
    expected_models = []
    expected_thresholds, final_tasks = {}, set()
    for task, task_spec in spec["tasks"].items():
        outer = folds["tasks"][task]["outer_folds"]
        for scope, cv, fold in [("outer-" + str(f["fold"]), f["inner_folds"], f) for f in outer] + [("final-selection", outer, None)]:
            key = task + ":" + scope
            expected_keys.add(key)
            if key not in state["completed"]:
                continue
            records = [r for r in state["ledger"] if r["task_id"] == task and r["scope"] == scope]
            evidence = [r for r in state["fit_evidence"] if r["task_id"] == task and r["scope"] == scope]
            expected = {(c["candidate_id"], t): (c, p) for c in task_spec["cells"]
                        for t, p in declared_trials(c, spec["seed"], records)}
            actual = [(r["candidate_id"], r["trial_id"]) for r in records]
            if (len(actual) != len(expected) or set(actual) != set(expected)
                    or any(sum(r["candidate_id"] == c["candidate_id"] for r in records) != c["n_trials"] + 1
                           for c in task_spec["cells"])):
                raise TrainingError("Completed scope trial coverage mismatch")
            candidates = []
            expected_fits = []
            for r in records:
                cell, params = expected[(r["candidate_id"], r["trial_id"])]
                if (r["status"] != "complete" or r["parameters"] != params or
                    r["family"] != cell["family"] or r["algorithm"] != cell["algorithm"] or
                    [f["fold"] for f in r["fold_scores"]] != [f["fold"] for f in cv] or
                    type(r.get("score")) not in (int, float) or
                    not np.isfinite(r.get("score", float("nan")))):
                    raise TrainingError("Completed scope trial evidence mismatch")
                if r.get("selection_eligible", True):
                    candidates.append((score_order(r["score"], spec), cell["candidate_id"], r["trial_id"], cell, params, r))
                expected_fits.extend((cell["candidate_id"], r["trial_id"], f["fold"], None) for f in cv)
            if not candidates:
                raise TrainingError("Completed scope has no eligible candidate under selection constraints")
            _, _, trial_id, cell, params, record = min(candidates, key=lambda x: x[:3])
            chosen = selection(cell, trial_id, params, spec, record)
            if state["completed"][key] != chosen:
                raise TrainingError("Completed scope selection mismatch")
            if fold is None:
                final_tasks.add(task)
                if state["final_selection"].get(task) != chosen:
                    raise TrainingError("Completed scope final selection mismatch")
            else:
                expected_fits.append((cell["candidate_id"], None, None, "outer-refit"))
                expected_oof.extend((task, r, fold["fold"], "selection_procedure") for r in fold["validation_row_ids"])
                model_path = str(out / "outer_models" / (digest([task, scope]) + ".joblib"))
                expected_models.append(model_path)
                artifact = next((a for a in state["artifacts"] if a["path"] == model_path), None)
                if artifact is None or binding(model_path) != artifact:
                    raise TrainingError("Completed scope model artifact hash mismatch")
                model = joblib.load(model_path)
                validate_fitted_identity(model, chosen, spec, task, state["identity"])
                if (getattr(model, "fit_training_row_ids_", None) != fold["train_row_ids"]
                        or model.named_steps["audit"].fit_row_ids_ != fold["train_row_ids"]
                        or (task_type(spec) == "classification" and (
                            getattr(model, "positive_class_", None) != 1
                            or getattr(model, "threshold_", None) != chosen["threshold"]))):
                    raise TrainingError("Completed scope fitted model training cohort/threshold mismatch")
                del model
                if task_type(spec) == "classification":
                    expected_thresholds[(task, fold["fold"])] = chosen["threshold"]
            actual_fits = [(r.get("candidate_id"), r.get("trial_id"), r.get("fold"), r.get("phase")) for r in evidence]
            if sorted(map(repr, actual_fits)) != sorted(map(repr, expected_fits)):
                raise TrainingError("Completed scope fit evidence mismatch")
    if not set(state["completed"]) <= expected_keys:
        raise TrainingError("Completed scope unknown identity")
    if state.get("status") == "complete" and set(state["completed"]) != expected_keys:
        raise TrainingError("Completed scope coverage contradicts complete checkpoint status")
    if set(state["final_selection"]) != final_tasks:
        raise TrainingError("Completed scope final selection task coverage mismatch")
    if sorted((r["task_id"], r["output_row_id"], r["outer_fold"], r["candidate_id"]) for r in state["oof"]) != sorted(expected_oof) or any(type(r.get("prediction")) not in (int, float) or not np.isfinite(r["prediction"]) for r in state["oof"]):
        raise TrainingError("Completed scope OOF coverage mismatch")
    if task_type(spec) == "classification" and any(
            r.get("task_type") != "classification" or type(r.get("positive_class")) is not int or r.get("positive_class") != 1
            or not valid_threshold(r.get("threshold"))
            or r["threshold"] != expected_thresholds[(r["task_id"], r["outer_fold"])]
            or not 0 <= r["prediction"] <= 1 for r in state["oof"]):
        raise TrainingError("Completed scope OOF classification probability/threshold mismatch")
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
        validate_completed_scopes(state, spec, folds, out, target)
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
    def save_progress():
        dump(checkpoint, state)

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
                                     "backend": backend(cell), "actual_concurrent_fits": 1,
                                     "expected_search_trials": cell["n_trials"], "expected_anchor_trials": 1,
                                     "attempted_search_trials": sum(r["kind"] == "search" for r in records),
                                     "distinct_search_configurations": len({digest(r["parameters"]) for r in records if r["kind"] == "search"}),
                                     "complete": sum(r["status"] == "complete" for r in records),
                                     "failed": sum(r["status"] == "failed" for r in records),
                                     "pruned": sum(r["status"] == "pruned" for r in records),
                                     "incomplete": sum(r["status"] == "incomplete" for r in records),
                                     "pending": cell["n_trials"] + 1 - sum(r["status"] in ("complete", "failed", "pruned") for r in records)})
        dump(out / "search_coverage.json", {"schema_version": "1.0", "status": state["status"], "cells": coverage,
             "attempted_trials": len(all_attempts),
             "failed_attempts": sum(r["status"] == "failed" for r in all_attempts),
             "pruned_attempts": sum(r["status"] == "pruned" for r in all_attempts),
             "attempt_seconds": sum(r["seconds"] for r in all_attempts),
             "expected_trials": expected, "completed_trials": sum(r["status"] == "complete" for r in state["ledger"]),
             "failed_trials": sum(r["status"] == "failed" for r in state["ledger"]),
             "incomplete_trials": sum(r["status"] == "incomplete" for r in state["ledger"]),
             "completed_scopes": sorted(state["completed"]), "failure_reason": state.get("failure_reason")})
        selected = {"schema_version": "1.0", "status": state["status"],
             "method": "fresh full-development CV; no outer evaluation scores used", "tasks": state["final_selection"], "identity": identity}
        if state.get("studies"):
            selected["search_checkpoint"] = binding(checkpoint)
        dump(out / "final_selection.json", selected)
        with (out / "selection_procedure_oof.csv").open("w", newline="", encoding="utf-8") as stream:
            columns = ["task_id", "output_row_id", "outer_fold", "candidate_id", "prediction"]
            if task_type(spec) == "classification":
                columns.extend(["task_type", "positive_class", "threshold"])
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(state["oof"])
        tasks = {}
        for task, task_folds in folds["tasks"].items():
            entries = []
            for fold in task_folds["outer_folds"]:
                scope = "outer-" + str(fold["fold"])
                chosen = state["completed"].get(task + ":" + scope)
                if chosen is None:
                    continue
                path = str(out / "outer_models" / (digest([task, scope]) + ".joblib"))
                artifact = next(a for a in state["artifacts"] if a["path"] == path)
                entries.append({"fold": fold["fold"], "threshold": chosen.get("threshold"), "model": artifact,
                    "selection": chosen, "training_identity_sha256": digest(identity),
                    "frozen_recipe_sha256": frozen_recipe(chosen, chosen["parameters"], {**spec, **chosen}),
                    "threshold_provenance": {"source": "inner_cv" if "threshold_grid" in spec else "contract_fixed",
                        "scope": scope, "trial_id": chosen["trial_id"],
                        "fit_axis_id": axis_reference(axes, fold["train_row_ids"])}})
            tasks[task] = {"task_type": task_type(spec), "positive_class": 1 if task_type(spec) == "classification" else None,
                           "outer_folds": entries}
        dump(out / "selection_procedure_oof_manifest.json", {"schema_version": "1.0", "status": state["status"],
             "inputs": identity["inputs"], "identity": identity, "fit_index_registry": state["fit_index_registry"],
             "predictions": binding(out / "selection_procedure_oof.csv"), "tasks": tasks})
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
                # Only SQLite-bound terminal Optuna trials are reusable. Partial
                # folds and anchors restart with their execution cost preserved.
                old_ledger = [r for r in state["ledger"] if r["task_id"] == task and r["scope"] == scope]
                old_evidence = [r for r in state["fit_evidence"] if r["task_id"] == task and r["scope"] == scope]
                adaptive = {c["candidate_id"] for c in task_spec["cells"] if backend(c) == "optuna"}
                reusable = {(r["candidate_id"], r["trial_id"]) for r in old_ledger if r["candidate_id"] in adaptive
                            and r["trial_id"] != "anchor" and r["status"] in ("complete", "failed", "pruned")}
                restarted_ledger = [r for r in old_ledger if (r["candidate_id"], r["trial_id"]) not in reusable]
                restarted_evidence = [r for r in old_evidence if (r["candidate_id"], r.get("trial_id")) not in reusable]
                if restarted_ledger or restarted_evidence:
                    state.setdefault("attempt_history", []).append({"task_id": task, "scope": scope,
                        "ledger": restarted_ledger, "fit_evidence": restarted_evidence,
                        "restart_policy": "partial folds and anchors restart; only digest-bound terminal Optuna trials reused",
                        "failure_reason": state.get("failure_reason"),
                        "failure_warnings": state.get("failure_warnings", [])})
                state["ledger"] = [r for r in state["ledger"] if not (r["task_id"] == task and r["scope"] == scope)
                                   or (r["candidate_id"], r["trial_id"]) in reusable]
                state["fit_evidence"] = [r for r in state["fit_evidence"] if not (r["task_id"] == task and r["scope"] == scope)
                                         or (r["candidate_id"], r.get("trial_id")) in reusable]
                state["status"] = "incomplete"
                state.pop("failure_reason", None)
                state.pop("failure_warnings", None)
                chosen = search_scope(task, scope, task_spec["cells"], cv, target, features, spec, state["ledger"], state["fit_evidence"], axes,
                    save_progress, {"out": out, "identity": identity, "state": state})
                if fold is None:
                    state["final_selection"][task] = chosen
                else:
                    model, predictions = fit_predict(chosen, chosen["parameters"], fold["train_row_ids"], fold["validation_row_ids"],
                                                     target, features, spec["seed"], state["fit_evidence"],
                                                     {"task_id": task, "scope": scope, "phase": "outer-refit", "candidate_id": chosen["candidate_id"]},
                                                     axes, task_spec={**spec, **chosen})
                    model.training_identity_sha256_ = digest(identity)
                    path = out / "outer_models" / (digest([task, scope]) + ".joblib")
                    joblib.dump(model, path)
                    state["artifacts"].append(binding(path))
                    for row_id, prediction in zip(fold["validation_row_ids"], predictions):
                        row = {"task_id": task, "output_row_id": row_id, "outer_fold": fold["fold"],
                               "candidate_id": "selection_procedure", "prediction": float(prediction)}
                        if task_type(spec) == "classification":
                            row.update(task_type="classification", positive_class=1, threshold=chosen["threshold"])
                        state["oof"].append(row)
                state["completed"][key] = chosen
                state["status"] = "incomplete"
                state.pop("failure_reason", None)
                save()
        if len(state["oof"]) != len(rows):
            raise TrainingError("OOF coverage incomplete")
        validate_completed_scopes(state, spec, folds, out, target)
        verify_identity(identity)
        if binding(state["fit_index_registry"]["path"]) != state["fit_index_registry"]:
            raise TrainingError("Fit-index registry hash mismatch at search completion")
        state["status"] = "complete"
    except BaseException as error:
        state["status"] = "incomplete" if isinstance(error, (KeyboardInterrupt, SystemExit, TrainingBudgetExhausted)) else "failed"
        state["failure_reason"] = f"{type(error).__name__}: {error}"
        state["failure_warnings"] = getattr(error, "fit_warnings", [])
        save()
        if isinstance(error, TrainingBudgetExhausted):
            return state
        raise
    save()
    return state


def final_fit(development, folds_path, manifest_path, contract_path, selection_path, lock_path, output_dir, resume=False):
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
    search_state = None
    if any(backend(c) == "optuna" for task in spec["tasks"].values() for c in task["cells"]):
        receipt = selection.get("search_checkpoint")
        if not isinstance(receipt, dict) or binding(receipt["path"]) != receipt:
            raise TrainingError("Optuna final fitting requires its exact search checkpoint")
        search_state = load(receipt["path"])
        if search_state.get("identity") != identity or search_state.get("status") != "complete" or search_state["final_selection"] != selection["tasks"]:
            raise TrainingError("Optuna final selection differs from completed search evidence")
        validate_completed_scopes(search_state, spec, folds, Path(receipt["path"]).parent, target)
        for artifact in search_state["artifacts"]:
            if binding(artifact["path"]) != artifact:
                raise TrainingError("Optuna search model artifact hash mismatch")
    out = Path(output_dir).resolve()
    protected = identity["inputs"] + [binding(selection_path), binding(lock_path)]
    if any(Path(a["path"]).is_relative_to(out) for a in protected):
        raise TrainingError("Output overlaps protected input")
    out.mkdir(parents=True, exist_ok=True)
    if resume:
        report = load(out / "final_fit.json")
        if (report.get("identity") != identity or report.get("selection") != selected_binding or report.get("lock") != lock_binding
                or set(report.get("models", {})) - set(selection["tasks"])):
            raise TrainingError("Final fit resume identity/selection/lock mismatch")
        for task, saved in report["models"].items():
            if binding(saved["artifact"]["path"]) != saved["artifact"]:
                raise TrainingError("Final fit resume model artifact hash mismatch")
            model = joblib.load(saved["artifact"]["path"])
            fits = [r for r in report["fit_evidence"] if r["task_id"] == task]
            chosen = selection["tasks"][task]
            validate_fitted_identity(model, chosen, spec, task, identity)
            if (len(fits) != 1 or fits[0]["fit_row_ids"] != folds["tasks"][task]["row_ids"]
                    or model.named_steps["audit"].fit_row_ids_ != fits[0]["fit_row_ids"]
                    or model.fit_training_row_ids_ != fits[0]["fit_row_ids"]
                    or saved["pipeline_provenance"] != model_provenance(model, chosen)
                    or saved["candidate_id"] != chosen["candidate_id"]
                    or saved.get("training_identity_sha256") != digest(identity)
                    or model.task_type_ != task_type(spec)
                    or (task_type(spec) == "classification" and (model.positive_class_ != 1 or model.threshold_ != chosen["threshold"]))):
                raise TrainingError("Final fit resume model/fit evidence mismatch")
            del model
        if ({r.get("task_id") for r in report["fit_evidence"]} != set(report["models"])
                or len(report["fit_evidence"]) != len(report["models"])
                or (report.get("status") == "complete" and set(report["models"]) != set(selection["tasks"]))):
            raise TrainingError("Final fit resume model/fit task coverage mismatch")
    else:
        if any(out.iterdir()):
            raise TrainingError("Final fit requires empty versioned output directory")
        report = {"schema_version": "1.0", "status": "incomplete", "identity": identity,
                  "selection": selected_binding, "lock": lock_binding, "models": {}, "fit_evidence": []}
    dump(out / "final_fit.json", report)
    try:
        for task, chosen in selection["tasks"].items():
            if task in report["models"]:
                continue
            cell = next(c for c in spec["tasks"][task]["cells"] if c["candidate_id"] == chosen["candidate_id"])
            if candidate_contract(chosen) != candidate_contract(cell):
                raise TrainingError("Locked pipeline disagrees with contract")
            records = [] if search_state is None else [r for r in search_state["ledger"] if r["task_id"] == task and r["scope"] == "final-selection"]
            if (chosen["trial_id"], chosen["parameters"]) not in declared_trials(cell, spec["seed"], records):
                raise TrainingError("Locked trial was not in declared search")
            if chosen.get("task_type") != task_type(spec):
                raise TrainingError("Locked task_type disagrees with contract")
            if task_type(spec) == "classification" and (
                    type(chosen.get("positive_class")) is not int or chosen.get("positive_class") != 1
                    or not valid_threshold(chosen.get("threshold"))
                    or chosen["threshold"] not in thresholds(spec)):
                raise TrainingError("Locked classification threshold/positive_class disagrees with contract")
            ids = folds["tasks"][task]["row_ids"]
            check_deadline(spec, cell, {"task_id": task, "phase": "final-fit"})
            X, index = features[chosen["family"]]
            model = pipeline(cell, chosen["parameters"], spec["seed"], task_type(spec), resource_controls(spec)["threads"])
            fit_warnings = []
            report["active_task"] = task
            dump(out / "final_fit.json", report)
            capture_fit_warnings(lambda: fit_training_model(model, cell, X[[index[r] for r in ids]],
                                 np.array([target[r] for r in ids]), ids, spec, features.group_ids),
                                 {"task_id": task, "phase": "final-fit"}, fit_warnings)
            model.frozen_recipe_sha256_ = frozen_recipe(cell, chosen["parameters"], {**spec, **chosen})
            model.training_identity_sha256_ = digest(identity)
            attach_fit_metadata(model, cell, ids, features, {**spec, **chosen}, fit_domain=True)
            path = out / (digest(task) + ".joblib")
            joblib.dump(model, path)
            report["models"][task] = {"artifact": binding(path), "candidate_id": chosen["candidate_id"],
                                      "family": chosen["family"], "task_type": task_type(spec),
                                      "training_identity_sha256": digest(identity),
                                      "feature_route": feature_route(cell),
                                      "feature_names": features.metadata[chosen["family"]]["feature_names"],
                                      "pipeline_provenance": model_provenance(model, cell)}
            if task_type(spec) == "classification":
                report["models"][task].update(positive_class=1, threshold=chosen["threshold"])
            report["fit_evidence"].append({"task_id": task, "fit_row_ids": model.named_steps["audit"].fit_row_ids_,
                                           **model_provenance(model, cell),
                                           "warnings": fit_warnings, "nonconverged": False})
            if model.applicability_domain_ is not None:
                domain = model.applicability_domain_.to_dict()
                domain_evidence = {k: domain[k] for k in ("artifact_sha256", "recipe_sha256", "training_sha256",
                                                         "references_sha256", "calibration_sha256", "cutoff")}
                report["models"][task]["applicability_domain"] = domain_evidence
                report["fit_evidence"][-1]["applicability_domain"] = domain_evidence
            del X, model
            report.pop("active_task", None)
            dump(out / "final_fit.json", report)
        verify_identity(identity)
        if binding(selection_path) != selected_binding or binding(lock_path) != lock_binding:
            raise TrainingError("Selection/lock changed during final fit")
        report.pop("failure_reason", None)
        report.pop("failure_warnings", None)
        report.pop("active_task", None)
        report["status"] = "complete"
    except BaseException as error:
        report["status"] = "incomplete" if isinstance(error, (KeyboardInterrupt, SystemExit, TrainingBudgetExhausted)) else "failed"
        report["failure_reason"] = f"{type(error).__name__}: {error}"
        report["failure_warnings"] = getattr(error, "fit_warnings", [])
        report.setdefault("attempt_history", []).append({"task_id": report.get("active_task"), "status": report["status"],
            "failure_reason": report["failure_reason"], "warnings": report["failure_warnings"],
            "partial_calibration": getattr(locals().get("model"), "calibration_audit_", None),
            "restart_policy": "unfinished final task restarts; completed hash-verified final models reused"})
        dump(out / "final_fit.json", report)
        if isinstance(error, TrainingBudgetExhausted):
            return report
        raise
    dump(out / "final_fit.json", report)
    return report


def synthetic_fixture(root, kind="regression"):
    task_type({"task_type": kind})
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(910)
    X = rng.normal(size=(24, 5))
    X[1, 2] = np.nan
    column = "target_class" if kind == "classification" else "target_pLD50"
    rows = [{"output_row_id": f"r{i}", "task_id": "oral", "group_id": f"g{i}",
             "partition": "development", "parent_smiles": "C",
             column: int((i // 3) % 2) if kind == "classification" else float(2 * X[i, 0] + X[i, 1])} for i in range(24)]
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
    if kind == "classification":
        cell.update(candidate_id="logistic-synthetic", algorithm="LogisticRegression",
                    search_space={"model__C": [0.1, 1.0], "select__k": [2, "all"]},
                    anchor={"model__C": 1.0, "select__k": "all"})
    training = {"target_column": column, "metric": "mcc" if kind == "classification" else "rmse",
                "seed": 71, "tasks": {"oral": {"cells": [cell]}}}
    if kind == "classification":
        training["task_type"] = kind
    dump(root / "contract.json", {"preparation": {"target_column": column}, "training": training})
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
              "pipeline_identity": binding(__file__),
              "source_hashes": [binding(module.__file__) for module in (qsar_models, qsar_domain, qsar_calibration, qsar_search)],
              "independent_review_required": True, "checks": []}
    try:
        declared = None
        if contract_path is not None:
            report["contract"] = binding(contract_path)
            declared = load(contract_path)["training"]
            validate_training_spec(declared)
        kind = task_type(declared or {})
        root = synthetic_fixture(out / "synthetic-fixture", kind)
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
        altered = {r: ((1 - y if kind == "classification" else y + 100000)
                       if r in outer["validation_row_ids"] else y) for r, y in target.items()}
        second = select(altered)
        if first[0] != second[0]:
            raise TrainingError("Holdout labels changed model selection")
        np.testing.assert_array_equal(first[1], second[1])
        np.testing.assert_array_equal(first[2], second[2])
        report["checks"] = ["complete nested synthetic search", "all/search anchor coverage", "fit row disjointness",
                            "holdout-label perturbation leaves selection, preprocessing and predictions unchanged"]
        report["task_type"] = kind
        report["fixture_checkpoint"] = binding(root / "search" / "checkpoint.json")
        report["declared_cell_checks"] = []
        if contract_path is not None:
            for task, task_spec in declared["tasks"].items():
                for cell in task_spec["cells"]:
                    check = {"task_id": task, "candidate_id": cell["candidate_id"], "status": "running", "fit_evidence": [],
                             "fit_index_axes": {}}
                    report["declared_cell_checks"].append(check)
                    choices = trials(cell, declared["seed"])
                    coordinator = None
                    if backend(cell) == "optuna":
                        coordinator = qsar_search.OptunaStudyCoordinator(out / (digest([task, cell["candidate_id"]]) + ".sqlite3"),
                            study_spec(cell, declared), {"preflight_contract": report["contract"], "task_id": task,
                                                      "candidate_id": cell["candidate_id"], "synthetic_only": True})
                        handle = coordinator.ask()
                        choices.append((handle["trial_id"], handle["parameters"]))
                    parents = ["C", "CC", "CCC", "CCCC", "CCO", "CCN", "CO", "CN", "CCCl", "CCBr", "CCF", "COC",
                               "CCOC", "CCCO", "CCCN", "CCS", "c1ccccc1", "C1CCCCC1", "c1ccncc1", "C1CCCC1",
                               "CC(=O)O", "CC(=O)N", "CC(C)O", "CC(C)N"]
                    if feature_route(cell) == "smiles":
                        X = np.asarray(parents, dtype=object)
                        if cell["algorithm"] == "FrozenSmilesEncoder":
                            check["checkpoint"] = prepare_checkpoint(cell, Path(contract_path).resolve().parent)
                        synthetic_target = np.arange(24, dtype=float) / 24
                    else:
                        counts = cell["search_space"]["select__k"]
                        counts = counts["choices"] if isinstance(counts, dict) else counts
                        columns = max(k for k in counts if k != "all") + 2
                        X = np.random.default_rng(declared["seed"]).normal(size=(24, columns))
                        synthetic_target = X[:, 0] * 2 + X[:, 1]
                    ids = [f"synthetic-{i}" for i in range(24)]
                    cell_axes = check["fit_index_axes"]
                    register_axis(cell_axes, ids[:16])
                    register_axis(cell_axes, ids[16:])
                    y = {r: i % 2 if kind == "classification" else float(synthetic_target[i]) for i, r in enumerate(ids)}
                    matrices = {cell["family"]: (X, {r: i for i, r in enumerate(ids)})}
                    try:
                        for trial_id, parameters in (choices[0], choices[1]):
                            _, pred = fit_predict(cell, parameters, ids[:16], ids[16:], y, matrices, declared["seed"],
                                                  check["fit_evidence"], {"trial_id": trial_id}, cell_axes, task_spec=declared,
                                                  groups={r: r for r in ids})
                            changed = {r: ((1 - value if kind == "classification" else value + 100000)
                                           if r in ids[16:] else value) for r, value in y.items()}
                            _, same = fit_predict(cell, parameters, ids[:16], ids[16:], changed, matrices, declared["seed"],
                                                  check["fit_evidence"], {"trial_id": trial_id, "perturbed": True}, cell_axes, task_spec=declared,
                                                  groups={r: r for r in ids})
                            np.testing.assert_array_equal(pred, same)
                            if coordinator is not None and trial_id != "anchor":
                                labels = np.asarray([y[r] for r in ids[16:]])
                                score = (classification_score(declared["metric"], labels, pred, declared.get("threshold", 0.5))
                                         if kind == "classification" else float(np.sqrt(np.mean((pred - labels) ** 2))
                                         if declared["metric"] == "rmse" else np.mean(np.abs(pred - labels))))
                                coordinator.tell(handle, score, evidence_sha256=digest(check["fit_evidence"]))
                                check["study_snapshot"] = coordinator.snapshot()
                    finally:
                        if coordinator is not None:
                            coordinator.close()
                    if "applicability_domain" in declared:
                        domain = qsar_domain.fit(parents[:16], ids[:16], declared["applicability_domain"])
                        check["applicability_domain_sha256"] = domain.to_dict()["artifact_sha256"]
                    if "checkpoint" in check:
                        qsar_models._checkpoint_files(check["checkpoint"]["checkpoint_dir"], check["checkpoint"]["checkpoint_inventory"])
                    check["status"] = "complete"
            if binding(contract_path) != report["contract"]:
                raise TrainingError("Contract changed during preflight")
        if binding(__file__) != report["pipeline_identity"] or any(binding(b["path"]) != b for b in report["source_hashes"]):
            raise TrainingError("Training/model/domain source changed during preflight")
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
    final.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "search":
        result = run_search(args.development, args.folds, args.features, args.contract, args.output, args.resume)
        print(json.dumps({"status": result["status"], "output": args.output}))
    elif args.command == "final-fit":
        result = final_fit(args.development, args.folds, args.features, args.contract, args.selection, args.lock, args.output, args.resume)
        print(json.dumps({"status": result["status"], "output": args.output}))
    else:
        print(json.dumps(run_preflight(args.output, args.contract)))


if __name__ == "__main__":
    # Keep custom transformer pickle names importable by the separate evaluator.
    from qsar_train import main as entrypoint
    entrypoint()
