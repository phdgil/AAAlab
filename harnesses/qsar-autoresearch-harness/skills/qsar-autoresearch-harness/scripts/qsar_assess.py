"""Prediction-level evaluation; no fitting or candidate selection.

Use under the reviewed runtime. This module validates numerical evidence, not
native reviewer identity or the scientific adequacy of the endpoint.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import joblib
import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

from qsar_data import DataError, digest, dump, handoff, load, output_dir


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if not headers or any(not h.strip() for h in headers) or len(set(headers)) != len(headers):
            raise DataError("Empty or duplicate CSV headers")
        rows = list(reader)
        if any(None in r or any(v is None for v in r.values()) for r in rows):
            raise DataError("Malformed CSV row width")
        return rows


CLASSIFICATION_METRICS = ("mcc", "sensitivity", "specificity", "balanced_accuracy", "accuracy", "roc_auc", "average_precision")
OOF_REPLAY_RTOL = 1e-7
OOF_REPLAY_ATOL = 1e-9


def evaluation_task_type(contract):
    kind = contract.get("training", {}).get("task_type", "regression")
    if kind not in {"regression", "classification"}:
        raise DataError("Unsupported training task_type")
    if kind == "classification" and contract.get("training", {}).get("positive_class", 1) != 1:
        raise DataError("Classification positive_class must be 1")
    return kind


def classification_arrays(y, prediction, threshold):
    y, prediction = np.asarray(y, dtype=float), np.asarray(prediction, dtype=float)
    if y.ndim != 1 or y.shape != prediction.shape:
        raise DataError("Classification arrays must have the same row axis")
    if not np.isfinite(y).all() or not np.isfinite(prediction).all():
        raise DataError("Nonfinite classification targets or probabilities")
    if not np.isin(y, [0, 1]).all():
        raise DataError("Classification labels must be 0/1")
    if ((prediction < 0) | (prediction > 1)).any():
        raise DataError("Classification probability outside [0, 1]")
    if threshold is None or isinstance(threshold, (bool, np.bool_)):
        raise DataError("Classification requires a frozen threshold")
    threshold = np.asarray(threshold, dtype=float)
    if threshold.ndim == 0:
        if not np.isfinite(threshold) or not 0 <= threshold <= 1:
            raise DataError("Frozen classification threshold must be finite and within [0, 1]")
        threshold = np.full(len(y), float(threshold))
    if threshold.shape != y.shape or not np.isfinite(threshold).all() or ((threshold < 0) | (threshold > 1)).any():
        raise DataError("Frozen classification threshold must be finite and within [0, 1] on the prediction row axis")
    return y, prediction, threshold


def classification_metrics(y, prediction, threshold):
    y, prediction, threshold = classification_arrays(y, prediction, threshold)
    label = prediction >= threshold
    positive, negative = y == 1, y == 0
    tp, tn = int(np.sum(positive & label)), int(np.sum(negative & ~label))
    fp, fn = int(np.sum(negative & label)), int(np.sum(positive & ~label))
    n = len(y)
    result = {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
              "positive_count": tp + fn, "negative_count": tn + fp}
    for name in CLASSIFICATION_METRICS:
        result[name], result[name + "_undefined_reason"] = None, "empty_slice" if not n else None
    if not n:
        return result
    result["accuracy"] = (tp + tn) / n
    if tp + fn:
        result["sensitivity"] = tp / (tp + fn)
    else:
        result["sensitivity_undefined_reason"] = "no_positive_targets"
    if tn + fp:
        result["specificity"] = tn / (tn + fp)
    else:
        result["specificity_undefined_reason"] = "no_negative_targets"
    single_class = not (tp + fn and tn + fp)
    if single_class:
        for name in ("balanced_accuracy", "roc_auc", "average_precision"):
            result[name + "_undefined_reason"] = "single_class_target"
    else:
        result["balanced_accuracy"] = (result["sensitivity"] + result["specificity"]) / 2
        result["roc_auc"] = float(roc_auc_score(y, prediction))
        result["average_precision"] = float(average_precision_score(y, prediction))
    denominator = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    if denominator:
        result["mcc"] = float((tp * tn - fp * fn) / np.sqrt(float(denominator)))
    else:
        result["mcc_undefined_reason"] = "single_class_target" if single_class else "single_class_prediction"
    return result


def metrics(y, prediction, task_type="regression", threshold=None):
    if task_type == "classification":
        return classification_metrics(y, prediction, threshold)
    if task_type != "regression":
        raise DataError("Unsupported metric task_type")
    y, prediction = np.asarray(y, dtype=float), np.asarray(prediction, dtype=float)
    if y.ndim != 1 or y.shape != prediction.shape or not len(y):
        raise DataError("Metric arrays must have the same nonempty row axis")
    if not np.isfinite(y).all() or not np.isfinite(prediction).all():
        raise DataError("Nonfinite targets or predictions")
    residual = y - prediction
    sse = float(np.dot(residual, residual))
    centered = y - np.mean(y)
    sst = float(np.dot(centered, centered))
    if not np.isfinite([sse, sst, np.abs(residual).mean(), y.mean()]).all():
        raise DataError("Nonfinite computed metrics; numeric overflow")
    r2 = 1 - sse / sst if sst > 0 and len(y) > 1 else None
    if r2 is not None and not np.isfinite(r2):
        raise DataError("Nonfinite computed R2; numeric overflow")
    return {"n": len(y), "mae": float(np.abs(residual).mean()),
            "rmse": float(np.sqrt(sse / len(y))), "sse": sse, "sst": sst,
            "r2": r2,
            "r2_undefined_reason": None if sst > 0 and len(y) > 1 else "constant_target_or_single_row",
            "target_mean": float(y.mean()), "target_population_sd": float(np.std(y))}


def group_intervals(y, prediction, groups, repetitions, seed, task_type="regression", threshold=None):
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise DataError("Declare positive integer bootstrap_repetitions")
    metrics(y, prediction, task_type, threshold)
    y, prediction = np.asarray(y), np.asarray(prediction)
    if task_type == "classification":
        _, _, threshold = classification_arrays(y, prediction, threshold)
    groups = np.asarray(groups)
    if groups.ndim != 1 or groups.shape != y.shape or any(not str(g).strip() for g in groups):
        raise DataError("Bootstrap molecular groups must match prediction rows")
    unique = sorted(set(groups.tolist()))
    keys = CLASSIFICATION_METRICS if task_type == "classification" else ("mae", "rmse", "r2")
    if len(unique) < 2:
        return {"status": "undefined", "reason": "fewer_than_two_groups", "repetitions": repetitions,
                "attempted_replicates": 0, "seed": seed,
                "replicate_accounting": {key: {"valid_replicates": 0, "invalid_replicates": 0} for key in keys}}
    positions = {g: np.flatnonzero(groups == g) for g in unique}
    rng = np.random.default_rng(seed)
    samples = {k: [] for k in keys}
    undefined = {k: {} for k in keys}
    for _ in range(repetitions):
        indices = np.concatenate([positions[g] for g in rng.choice(unique, size=len(unique), replace=True)])
        result = metrics(y[indices], prediction[indices], task_type,
                         threshold[indices] if task_type == "classification" else None)
        for key in samples:
            if result[key] is not None:
                samples[key].append(result[key])
            else:
                reason = result[key + "_undefined_reason"]
                undefined[key][reason] = undefined[key].get(reason, 0) + 1
    return {"method": "molecular_group_percentile_bootstrap", "repetitions": repetitions, "seed": seed,
            "attempted_replicates": repetitions,
            "intervals": {key: {"lower": float(np.quantile(values, .025)), "upper": float(np.quantile(values, .975)),
                                 "valid_replicates": len(values), "invalid_replicates": repetitions - len(values)}
                          if values else None for key, values in samples.items()},
            "replicate_accounting": {key: {"valid_replicates": len(values), "invalid_replicates": repetitions - len(values),
                                            "undefined_reasons": undefined[key]} for key, values in samples.items()},
            "limitation": "Conditional on saved predictions; does not repeat model selection or capture training uncertainty"}


def verify_artifact(item):
    if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
            or item.get("sha256") != digest(item["path"])):
        raise DataError("Changed or invalid artifact binding")
    return Path(item["path"])


def validate_domain(fitted, training_rows, recipe):
    domain = getattr(fitted, "applicability_domain_", None)
    if domain is None:
        if recipe is not None:
            raise DataError("Locked model lacks the required training-reference applicability domain")
        return None
    if recipe is None or domain.recipe != recipe:
        raise DataError("Locked applicability-domain recipe differs from training contract")
    artifact = domain.to_dict()
    expected = {r["output_row_id"] for r in training_rows}
    records = artifact["training_rows"]
    if (len(records) != len(expected) or {r["row_id"] for r in records} != expected
            or getattr(fitted, "applicability_domain_sha256_", None) != artifact["artifact_sha256"]):
        raise DataError("Applicability-domain reference cohort differs from permitted training rows")
    from rdkit import Chem
    parents = {}
    for row in training_rows:
        mol = Chem.MolFromSmiles(row["parent_smiles"])
        if mol is None:
            raise DataError("Invalid training-reference parent structure")
        for atom in mol.GetAtoms():
            atom.SetAtomMapNum(0)
        parents[row["output_row_id"]] = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    if any(parents[r["row_id"]] != r["parent_smiles"] for r in records):
        raise DataError("Applicability-domain reference parent identity mismatch")
    reference_ids = []
    for reference in artifact["references"]:
        if any(parents.get(r) != reference["parent_smiles"] for r in reference["row_ids"]):
            raise DataError("Applicability-domain reference membership mismatch")
        reference_ids.extend(reference["row_ids"])
    if len(reference_ids) != len(expected) or set(reference_ids) != expected:
        raise DataError("Applicability-domain reference coverage mismatch")
    return domain


def domain_scores(domain, rows):
    scores = domain.score([r["parent_smiles"] for r in rows], [r["output_row_id"] for r in rows])
    if len(scores) != len(rows) or any(s["row_id"] != r["output_row_id"] for s, r in zip(scores, rows)):
        raise DataError("Applicability-domain query row axis mismatch")
    return scores


def slice_metrics(y, prediction, indices, task_type, threshold=None):
    y, prediction = np.asarray(y), np.asarray(prediction)
    indices = np.asarray(indices, dtype=int)
    if task_type == "classification":
        _, _, threshold = classification_arrays(y, prediction, threshold)
        return metrics(y[indices], prediction[indices], task_type, threshold[indices])
    if not len(indices):
        return {"n": 0, **{k: None for k in ("mae", "rmse", "sse", "sst", "r2", "target_mean", "target_population_sd")},
                "undefined_reason": "empty_slice", "r2_undefined_reason": "empty_slice"}
    return metrics(y[indices], prediction[indices])


def source_metrics(rows, y, prediction, task_type, threshold=None):
    return {source: slice_metrics(y, prediction, [i for i, r in enumerate(rows) if r.get("source_id", "unknown") == source],
                                  task_type, threshold)
            for source in sorted({r.get("source_id", "unknown") for r in rows})}


def domain_summary(rows, scores, y, prediction, task_type, threshold=None):
    if len(rows) != len(scores):
        raise DataError("Applicability-domain summary row axis mismatch")

    def summary(indices):
        slices = {name: [i for i in indices if scores[i]["in_domain"] is status]
                  for name, status in (("in_domain", True), ("out_of_domain", False), ("abstained", None))}
        return {"coverage": {"rows": len(indices), **{name: len(ix) for name, ix in slices.items()}},
                "metrics": {name: slice_metrics(y, prediction, ix, task_type, threshold) for name, ix in slices.items()}}

    return {**summary(list(range(len(rows)))),
            "source_slices": {source: summary([i for i, r in enumerate(rows) if r.get("source_id", "unknown") == source])
                              for source in sorted({r.get("source_id", "unknown") for r in rows})},
            "policy": "All predictions retained, including out-of-domain rows and domain abstentions; no evaluation-time cutoff fitting"}


def reconstruct_training_inputs(identity, development, folds_path, feature_path, contract_path):
    from qsar_train import read_inputs
    try:
        current = read_inputs(development, folds_path, feature_path, contract_path)
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        raise DataError("Cannot reconstruct frozen training identity: " + str(error)) from error
    if current[-1] != identity:
        raise DataError("Frozen training identity differs from current inputs/code/environment")
    return current


def load_oof_models(manifest_path, development, folds_path, predictions_path, contract_path, tasks, rows, contract):
    from qsar_train import TrainingError, digest as identity_digest, frozen_recipe, positive_probability, validate_fitted_identity
    manifest = load(manifest_path)
    kind = evaluation_task_type(contract)
    if manifest.get("status") != "complete" or set(manifest.get("tasks", {})) != set(tasks):
        raise DataError("Incomplete OOF provenance or task coverage")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise DataError("OOF provenance requires bound training inputs")
    for item in inputs:
        verify_artifact(item)
    for path in (development, folds_path, contract_path):
        if {"path": str(Path(path).resolve()), "sha256": digest(path)} not in inputs:
            raise DataError("OOF provenance differs from frozen evaluation inputs")
    if manifest.get("predictions") != {"path": str(Path(predictions_path).resolve()), "sha256": digest(predictions_path)}:
        raise DataError("OOF prediction binding mismatch")
    verify_artifact(manifest["predictions"])
    identity = manifest.get("identity")
    if not isinstance(identity, dict) or inputs != identity.get("inputs") or len(inputs) < 5:
        raise DataError("OOF manifest inputs differ from complete frozen training identity inputs")
    spec, frozen_folds, _, _, features, _ = reconstruct_training_inputs(
        identity, development, folds_path, inputs[2]["path"], contract_path)
    if frozen_folds["tasks"] != tasks:
        raise DataError("OOF frozen training fold mismatch")
    training_sha256 = identity_digest(identity)
    registry = load(verify_artifact(manifest["fit_index_registry"]))["axes"]
    models = {}
    for task, plan in tasks.items():
        record = manifest["tasks"][task]
        if record.get("task_type") != kind or record.get("positive_class") != (1 if kind == "classification" else None):
            raise DataError("OOF task type or positive class differs from contract")
        entries = {str(f["fold"]): f for f in record["outer_folds"]}
        if len(entries) != len(record["outer_folds"]) or set(entries) != {str(f["fold"]) for f in plan["outer_folds"]}:
            raise DataError("OOF threshold provenance fold coverage mismatch")
        models[task] = {}
        for fold in plan["outer_folds"]:
            fid = str(fold["fold"])
            entry = entries[fid]
            provenance = entry.get("threshold_provenance", {})
            axis_id = hashlib.sha256(json.dumps(fold["train_row_ids"], sort_keys=True, allow_nan=False).encode()).hexdigest()
            if (provenance.get("scope") != "outer-" + fid or provenance.get("fit_axis_id") != axis_id
                    or registry.get(axis_id, {}).get("row_ids") != fold["train_row_ids"]
                    or not isinstance(provenance.get("trial_id"), str) or not provenance["trial_id"]):
                raise DataError("OOF threshold provenance differs from outer training boundary")
            fitted = joblib.load(verify_artifact(entry["model"]))
            chosen = entry.get("selection")
            try:
                validate_fitted_identity(fitted, chosen, spec, task, identity)
            except TrainingError as error:
                raise DataError(str(error)) from error
            if (entry.get("training_identity_sha256") != training_sha256
                    or entry.get("frozen_recipe_sha256") != frozen_recipe(chosen, chosen["parameters"], {**spec, **chosen})
                    or chosen["trial_id"] != provenance["trial_id"]):
                raise DataError("OOF selected recipe/training identity provenance mismatch")
            if (getattr(fitted, "fit_training_row_ids_", None) != fold["train_row_ids"]
                    or fitted.named_steps["audit"].fit_row_ids_ != fold["train_row_ids"]
                    or getattr(fitted, "task_type_", None) != kind):
                raise DataError("OOF model differs from permitted outer training cohort or task")
            if kind == "classification":
                threshold = entry.get("threshold")
                if type(threshold) not in (int, float):
                    raise DataError("OOF provenance requires a frozen scalar threshold")
                classification_arrays([], [], threshold)
                classes = np.asarray(getattr(fitted, "classes_", []))
                if (classes.ndim != 1 or len(classes) != 2 or set(classes.tolist()) != {0, 1}
                        or getattr(fitted, "positive_class_", None) != 1 or getattr(fitted, "threshold_", None) != threshold):
                    raise DataError("OOF threshold/positive-class model provenance mismatch")
                if chosen.get("threshold") != threshold or chosen.get("positive_class") != 1:
                    raise DataError("OOF selected recipe threshold/positive-class provenance mismatch")
                source = provenance.get("source")
                training = contract["training"]
                if source == "inner_cv":
                    if threshold not in training.get("threshold_grid", []):
                        raise DataError("OOF threshold is not in the frozen inner-selection grid")
                elif source != "contract_fixed" or threshold != training.get("threshold", .5):
                    raise DataError("OOF threshold lacks inner-only or fixed-contract provenance")
            X, index = features[chosen["family"]]
            validation = X[[index[r] for r in fold["validation_row_ids"]]]
            try:
                replayed = positive_probability(fitted, validation) if kind == "classification" else fitted.predict(validation)
            except (ValueError, TypeError) as error:
                raise DataError("OOF fitted prediction replay failed: " + str(error)) from error
            replayed = np.asarray(replayed, dtype=float)
            if replayed.shape != (len(fold["validation_row_ids"]),) or not np.isfinite(replayed).all():
                raise DataError("OOF model replay returned invalid prediction row axis or nonfinite values")
            domain = validate_domain(fitted, [rows[r] for r in fold["train_row_ids"]], contract.get("training", {}).get("applicability_domain"))
            models[task][fid] = {"threshold": entry.get("threshold"), "threshold_provenance": provenance,
                                  "domain": domain, "model": entry["model"], "replayed_predictions": replayed}
    return models


def evaluate_development(development, folds_path, predictions_path, contract_path, split_manifest_path, destination, oof_manifest_path=None):
    inputs = [development, folds_path, predictions_path, contract_path, split_manifest_path]
    bindings = {str(Path(p).resolve()): digest(p) for p in inputs}
    split = load(split_manifest_path)
    for key, path in (("development", development), ("folds", folds_path), ("contract", contract_path)):
        expected = split.get(key, {})
        if expected.get("path") != str(Path(path).resolve()) or expected.get("sha256") != digest(path):
            raise DataError("Evaluation inputs differ from frozen preprocessing manifest")
    contract = load(contract_path)
    kind = evaluation_task_type(contract)
    if oof_manifest_path is None:
        raise DataError("Development evaluation requires a bound OOF manifest")
    inputs.append(oof_manifest_path)
    bindings[str(Path(oof_manifest_path).resolve())] = digest(oof_manifest_path)
    policy = contract["evaluation"]
    target = contract["preparation"]["target_column"]
    if policy.get("target_column") != target or policy.get("mode") != "development":
        raise DataError("Evaluation mode and target must match preparation contract")
    if policy.get("uncertainty") != "molecular_group_bootstrap":
        raise DataError("Unsupported uncertainty contract")
    rows = read_csv(development)
    if any(r.get("partition") != "development" for r in rows):
        raise DataError("Development evaluation rejects terminal or unlabeled partitions")
    indexed = {r["output_row_id"]: r for r in rows}
    if len(indexed) != len(rows) or not rows:
        raise DataError("Duplicate or empty development row axis")
    tasks = load(folds_path)["tasks"]
    if set(tasks) != {r["task_id"] for r in rows}:
        raise DataError("Fold task coverage mismatch")
    assignment, baselines = {}, {}
    for task, plan in tasks.items():
        expected = {rid for rid, r in indexed.items() if r["task_id"] == task}
        if set(plan["row_ids"]) != expected or len(plan["row_ids"]) != len(expected):
            raise DataError("Fold row membership mismatch")
        seen, fold_ids = set(), set()
        for fold in plan["outer_folds"]:
            fid = str(fold["fold"])
            train, validation = fold["train_row_ids"], fold["validation_row_ids"]
            if fid in fold_ids or not train or not validation or len(set(train)) != len(train) or len(set(validation)) != len(validation):
                raise DataError("Invalid fold identity or membership")
            fold_ids.add(fid)
            if set(train) & set(validation) or set(train) | set(validation) != expected or seen & set(validation):
                raise DataError("Fold overlap or incomplete membership")
            if {indexed[r]["group_id"] for r in train} & {indexed[r]["group_id"] for r in validation}:
                raise DataError("Molecular group leakage")
            baseline = float(np.mean([float(indexed[r][target]) for r in train]))
            for rid in validation:
                assignment[rid] = fid
                baselines[rid] = baseline
            seen.update(validation)
        if seen != expected:
            raise DataError("Incomplete outer predictions expected")
    outer_models = load_oof_models(oof_manifest_path, development, folds_path, predictions_path, contract_path,
                                   tasks, indexed, contract)
    predictions = read_csv(predictions_path)
    candidate_rows, thresholds = {}, {}
    for row in predictions:
        rid = row["output_row_id"]
        if rid not in indexed or row["task_id"] != indexed[rid]["task_id"] or str(row["outer_fold"]) != assignment[rid]:
            raise DataError("Prediction row/task/fold mismatch")
        candidate = row["candidate_id"]
        if not candidate:
            raise DataError("Missing candidate identity")
        key = (row["task_id"], candidate)
        if rid in candidate_rows.setdefault(key, {}):
            raise DataError("Duplicate prediction row")
        candidate_rows[key][rid] = float(row["prediction"])
        if kind == "classification":
            threshold = float(row["threshold"])
            if (row.get("task_type") != kind or row.get("positive_class") != "1"
                    or threshold != outer_models[row["task_id"]][assignment[rid]]["threshold"]):
                raise DataError("OOF row threshold/positive class differs from bound outer model")
            thresholds[rid] = threshold
    required = policy.get("required_candidates")
    if not isinstance(required, list) or not required or len(set(required)) != len(required):
        raise DataError("Declare unique required_candidates")
    if required != ["selection_procedure"]:
        raise DataError("Bound OOF provenance covers selection_procedure only")
    if set(candidate_rows) != {(t, c) for t in tasks for c in required}:
        raise DataError("Candidate/task coverage differs from evaluation contract")
    results = []
    for (task, candidate), predicted in sorted(candidate_rows.items()):
        axis = tasks[task]["row_ids"]
        if set(predicted) != set(axis):
            raise DataError("Missing candidate predictions; incomparable cohort")
        for fold in tasks[task]["outer_folds"]:
            saved = np.asarray([predicted[r] for r in fold["validation_row_ids"]], dtype=float)
            replayed = outer_models[task][str(fold["fold"])]["replayed_predictions"]
            if not np.isfinite(saved).all():
                raise DataError("Nonfinite saved OOF predictions")
            if not np.allclose(saved, replayed, rtol=OOF_REPLAY_RTOL, atol=OOF_REPLAY_ATOL, equal_nan=False):
                raise DataError("Saved OOF predictions differ from fitted model replay (rtol=1e-7, atol=1e-9)")
            if kind == "classification":
                locked = outer_models[task][str(fold["fold"])]["threshold"]
                if not np.array_equal(saved >= locked, replayed >= locked):
                    raise DataError("Saved OOF threshold classifications differ from fitted model replay")
        y = [float(indexed[r][target]) for r in axis]
        p = [predicted[r] for r in axis]
        groups = [indexed[r]["group_id"] for r in axis]
        threshold = [thresholds[r] for r in axis] if kind == "classification" else None
        current = [indexed[r] for r in axis]
        result = {"task_id": task, "candidate_id": candidate, "target_column": target, "task_type": kind,
                  "metrics": metrics(y, p, kind, threshold),
                  "uncertainty": group_intervals(y, p, groups, policy["bootstrap_repetitions"], policy["seed"], kind, threshold),
                  "source_metrics": source_metrics(current, y, p, kind, threshold), "row_ids": axis}
        if kind == "classification":
            result.update(positive_class=1, threshold_policy="locked_per_outer_model_inner_only",
                          fold_thresholds={fid: entry["threshold"] for fid, entry in outer_models[task].items()},
                          threshold_provenance={fid: entry["threshold_provenance"] for fid, entry in outer_models[task].items()},
                          training_prevalence_baseline=metrics(y, [baselines[r] for r in axis], kind, threshold))
        else:
            result["training_mean_baseline"] = metrics(y, [baselines[r] for r in axis])
        if contract.get("training", {}).get("applicability_domain") is not None:
            scored = {}
            for fold in tasks[task]["outer_folds"]:
                valid = [indexed[r] for r in fold["validation_row_ids"]]
                for row, score in zip(valid, domain_scores(outer_models[task][str(fold["fold"])]["domain"], valid)):
                    scored[row["output_row_id"]] = score
            result["applicability_domain"] = domain_summary(current, [scored[r] for r in axis], y, p, kind, threshold)
            result["domain_rows"] = [scored[r] for r in axis]
        results.append(result)
    out = output_dir(destination, inputs)
    report = {"mode": "development", "task_type": kind, "input_hashes": bindings, "results": results,
              "oof_replay_tolerance": {"rtol": OOF_REPLAY_RTOL, "atol": OOF_REPLAY_ATOL,
                                       "threshold_classifications": "exact"},
              "claim": "Exploratory development estimates; fit and selection independence require separate training review",
              "candidate_ranking": "Not performed; outer outcomes must not select the terminal pipeline"}
    dump(out / "evaluation.json", report)
    for p, sha in bindings.items():
        if digest(p) != sha:
            raise DataError("Input changed during evaluation")
    for models in outer_models.values():
        for model in models.values():
            verify_artifact(model["model"])
    manifest = load(oof_manifest_path)
    identity = manifest["identity"]
    for item in (manifest["inputs"] + [manifest["predictions"], manifest["fit_index_registry"], identity["code"]]
                 + identity["source_hashes"]):
        verify_artifact(item)
    return handoff(out, "development-evaluation", "Prediction-level development evaluation produced",
                   ["Exact row/task/fold coverage", "Molecular group disjointness", "Metrics independently recomputed from predictions",
                    "Frozen fitted training identity/recipe", "OOF predictions replayed without fitting",
                    "Baseline derived from each outer training partition"], [report["claim"]], {"tasks": len(tasks), "candidate_tasks": len(results)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", required=True)
    parser.add_argument("--folds", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--split-manifest", required=True)
    parser.add_argument("--oof-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate_development(args.development, args.folds, args.predictions, args.contract, args.split_manifest, args.output_dir, args.oof_manifest)
        print(json.dumps({"summary": result["summary"], "coverage": result["coverage"]}))
        return 0
    except (DataError, KeyError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
