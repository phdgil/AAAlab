"""Reviewed improvement handoffs and locked terminal regression evaluation.

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

from qsar_assess import metrics, group_intervals, read_csv
from qsar_data import DataError, digest, dump, handoff, load, output_dir, write_csv
from qsar_prepare import fingerprint_generator


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
    else:
        generator, width, _ = fingerprint_generator(family, record["parameters"])
        if names != [f"{family}:bit:{i}" for i in range(width)]:
            raise DataError("Fingerprint order/dimension differs from locked recipe")
    matrix = np.empty((len(smiles), len(names)), dtype=float)
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
            DataStructs.ConvertToNumpyArray(generator(mol), matrix[i])
    return matrix


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


def terminal(development_path, terminal_path, split_path, feature_path, final_fit_path, contract_path, destination):
    paths = [development_path, terminal_path, split_path, feature_path, final_fit_path, contract_path]
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
    for key, path in (("development", development_path), ("sealed_terminal", terminal_path), ("contract", contract_path)):
        item = split[key]
        if str(Path(path).resolve()) != item["path"]:
            raise DataError("Terminal inputs differ from frozen split")
        verify_binding(item)
    for item in final["identity"]["inputs"]:
        verify_binding(item)
    for path in (development_path, feature_path, contract_path):
        if {"path": str(Path(path).resolve()), "sha256": digest(path)} not in final["identity"]["inputs"]:
            raise DataError("Final fit does not bind terminal inference inputs")
    verify_binding(final["identity"]["code"])
    verify_binding(final["selection"])
    verify_binding(final["lock"])
    lock = load(final["lock"]["path"])
    if lock.get("decision") != "STOP" or lock.get("selection") != final["selection"]:
        raise DataError("Final fit differs from stopped model lock")
    dev, held = read_csv(development_path), read_csv(terminal_path)
    if not held or any(r.get("partition") != "terminal" for r in held) or any(r.get("partition") != "development" for r in dev):
        raise DataError("Invalid terminal/development partition")
    ids = [r["output_row_id"] for r in dev + held]
    if len(set(ids)) != len(ids) or {r["group_id"] for r in dev} & {r["group_id"] for r in held}:
        raise DataError("Terminal row/group leakage")
    if set(final["models"]) != {r["task_id"] for r in held} or set(final["models"]) != {r["task_id"] for r in dev}:
        raise DataError("Terminal task/model coverage mismatch")
    policy = contract["evaluation"]
    if policy.get("uncertainty") != "molecular_group_bootstrap":
        raise DataError("Unsupported uncertainty contract")
    target = contract["preparation"]["target_column"]
    if policy.get("target_column") != target:
        raise DataError("Terminal target scale mismatch")
    out = output_dir(destination, paths)
    predictions, results, nonfinite_ledger = [], [], []
    for task, model in sorted(final["models"].items()):
        artifact = verify_binding(model["artifact"])
        current = [r for r in held if r["task_id"] == task]
        family = model["family"]
        record = feature["families"][family]
        if model["feature_names"] != record["feature_names"]:
            raise DataError("Final model feature order mismatch")
        X = replay([r["parent_smiles"] for r in current], family, record, feature["environment"],
                   [r["output_row_id"] for r in current], nonfinite_ledger)
        # Only load locally produced, hash-bound models. Pickle is not an untrusted format.
        fitted = joblib.load(artifact)
        p = np.asarray(fitted.predict(X), dtype=float)
        y = np.array([float(r[target]) for r in current])
        baseline = float(np.mean([float(r[target]) for r in dev if r["task_id"] == task]))
        result = {"task_id": task, "candidate_id": model["candidate_id"], "target_column": target,
                  "metrics": metrics(y, p), "training_mean_baseline": metrics(y, np.full(len(y), baseline)),
                  "uncertainty": group_intervals(y, p, [r["group_id"] for r in current], policy["bootstrap_repetitions"], policy["seed"])}
        results.append(result)
        predictions.extend({"output_row_id": r["output_row_id"], "task_id": task, "group_id": r["group_id"],
                            "target": float(yy), "prediction": float(pp), "target_column": target,
                            "source_id": r.get("source_id", "unknown")} for r, yy, pp in zip(current, y, p))
        verify_binding(model["artifact"])
    write_csv(out / "terminal_predictions.csv", predictions, ["output_row_id", "task_id", "target", "prediction"])
    write_csv(out / "terminal_nonfinite_ledger.csv", nonfinite_ledger, ["output_row_id", "family", "feature", "reason"])
    report = {"mode": "terminal", "input_hashes": bindings, "results": results,
              "exposure_history": split["policy"]["prior_exposure"],
              "claim": "Reserved internal benchmark; prior exposure remains as declared, not an independent external validation"}
    for path, sha in bindings.items():
        if digest(path) != sha:
            raise DataError("Terminal input changed during evaluation")
    dump(out / "terminal_evaluation.json", report)
    return handoff(out, "terminal-evaluation", "Locked pipeline terminal predictions produced; review required",
                   ["Frozen split and model hashes", "No refitting or selection", "Full terminal task/row coverage"],
                   [report["claim"]], {"tasks": len(results), "rows": len(predictions)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    decision = sub.add_parser("improvement")
    for name in ("evaluation", "coverage", "selection", "proposal"):
        decision.add_argument("--" + name, required=True)
    end = sub.add_parser("terminal")
    for name in ("development", "terminal", "split", "features", "final-fit", "contract"):
        end.add_argument("--" + name, required=True)
    for p in (decision, end):
        p.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = improvement(args.evaluation, args.coverage, args.selection, args.proposal, args.output_dir) if args.mode == "improvement" else terminal(
            args.development, args.terminal, args.split, args.features, args.final_fit, args.contract, args.output_dir)
        print(json.dumps({"summary": result["summary"], "coverage": result["coverage"]}))
        return 0
    except (DataError, ValueError, KeyError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
