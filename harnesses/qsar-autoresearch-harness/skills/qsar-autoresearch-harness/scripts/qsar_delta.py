"""Read-only, row-identity CSV delta diagnostics, never approval or hash bypass.

Effective identity covers explicitly declared columns and exact decoded strings,
not numerical tolerance, chemical equivalence, or complete pipeline inputs. Row
and header order, BOM and CSV quoting/newlines do not affect content identity.
Other CSV columns are also checked and reported separately, never hidden by a
no-op diagnostic. Every row is retained regardless of eligibility.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

SCHEMA_VERSION = "1.0"


class DeltaError(ValueError):
    pass


def _digest(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _binding(path, data=None):
    path = Path(path).resolve()
    if data is None:
        data = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def _columns(target_column, structure_column, task_column, group_column, eligibility_column):
    columns = {"row_id": "output_row_id", "target": target_column, "structure": structure_column,
               "task": task_column, "group": group_column, "eligibility": eligibility_column}
    configured = [column for role, column in columns.items() if role != "eligibility" or column is not None]
    if any(not isinstance(column, str) or not column.strip() for column in configured):
        raise DeltaError("Comparison column names must be nonempty strings")
    if len(set(configured)) != len(configured):
        raise DeltaError("Comparison roles must use distinct columns")
    return columns


def _snapshot(path, columns):
    path = Path(path).resolve()
    data = path.read_bytes()
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        fields = next(reader, [])
        if not fields or len(set(fields)) != len(fields) or any(not field.strip() for field in fields):
            raise DeltaError("Empty or duplicate CSV columns: " + str(path))
        required = {column for column in columns.values() if column is not None}
        if not required.issubset(fields):
            raise DeltaError("Missing comparison columns: " + ", ".join(sorted(required - set(fields))))
        rows = {}
        for values in reader:
            if not values:  # Blank physical records are CSV layout, not data rows.
                continue
            if len(values) != len(fields):
                raise DeltaError(f"Malformed CSV row width at line {reader.line_num}: {path}")
            row = dict(zip(fields, values))
            rid = row["output_row_id"]
            if not rid.strip():
                raise DeltaError(f"Empty output_row_id at line {reader.line_num}: {path}")
            if rid in rows:
                raise DeltaError("Duplicate output_row_id: " + rid)
            rows[rid] = row
    except (csv.Error, UnicodeError) as exc:
        raise DeltaError("Invalid UTF-8 CSV: " + str(path) + ": " + str(exc)) from exc
    selected = [{role: rows[rid][column] for role, column in columns.items() if column is not None}
                for rid in sorted(rows)]
    complete = [rows[rid] for rid in sorted(rows)]
    return {"binding": _binding(path, data), "fields": fields, "rows": rows,
            "effective_content_sha256": _digest({"columns": columns, "rows": selected}),
            "csv_semantics_sha256": _digest({"columns": sorted(fields), "rows": complete}),
            "other_columns": sorted(set(fields) - required)}


def _verify_inputs(bindings):
    for expected in bindings:
        if _binding(expected["path"]) != expected:
            raise DeltaError("Input changed during delta comparison: " + expected["path"])


def _input_bindings(report):
    return list(report["input_bindings"].values()) + [binding for side in ("old", "new")
                                                       if (binding := report["policy_bindings"][side]) is not None]


def compare_datasets(old_csv, new_csv, *, target_column, structure_column="parent_smiles",
                     task_column="task_id", group_column="group_id", eligibility_column=None,
                     old_policy=None, new_policy=None):
    """Compare immutable input snapshots without approving or modifying anything.

    Policies, when supplied, are file-byte bindings reported separately from data
    identity. An omitted eligibility column means no eligibility is inferred;
    an eligibility-like extra column still participates in other-column checks.
    Targets/structures are exact strings: '1' versus '1.0', or equivalent SMILES
    spellings, conservatively count as changes rather than scientific equivalence.
    """
    columns = _columns(target_column, structure_column, task_column, group_column, eligibility_column)
    old_policy_binding = _binding(old_policy) if old_policy is not None else None
    new_policy_binding = _binding(new_policy) if new_policy is not None else None
    old, new = _snapshot(old_csv, columns), _snapshot(new_csv, columns)
    old_ids, new_ids = set(old["rows"]), set(new["rows"])
    common = sorted(old_ids & new_ids)
    changes = {}
    for role, name in (("target", "changed_targets"), ("structure", "changed_structures"),
                       ("task", "changed_tasks"), ("group", "changed_groups"),
                       ("eligibility", "changed_eligibility")):
        column = columns[role]
        changes[name] = [] if column is None else [
            {"output_row_id": rid, "old": old["rows"][rid][column], "new": new["rows"][rid][column]}
            for rid in common if old["rows"][rid][column] != new["rows"][rid][column]]
    old_other, new_other = set(old["other_columns"]), set(new["other_columns"])
    other_changes = {"added_columns": sorted(new_other - old_other),
                     "removed_columns": sorted(old_other - new_other),
                     "changed_cells": [
                         {"output_row_id": rid, "column": column,
                          "old": old["rows"][rid][column], "new": new["rows"][rid][column]}
                         for rid in common for column in sorted(old_other & new_other)
                         if old["rows"][rid][column] != new["rows"][rid][column]]}
    old_policy_hash = old_policy_binding["sha256"] if old_policy_binding else None
    new_policy_hash = new_policy_binding["sha256"] if new_policy_binding else None
    policy_changed = old_policy_hash != new_policy_hash
    byte_identity = old["binding"]["sha256"] == new["binding"]["sha256"]
    declared_identity = old["effective_content_sha256"] == new["effective_content_sha256"]
    csv_identity = old["csv_semantics_sha256"] == new["csv_semantics_sha256"]
    no_op = csv_identity and not policy_changed
    report = {"schema_version": SCHEMA_VERSION, "mode": "dataset_delta_diagnostic",
              "input_bindings": {"old": old["binding"], "new": new["binding"]},
              "comparison": {"columns": columns, "cell_semantics": "exact decoded strings; row/header order independent",
                             "identity_scope": "declared columns only; not complete pipeline-input equality",
                             "eligibility_handling": "all rows retained; no filtering or inferred eligibility",
                             "numeric_or_chemical_equivalence": "not inferred"},
              "row_counts": {"old": len(old_ids), "new": len(new_ids), "common": len(common)},
              "bytes_identical": byte_identity, "effective_content_identical": declared_identity,
              "effective_content_sha256": {"old": old["effective_content_sha256"], "new": new["effective_content_sha256"]},
              "complete_csv_semantics_identical": csv_identity,
              "csv_semantics_sha256": {"old": old["csv_semantics_sha256"], "new": new["csv_semantics_sha256"]},
              "added_ids": sorted(new_ids - old_ids), "removed_ids": sorted(old_ids - new_ids),
              **changes, "other_column_changes": other_changes,
              "policy_bindings": {"old": old_policy_binding, "new": new_policy_binding, "changed": policy_changed,
                                  "comparison": "supplied file bytes only; absent policies remain unassessed"},
              "no_op_diagnostic": {"is_no_op": no_op, "advisory_only": True,
                                   "scope": "CSV semantics and supplied policy bindings only",
                                   "message": "No observed semantic change" if no_op else "Observed data or policy-binding change"},
              "approval_granted": False, "hash_bypass_authorized": False,
              "retraining_decision": "Not made; exact input-hash invalidation and all existing scientific/review gates still apply"}
    _verify_inputs(_input_bindings(report))
    return report


def write_delta(old_csv, new_csv, destination, **options):
    """Write only delta.json into a new directory, preserving all original files."""
    report = compare_datasets(old_csv, new_csv, **options)
    out = Path(destination).resolve()
    bindings = _input_bindings(report)
    for expected in bindings:
        source = Path(expected["path"])
        if out == source or source.is_relative_to(out) or out.is_relative_to(source):
            raise DeltaError("Destination overlaps a protected input")
    _verify_inputs(bindings)
    text = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise DeltaError("Destination must be fresh; version outputs") from exc
    artifact = out / "delta.json"
    created = False
    try:
        with artifact.open("x", encoding="utf-8", newline="\n") as stream:
            created = True
            stream.write(text)
        _verify_inputs(bindings)
    except BaseException:
        # Remove only our newly-created artifact, never any input or pre-existing
        # directory. Do not leave apparently complete evidence after a race.
        if created:
            artifact.unlink(missing_ok=True)
        if not any(out.iterdir()):
            out.rmdir()
        raise
    return _binding(artifact)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", required=True, help="Old CSV input")
    parser.add_argument("--new", required=True, help="New CSV input")
    parser.add_argument("--target-column", required=True)
    parser.add_argument("--structure-column", default="parent_smiles")
    parser.add_argument("--task-column", default="task_id")
    parser.add_argument("--group-column", default="group_id")
    parser.add_argument("--eligibility-column")
    parser.add_argument("--old-policy", help="Optional old policy/contract file to bind")
    parser.add_argument("--new-policy", help="Optional new policy/contract file to bind")
    parser.add_argument("--out", required=True, help="New directory for delta.json only")
    args = parser.parse_args(argv)
    try:
        receipt = write_delta(args.old, args.new, args.out, target_column=args.target_column,
                              structure_column=args.structure_column, task_column=args.task_column,
                              group_column=args.group_column, eligibility_column=args.eligibility_column,
                              old_policy=args.old_policy, new_policy=args.new_policy)
        print(json.dumps(receipt, ensure_ascii=False, allow_nan=False))
    except (DeltaError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
