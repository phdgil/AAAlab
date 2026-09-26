import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from html import escape
from pathlib import Path

from report_validation import validate_report


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _cell_reference(column, row):
    letters = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row}"


def _write_workbook(path, sheets, *, shared_strings=False, image=False):
    shared = []
    shared_index = {}

    def cell_xml(value, column, row):
        reference = _cell_reference(column, row)
        if isinstance(value, (int, float)):
            return f'<c r="{reference}"><v>{value}</v></c>'
        text = str(value)
        if shared_strings:
            if text not in shared_index:
                shared_index[text] = len(shared)
                shared.append(text)
            return f'<c r="{reference}" t="s"><v>{shared_index[text]}</v></c>'
        return (
            f'<c r="{reference}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'
        )

    sheet_parts = []
    for sheet_number, (_, rows) in enumerate(sheets.items(), 1):
        row_parts = []
        for row_number, values in enumerate(rows, 1):
            cells = "".join(
                cell_xml(value, column, row_number)
                for column, value in enumerate(values)
                if value is not None
            )
            row_parts.append(f'<row r="{row_number}">{cells}</row>')
        sheet_parts.append(
            (
                f"xl/worksheets/sheet{sheet_number}.xml",
                f'<worksheet xmlns="{MAIN_NS}"><sheetData>{"".join(row_parts)}</sheetData></worksheet>',
            )
        )

    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheets, 1)
    )
    workbook = (
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
        f"<sheets>{workbook_sheets}</sheets></workbook>"
    )
    workbook_rels = "".join(
        f'<Relationship Id="rId{index}" Type="{REL_NS}/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheets) + 1)
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{PKG_REL_NS}">{workbook_rels}</Relationships>',
        )
        for member, xml in sheet_parts:
            archive.writestr(member, xml)
        if shared_strings:
            items = "".join(f"<si><t>{escape(value)}</t></si>" for value in shared)
            archive.writestr(
                "xl/sharedStrings.xml",
                f'<sst xmlns="{MAIN_NS}" count="{len(shared)}" uniqueCount="{len(shared)}">{items}</sst>',
            )
        if image:
            archive.writestr("xl/media/image1.png", b"not-empty")


def _input_rows():
    return [
        [
            "row_type",
            "substance_id",
            "substance_name",
            "cas_no",
            "composition(%)",
            "concentration_unit",
            "rm_model",
            "param_a",
            "param_b",
            "param_c",
            "param_d",
            "param_e",
            "ec50",
            "effect_scale",
        ],
        [
            "INPUT",
            "S001",
            "Alpha",
            "111-11-1",
            60,
            "µM",
            "Logistic4",
            80,
            2,
            10,
            5,
            "",
            9.5,
            "percent",
        ],
        [
            "INPUT",
            "S002",
            "Beta",
            "222-22-2",
            40,
            "µM",
            "Logistic4",
            100,
            3,
            20,
            0,
            "",
            20,
            "percent",
        ],
    ]


def _dataset_rows(
    *,
    wrong_name=False,
    wrong_ratio=False,
    wrong_cas=False,
    first_parameters="amplitude=0.8, slope=2, ec50=10, bottom=0.05",
    first_model="Logistic4",
):
    return [
        ["Input dataset"],
        [
            "Substance ID",
            "Substance name",
            "CAS No.",
            "Mixture ratio",
            "Unit",
            "RM model",
            "Parameters",
        ],
        [
            "S001",
            "Wrong Alpha" if wrong_name else "Alpha",
            "999-99-9" if wrong_cas else "111-11-1",
            61 if wrong_ratio else 60,
            "µM",
            first_model,
            first_parameters,
        ],
        [
            "S002",
            "Beta",
            "222-22-2",
            40,
            "µM",
            "Logistic4",
            "amplitude=1, slope=3, ec50=20, bottom=0",
        ],
    ]


def _report_rows(input_name, predictions, **dataset_options):
    return [
        ["OpenMRA | Mixture Toxicity Prediction Report"],
        ["Input file", input_name],
        ["Model prediction values"],
        ["Model", "Effect (%)", "Predicted concentration", "Unit", "Status / warning"],
        *predictions,
        *_dataset_rows(**dataset_options),
    ]


class ReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)
        self.input_path = self.directory / "OpenMRA_RM_fixture.xlsx"
        self.report_path = self.directory / "report.xlsx"
        _write_workbook(
            self.input_path,
            {"RM_INPUT": _input_rows()},
            shared_strings=True,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def write_report(self, predictions, **dataset_options):
        _write_workbook(
            self.report_path,
            {
                "Report": _report_rows(
                    self.input_path.name, predictions, **dataset_options
                )
            },
            image=True,
        )

    def test_partial_results_with_explicit_warning_pass_and_preserve_warning(self):
        ca_warning = "Skipped EC10: RM cannot represent effect 0.1."
        ia_warning = "Stopped before EC40: target is unavailable."
        self.write_report(
            [
                ["CA", 20, 2.0, "µM", ca_warning],
                ["CA", 30, 3.0, "µM", ""],
                ["IA", 20, 2.5, "µM", ""],
                ["IA", 30, 4.0, "µM", ia_warning],
            ]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("PASS", result["status"], result["errors"])
        self.assertEqual([ca_warning, ia_warning], result["warnings"])
        self.assertEqual([ca_warning], result["model_warnings"]["CA"])
        self.assertEqual([ia_warning], result["model_warnings"]["IA"])
        self.assertEqual(4, len(result["predictions"]))
        self.assertEqual("xl/media/image1.png", result["images"][0])

    def test_missing_model_series_without_explicit_unavailable_row_fails(self):
        self.write_report(
            [["CA", effect, effect * 2, "µM", ""] for effect in range(10, 100, 10)]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(
            any("IA: prediction series is missing" in error for error in result["errors"])
        )

    def test_explicit_unavailable_row_allows_missing_model_series(self):
        predictions = [
            ["CA", effect, effect * 2, "µM", ""] for effect in range(10, 100, 10)
        ]
        predictions.append(
            ["IA", "", "", "", "Stopped before EC10: no valid prediction is available."]
        )
        self.write_report(predictions)

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("PASS", result["status"], result["errors"])
        self.assertTrue(result["model_warnings"]["IA"])

    def test_other_models_warning_does_not_justify_partial_range(self):
        self.write_report(
            [
                ["CA", 20, 2.0, "µM", "Skipped EC10: RM cannot represent effect 0.1."],
                ["CA", 30, 3.0, "µM", ""],
                ["IA", 20, 2.5, "µM", ""],
                ["IA", 30, 4.0, "µM", ""],
            ]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(
            any("IA: effect range is incomplete" in error for error in result["errors"])
        )

    def test_generic_warning_does_not_justify_partial_range(self):
        self.write_report(
            [
                ["CA", 20, 2.0, "µM", "limited range"],
                ["IA", 20, 2.5, "µM", "limited range"],
            ]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertEqual(
            2,
            sum("explicit availability warning" in error for error in result["errors"]),
        )

    def test_wrong_mixture_identity_and_ratio_fail(self):
        self.write_report(
            [["CA", 20, 2.0, "µM", "limited range"]],
            wrong_name=True,
            wrong_ratio=True,
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("name" in error for error in result["errors"]))
        self.assertTrue(any("mixture ratio" in error for error in result["errors"]))

    def test_changed_cas_number_fails(self):
        self.write_report(
            [
                ["CA", 20, 2.0, "µM", "Stopped before EC30: unavailable."],
                ["IA", 20, 3.0, "µM", "Stopped before EC30: unavailable."],
            ],
            wrong_cas=True,
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("CAS No." in error for error in result["errors"]))

    def test_empty_prediction_export_fails(self):
        self.write_report([])

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("no valid CA or IA" in error for error in result["errors"]))

    def test_nonfinite_and_nonpositive_predictions_fail(self):
        self.write_report(
            [
                ["CA", 10, "nan", "µM", ""],
                ["CA", 20, -1, "µM", ""],
            ]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertGreaterEqual(
            sum("not finite and positive" in error for error in result["errors"]), 2
        )

    def test_nonmonotonic_predictions_fail(self):
        self.write_report(
            [
                ["CA", 10, 5, "µM", "range limited"],
                ["CA", 20, 4, "µM", ""],
            ]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("do not increase" in error for error in result["errors"]))

    def test_missing_and_unknown_exported_parameters_fail(self):
        self.write_report(
            [
                ["CA", 20, 2, "µM", "limited range"],
                ["IA", 20, 3, "µM", "limited range"],
            ],
            first_parameters="amplitude=0.8, slope=2, mystery=7",
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("unknown field" in error for error in result["errors"]))
        self.assertTrue(any("omits required" in error for error in result["errors"]))

    def test_unsupported_rm_model_fails_closed(self):
        rows = _input_rows()
        rows[1][6] = "Sigmoid4"
        _write_workbook(
            self.input_path,
            {"RM_INPUT": rows},
            shared_strings=True,
        )
        self.write_report(
            [
                ["CA", 20, 2, "µM", "limited range"],
                ["IA", 20, 3, "µM", "limited range"],
            ],
            first_model="Sigmoid4",
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("unsupported" in error for error in result["errors"]))

    def test_prediction_unit_must_match_rm_input(self):
        self.write_report(
            [
                ["CA", 20, 2, "mg/L", "limited range"],
                ["IA", 20, 3, "mg/L", "limited range"],
            ]
        )

        result = validate_report(self.report_path, self.input_path)

        self.assertEqual("FAIL", result["status"])
        self.assertEqual(
            2,
            sum("prediction unit" in error for error in result["errors"]),
        )

    def test_cli_returns_nonzero_and_writes_json_on_failure(self):
        self.write_report([])
        output = self.directory / "validation.json"

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("report_validation.py")),
                "--report",
                str(self.report_path),
                "--input",
                str(self.input_path),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(1, completed.returncode)
        self.assertEqual("FAIL", json.loads(output.read_text(encoding="utf-8"))["status"])
        self.assertEqual([], list(self.directory.glob(".validation.json.*.tmp")))

    def test_cli_rejects_output_colliding_with_workbooks_and_preserves_bytes(self):
        self.write_report([])
        script = str(Path(__file__).with_name("report_validation.py"))
        original_report = self.report_path.read_bytes()
        original_input = self.input_path.read_bytes()

        for collision in (self.report_path, self.input_path):
            with self.subTest(collision=collision.name):
                completed = subprocess.run(
                    [
                        sys.executable,
                        script,
                        "--report",
                        str(self.report_path),
                        "--input",
                        str(self.input_path),
                        "--output",
                        str(collision),
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(2, completed.returncode)
                self.assertIn("must not alias", completed.stderr)
                self.assertEqual(original_report, self.report_path.read_bytes())
                self.assertEqual(original_input, self.input_path.read_bytes())

    def test_cli_rejects_hardlink_output_alias(self):
        self.write_report([])
        alias = self.directory / "report-alias.json"
        os.link(self.report_path, alias)
        original = self.report_path.read_bytes()

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("report_validation.py")),
                "--report",
                str(self.report_path),
                "--input",
                str(self.input_path),
                "--output",
                str(alias),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(2, completed.returncode)
        self.assertEqual(original, self.report_path.read_bytes())
        self.assertEqual(original, alias.read_bytes())

    def test_cli_rejects_symlink_output_alias_when_supported(self):
        self.write_report([])
        alias = self.directory / "report-symlink.json"
        try:
            alias.symlink_to(self.report_path)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        original = self.report_path.read_bytes()

        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("report_validation.py")),
                "--report",
                str(self.report_path),
                "--input",
                str(self.input_path),
                "--output",
                str(alias),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(2, completed.returncode)
        self.assertEqual(original, self.report_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
