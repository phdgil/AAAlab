#!/usr/bin/env python3
"""Read-only structural and provenance validation for OpenMRA XLSX reports."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET


REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
STANDARD_EFFECTS = set(range(10, 100, 10))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference)
    if not letters:
        return -1
    result = 0
    for char in letters.group(0).upper():
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def _resolve_part(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath(base).parent.joinpath(target))


def _parse_xml(archive: zipfile.ZipFile, member: str) -> ET.Element:
    return ET.fromstring(archive.read(member))


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _parse_xml(archive, "xl/sharedStrings.xml")
    return [
        "".join(node.text or "" for node in item.iter() if _local_name(node.tag) == "t")
        for item in root
        if _local_name(item.tag) == "si"
    ]


def _sheet_rows(
    archive: zipfile.ZipFile, member: str, shared: list[str]
) -> list[dict[int, str]]:
    root = _parse_xml(archive, member)
    rows: list[dict[int, str]] = []
    for row_node in (node for node in root.iter() if _local_name(node.tag) == "row"):
        row: dict[int, str] = {}
        for cell in (node for node in row_node if _local_name(node.tag) == "c"):
            index = _column_index(cell.attrib.get("r", ""))
            if index < 0:
                continue
            cell_type = cell.attrib.get("t", "")
            value_node = next(
                (node for node in cell if _local_name(node.tag) == "v"), None
            )
            if cell_type == "inlineStr":
                value = "".join(
                    node.text or "" for node in cell.iter() if _local_name(node.tag) == "t"
                )
            elif value_node is None:
                value = ""
            elif cell_type == "s":
                try:
                    value = shared[int(value_node.text or "")]
                except (ValueError, IndexError):
                    value = ""
            elif cell_type == "b":
                value = "TRUE" if value_node.text == "1" else "FALSE"
            else:
                value = value_node.text or ""
            row[index] = value
        rows.append(row)
    return rows


def _read_workbook(path: Path) -> tuple[dict[str, list[dict[int, str]]], list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
        missing = required - names
        if missing:
            raise ValueError(f"missing workbook part(s): {', '.join(sorted(missing))}")

        shared = _shared_strings(archive)
        workbook = _parse_xml(archive, "xl/workbook.xml")
        relationships = _parse_xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {
            rel.attrib.get("Id", ""): _resolve_part(
                "xl/workbook.xml", rel.attrib.get("Target", "")
            )
            for rel in relationships
            if _local_name(rel.tag) == "Relationship"
        }
        sheets: dict[str, list[dict[int, str]]] = {}
        for sheet in (node for node in workbook.iter() if _local_name(node.tag) == "sheet"):
            name = sheet.attrib.get("name", "")
            relationship_id = sheet.attrib.get(f"{{{REL_NS}}}id", "")
            member = targets.get(relationship_id, "")
            if name and member in names:
                sheets[name] = _sheet_rows(archive, member, shared)

        images = [
            name
            for name in names
            if name.startswith("xl/media/")
            and not name.endswith("/")
            and archive.getinfo(name).file_size > 0
        ]
        return sheets, sorted(images)


def _find_sheet(
    sheets: dict[str, list[dict[int, str]]], name: str
) -> list[dict[int, str]] | None:
    expected = name.casefold()
    return next((rows for key, rows in sheets.items() if key.casefold() == expected), None)


def _find_label(rows: list[dict[int, str]], label: str) -> tuple[int, int] | None:
    expected = _norm(label)
    for row_index, row in enumerate(rows):
        for column, value in row.items():
            if _norm(value) == expected:
                return row_index, column
    return None


def _header_map(row: dict[int, str]) -> dict[str, int]:
    return {_norm(value): column for column, value in row.items() if _norm(value)}


def _section_table(
    rows: list[dict[int, str]], title: str
) -> tuple[dict[str, int], list[dict[int, str]]]:
    marker = _find_label(rows, title)
    if marker is None:
        return {}, []
    header_index = marker[0] + 1
    while header_index < len(rows) and not any(_norm(value) for value in rows[header_index].values()):
        header_index += 1
    if header_index >= len(rows):
        return {}, []
    header = _header_map(rows[header_index])
    data: list[dict[int, str]] = []
    for row in rows[header_index + 1 :]:
        if not any(_norm(value) for value in row.values()):
            if data:
                break
            continue
        data.append(row)
    return header, data


def _float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=2e-5, abs_tol=2e-6)


def _cell(row: dict[int, str], header: dict[str, int], *names: str) -> str:
    for name in names:
        column = header.get(_norm(name))
        if column is not None:
            return row.get(column, "")
    return ""


def _input_records(rows: list[dict[int, str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if "substance_id" in _header_map(row) and "rm_model" in _header_map(row)
        ),
        None,
    )
    if header_index is None:
        return []
    header = _header_map(rows[header_index])
    records = []
    for row in rows[header_index + 1 :]:
        if _norm(_cell(row, header, "row_type")) != "input":
            continue
        records.append({name: row.get(column, "") for name, column in header.items()})
    return records


def _report_dataset(rows: list[dict[int, str]]) -> list[dict[str, str]]:
    header, data = _section_table(rows, "Input dataset")
    records = []
    for row in data:
        substance_id = _cell(row, header, "Substance ID")
        if not substance_id:
            continue
        records.append(
            {
                "substance_id": substance_id,
                "substance_name": _cell(row, header, "Substance name"),
                "cas_no": _cell(row, header, "CAS No."),
                "composition(%)": _cell(row, header, "Mixture ratio"),
                "concentration_unit": _cell(row, header, "Unit"),
                "rm_model": _cell(row, header, "RM model"),
                "parameters": _cell(row, header, "Parameters"),
            }
        )
    return records


def _parse_parameters(value: str) -> tuple[dict[str, float], list[str]]:
    parsed: dict[str, float] = {}
    issues: list[str] = []
    for item in re.split(r"[,;]", value or ""):
        if not item.strip():
            continue
        if "=" not in item:
            issues.append(f"malformed parameter {item.strip()!r}")
            continue
        name, raw = item.split("=", 1)
        normalized_name = _norm(name)
        number = _float(raw)
        if not normalized_name or number is None or not math.isfinite(number):
            issues.append(f"invalid parameter {item.strip()!r}")
            continue
        if normalized_name in parsed:
            issues.append(f"duplicate parameter {normalized_name!r}")
            continue
        parsed[normalized_name] = number
    return parsed, issues


def _expected_parameters(record: dict[str, str]) -> tuple[set[str], dict[str, float]] | None:
    scale = 0.01 if _norm(record.get("effect_scale")) == "percent" else 1.0
    model = _norm(record.get("rm_model"))
    if model == "logistic4":
        mappings = {
            "amplitude": ("param_a", scale),
            "slope": ("param_b", 1.0),
            "ec50": ("param_c", 1.0),
            "bottom": ("param_d", scale),
        }
        required = set(mappings)
    elif model == "logistic3":
        mappings = {
            "amplitude": ("param_a", scale),
            "slope": ("param_b", 1.0),
            "ec50": ("param_c", 1.0),
        }
        required = set(mappings)
    else:
        return None

    result: dict[str, float] = {}
    for exported_name, (input_name, multiplier) in mappings.items():
        number = _float(record.get(input_name, ""))
        if number is not None:
            result[exported_name] = number * multiplier
    if model == "logistic3":
        # OpenMRA may include the fixed Logistic3 baseline in its standardized export.
        result["bottom"] = 0.0
    return required, result


def _compare_dataset(
    exported: list[dict[str, str]], source: list[dict[str, str]], errors: list[str]
) -> None:
    if not exported:
        errors.append("Report does not contain an Input dataset table.")
        return
    if not source:
        errors.append("Input workbook RM_INPUT contains no INPUT records.")
        return
    source_by_id = {_norm(item.get("substance_id")): item for item in source}
    exported_ids = {_norm(item.get("substance_id")) for item in exported}
    if exported_ids != set(source_by_id):
        errors.append("Report substance IDs do not match RM_INPUT.")
    if len(exported) != len(source):
        errors.append("Report and RM_INPUT contain different numbers of substances.")

    for item in exported:
        identifier = _norm(item.get("substance_id"))
        expected = source_by_id.get(identifier)
        if expected is None:
            continue
        label = item.get("substance_id") or "unknown substance"
        for report_key, input_key, description in (
            ("substance_name", "substance_name", "name"),
            ("cas_no", "cas_no", "CAS No."),
            ("rm_model", "rm_model", "RM model"),
            ("concentration_unit", "concentration_unit", "unit"),
        ):
            if _norm(item.get(report_key)) != _norm(expected.get(input_key)):
                errors.append(f"{label}: exported {description} does not match RM_INPUT.")
        report_ratio = _float(item.get("composition(%)"))
        input_ratio = _float(expected.get("composition(%)"))
        if (
            report_ratio is None
            or input_ratio is None
            or not _close(report_ratio, input_ratio)
        ):
            errors.append(f"{label}: exported mixture ratio does not match RM_INPUT.")

        parameter_contract = _expected_parameters(expected)
        if parameter_contract is None:
            errors.append(
                f"{label}: RM model {expected.get('rm_model')!r} is unsupported by this validator."
            )
            continue
        required_parameters, expected_parameters = parameter_contract
        parameters, parameter_issues = _parse_parameters(item.get("parameters", ""))
        if parameter_issues:
            errors.extend(f"{label}: {issue}." for issue in parameter_issues)
        unknown = set(parameters) - set(expected_parameters)
        if unknown:
            errors.append(
                f"{label}: exported Parameters contains unknown field(s): {', '.join(sorted(unknown))}."
            )
        missing = required_parameters - set(parameters)
        if missing:
            errors.append(
                f"{label}: exported Parameters omits required field(s): {', '.join(sorted(missing))}."
            )
        for name, value in parameters.items():
            expected_value = expected_parameters.get(name)
            if expected_value is not None and not _close(value, expected_value):
                errors.append(
                    f"{label}: exported parameter {name} does not match RM_INPUT."
                )


def _explicitly_unavailable(warning: str) -> bool:
    normalized = _norm(warning)
    return bool(normalized) and any(
        marker in normalized
        for marker in (
            "unavailable",
            "cannot represent",
            "not finite",
            "stopped before",
            "skipped ec",
            "below the predicted baseline",
            "no prediction",
            "no valid",
        )
    )


def _predictions(
    rows: list[dict[int, str]], errors: list[str], warnings: list[str]
) -> tuple[list[dict[str, Any]], dict[str, list[str]], set[str]]:
    header, data = _section_table(rows, "Model prediction values")
    required = ("model", "effect (%)", "predicted concentration", "unit")
    if not header or any(_norm(name) not in header for name in required):
        errors.append("Report does not contain a valid model prediction table.")
        return [], {"CA": [], "IA": []}, set()

    predictions: list[dict[str, Any]] = []
    model_warnings: dict[str, list[str]] = {"CA": [], "IA": []}
    unavailable: set[str] = set()
    started = False
    for row in data:
        model = _cell(row, header, "Model").strip().upper()
        if model not in {"CA", "IA"}:
            if started:
                break
            continue
        started = True
        warning = _cell(row, header, "Status / warning").strip()
        if warning and warning not in warnings:
            warnings.append(warning)
        if warning and warning not in model_warnings[model]:
            model_warnings[model].append(warning)
        raw_effect = _cell(row, header, "Effect (%)").strip()
        raw_concentration = _cell(row, header, "Predicted concentration").strip()
        if not raw_effect and not raw_concentration and _explicitly_unavailable(warning):
            unavailable.add(model)
            continue
        effect = _float(raw_effect)
        concentration = _float(raw_concentration)
        unit = _cell(row, header, "Unit").strip()
        if effect is None or not math.isfinite(effect) or not 0 < effect <= 100:
            errors.append(f"{model}: prediction has an invalid effect value.")
            continue
        if (
            concentration is None
            or not math.isfinite(concentration)
            or concentration <= 0
        ):
            errors.append(f"{model} EC{effect:g}: concentration is not finite and positive.")
            continue
        if not unit:
            errors.append(f"{model} EC{effect:g}: concentration unit is missing.")
            continue
        predictions.append(
            {
                "model": model,
                "effect": effect,
                "concentration": concentration,
                "unit": unit,
            }
        )

    if not predictions:
        errors.append("Report contains no valid CA or IA predictions.")
        return [], model_warnings, unavailable

    for model in ("CA", "IA"):
        series = [item for item in predictions if item["model"] == model]
        if not series:
            if model not in unavailable:
                errors.append(
                    f"{model}: prediction series is missing without an explicit unavailable warning row."
                )
            continue
        effects = [item["effect"] for item in series]
        concentrations = [item["concentration"] for item in series]
        if any(right <= left for left, right in zip(effects, effects[1:])):
            errors.append(f"{model}: effect levels are not strictly increasing.")
        if any(right <= left for left, right in zip(concentrations, concentrations[1:])):
            errors.append(f"{model}: concentrations do not increase with effect level.")
        available = {int(value) for value in effects if value.is_integer()}
        if not STANDARD_EFFECTS.issubset(available) and not any(
            _explicitly_unavailable(warning) for warning in model_warnings[model]
        ):
            errors.append(
                f"{model}: effect range is incomplete without an explicit availability warning."
            )
    return predictions, model_warnings, unavailable


def _compare_prediction_units(
    predictions: list[dict[str, Any]], source: list[dict[str, str]], errors: list[str]
) -> None:
    source_units = {
        _norm(record.get("concentration_unit"))
        for record in source
        if _norm(record.get("concentration_unit"))
    }
    if len(source_units) != 1:
        errors.append("RM_INPUT must use one common concentration unit for the mixture.")
        return
    expected_unit = next(iter(source_units))
    for prediction in predictions:
        if _norm(prediction["unit"]) != expected_unit:
            errors.append(
                f"{prediction['model']} EC{prediction['effect']:g}: prediction unit "
                f"{prediction['unit']!r} does not match RM_INPUT."
            )


def validate_report(report_path: str | Path, input_path: str | Path) -> dict[str, Any]:
    """Validate an OpenMRA report against its source input without modifying either."""

    report = Path(report_path).expanduser().resolve()
    source = Path(input_path).expanduser().resolve()
    result: dict[str, Any] = {
        "status": "FAIL",
        "report_file": report.name,
        "input_file": None,
        "expected_input_file": source.name,
        "images": [],
        "predictions": [],
        "model_warnings": {"CA": [], "IA": []},
        "input_dataset": [],
        "errors": [],
        "warnings": [],
    }
    errors: list[str] = result["errors"]
    warnings: list[str] = result["warnings"]

    if not report.is_file():
        errors.append(f"Report file does not exist: {report}")
        return result
    if not source.is_file():
        errors.append(f"Input file does not exist: {source}")
        return result

    try:
        report_sheets, images = _read_workbook(report)
        input_sheets, _ = _read_workbook(source)
    except (OSError, KeyError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
        errors.append(f"Cannot read XLSX package: {exc}")
        return result

    result["images"] = images
    if not images:
        errors.append("Report has no non-empty embedded image under xl/media.")

    report_rows = next(iter(report_sheets.values()), None)
    if report_rows is None:
        errors.append("Report workbook contains no readable worksheet.")
        return result

    input_label = _find_label(report_rows, "Input file")
    if input_label is None:
        errors.append("Report does not identify its input file.")
    else:
        row_index, column = input_label
        recorded = report_rows[row_index].get(column + 1, "").strip()
        result["input_file"] = recorded or None
        if recorded.casefold() != source.name.casefold():
            errors.append(
                f"Report input file {recorded!r} does not match {source.name!r}."
            )

    predictions, model_warnings, _ = _predictions(report_rows, errors, warnings)
    result["predictions"] = predictions
    result["model_warnings"] = model_warnings
    exported = _report_dataset(report_rows)
    result["input_dataset"] = exported
    rm_input = _find_sheet(input_sheets, "RM_INPUT")
    if rm_input is None:
        errors.append("Input workbook does not contain an RM_INPUT sheet.")
    else:
        source_records = _input_records(rm_input)
        _compare_dataset(exported, source_records, errors)
        _compare_prediction_units(predictions, source_records, errors)

    result["status"] = "PASS" if not errors else "FAIL"
    return result


def _paths_alias(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.exists() and right.exists() and left.samefile(right)
    except OSError:
        return False


def _atomic_write(path: Path, payload: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="OpenMRA report XLSX")
    parser.add_argument("--input", required=True, dest="input_path", help="Source input XLSX")
    parser.add_argument("--output", help="Optional JSON result path")
    args = parser.parse_args(argv)

    report_path = Path(args.report).expanduser().resolve()
    input_path = Path(args.input_path).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else None
    if output_path is not None:
        for workbook_path, role in ((report_path, "report"), (input_path, "input")):
            if _paths_alias(output_path, workbook_path):
                parser.error(f"--output must not alias the {role} workbook")

    result = validate_report(report_path, input_path)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if output_path is not None:
        _atomic_write(output_path, payload)
    else:
        sys.stdout.buffer.write((payload + "\n").encode("utf-8"))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
