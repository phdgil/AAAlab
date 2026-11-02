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
import shutil
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


def read_rows(path, target=None, allow_group_merges=False):
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
        for atom in mol.GetAtoms():
            atom.SetAtomMapNum(0)
        # Connectivity groups must also cover alternative stereo spellings.
        canonical = Chem.MolToSmiles(mol, isomericSmiles=False)
        if not allow_group_merges and canonical in structure_groups and structure_groups[canonical] != row["group_id"]:
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


def exposure_policy(value, name):
    if not isinstance(value, dict) or not isinstance(value.get("status"), str) or value["status"] not in {"unknown", "none_known", "exposed"}:
        raise PreparationError(name + ": explicit prior-exposure status is required")
    if not isinstance(value.get("evidence"), list) or not isinstance(value.get("description"), str) or not value["description"].strip():
        raise PreparationError(name + ": prior exposure needs evidence list and description")
    return value


def group_rows(rows, policy, empty_scaffold_policy=None, include_scaffolds=False, cohort="curated"):
    """Coarsen declared groups; never disconnect any declared group or parent."""
    from rdkit import Chem
    from rdkit.Chem.Scaffolds import MurckoScaffold
    parents, first_keys, chemistry, mapping = {}, {}, {}, []

    def root(group):
        parents.setdefault(group, group)
        while parents[group] != group:
            parents[group] = parents[parents[group]]
            group = parents[group]
        return group

    for row in rows:
        smiles = row["parent_smiles"]
        if smiles not in chemistry:
            mol = Chem.MolFromSmiles(smiles)
            for atom in mol.GetAtoms():
                atom.SetAtomMapNum(0)
            canonical = Chem.MolToSmiles(mol, isomericSmiles=False)
            scaffold = Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(mol), isomericSmiles=False) if include_scaffolds or policy == "bemis_murcko" else ""
            chemistry[smiles] = canonical, scaffold
        canonical, scaffold = chemistry[smiles]
        declared = row["group_id"]
        basis, key = "declared_group", declared
        if policy == "parent":
            basis, key = "parent_connectivity", "parent:" + canonical
        elif policy == "bemis_murcko":
            if scaffold:
                basis, key = "bemis_murcko", "scaffold:" + scaffold
            elif empty_scaffold_policy == "parent":
                basis, key = "empty_scaffold_parent_connectivity", "parent:" + canonical
            else:
                raise PreparationError("Empty Bemis-Murcko scaffold: " + row["output_row_id"])
        root(declared)
        if policy != "declared":
            previous = first_keys.setdefault(key, declared)
            left, right = sorted((root(previous), root(declared)))
            parents[right] = left
        mapping.append({"cohort": cohort, "output_row_id": row["output_row_id"], "task_id": row["task_id"],
                        "declared_group_id": declared, "canonical_parent_smiles": canonical,
                        "scaffold_smiles": scaffold, "grouping_basis": basis, "grouping_key": key})
    components = defaultdict(set)
    for item in mapping:
        components[root(item["declared_group_id"])].add(item["grouping_key"])
    effective = {group: policy + ":" + hashlib.sha256(json.dumps(sorted(keys), separators=(",", ":")).encode()).hexdigest()
                 for group, keys in components.items()}
    for row, item in zip(rows, mapping):
        group = item["declared_group_id"] if policy == "declared" else effective[root(item["declared_group_id"])]
        row["group_id"] = group
        item["effective_group_id"] = group
    return mapping


def external_exclusions(mapping, external_mapping, exclude_scaffold_overlap):
    identities, scaffolds = defaultdict(list), defaultdict(list)
    for item in external_mapping:
        identities[item["canonical_parent_smiles"]].append(item["output_row_id"])
        # An empty scaffold is absence of a scaffold, not a shared chemical core.
        if item["scaffold_smiles"]:
            scaffolds[item["scaffold_smiles"]].append(item["output_row_id"])
    ledger = []
    for item in mapping:
        matched = set(identities[item["canonical_parent_smiles"]])
        reasons = ["canonical_parent_overlap"] if matched else []
        if exclude_scaffold_overlap and item["scaffold_smiles"] and scaffolds[item["scaffold_smiles"]]:
            matched.update(scaffolds[item["scaffold_smiles"]])
            reasons.append("bemis_murcko_overlap")
        if reasons:
            ledger.append({key: item[key] for key in ("output_row_id", "task_id", "declared_group_id", "effective_group_id", "canonical_parent_smiles")})
            ledger[-1].update(reasons=json.dumps(reasons), matched_external_row_ids=json.dumps(sorted(matched)))
    return ledger


def fold_sets(rows, count, seed, scope, target=None, stratify=False):
    groups = ordered_groups((r["group_id"] for r in rows), seed, scope)
    if len(groups) < count:
        raise PreparationError(f"Infeasible {scope}: {len(groups)} groups for {count} folds")
    if stratify:
        if not target:
            raise PreparationError("Classification stratification requires a target column")
        from sklearn.model_selection import StratifiedGroupKFold
        labels = [int(float(row[target])) for row in rows]
        for label in (0, 1):
            class_groups = {row["group_id"] for row, value in zip(rows, labels) if value == label}
            if len(class_groups) < count:
                raise PreparationError(f"Infeasible {scope}: class {label} occupies {len(class_groups)} groups for {count} folds")
        scoped_seed = int.from_bytes(hashlib.sha256(json.dumps([seed, scope], separators=(",", ":")).encode()).digest()[:4], "big")
        splitter = StratifiedGroupKFold(n_splits=count, shuffle=True, random_state=scoped_seed)
        folds = [{"fold": index, "train_row_ids": [rows[i]["output_row_id"] for i in train],
                  "validation_row_ids": [rows[i]["output_row_id"] for i in valid]}
                 for index, (train, valid) in enumerate(splitter.split([0] * len(rows), labels, [r["group_id"] for r in rows]))]
    else:
        assignment = {group: index % count for index, group in enumerate(groups)}
        folds = [{"fold": index,
                  "train_row_ids": [r["output_row_id"] for r in rows if assignment[r["group_id"]] != index],
                  "validation_row_ids": [r["output_row_id"] for r in rows if assignment[r["group_id"]] == index]}
                 for index in range(count)]
    if target:
        labels = {row["output_row_id"]: int(float(row[target])) for row in rows}
        for fold in folds:
            for axis in ("train_row_ids", "validation_row_ids"):
                if {labels[row_id] for row_id in fold[axis]} != {0, 1}:
                    raise PreparationError(f"Infeasible {scope}/{fold['fold']}: {axis} lacks both binary classes")
    return folds


def prepare_splits(curated_csv, contract_path, output_dir, curation_manifest=None, external_csv=None):
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
        raise PreparationError("group_column must be group_id")
    grouping = spec.get("grouping_policy", "declared")
    if not isinstance(grouping, str) or grouping not in {"declared", "parent", "bemis_murcko"}:
        raise PreparationError("Unsupported preparation grouping_policy")
    empty_scaffold = spec.get("empty_scaffold_policy")
    if grouping == "bemis_murcko":
        if not isinstance(empty_scaffold, str) or empty_scaffold not in {"parent", "error"}:
            raise PreparationError("Bemis-Murcko grouping requires explicit empty_scaffold_policy: parent or error")
    elif empty_scaffold is not None:
        raise PreparationError("empty_scaffold_policy applies only to bemis_murcko grouping")
    training = contract.get("training", {})
    if not isinstance(training, dict):
        raise PreparationError("training must be an object")
    task_type = training.get("task_type", "regression")
    if not isinstance(task_type, str) or task_type not in {"regression", "classification"}:
        raise PreparationError("Unsupported training.task_type")
    stratify = spec.get("stratify", False)
    if type(stratify) is not bool or stratify and task_type != "classification":
        raise PreparationError("preparation.stratify must be boolean and requires training.task_type=classification")
    seed = integer(spec.get("seed"), "seed", 0)
    outer = integer(spec.get("outer_folds"), "outer_folds", 2)
    inner = integer(spec.get("inner_folds"), "inner_folds", 2)
    fraction = spec.get("terminal_fraction")
    if type(fraction) not in (int, float) or not 0 < fraction < 1:
        raise PreparationError("terminal_fraction must lie strictly between zero and one")
    exposure = exposure_policy(spec.get("prior_exposure"), "Reserved terminal exposure")
    if not isinstance(spec.get("unfitted_transforms"), dict) or not spec["unfitted_transforms"]:
        raise PreparationError("Explicit unfitted transformation policy required")
    external_spec = spec.get("external_cohort")
    external_input, external_rows, external_mapping, exclusions = None, [], [], []
    exclude_scaffolds = False
    if external_csv is not None:
        if not isinstance(external_spec, dict):
            raise PreparationError("External input requires preparation.external_cohort exposure policy")
        external_exposure = exposure_policy(external_spec.get("prior_exposure"), "External cohort exposure")
        exclude_scaffolds = external_spec.get("exclude_scaffold_overlap", False)
        if type(exclude_scaffolds) is not bool:
            raise PreparationError("external_cohort.exclude_scaffold_overlap must be boolean")
        external_input = binding(external_csv)
        external_fields, external_rows = read_rows(external_csv, spec["target_column"])
        if "partition" in external_fields:
            raise PreparationError("External input already has partition; supply the original external cohort")
        external_mapping = group_rows(external_rows, "declared", include_scaffolds=exclude_scaffolds, cohort="external")
        for item in external_mapping:
            item["partition"] = "external"
    elif external_spec is not None:
        raise PreparationError("preparation.external_cohort requires an explicit external CSV")
    fields, rows = read_rows(curated_csv, spec["target_column"], grouping != "declared")
    if "partition" in fields:
        raise PreparationError("Curated input already has partition; do not reshuffle an existing benchmark")
    input_rows = len(rows)
    task_ids = {row["task_id"] for row in rows}
    if {row["task_id"] for row in external_rows} - task_ids:
        raise PreparationError("External tasks must be represented by the development candidates")
    if task_type == "classification":
        for row in rows + external_rows:
            if float(row[spec["target_column"]]) not in (0.0, 1.0):
                raise PreparationError("Classification requires binary labels 0/1: " + row["output_row_id"])
    mapping = group_rows(rows, grouping, empty_scaffold, exclude_scaffolds)
    if external_rows:
        exclusions = external_exclusions(mapping, external_mapping, exclude_scaffolds)
    excluded_ids = {row["output_row_id"] for row in exclusions}
    rows = [row for row in rows if row["output_row_id"] not in excluded_ids]
    if {row["output_row_id"] for row in rows} & {row["output_row_id"] for row in external_rows}:
        raise PreparationError("Surviving candidate/external output_row_id collision")
    protected = [curated_csv, contract_path] + [x["path"] for x in evidence]
    if external_input:
        protected.append(external_csv)
    out = output_folder(output_dir, protected)
    exclusion_fields = ["output_row_id", "task_id", "declared_group_id", "effective_group_id", "canonical_parent_smiles", "reasons", "matched_external_row_ids"]
    if external_input:
        write_csv(out / "external_exclusions.csv", exclusions, exclusion_fields)
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
        for task in sorted(task_ids):
            current = task_rows[task]
            terminal_count = sum(r["task_id"] == task for r in terminal)
            if not current or not terminal_count:
                raise PreparationError(f"Infeasible task {task}: missing development or terminal records")
            classification_target = spec["target_column"] if task_type == "classification" else None
            folds = fold_sets(current, outer, seed, task + "/outer", classification_target, stratify)
            for fold in folds:
                train = set(fold["train_row_ids"])
                fold["inner_folds"] = fold_sets([r for r in current if r["output_row_id"] in train],
                                                inner, seed, task + "/inner/" + str(fold["fold"]), classification_target, stratify)
            tasks[task] = {"row_ids": [r["output_row_id"] for r in current], "outer_folds": folds}
            counts[task] = {"development": len(current), "terminal": terminal_count}
            if external_input:
                counts[task].update(external=sum(row["task_id"] == task for row in external_rows),
                                    excluded=sum(row["task_id"] == task for row in exclusions))
    except PreparationError as error:
        dump(out / "feasibility.json", {"status": "blocked", "reason": str(error),
                                      "input_rows": input_rows, "excluded_rows": len(exclusions)})
        raise
    write_csv(out / "development.csv", development, fields + ["partition"])
    write_csv(out / "sealed_terminal.csv", terminal, fields + ["partition"])
    membership_fields = ["output_row_id", "task_id", "group_id", "partition"]
    write_csv(out / "membership.csv", [{k: r[k] for k in membership_fields} for r in rows], membership_fields)
    partitions = {row["output_row_id"]: row["partition"] for row in rows}
    for item in mapping:
        item["partition"] = partitions.get(item["output_row_id"], "excluded")
    mapping_fields = ["cohort", "output_row_id", "task_id", "declared_group_id", "effective_group_id", "canonical_parent_smiles",
                      "scaffold_smiles", "grouping_basis", "grouping_key", "partition"]
    write_csv(out / "grouping_mapping.csv", mapping + external_mapping, mapping_fields)
    write_csv(out / "development_grouping_mapping.csv", [item for item in mapping if item["partition"] == "development"], mapping_fields)
    dump(out / "folds.json", {"schema_version": VERSION, "tasks": tasks})
    dump(out / "exposure_ledger.json", {**exposure, "terminal_custodian": spec["terminal_custodian"],
        "claim": "Untouched status requires independent exposure review; random reassignment cannot erase past access."})
    dump(out / "unfitted_pipeline.json", spec["unfitted_transforms"])
    import rdkit
    manifest = {"schema_version": VERSION, "stage": "preprocessing", "status": "complete",
        **initial,
        "environment": {"rdkit": rdkit.__version__, "python": sys.version},
        "policy": spec, "input_rows": input_rows, "development_rows": len(development),
        "terminal_rows": len(terminal), "excluded_rows": len(exclusions), "task_counts": counts,
        "split_algorithm": "sha256(seed,scope,group); terminal ceil(fraction*groups); " +
                           ("development-only StratifiedGroupKFold nested folds" if stratify else "group-round-robin nested folds"),
        "grouping_policy": grouping, "grouping_mapping": binding(out / "grouping_mapping.csv"),
        "development_grouping_mapping": binding(out / "development_grouping_mapping.csv"),
        "task_type": task_type, "stratify": stratify,
        "development": binding(out / "development.csv"), "sealed_terminal": binding(out / "sealed_terminal.csv"),
        "folds": binding(out / "folds.json"), "membership": binding(out / "membership.csv"),
        "exposure_ledger": binding(out / "exposure_ledger.json"), "unfitted_pipeline": binding(out / "unfitted_pipeline.json"),
        "checks": ["all curated rows assigned once or explicitly excluded for external overlap", "global terminal group disjointness",
                   "per-task outer and inner group disjointness", "no fitted transformations"],
        "limitations": ["Group-count balance does not guarantee row/target balance.",
                        "Filesystem secrecy requires custodian access controls; this manifest has no terminal labels."]}
    if stratify:
        import sklearn
        manifest["stratification"] = {"algorithm": "sklearn.model_selection.StratifiedGroupKFold", "sklearn": sklearn.__version__,
                                      "shuffle": True, "seed_derivation": "first 4 SHA-256 bytes of compact JSON [seed,scope], big-endian",
                                      "classes": [0, 1], "scope": "development folds only; terminal reservation is label-blind"}
    if external_input:
        shutil.copyfile(external_csv, out / "sealed_external.csv")
        dump(out / "external_exposure_ledger.json", {**external_exposure, "cohort": "external", "input": external_input,
            "terminal_custodian": spec["terminal_custodian"],
            "claim": "External origin does not establish untouched status; independent exposure review is required."})
        manifest["external_cohort"] = {"input": external_input, "artifact": binding(out / "sealed_external.csv"),
            "rows": len(external_rows), "prior_exposure": external_exposure,
            "exposure_ledger": binding(out / "external_exposure_ledger.json"), "exclusions": binding(out / "external_exclusions.csv"),
            "excluded_development_candidates": len(exclusions),
            "overlap_policy": {"identity": "canonical non-stereochemical parent_smiles", "exclude_scaffold_overlap": exclude_scaffolds}}
        if manifest["external_cohort"]["artifact"]["sha256"] != external_input["sha256"]:
            raise PreparationError("External cohort changed during preparation")
    manifest["custodian_only"] = [initial["input"], manifest["sealed_terminal"], manifest["grouping_mapping"]]
    if external_input:
        manifest["custodian_only"].extend(manifest["external_cohort"][key] for key in ("input", "artifact", "exposure_ledger", "exclusions"))
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
    if external_input and binding(external_csv) != external_input:
        raise PreparationError("External input changed during preparation")
    dump(out / "preprocessing_manifest.json", manifest)
    return manifest


def fingerprint_generator(name, parameters):
    from rdkit.Chem import rdFingerprintGenerator as F, MACCSkeys
    p = dict(parameters)
    morgan = {"Morgan2": (2, False, False), "Morgan3": (3, False, False),
              "Morgan2Count": (2, False, True), "Morgan3Count": (3, False, True),
              "FCFP2": (2, True, False), "FCFP3": (3, True, False),
              "FCFP2Count": (2, True, True), "FCFP3Count": (3, True, True)}
    method, invariants = "GetFingerprint", None
    def take(key, kind):
        value = p.pop(key, None)
        if kind is bool:
            if type(value) is not bool:
                raise PreparationError(name + " requires boolean " + key)
        else:
            integer(value, name + "." + key)
        return value
    if name == "MACCS":
        generator, width = MACCSkeys.GenMACCSKeys, 167
        method = "GenMACCSKeys"
        recipe = {"algorithm": "RDKit MACCS keys", "factory": "rdkit.Chem.MACCSkeys.GenMACCSKeys", "arguments": {}, "unused_bit": 0}
    else:
        width = take("n_bits", int)
        if name in morgan:
            radius, feature_invariants, counts = morgan[name]
            args = dict(radius=radius, fpSize=width, includeChirality=take("use_chirality", bool),
                        countSimulation=False, useBondTypes=True, includeRingMembership=True,
                        onlyNonzeroInvariants=False, includeRedundantEnvironments=False)
            native_args = dict(args)
            if feature_invariants:
                native_args["atomInvariantsGenerator"] = F.GetMorganFeatureAtomInvGen()
                invariants = {"factory": "GetMorganFeatureAtomInvGen", "arguments": {}}
            native = F.GetMorganGenerator(**native_args)
            factory = "GetMorganGenerator"
            if counts:
                method = "GetCountFingerprint"
        elif name == "RDKitpath":
            args = dict(fpSize=width, minPath=take("min_path", int), maxPath=take("max_path", int),
                        useHs=True, branchedPaths=True, useBondOrder=True, countSimulation=False, numBitsPerFeature=2)
            if args["minPath"] > args["maxPath"]:
                raise PreparationError("min_path exceeds max_path")
            native = F.GetRDKitFPGenerator(**args)
            factory = "GetRDKitFPGenerator"
        elif name == "atom_pair":
            args = dict(fpSize=width, minDistance=take("min_distance", int), maxDistance=take("max_distance", int),
                        includeChirality=take("use_chirality", bool), use2D=True, countSimulation=False)
            if args["minDistance"] > args["maxDistance"]:
                raise PreparationError("min_distance exceeds max_distance")
            native = F.GetAtomPairGenerator(**args)
            factory = "GetAtomPairGenerator"
        elif name == "torsion":
            args = dict(fpSize=width, torsionAtomCount=take("torsion_atom_count", int),
                        includeChirality=take("use_chirality", bool), countSimulation=False)
            native = F.GetTopologicalTorsionGenerator(**args)
            factory = "GetTopologicalTorsionGenerator"
        elif name == "Avalon":
            from rdkit.Avalon import pyAvalonTools
            args = dict(nBits=width, isQuery=False, resetVect=False, bitFlags=15761407)
            generator = lambda mol: pyAvalonTools.GetAvalonFP(mol, **args)
            factory, method = "rdkit.Avalon.pyAvalonTools.GetAvalonFP", "GetAvalonFP"
        else:
            raise PreparationError("Unsupported representation: " + name)
        if name != "Avalon":
            generator = getattr(native, method)
        recipe = {"algorithm": name, "factory": factory, "arguments": args}
        if name != "Avalon":
            recipe["generator_info"] = native.GetInfoString()
        if invariants is not None:
            recipe["atom_invariants_generator"] = invariants
    if p:
        raise PreparationError("Unsupported family parameters: " + str(sorted(p)))
    recipe["method"] = method
    recipe["dtype"] = "uint32" if method == "GetCountFingerprint" else "uint8"
    recipe["representation"] = ("hashed nonnegative counts (collisions accumulate; no count simulation)" if method == "GetCountFingerprint"
                                else "binary bits (hashed collisions retained; no count simulation)")
    return generator, width, recipe


def fingerprint_dtype(recipe):
    if recipe["method"] not in {"GetFingerprint", "GetCountFingerprint", "GenMACCSKeys", "GetAvalonFP"}:
        raise PreparationError("Unsupported fingerprint generation method")
    expected = "uint32" if recipe["method"] == "GetCountFingerprint" else "uint8"
    if recipe["dtype"] != expected:
        raise PreparationError("Fingerprint dtype differs from generator method")
    return expected


def fingerprint_feature_names(family, width, recipe):
    kind = "count" if fingerprint_dtype(recipe) == "uint32" else "bit"
    return [f"{family}:{kind}:{index}" for index in range(width)]


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
        names = [entry[0] for entry in inventory] if name == "RDKit2D" else fingerprint_feature_names(name, generators[name][1], generators[name][2])
        matrix = np.empty((len(rows), len(names)), dtype=np.float64 if name == "RDKit2D" else fingerprint_dtype(generators[name][2]))
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
                    width = vector.GetLength() if generators[name][2]["method"] == "GetCountFingerprint" else vector.GetNumBits()
                    if width != len(names):
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
                        "Fixed hashed bit/count fingerprints retain collisions; no learned representations."]}
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
    split.add_argument("--external", help="Original external cohort CSV; requires an explicit external exposure policy")
    feature = commands.add_parser("features")
    feature.add_argument("--development", required=True)
    feature.add_argument("--split-manifest", required=True)
    for command in (split, feature):
        command.add_argument("--contract", required=True)
        command.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        result = prepare_splits(args.curated, args.contract, args.output_dir, args.curation_manifest, args.external) if args.command == "split" else generate_features(
            args.development, args.split_manifest, args.contract, args.output_dir)
        print(json.dumps({"status": result["status"], "stage": result["stage"]}))
        return 0
    except (PreparationError, KeyError, OSError, ImportError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
