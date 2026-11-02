"""Training-only RDKit Morgan applicability domains, independent of sklearn.

Inputs are already selected parent SMILES: canonicalization removes atom maps,
not fragments, stereochemistry, isotopes, salts, or tautomers. References are
unique canonical isomeric parents. No target or evaluation data enters fitting.
Callers supply the reviewed training cohort. Hashes verify integrity and replay,
not independent source validation or scientific approval.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

SCHEMA_VERSION = "1.0"
RECIPE_VERSION = "rdkit-morgan-parent-bits-v1"


class DomainError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _chemistry():
    try:
        from rdkit import Chem, DataStructs, rdBase
        from rdkit.Chem import rdFingerprintGenerator
    except ImportError as exc:
        raise DomainError("RDKit is required for the training-reference applicability domain") from exc
    return Chem, DataStructs, rdBase, rdFingerprintGenerator


def _integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise DomainError(f"{name} must be an explicit integer >= {minimum}")
    return value


def _unit_interval(value, name):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise DomainError(name + " must be a finite number in [0, 1]")
    return float(value)


def _recipe(value):
    required = {"radius", "n_bits", "use_chirality", "similarity", "min_references", "cutoff"}
    if not isinstance(value, Mapping) or not required.issubset(value) or set(value) - required - {"k"}:
        raise DomainError("Recipe requires radius, n_bits, use_chirality, similarity, min_references and cutoff only (plus k for knn_mean)")
    result = dict(value)
    _integer(result["radius"], "radius", 0)
    _integer(result["n_bits"], "n_bits")
    _integer(result["min_references"], "min_references")
    if type(result["use_chirality"]) is not bool:
        raise DomainError("use_chirality must be an explicit boolean")
    if result["similarity"] not in ("max", "knn_mean"):
        raise DomainError("similarity must be max or knn_mean")
    if result["similarity"] == "knn_mean":
        _integer(result.get("k"), "k")
    elif "k" in result:
        raise DomainError("k is supported only for knn_mean")
    cutoff = result["cutoff"]
    if not isinstance(cutoff, Mapping):
        raise DomainError("cutoff must declare fixed or training_quantile")
    method = cutoff.get("method")
    parameter = "value" if method == "fixed" else "quantile" if method == "training_quantile" else None
    if parameter is None or set(cutoff) != {"method", parameter}:
        raise DomainError("cutoff must be {method:fixed,value} or {method:training_quantile,quantile}")
    result["cutoff"] = {"method": method, parameter: _unit_interval(cutoff[parameter], "cutoff " + parameter)}
    return result


def _sequence(values, name):
    if isinstance(values, (str, bytes, Mapping)):
        raise DomainError(name + " must be a row-aligned sequence")
    try:
        return list(values)
    except TypeError as exc:
        raise DomainError(name + " must be a row-aligned sequence") from exc


def _row_ids(values, count):
    ids = _sequence(values, "row_ids")
    if len(ids) != count or any(not isinstance(rid, str) or not rid.strip() for rid in ids):
        raise DomainError("row_ids must contain one nonempty string per structure")
    if len(set(ids)) != len(ids):
        raise DomainError("Duplicate row identity")
    return ids


def _parent(value):
    Chem, _, _, _ = _chemistry()
    if not isinstance(value, str) or not value.strip():
        return None, None
    try:
        mol = Chem.MolFromSmiles(value)
        if mol is None or mol.GetNumAtoms() == 0:
            return None, None
        for atom in mol.GetAtoms():
            atom.SetAtomMapNum(0)
        return Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True), mol
    except (ValueError, RuntimeError):
        return None, None


def _generator(recipe):
    _, _, _, generators = _chemistry()
    try:
        return generators.GetMorganGenerator(radius=recipe["radius"], fpSize=recipe["n_bits"],
                                             includeChirality=recipe["use_chirality"],
                                             countSimulation=False, useBondTypes=True,
                                             includeRingMembership=True, onlyNonzeroInvariants=False,
                                             includeRedundantEnvironments=False)
    except (ValueError, RuntimeError, OverflowError) as exc:
        raise DomainError("Unsupported RDKit Morgan recipe: " + str(exc)) from exc


def _required(recipe):
    return max(recipe["min_references"], recipe.get("k", 1))


def _similarity(query, references, recipe):
    _, DataStructs, _, _ = _chemistry()
    values = DataStructs.BulkTanimotoSimilarity(query, references)
    if recipe["similarity"] == "max":
        return float(max(values))
    return math.fsum(sorted(values, reverse=True)[:recipe["k"]]) / recipe["k"]


def fit(training_structures, row_ids, recipe):
    """Fit only the supplied training parents; infeasible references fail closed."""
    recipe = _recipe(recipe)
    structures = _sequence(training_structures, "training_structures")
    ids = _row_ids(row_ids, len(structures))
    if not structures:
        raise DomainError("No training structures")
    generator = _generator(recipe)
    training, parents = [], {}
    for rid, structure in zip(ids, structures):
        canonical, mol = _parent(structure)
        if canonical is None:
            raise DomainError("Invalid training structure: " + rid)
        training.append({"row_id": rid, "parent_smiles": canonical})
        if canonical not in parents:
            parents[canonical] = {"fingerprint": generator.GetFingerprint(mol), "row_ids": []}
        parents[canonical]["row_ids"].append(rid)
    keys = sorted(parents)
    count = len(keys)
    leave_parent_out = recipe["cutoff"]["method"] == "training_quantile"
    needed = _required(recipe) + int(leave_parent_out)
    if count < needed:
        raise DomainError(f"Infeasible domain: {count} distinct training parents; {needed} required")
    fingerprints = [parents[key]["fingerprint"] for key in keys]
    references = [{"parent_smiles": key, "row_ids": parents[key]["row_ids"],
                   "on_bits": list(parents[key]["fingerprint"].GetOnBits())} for key in keys]
    if leave_parent_out:
        similarities = [{"parent_smiles": key, "reference_count": count - 1,
                         "similarity": _similarity(fingerprints[i], fingerprints[:i] + fingerprints[i + 1:], recipe)}
                        for i, key in enumerate(keys)]
        quantile = recipe["cutoff"]["quantile"]
        ordered = sorted(row["similarity"] for row in similarities)
        cutoff = ordered[math.floor((len(ordered) - 1) * quantile)]
        calibration = {"method": "training_quantile", "quantile": quantile,
                       "quantile_rule": "lower_order_statistic", "exclusion": "canonical_parent",
                       "population": "distinct_training_parents", "similarities": similarities}
    else:
        cutoff = recipe["cutoff"]["value"]
        calibration = {"method": "fixed", "source": "recipe", "value": cutoff}
    _, _, rdBase, _ = _chemistry()
    artifact = {"schema_version": SCHEMA_VERSION, "artifact_type": "training_reference_domain",
                "recipe_version": RECIPE_VERSION, "rdkit_version": rdBase.rdkitVersion,
                "recipe": recipe, "recipe_sha256": _digest(recipe),
                "training_rows": training, "training_sha256": _digest(training),
                "references": references, "references_sha256": _digest(references),
                "cutoff": cutoff, "calibration": calibration, "calibration_sha256": _digest(calibration)}
    artifact["artifact_sha256"] = _digest(artifact)
    return ApplicabilityDomain._create(artifact)


def _validate_artifact(value):
    fields = {"schema_version", "artifact_type", "recipe_version", "rdkit_version", "recipe", "recipe_sha256",
              "training_rows", "training_sha256", "references", "references_sha256", "cutoff", "calibration",
              "calibration_sha256", "artifact_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise DomainError("Incomplete or unsupported domain artifact")
    try:
        if value["artifact_sha256"] != _digest({k: v for k, v in value.items() if k != "artifact_sha256"}):
            raise DomainError("Domain artifact hash mismatch")
        for field, hash_field in (("recipe", "recipe_sha256"), ("training_rows", "training_sha256"),
                                  ("references", "references_sha256"), ("calibration", "calibration_sha256")):
            if value[hash_field] != _digest(value[field]):
                raise DomainError("Domain " + field + " hash mismatch")
        _, _, rdBase, _ = _chemistry()
        if value["schema_version"] != SCHEMA_VERSION or value["artifact_type"] != "training_reference_domain":
            raise DomainError("Unsupported domain schema")
        if value["recipe_version"] != RECIPE_VERSION or value["rdkit_version"] != rdBase.rdkitVersion:
            raise DomainError("Domain RDKit/recipe version mismatch; refit under the reviewed runtime")
        training = value["training_rows"]
        if not isinstance(training, list) or any(not isinstance(row, dict) or set(row) != {"row_id", "parent_smiles"}
                                                 for row in training):
            raise DomainError("Invalid training row identities")
        # Rebuild even after hashes pass: self-consistent edited bits or thresholds
        # are not accepted as a fitted domain for the declared recipe/training rows.
        expected = fit([row["parent_smiles"] for row in training], [row["row_id"] for row in training], value["recipe"])
        if _json(expected.to_dict()) != _json(value):
            raise DomainError("Domain references/cutoff do not reproduce from training rows and recipe")
    except (KeyError, TypeError, OverflowError) as exc:
        raise DomainError("Malformed domain artifact") from exc
    except ValueError as exc:
        if isinstance(exc, DomainError):
            raise
        raise DomainError("Malformed domain artifact: " + str(exc)) from exc
    return value


class ApplicabilityDomain:
    def __init__(self, artifact):
        other = self._create(_validate_artifact(artifact))
        self.__dict__.update(other.__dict__)

    @classmethod
    def _create(cls, artifact):
        _, DataStructs, _, _ = _chemistry()
        model = object.__new__(cls)
        model._artifact = json.loads(_json(artifact))
        model._generator = _generator(model._artifact["recipe"])
        model._fingerprints = []
        for reference in model._artifact["references"]:
            vector = DataStructs.ExplicitBitVect(model._artifact["recipe"]["n_bits"])
            vector.SetBitsFromList(reference["on_bits"])
            model._fingerprints.append(vector)
        model._training = {row["row_id"]: row["parent_smiles"] for row in model._artifact["training_rows"]}
        return model

    @property
    def recipe(self):
        return json.loads(_json(self._artifact["recipe"]))

    @property
    def cutoff(self):
        return self._artifact["cutoff"]

    @property
    def reference_count(self):
        return len(self._fingerprints)

    @property
    def training_row_ids(self):
        return [row["row_id"] for row in self._artifact["training_rows"]]

    def to_dict(self):
        return json.loads(_json(self._artifact))

    def __reduce__(self):
        # RDKit generators are not pickleable. Rebuild from the validated,
        # version-bound training artifact instead of serializing native objects.
        return type(self), (self.to_dict(),)

    def save(self, path):
        return save(self, path)

    def score(self, query_structures, row_ids=None, *, exclude_own_identity=False):
        """Retain every query, including invalid/OOD rows; never refit the cutoff.

        Exclusion is opt-in for training diagnostics and removes the whole matching
        canonical parent, including its replicate row IDs. Without it, an identical
        query remains a valid reference match even if its row ID was used in fitting.
        """
        if type(exclude_own_identity) is not bool:
            raise DomainError("exclude_own_identity must be a boolean")
        structures = _sequence(query_structures, "query_structures")
        ids = [None] * len(structures) if row_ids is None else _row_ids(row_ids, len(structures))
        recipe = self._artifact["recipe"]
        results, cache = [], {}
        for rid, structure in zip(ids, structures):
            canonical, mol = _parent(structure)
            result = {"row_id": rid, "similarity": None, "in_domain": None,
                      "reason": None, "reference_count": self.reference_count, "cutoff": self.cutoff}
            if canonical is None:
                result["reason"] = "invalid_query_structure"
            else:
                if exclude_own_identity and rid in self._training and self._training[rid] != canonical:
                    raise DomainError("Training diagnostic row identity/structure mismatch: " + rid)
                references = [fp for ref, fp in zip(self._artifact["references"], self._fingerprints)
                              if not exclude_own_identity or ref["parent_smiles"] != canonical]
                result["reference_count"] = len(references)
                if len(references) < _required(recipe):
                    result["reason"] = "insufficient_references"
                else:
                    if canonical not in cache:
                        cache[canonical] = self._generator.GetFingerprint(mol)
                    similarity = _similarity(cache[canonical], references, recipe)
                    result.update(similarity=similarity, in_domain=similarity >= self.cutoff,
                                  reason="within_cutoff" if similarity >= self.cutoff else "below_cutoff")
            results.append(result)
        return results


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DomainError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_keys)
    except (ValueError, UnicodeError) as exc:
        raise DomainError("Invalid JSON: " + str(exc)) from exc


def _write_new(path, value):
    path = Path(path).resolve()
    text = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError as exc:
        raise DomainError("Destination already exists; version outputs") from exc
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def save(model, path):
    if not isinstance(model, ApplicabilityDomain):
        raise DomainError("save requires a fitted ApplicabilityDomain")
    return _write_new(path, _validate_artifact(model.to_dict()))


def load(path):
    return ApplicabilityDomain(_load_json(path))


def _read_structures(path, structure_column, row_id_column, require_ids):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields) or not fields or any(not field.strip() for field in fields):
            raise DomainError("Empty or duplicate CSV header")
        if structure_column not in fields or (require_ids and row_id_column not in fields):
            raise DomainError("Missing structure or row identity column")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise DomainError("Malformed CSV row width")
    return [row[structure_column] for row in rows], ([row[row_id_column] for row in rows] if row_id_column in fields else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fitting = commands.add_parser("fit")
    fitting.add_argument("--training", required=True)
    fitting.add_argument("--recipe", required=True, help="JSON containing the explicit domain recipe")
    scoring = commands.add_parser("score")
    scoring.add_argument("--artifact", required=True)
    scoring.add_argument("--queries", required=True)
    scoring.add_argument("--exclude-own-identity", action="store_true", help="Training diagnostics only")
    for command in (fitting, scoring):
        command.add_argument("--structure-column", default="parent_smiles")
        command.add_argument("--row-id-column", default="output_row_id")
        command.add_argument("--out", required=True, help="Fresh JSON output file")
    args = parser.parse_args(argv)
    try:
        if args.command == "fit":
            structures, ids = _read_structures(args.training, args.structure_column, args.row_id_column, True)
            receipt = fit(structures, ids, _load_json(args.recipe)).save(args.out)
        else:
            model = load(args.artifact)
            structures, ids = _read_structures(args.queries, args.structure_column, args.row_id_column, False)
            receipt = _write_new(args.out, {"domain_artifact_sha256": model.to_dict()["artifact_sha256"],
                                            "scores": model.score(structures, ids, exclude_own_identity=args.exclude_own_identity)})
        print(json.dumps(receipt, ensure_ascii=False, allow_nan=False))
    except (DomainError, OSError, csv.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
