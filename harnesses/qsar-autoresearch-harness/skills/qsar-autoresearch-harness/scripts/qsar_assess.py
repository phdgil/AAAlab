"""Prediction-level regression evaluation; no fitting or candidate selection.

Use under the reviewed runtime. This module validates numerical evidence, not
native reviewer identity or the scientific adequacy of the endpoint.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np

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


def metrics(y, prediction):
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


def group_intervals(y, prediction, groups, repetitions, seed):
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
        raise DataError("Declare positive integer bootstrap_repetitions")
    groups = np.asarray(groups)
    unique = sorted(set(groups.tolist()))
    if len(unique) < 2:
        return {"status": "undefined", "reason": "fewer_than_two_groups"}
    positions = {g: np.flatnonzero(groups == g) for g in unique}
    rng = np.random.default_rng(seed)
    samples = {k: [] for k in ("mae", "rmse", "r2")}
    y, prediction = np.asarray(y), np.asarray(prediction)
    for _ in range(repetitions):
        indices = np.concatenate([positions[g] for g in rng.choice(unique, size=len(unique), replace=True)])
        result = metrics(y[indices], prediction[indices])
        for key in samples:
            if result[key] is not None:
                samples[key].append(result[key])
    return {"method": "molecular_group_percentile_bootstrap", "repetitions": repetitions, "seed": seed,
            "intervals": {key: {"lower": float(np.quantile(values, .025)), "upper": float(np.quantile(values, .975)),
                                 "valid_replicates": len(values)} if values else None for key, values in samples.items()},
            "limitation": "Conditional on saved predictions; does not repeat model selection or capture training uncertainty"}


def evaluate_development(development, folds_path, predictions_path, contract_path, split_manifest_path, destination):
    inputs = [development, folds_path, predictions_path, contract_path, split_manifest_path]
    bindings = {str(Path(p).resolve()): digest(p) for p in inputs}
    split = load(split_manifest_path)
    for key, path in (("development", development), ("folds", folds_path), ("contract", contract_path)):
        expected = split.get(key, {})
        if expected.get("path") != str(Path(path).resolve()) or expected.get("sha256") != digest(path):
            raise DataError("Evaluation inputs differ from frozen preprocessing manifest")
    contract = load(contract_path)
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
    predictions = read_csv(predictions_path)
    candidate_rows = {}
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
    required = policy.get("required_candidates")
    if not isinstance(required, list) or not required or len(set(required)) != len(required):
        raise DataError("Declare unique required_candidates")
    if set(candidate_rows) != {(t, c) for t in tasks for c in required}:
        raise DataError("Candidate/task coverage differs from evaluation contract")
    results = []
    for (task, candidate), predicted in sorted(candidate_rows.items()):
        axis = tasks[task]["row_ids"]
        if set(predicted) != set(axis):
            raise DataError("Missing candidate predictions; incomparable cohort")
        y = [float(indexed[r][target]) for r in axis]
        p = [predicted[r] for r in axis]
        groups = [indexed[r]["group_id"] for r in axis]
        strata = {}
        for source in sorted({indexed[r].get("source_id", "unknown") for r in axis}):
            ix = [i for i, r in enumerate(axis) if indexed[r].get("source_id", "unknown") == source]
            strata[source] = metrics(np.asarray(y)[ix], np.asarray(p)[ix])
        results.append({"task_id": task, "candidate_id": candidate, "target_column": target,
                        "metrics": metrics(y, p), "training_mean_baseline": metrics(y, [baselines[r] for r in axis]),
                        "uncertainty": group_intervals(y, p, groups, policy["bootstrap_repetitions"], policy["seed"]),
                        "source_metrics": strata, "row_ids": axis})
    out = output_dir(destination, inputs)
    report = {"mode": "development", "input_hashes": bindings, "results": results,
              "claim": "Exploratory development estimates; fit and selection independence require separate training review",
              "candidate_ranking": "Not performed; outer outcomes must not select the terminal pipeline"}
    dump(out / "evaluation.json", report)
    for p, sha in bindings.items():
        if digest(p) != sha:
            raise DataError("Input changed during evaluation")
    return handoff(out, "development-evaluation", "Prediction-level development evaluation produced",
                   ["Exact row/task/fold coverage", "Molecular group disjointness", "Metrics independently recomputed from predictions",
                    "Baseline derived from each outer training partition"], [report["claim"]], {"tasks": len(tasks), "candidate_tasks": len(results)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", required=True)
    parser.add_argument("--folds", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--split-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate_development(args.development, args.folds, args.predictions, args.contract, args.split_manifest, args.output_dir)
        print(json.dumps({"summary": result["summary"], "coverage": result["coverage"]}))
        return 0
    except (DataError, KeyError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
