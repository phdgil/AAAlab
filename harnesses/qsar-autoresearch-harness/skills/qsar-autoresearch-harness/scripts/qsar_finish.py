"""Reviewed improvement handoffs and locked terminal/external evaluation.

Runtime stage gates must authorize invocation. These functions neither create
review receipts nor infer acceptance from a user-controlled boolean.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

import joblib
import numpy as np
from rdkit import Chem, DataStructs, rdBase
from rdkit.Chem import Descriptors

from qsar_assess import (classification_arrays, domain_scores, domain_summary, evaluation_task_type,
                         group_intervals, metrics, read_csv, reconstruct_training_inputs, source_metrics, validate_domain)
from qsar_data import DataError, digest, dump, handoff, load, output_dir, write_csv
from qsar_prepare import fingerprint_dtype, fingerprint_feature_names, fingerprint_generator


def verify_binding(item):
    if not isinstance(item, dict) or digest(item["path"]) != item["sha256"]:
        raise DataError("Changed or invalid artifact binding")
    return Path(item["path"])


def replay(smiles, family, record, environment, row_ids=None, ledger=None):
    if environment.get("rdkit") != rdBase.rdkitVersion:
        raise DataError("Feature toolkit version differs from locked recipe")
    names = record["feature_names"]
    if family == "RDKit2D":
        inventory = list(Descriptors._descList)
        if names != [name for name, _ in inventory]:
            raise DataError("Full descriptor inventory/order differs from locked recipe")
        generator = None
        dtype = np.float64
    else:
        generator, width, recipe = fingerprint_generator(family, record["parameters"])
        if recipe != record["recipe"]:
            raise DataError("Fingerprint generator differs from locked recipe")
        if names != fingerprint_feature_names(family, width, recipe):
            raise DataError("Fingerprint order/dimension differs from locked recipe")
        dtype = fingerprint_dtype(recipe)
    matrix = np.empty((len(smiles), len(names)), dtype=dtype)
    row_ids = list(range(len(smiles))) if row_ids is None else row_ids
    ledger = [] if ledger is None else ledger
    if len(row_ids) != len(smiles):
        raise DataError("Replay row axis mismatch")
    for i, smi in enumerate(smiles):
        mol = Chem.MolFromSmiles(smi)
        if mol is None or not mol.GetNumAtoms() or any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
            raise DataError("Invalid terminal structure; no row dropping")
        if generator is None:
            for j, (name, function) in enumerate(inventory):
                reason = "nonfinite"
                try:
                    matrix[i, j] = float(function(mol))
                except Exception as error:
                    matrix[i, j] = np.nan
                    reason = type(error).__name__ + ": " + str(error)
                if not np.isfinite(matrix[i, j]):
                    ledger.append({"output_row_id": row_ids[i], "family": family, "feature": name, "reason": reason})
                    matrix[i, j] = np.nan
        else:
            vector = generator(mol)
            width = vector.GetLength() if recipe["method"] == "GetCountFingerprint" else vector.GetNumBits()
            if width != len(names):
                raise DataError("Fingerprint replay dimension differs from locked recipe")
            DataStructs.ConvertToNumpyArray(vector, matrix[i])
    return matrix


def features_for(rows, model, feature, ledger=None):
    route = model.get("feature_route", "numeric")
    if route == "smiles":
        if model["family"] != "SMILES" or model["feature_names"] != ["parent_smiles"]:
            raise DataError("Locked SMILES feature route mismatch")
        return np.asarray([r["parent_smiles"] for r in rows], dtype=object)
    record = feature["families"][model["family"]]
    if model["feature_names"] != record["feature_names"]:
        raise DataError("Final model feature order mismatch")
    if route != "numeric" or record.get("feature_route", "numeric") != "numeric":
        raise DataError("Unknown or mismatched locked feature route")
    return replay([r["parent_smiles"] for r in rows], model["family"], record, feature["environment"],
                  [r["output_row_id"] for r in rows], ledger)


def positive_probability(fitted, X):
    classes = np.asarray(getattr(fitted, "classes_", []))
    if classes.ndim != 1 or len(classes) != 2 or set(classes.tolist()) != {0, 1}:
        raise DataError("Locked classifier must expose exactly classes 0/1")
    if not callable(getattr(fitted, "predict_proba", None)):
        raise DataError("Locked classifier lacks positive-class probability prediction")
    probabilities = np.asarray(fitted.predict_proba(X), dtype=float)
    if probabilities.shape != (len(X), len(classes)):
        raise DataError("Classifier probability row/class axis mismatch")
    if not np.isfinite(probabilities).all():
        raise DataError("Nonfinite classifier probabilities")
    if ((probabilities < 0) | (probabilities > 1)).any():
        raise DataError("Classifier probability outside [0, 1]")
    if not np.allclose(probabilities.sum(axis=1), 1, rtol=0, atol=1e-6):
        raise DataError("Classifier probabilities must sum to one")
    return probabilities[:, int(np.flatnonzero(classes == 1)[0])]


def parent_identity(row, scaffold=False):
    mol = Chem.MolFromSmiles(row["parent_smiles"])
    if mol is None or not mol.GetNumAtoms() or any(a.GetAtomicNum() == 0 for a in mol.GetAtoms()):
        raise DataError("Invalid evaluation/development parent; no row dropping")
    for atom in mol.GetAtoms():
        atom.SetAtomMapNum(0)
    if scaffold:
        from rdkit.Chem.Scaffolds import MurckoScaffold
        mol = MurckoScaffold.GetScaffoldForMol(mol)
    return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=False)


def improvement(evaluation_path, coverage_path, selection_path, proposal_path, destination):
    paths = [evaluation_path, coverage_path, selection_path, proposal_path]
    bindings = {str(Path(p).resolve()): digest(p) for p in paths}
    evaluation, coverage, selection, proposal = [load(p) for p in paths]
    if evaluation.get("mode") != "development":
        raise DataError("Improvement may consume development evidence only")
    for key in ("reason", "failure_class", "hypothesis", "expected_comparison", "stopping_rule"):
        if not isinstance(proposal.get(key), str) or not proposal[key].strip():
            raise DataError("Missing improvement rationale: " + key)
    if not isinstance(proposal.get("rejected_alternatives"), list) or not isinstance(proposal.get("evidence"), list) or not proposal["evidence"]:
        raise DataError("Improvement requires evidence and rejected-alternative accounting")
    evidence = [{"path": str(Path(p).resolve()), "sha256": digest(p)} for p in proposal["evidence"]]
    decision = proposal.get("decision")
    if decision not in {"STOP", "CONTINUE"}:
        raise DataError("Improvement decision must be STOP or CONTINUE")
    if decision == "STOP":
        if coverage.get("status") != "complete" or selection.get("status") != "complete":
            raise DataError("Incomplete search cannot lock a terminal candidate")
        if coverage.get("failed_trials", 0) or coverage.get("completed_trials") != coverage.get("expected_trials"):
            raise DataError("Unresolved trial coverage prevents successful STOP")
        lock = {"decision": "STOP", "selection": {"path": str(Path(selection_path).resolve()), "sha256": digest(selection_path)},
                "tasks": {t: selected["candidate_id"] for t, selected in selection["tasks"].items()}}
    else:
        if proposal.get("restart_from") not in {"collection", "curation", "preprocessing", "features", "training-preflight", "training"}:
            raise DataError("Continuation must name earliest affected stage")
        if type(proposal.get("remaining_budget")) is not int or proposal["remaining_budget"] <= 0:
            raise DataError("Continuation requires positive explicit remaining budget")
        if not isinstance(proposal.get("proposed_change"), str) or not proposal["proposed_change"].strip():
            raise DataError("Continuation requires a concrete change")
        lock = None
    out = output_dir(destination, paths + proposal["evidence"])
    report = {**proposal, "input_hashes": bindings, "bound_evidence": evidence,
              "status": "proposed_requires_independent_review", "terminal_results_used": False}
    dump(out / "next_experiment_manifest.json", report)
    if lock:
        dump(out / "model_lock.json", lock)
    for p, sha in bindings.items():
        if digest(p) != sha:
            raise DataError("Improvement input changed")
    for item in evidence:
        verify_binding(item)
    result = handoff(out, "improvement", "Development intervention proposed; independent review required",
                     ["Development-only input mode", "Explicit decision and rationale", "Coverage reconciled for STOP"],
                     ["A proposal is not scientific acceptance or proof that no further useful improvement exists"],
                     {"decision": decision})
    result.update(decision=decision, reason=proposal["reason"])
    if lock:
        result["model_lock"] = [str(Path(selection_path).resolve())]
    else:
        result["restart_from"] = proposal["restart_from"]
    dump(out / "manifest.json", result)
    return result


def evaluate_locked(development_path, held_path, split_path, feature_path, final_fit_path, contract_path, destination, mode):
    if mode not in {"terminal", "external"}:
        raise DataError("Locked evaluation requires terminal or external mode")
    paths = [development_path, held_path, split_path, feature_path, final_fit_path, contract_path]
    bindings = {str(Path(p).resolve()): digest(p) for p in paths}
    split, feature, final, contract = [load(p) for p in (split_path, feature_path, final_fit_path, contract_path)]
    supplied_split = {"path": str(Path(split_path).resolve()), "sha256": digest(split_path)}
    if feature.get("split_manifest") != supplied_split or supplied_split not in final["identity"]["inputs"]:
        raise DataError("Terminal split differs from locked feature/training lineage")
    for key, path in (("input", development_path), ("contract", contract_path)):
        if feature.get(key) != {"path": str(Path(path).resolve()), "sha256": digest(path)}:
            raise DataError("Terminal inputs differ from locked feature lineage")
    if final.get("status") != "complete":
        raise DataError("Final fit is incomplete")
    for package, version in final["identity"]["environment"].items():
        actual = platform.python_version() if package == "python" else importlib.metadata.version(package)
        if actual != version:
            raise DataError("Inference dependency version drift: " + package)
    for key, path in (("development", development_path), ("contract", contract_path)):
        item = split[key]
        if str(Path(path).resolve()) != item["path"]:
            raise DataError("Terminal inputs differ from frozen split")
        verify_binding(item)
    if mode == "terminal":
        sealed = split["sealed_terminal"]
        prior_exposure = split["policy"]["prior_exposure"]
        exposure_binding = split["exposure_ledger"]
        verify_binding(exposure_binding)
    else:
        cohort = split.get("external_cohort")
        if not isinstance(cohort, dict):
            raise DataError("Frozen split has no external cohort")
        sealed = cohort["artifact"]
        prior_exposure = cohort["prior_exposure"]
        exposure_binding = cohort["exposure_ledger"]
        for name in ("input", "exposure_ledger", "exclusions"):
            verify_binding(cohort[name])
            paths.append(cohort[name]["path"])
            bindings[cohort[name]["path"]] = cohort[name]["sha256"]
        if cohort["input"]["sha256"] != sealed["sha256"]:
            raise DataError("External cohort no longer matches its original sealed input")
        ledger = load(exposure_binding["path"])
        if any(ledger.get(key) != prior_exposure[key] for key in ("status", "evidence", "description")):
            raise DataError("External prior exposure differs from its frozen ledger")
    if str(Path(held_path).resolve()) != sealed["path"]:
        raise DataError("Evaluation cohort differs from frozen split")
    verify_binding(sealed)
    for item in final["identity"]["inputs"]:
        verify_binding(item)
    for path in (development_path, feature_path, contract_path):
        if {"path": str(Path(path).resolve()), "sha256": digest(path)} not in final["identity"]["inputs"]:
            raise DataError("Final fit does not bind terminal inference inputs")
    verify_binding(final["identity"]["code"])
    for item in final["identity"].get("source_hashes", []):
        verify_binding(item)
    verify_binding(final["selection"])
    verify_binding(final["lock"])
    lock = load(final["lock"]["path"])
    if lock.get("decision") != "STOP" or lock.get("selection") != final["selection"]:
        raise DataError("Final fit differs from stopped model lock")
    selection = load(final["selection"]["path"])
    if (selection.get("status") != "complete" or selection.get("identity") != final["identity"]
            or set(selection.get("tasks", {})) != set(final["models"])
            or lock.get("tasks") != {t: s["candidate_id"] for t, s in selection["tasks"].items()}):
        raise DataError("Stopped model lock training identity/task/candidate coverage mismatch")
    dev, held = read_csv(development_path), read_csv(held_path)
    if (not dev or not held or any(r.get("partition") != "development" for r in dev)
            or (mode == "terminal" and any(r.get("partition") != "terminal" for r in held))
            or (mode == "external" and any(r.get("partition") not in (None, "", "external") for r in held))):
        raise DataError("Invalid terminal/development partition")
    ids = [r["output_row_id"] for r in dev + held]
    if len(set(ids)) != len(ids):
        raise DataError("Evaluation row identity overlap or duplication")
    if mode == "terminal" and {r["group_id"] for r in dev} & {r["group_id"] for r in held}:
        raise DataError("Terminal row/group leakage")
    if {parent_identity(r) for r in dev} & {parent_identity(r) for r in held}:
        raise DataError(mode.capitalize() + " canonical-parent overlap with development")
    if mode == "external":
        if len(held) != cohort["rows"]:
            raise DataError("External cohort row coverage mismatch")
        if cohort["overlap_policy"]["exclude_scaffold_overlap"]:
            dev_scaffolds = {parent_identity(r, scaffold=True) for r in dev} - {""}
            if dev_scaffolds & {parent_identity(r, scaffold=True) for r in held}:
                raise DataError("External scaffold overlap violates the frozen exclusion policy")
    held_tasks = {r["task_id"] for r in held}
    if (set(final["models"]) != {r["task_id"] for r in dev} or held_tasks - set(final["models"])
            or (mode == "terminal" and set(final["models"]) != held_tasks)):
        raise DataError("Terminal task/model coverage mismatch")
    policy = contract["evaluation"]
    if policy.get("uncertainty") != "molecular_group_bootstrap":
        raise DataError("Unsupported uncertainty contract")
    target = contract["preparation"]["target_column"]
    if policy.get("target_column") != target:
        raise DataError("Terminal target scale mismatch")
    kind = evaluation_task_type(contract)
    folds_binding = split["folds"]
    verify_binding(folds_binding)
    if folds_binding not in final["identity"]["inputs"]:
        raise DataError("Final fit does not bind the frozen training row axis")
    spec, frozen_folds, _, _, _, _ = reconstruct_training_inputs(
        final["identity"], development_path, folds_binding["path"], feature_path, contract_path)
    folds = frozen_folds["tasks"]
    out = output_dir(destination, paths)
    predictions, results, nonfinite_ledger, domain_rows = [], [], [], []
    for task, model in sorted(final["models"].items()):
        current = [r for r in held if r["task_id"] == task]
        if not current:
            continue
        chosen = selection["tasks"][task]
        if (model.get("task_type") != kind or chosen.get("task_type") != kind
                or model["candidate_id"] != chosen["candidate_id"] or model["family"] != chosen["family"]):
            raise DataError("Final model differs from locked task/candidate selection")
        artifact = verify_binding(model["artifact"])
        X = features_for(current, model, feature, nonfinite_ledger)
        # Only load locally produced, hash-bound models. Pickle is not an untrusted format.
        fitted = joblib.load(artifact)
        from qsar_train import TrainingError, digest as identity_digest, validate_fitted_identity
        try:
            validate_fitted_identity(fitted, chosen, spec, task, final["identity"])
        except TrainingError as error:
            raise DataError(str(error)) from error
        if model.get("training_identity_sha256") != identity_digest(final["identity"]):
            raise DataError("Final model receipt differs from frozen training identity")
        training = [r for r in dev if r["task_id"] == task]
        if (getattr(fitted, "task_type_", None) != kind
                or getattr(fitted, "feature_route_", None) != model.get("feature_route", "numeric")
                or getattr(fitted, "fit_training_row_ids_", None) != folds[task]["row_ids"]
                or set(folds[task]["row_ids"]) != {r["output_row_id"] for r in training}):
            raise DataError("Locked fitted pipeline task/feature/training cohort mismatch")
        threshold = None
        if kind == "classification":
            threshold = chosen.get("threshold")
            if type(threshold) not in (int, float):
                raise DataError("Locked classification selection requires a frozen scalar threshold")
            classification_arrays([], [], threshold)
            if (chosen.get("positive_class") != 1 or model.get("positive_class") != 1
                    or getattr(fitted, "positive_class_", None) != 1
                    or model.get("threshold") != threshold or getattr(fitted, "threshold_", None) != threshold):
                raise DataError("Locked classification threshold/positive-class replay mismatch")
            p = positive_probability(fitted, X)
        else:
            p = np.asarray(fitted.predict(X), dtype=float)
        y = np.array([float(r[target]) for r in current])
        baseline = float(np.mean([float(r[target]) for r in training]))
        result = {"task_id": task, "candidate_id": model["candidate_id"], "target_column": target, "task_type": kind,
                  "metrics": metrics(y, p, kind, threshold),
                  "source_metrics": source_metrics(current, y, p, kind, threshold),
                  "uncertainty": group_intervals(y, p, [r["group_id"] for r in current], policy["bootstrap_repetitions"], policy["seed"], kind, threshold)}
        if kind == "classification":
            result.update(positive_class=1, threshold=threshold, threshold_policy="locked_training_selection",
                          training_prevalence_baseline=metrics(y, np.full(len(y), baseline), kind, threshold))
        else:
            result["training_mean_baseline"] = metrics(y, np.full(len(y), baseline))
        domain = validate_domain(fitted, training, contract.get("training", {}).get("applicability_domain"))
        scored = domain_scores(domain, current) if domain is not None else None
        if scored is not None:
            evidence = domain.to_dict()
            if model.get("applicability_domain") != {k: evidence[k] for k in (
                    "artifact_sha256", "recipe_sha256", "training_sha256", "references_sha256", "calibration_sha256", "cutoff")}:
                raise DataError("Final applicability-domain evidence differs from locked model")
            result["applicability_domain"] = domain_summary(current, scored, y, p, kind, threshold)
            domain_rows.extend({"task_id": task, "output_row_id": r["output_row_id"], **score} for r, score in zip(current, scored))
        results.append(result)
        for i, (row, yy, pp) in enumerate(zip(current, y, p)):
            predicted = {"output_row_id": row["output_row_id"], "task_id": task, "group_id": row["group_id"],
                         "target": float(yy), "prediction": float(pp), "target_column": target, "task_type": kind,
                         "source_id": row.get("source_id", "unknown")}
            if kind == "classification":
                predicted.update(positive_class=1, threshold=threshold, predicted_label=int(pp >= threshold))
            if scored is not None:
                predicted.update(domain_similarity=scored[i]["similarity"], in_domain=scored[i]["in_domain"],
                                 domain_reason=scored[i]["reason"])
            predictions.append(predicted)
        verify_binding(model["artifact"])
    if len(predictions) != len(held):
        raise DataError("Evaluation did not retain the complete held cohort")
    write_csv(out / (mode + "_predictions.csv"), predictions, ["output_row_id", "task_id", "target", "prediction"])
    write_csv(out / (mode + "_nonfinite_ledger.csv"), nonfinite_ledger, ["output_row_id", "family", "feature", "reason"])
    if domain_rows:
        write_csv(out / (mode + "_domain.csv"), domain_rows, ["output_row_id", "task_id", "similarity", "in_domain", "reason"])
    report = {"mode": mode, "task_type": kind, "input_hashes": bindings, "results": results,
              "exposure_history": prior_exposure, "prior_exposure": prior_exposure, "exposure_ledger": exposure_binding,
              "sealed_input": sealed, "independent_holdout": False,
              "claim": ("Reserved internal benchmark; prior exposure remains as declared, not an independent external validation"
                        if mode == "terminal" else "External-origin cohort; sealed bytes and declared prior exposure do not establish independent untouched validation"),
              "coverage": {"rows": len(held), "predicted_rows": len(predictions), "dropped_rows": 0,
                           "tasks": len(results), "development_tasks_without_evaluation_rows": sorted(set(final["models"]) - held_tasks)}}
    for path, sha in bindings.items():
        if digest(path) != sha:
            raise DataError("Terminal input changed during evaluation")
    for item in final["identity"]["inputs"] + final["identity"].get("source_hashes", []):
        verify_binding(item)
    verify_binding(final["identity"]["code"])
    verify_binding(final["selection"])
    verify_binding(final["lock"])
    verify_binding(exposure_binding)
    dump(out / (mode + "_evaluation.json"), report)
    return handoff(out, "terminal-evaluation", "Locked pipeline " + mode + " predictions produced; review required",
                   ["Frozen split and model hashes", "No refitting or selection", "Full evaluation task/row coverage"],
                   [report["claim"]], {"tasks": len(results), "rows": len(predictions)})


def terminal(development_path, terminal_path, split_path, feature_path, final_fit_path, contract_path, destination):
    return evaluate_locked(development_path, terminal_path, split_path, feature_path, final_fit_path, contract_path, destination, "terminal")


def external(development_path, external_path, split_path, feature_path, final_fit_path, contract_path, destination):
    return evaluate_locked(development_path, external_path, split_path, feature_path, final_fit_path, contract_path, destination, "external")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    decision = sub.add_parser("improvement")
    for name in ("evaluation", "coverage", "selection", "proposal"):
        decision.add_argument("--" + name, required=True)
    end = sub.add_parser("terminal")
    outside = sub.add_parser("external")
    for p, cohort in ((end, "terminal"), (outside, "external")):
        for name in ("development", cohort, "split", "features", "final-fit", "contract"):
            p.add_argument("--" + name, required=True)
    for p in (decision, end, outside):
        p.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        if args.mode == "improvement":
            result = improvement(args.evaluation, args.coverage, args.selection, args.proposal, args.output_dir)
        else:
            producer = terminal if args.mode == "terminal" else external
            result = producer(args.development, getattr(args, args.mode), args.split, args.features, args.final_fit, args.contract, args.output_dir)
        print(json.dumps({"summary": result["summary"], "coverage": result["coverage"]}))
        return 0
    except (DataError, ValueError, KeyError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
