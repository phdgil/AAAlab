"""Unfitted grouped preparation and fixed, development-only molecular features."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

VERSION = "1.0"


class PreparationError(ValueError):
    pass


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def binding(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return {"path": str(path), "sha256": h.hexdigest()}


def output_folder(path, inputs):
    out = Path(path).resolve()
    if any(out == Path(p).resolve() or out.is_relative_to(Path(p).resolve())
           or Path(p).resolve().is_relative_to(out) for p in inputs):
        raise PreparationError("Output overlaps protected input")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise PreparationError("Output directory must be empty; version outputs")
    return out


def write_csv(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path, target=None):
    from rdkit import Chem
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames or []
        required = {"output_row_id", "task_id", "group_id", "parent_smiles"}
        if target:
            required.add(target)
        if len(fields) != len(set(fields)) or not required.issubset(fields):
            raise PreparationError("Missing or duplicate required columns")
        rows = list(reader)
    if not rows:
        raise PreparationError("No eligible records")
    seen, structure_groups = set(), {}
    for row in rows:
        if None in row or any(row.get(k) is None or not row[k].strip() for k in required):
            raise PreparationError("Malformed row or empty required value")
        identity = row["output_row_id"]
        if identity in seen:
            raise PreparationError("Duplicate output_row_id: " + identity)
        seen.add(identity)
        mol = Chem.MolFromSmiles(row["parent_smiles"])
        if mol is None or mol.GetNumAtoms() == 0:
            raise PreparationError("Invalid structure: " + identity)
        # Connectivity groups must also cover alternative stereo spellings.
        canonical = Chem.MolToSmiles(mol, isomericSmiles=False)
        if canonical in structure_groups and structure_groups[canonical] != row["group_id"]:
            raise PreparationError("Same parent connectivity assigned conflicting groups")
        structure_groups[canonical] = row["group_id"]
        if target:
            try:
                finite = math.isfinite(float(row[target]))
            except ValueError:
                finite = False
            if not finite:
                raise PreparationError("Nonfinite/non-numeric target: " + identity)
    return fields, rows


def integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise PreparationError(f"{name} must be an integer >= {minimum}")
    return value


def ordered_groups(groups, seed, scope):
    return sorted(set(groups), key=lambda group: (hashlib.sha256(
        json.dumps([seed, scope, group], separators=(",", ":")).encode()).hexdigest(), group))


def fold_sets(rows, count, seed, scope):
    groups = ordered_groups((r["group_id"] for r in rows), seed, scope)
    if len(groups) < count:
        raise PreparationError(f"Infeasible {scope}: {len(groups)} groups for {count} folds")
    assignment = {group: index % count for index, group in enumerate(groups)}
    return [{"fold": index,
             "train_row_ids": [r["output_row_id"] for r in rows if assignment[r["group_id"]] != index],
             "validation_row_ids": [r["output_row_id"] for r in rows if assignment[r["group_id"]] == index]}
            for index in range(count)]


def prepare_splits(curated_csv, contract_path, output_dir, curation_manifest=None):
    module_spec = importlib.util.spec_from_file_location("qsar_curation_evidence", Path(__file__).with_name("qsar_curation_evidence.py"))
    evidence_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(evidence_module)
    try:
        evidence = evidence_module.validate(contract_path, curation_manifest, curated_csv)
    except ValueError as error:
        raise PreparationError(str(error)) from error
    initial = {"input": binding(curated_csv), "contract": binding(contract_path), "code": binding(__file__)}
    contract = load(contract_path)
    spec = contract["preparation"]
    for key in ("target_column", "intended_generalization", "terminal_custodian"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise PreparationError("Missing preparation " + key)
    if spec.get("group_column") != "group_id":
        raise PreparationError("Only accepted group_id connectivity grouping is supported")
    seed = integer(spec.get("seed"), "seed", 0)
    outer = integer(spec.get("outer_folds"), "outer_folds", 2)
    inner = integer(spec.get("inner_folds"), "inner_folds", 2)
    fraction = spec.get("terminal_fraction")
    if type(fraction) not in (int, float) or not 0 < fraction < 1:
        raise PreparationError("terminal_fraction must lie strictly between zero and one")
    exposure = spec.get("prior_exposure")
    if not isinstance(exposure, dict) or exposure.get("status") not in {"unknown", "none_known", "exposed"}:
        raise PreparationError("Explicit prior-exposure status is required")
    if not isinstance(exposure.get("evidence"), list) or not isinstance(exposure.get("description"), str) or not exposure["description"].strip():
        raise PreparationError("Prior exposure needs evidence list and description")
    if not isinstance(spec.get("unfitted_transforms"), dict) or not spec["unfitted_transforms"]:
        raise PreparationError("Explicit unfitted transformation policy required")
    fields, rows = read_rows(curated_csv, spec["target_column"])
    if "partition" in fields:
        raise PreparationError("Curated input already has partition; do not reshuffle an existing benchmark")
    out = output_folder(output_dir, [curated_csv, contract_path] + [x["path"] for x in evidence])
    groups = ordered_groups((r["group_id"] for r in rows), seed, "terminal")
    terminal_groups = set(groups[:math.ceil(len(groups) * fraction)])
    development, terminal = [], []
    for row in rows:
        is_terminal = row["group_id"] in terminal_groups
        row["partition"] = "terminal" if is_terminal else "development"
        (terminal if is_terminal else development).append(row)
    task_rows = defaultdict(list)
    for row in development:
        task_rows[row["task_id"]].append(row)
    tasks, counts = {}, {}
    try:
        for task in sorted({r["task_id"] for r in rows}):
            current = task_rows[task]
            terminal_count = sum(r["task_id"] == task for r in terminal)
            if not current or not terminal_count:
                raise PreparationError(f"Infeasible task {task}: missing development or terminal records")
            folds = fold_sets(current, outer, seed, task + "/outer")
            for fold in folds:
                train = set(fold["train_row_ids"])
                fold["inner_folds"] = fold_sets([r for r in current if r["output_row_id"] in train],
                                                inner, seed, task + "/inner/" + str(fold["fold"]))
            tasks[task] = {"row_ids": [r["output_row_id"] for r in current], "outer_folds": folds}
            counts[task] = {"development": len(current), "terminal": terminal_count}
    except PreparationError as error:
        dump(out / "feasibility.json", {"status": "blocked", "reason": str(error),
                                      "input_rows": len(rows), "excluded_rows": 0})
        raise
    write_csv(out / "development.csv", development, fields + ["partition"])
    write_csv(out / "sealed_terminal.csv", terminal, fields + ["partition"])
    membership_fields = ["output_row_id", "task_id", "group_id", "partition"]
    write_csv(out / "membership.csv", [{k: r[k] for k in membership_fields} for r in rows], membership_fields)
    dump(out / "folds.json", {"schema_version": VERSION, "tasks": tasks})
    dump(out / "exposure_ledger.json", {**exposure, "terminal_custodian": spec["terminal_custodian"],
        "claim": "Untouched status requires independent exposure review; random reassignment cannot erase past access."})
    dump(out / "unfitted_pipeline.json", spec["unfitted_transforms"])
    manifest = {"schema_version": VERSION, "stage": "preprocessing", "status": "complete",
        **initial,
        "policy": spec, "input_rows": len(rows), "development_rows": len(development),
        "terminal_rows": len(terminal), "excluded_rows": 0, "task_counts": counts,
        "split_algorithm": "sha256(seed,scope,group); terminal ceil(fraction*groups); group-round-robin nested folds",
        "development": binding(out / "development.csv"), "sealed_terminal": binding(out / "sealed_terminal.csv"),
        "folds": binding(out / "folds.json"), "membership": binding(out / "membership.csv"),
        "checks": ["all eligible rows assigned exactly once", "global terminal group disjointness",
                   "per-task outer and inner group disjointness", "no fitted transformations"],
        "limitations": ["Group-count balance does not guarantee row/target balance.",
                        "Filesystem secrecy requires custodian access controls; this manifest has no terminal labels."]}
    for item in initial.values():
        if binding(item["path"]) != item:
            raise PreparationError("Input changed during preparation")
    for item in evidence:
        try:
            evidence_module.checked(item)
        except (ValueError, OSError) as error:
            raise PreparationError("Curation evidence changed during preparation: " + str(error)) from error
    if evidence:
        manifest["curation_evidence"] = evidence
    dump(out / "preprocessing_manifest.json", manifest)
    return manifest


def fingerprint_generator(name, parameters):
    from rdkit.Chem import rdFingerprintGenerator as F, MACCSkeys
    p = dict(parameters)
    def take(key, kind):
        value = p.pop(key, None)
        if kind is bool:
            if type(value) is not bool:
                raise PreparationError(name + " requires boolean " + key)
        else:
            integer(value, name + "." + key)
        return value
    if name == "MACCS":
        generator, width, recipe = MACCSkeys.GenMACCSKeys, 167, {"algorithm": "RDKit MACCS keys", "unused_bit": 0}
    else:
        width = take("n_bits", int)
        if name in {"Morgan2", "Morgan3"}:
            args = dict(radius=int(name[-1]), fpSize=width, includeChirality=take("use_chirality", bool),
                        countSimulation=False, useBondTypes=True, includeRingMembership=True)
            native = F.GetMorganGenerator(**args)
        elif name == "RDKitpath":
            args = dict(fpSize=width, minPath=take("min_path", int), maxPath=take("max_path", int),
                        useHs=True, branchedPaths=True, useBondOrder=True, countSimulation=False, numBitsPerFeature=2)
            if args["minPath"] > args["maxPath"]:
                raise PreparationError("min_path exceeds max_path")
            native = F.GetRDKitFPGenerator(**args)
        elif name == "atom_pair":
            args = dict(fpSize=width, minDistance=take("min_distance", int), maxDistance=take("max_distance", int),
                        includeChirality=take("use_chirality", bool), use2D=True, countSimulation=False)
            if args["minDistance"] > args["maxDistance"]:
                raise PreparationError("min_distance exceeds max_distance")
            native = F.GetAtomPairGenerator(**args)
        elif name == "torsion":
            args = dict(fpSize=width, torsionAtomCount=take("torsion_atom_count", int),
                        includeChirality=take("use_chirality", bool), countSimulation=False)
            native = F.GetTopologicalTorsionGenerator(**args)
        elif name == "Avalon":
            from rdkit.Avalon import pyAvalonTools
            args = dict(nBits=width, isQuery=False, resetVect=False, bitFlags=15761407)
            generator = lambda mol: pyAvalonTools.GetAvalonFP(mol, **args)
        else:
            raise PreparationError("Unsupported representation: " + name)
        if name != "Avalon":
            generator = native.GetFingerprint
        recipe = {"algorithm": name, "arguments": args}
        if name != "Avalon":
            recipe["generator_info"] = native.GetInfoString()
    if p:
        raise PreparationError("Unsupported family parameters: " + str(sorted(p)))
    recipe["representation"] = "binary bits (hashed collisions retained; no count simulation)"
    return generator, width, recipe


def generate_features(development_csv, split_manifest_path, contract_path, output_dir):
    initial = {"input": binding(development_csv), "split_manifest": binding(split_manifest_path),
               "contract": binding(contract_path), "code": binding(__file__)}
    import numpy as np
    import rdkit
    from rdkit import Chem, DataStructs
    from rdkit.Chem import Descriptors
    split, contract = load(split_manifest_path), load(contract_path)
    inherited_evidence = split.get('curation_evidence', [])
    for item in inherited_evidence:
        if binding(item['path']) != item:
            raise PreparationError('Inherited curation evidence changed before feature generation')
    if binding(development_csv) != split.get("development") or binding(contract_path) != split.get("contract"):
        raise PreparationError("Development input/contract does not match frozen split manifest")
    fields, rows = read_rows(development_csv)
    if "partition" not in fields or any(r["partition"] != "development" for r in rows):
        raise PreparationError("Features accept development-only rows")
    if len(rows) != split.get("development_rows"):
        raise PreparationError("Development row count mismatch")
    families = contract["features"]["families"]
    if not isinstance(families, dict) or "RDKit2D" not in families or families["RDKit2D"] != {}:
        raise PreparationError("Full current RDKit2D inventory must be requested as RDKit2D:{}")
    inventory = list(Descriptors._descList)
    if len({name for name, _ in inventory}) != len(inventory):
        raise PreparationError("Toolkit descriptor inventory has duplicate names")
    generators = {}
    for name, parameters in families.items():
        if not isinstance(parameters, dict):
            raise PreparationError("Family parameters must be objects")
        if name != "RDKit2D":
            generators[name] = fingerprint_generator(name, parameters)
    out = output_folder(output_dir, [development_csv, split_manifest_path, contract_path])
    row_ids = np.array([r["output_row_id"] for r in rows])
    task_ids = np.array([r["task_id"] for r in rows])
    ledger, records = [], {}
    for name in families:
        names = [entry[0] for entry in inventory] if name == "RDKit2D" else [f"{name}:bit:{i}" for i in range(generators[name][1])]
        matrix = np.empty((len(rows), len(names)), dtype=np.float64 if name == "RDKit2D" else np.uint8)
        first_rows = {}
        for index, row in enumerate(rows):
            previous = first_rows.get(row["parent_smiles"])
            if previous is not None:
                first, diagnostics = previous
                matrix[index] = matrix[first]
                ledger.extend({**item, "output_row_id": row["output_row_id"]} for item in diagnostics)
                continue
            ledger_start = len(ledger)
            mol = Chem.MolFromSmiles(row["parent_smiles"])
            if name == "RDKit2D":
                for column, (feature, function) in enumerate(inventory):
                    reason = "nonfinite"
                    try:
                        value = float(function(mol))
                    except Exception as error:
                        value, reason = float("nan"), type(error).__name__ + ": " + str(error)
                    matrix[index, column] = value
                    if not math.isfinite(value):
                        ledger.append({"output_row_id": row["output_row_id"], "family": name,
                                       "feature": feature, "reason": reason})
            else:
                try:
                    vector = generators[name][0](mol)
                    if vector.GetNumBits() != len(names):
                        raise PreparationError("Toolkit fingerprint dimension differs from declared width")
                    DataStructs.ConvertToNumpyArray(vector, matrix[index])
                except Exception as error:
                    dump(out / "generation_failure.json", {"status": "failed", "output_row_id": row["output_row_id"],
                                                           "family": name, "reason": str(error)})
                    raise PreparationError(f"Fingerprint generation failed for {name}/{row['output_row_id']}") from error
            first_rows[row["parent_smiles"]] = (index, ledger[ledger_start:])
        path = out / (name + ".npz")
        np.savez_compressed(path, X=matrix, row_ids=row_ids, task_ids=task_ids, feature_names=np.array(names))
        records[name] = {"artifact": binding(path), "shape": list(matrix.shape), "dtype": str(matrix.dtype),
            "feature_names": names, "parameters": families[name],
            "unique_structures_computed": len(first_rows),
            "recipe": {"inventory": "rdkit.Chem.Descriptors._descList", "versions": {n: getattr(f, "version", None) for n, f in inventory}}
                       if name == "RDKit2D" else generators[name][2],
            "nonfinite_count": sum(item["family"] == name for item in ledger)}
    write_csv(out / "nonfinite_ledger.csv", ledger, ["output_row_id", "family", "feature", "reason"])
    manifest = {"schema_version": VERSION, "stage": "features", "status": "complete",
        **initial,
        "environment": {"rdkit": rdkit.__version__, "numpy": np.__version__, "python": sys.version},
        "rows": len(rows), "row_ids": row_ids.tolist(), "families": records,
        "requested_families": list(families), "generated_families": list(records),
        "nonfinite_ledger": binding(out / "nonfinite_ledger.csv"),
        "checks": ["full dynamic RDKit descriptor inventory", "all development rows preserved", "no fitted transformations or filtering"],
        "limitations": ["Nonfinite descriptors require the reviewed fold-local training policy.",
                        "Only binary fingerprint families are supported; no learned representations."]}
    for item in initial.values():
        if binding(item["path"]) != item:
            raise PreparationError("Input changed during feature generation")
    for item in inherited_evidence:
        if binding(item['path']) != item:
            raise PreparationError('Inherited curation evidence changed during feature generation')
    if inherited_evidence:
        manifest['curation_evidence'] = inherited_evidence
    dump(out / "feature_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    split = commands.add_parser("split")
    split.add_argument("--curated", required=True)
    split.add_argument("--curation-manifest", help="Required bound curation evidence for strict scientific contracts")
    feature = commands.add_parser("features")
    feature.add_argument("--development", required=True)
    feature.add_argument("--split-manifest", required=True)
    for command in (split, feature):
        command.add_argument("--contract", required=True)
        command.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = prepare_splits(args.curated, args.contract, args.output_dir, args.curation_manifest) if args.command == "split" else generate_features(
            args.development, args.split_manifest, args.contract, args.output_dir)
        print(json.dumps({"status": result["status"], "stage": result["stage"]}))
        return 0
    except (PreparationError, KeyError, OSError, ImportError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
