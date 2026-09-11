#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_submission_bundle import (  # noqa: E402
    FindingLog,
    _formula_sheet_references,
    _parse_workbook,
    audit_bundle,
)
from test_submission_bundle import (  # noqa: E402
    HAS_PILLOW,
    PILLOW_REASON,
    R,
    REL,
    X,
    _create_bundle,
    _file_hash,
    _write_manifest,
)

WORKSHEET_RELATIONSHIP = f"{R}/worksheet"
TABLE_RELATIONSHIP = f"{R}/table"


def _xml_attribute(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _write_workbook_fixture(
    path: Path,
    *,
    sheet_names: tuple[str, ...] = ("Contents",),
    workbook_root: str = "workbook",
    worksheet_roots: dict[str, str] | None = None,
    cell_texts: dict[str, str] | None = None,
    formulas: dict[str, str] | None = None,
    defined_name_formula: str | None = None,
    table_part_ids: dict[str, tuple[str, ...]] | None = None,
    worksheet_relationships: dict[str, tuple[tuple[str, str, str], ...]] | None = None,
    table_members: dict[str, str] | None = None,
) -> None:
    worksheet_roots = worksheet_roots or {}
    cell_texts = cell_texts or {}
    formulas = formulas or {}
    table_part_ids = table_part_ids or {}
    worksheet_relationships = worksheet_relationships or {}
    table_members = table_members or {}

    sheet_elements = "".join(
        f'<sheet name="{_xml_attribute(name)}" '
        f'sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    defined_names = ""
    if defined_name_formula is not None:
        defined_names = (
            '<definedNames><definedName name="SyntheticName" localSheetId="0">'
            f"{escape(defined_name_formula)}</definedName></definedNames>"
        )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{workbook_root} xmlns="{X}" xmlns:r="{R}">'
        f"<sheets>{sheet_elements}</sheets>{defined_names}</{workbook_root}>"
    )
    workbook_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="{WORKSHEET_RELATIONSHIP}" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{REL}">{workbook_relationships}</Relationships>'
    )

    worksheets: dict[str, str] = {}
    worksheet_rels: dict[str, str] = {}
    for index, name in enumerate(sheet_names, start=1):
        cells: list[str] = []
        if name in cell_texts:
            cells.append(
                '<c r="A1" t="inlineStr"><is><t>'
                f"{escape(cell_texts[name])}</t></is></c>"
            )
        if name in formulas:
            cells.append(
                f'<c r="B1"><f>{escape(formulas[name])}</f><v>1</v></c>'
            )
        sheet_data = (
            f'<sheetData><row r="1">{"".join(cells)}</row></sheetData>'
            if cells
            else "<sheetData/>"
        )
        declared_ids = table_part_ids.get(name)
        table_parts_xml = ""
        if declared_ids is not None:
            declarations = "".join(
                f'<tablePart r:id="{_xml_attribute(rel_id)}"/>'
                for rel_id in declared_ids
            )
            table_parts_xml = (
                f'<tableParts count="{len(declared_ids)}">'
                f"{declarations}</tableParts>"
            )
        root = worksheet_roots.get(name, "worksheet")
        worksheets[f"xl/worksheets/sheet{index}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<{root} xmlns="{X}" xmlns:r="{R}">'
            f"{sheet_data}{table_parts_xml}</{root}>"
        )

        relations = worksheet_relationships.get(name, ())
        if relations:
            relation_xml = "".join(
                f'<Relationship Id="{_xml_attribute(rel_id)}" '
                f'Type="{_xml_attribute(rel_type)}" '
                f'Target="{_xml_attribute(target)}"/>'
                for rel_id, rel_type, target in relations
            )
            worksheet_rels[
                f"xl/worksheets/_rels/sheet{index}.xml.rels"
            ] = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f'<Relationships xmlns="{REL}">{relation_xml}</Relationships>'
            )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '</Types>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        for member, xml in worksheets.items():
            archive.writestr(member, xml)
        for member, xml in worksheet_rels.items():
            archive.writestr(member, xml)
        for member, xml in table_members.items():
            archive.writestr(member, xml)


def _table_xml(name: str, table_id: int = 1, *, root: str = "table") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{root} xmlns="{X}" id="{table_id}" name="{name}" '
        f'displayName="{name}" ref="A1:B2">'
        '<autoFilter ref="A1:B2"/>'
        '<tableColumns count="2">'
        '<tableColumn id="1" name="Column1"/>'
        '<tableColumn id="2" name="Column2"/>'
        f"</tableColumns></{root}>"
    )


def _parse(path: Path) -> tuple[dict[str, object], FindingLog]:
    findings = FindingLog()
    result = _parse_workbook(path, path.name, findings)
    return result, findings


def _codes(findings: FindingLog) -> set[str]:
    return {item["code"] for item in findings.items}


class DynamicWorkbookDependencyTests(unittest.TestCase):
    def test_indirect_internal_and_external_dependencies_are_unverified(self) -> None:
        formulas = (
            'INDIRECT("MissingSheet!A1")',
            'INDIRECT("\'[external.xlsx]Data\'!A1")',
        )
        for formula in formulas:
            with self.subTest(formula=formula), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "dynamic.xlsx"
                _write_workbook_fixture(path, formulas={"Contents": formula})
                original = path.read_bytes()

                result, findings = _parse(path)

                self.assertEqual(original, path.read_bytes())
                self.assertEqual(1, result["formula_count"])
                self.assertIn("dynamic_formula_dependency_unverified", _codes(findings))
                self.assertEqual("UNVERIFIED", findings.status("workbook"))

    def test_dynamic_defined_name_is_also_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "defined-name.xlsx"
            _write_workbook_fixture(
                path,
                defined_name_formula='INDIRECT("MissingSheet!A1")',
            )

            result, findings = _parse(path)

            self.assertEqual(1, result["formula_count"])
            self.assertIn("dynamic_formula_dependency_unverified", _codes(findings))
            self.assertEqual("UNVERIFIED", findings.status("workbook"))

    def test_string_literals_are_scrubbed_without_hiding_static_references(self) -> None:
        references, external, has_ref_error, dynamic = _formula_sheet_references(
            'IF(A1="MissingSheet!A1",\'Data\'!A1,0)'
        )

        self.assertEqual({"Data"}, references)
        self.assertFalse(external)
        self.assertFalse(has_ref_error)
        self.assertFalse(dynamic)


class WorksheetTablePartTests(unittest.TestCase):
    def test_declared_table_part_requires_its_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing-table-relationship.xlsx"
            _write_workbook_fixture(
                path,
                table_part_ids={"Contents": ("rIdTable",)},
            )

            result, findings = _parse(path)

            self.assertEqual([], result["excel_tables"])
            self.assertEqual(set(), result["table_identifiers"])
            self.assertIn("unresolved_excel_table_relationship", _codes(findings))
            self.assertEqual("FAIL", findings.status("workbook"))

    def test_orphan_table_relationship_cannot_manufacture_an_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "orphan-table-relationship.xlsx"
            _write_workbook_fixture(
                path,
                worksheet_relationships={
                    "Contents": (
                        ("rIdOrphan", TABLE_RELATIONSHIP, "../tables/table9.xml"),
                    )
                },
                table_members={
                    "xl/tables/table9.xml": _table_xml("Table_S9", table_id=9)
                },
            )

            result, findings = _parse(path)

            self.assertEqual([], result["excel_tables"])
            self.assertEqual(set(), result["table_identifiers"])
            self.assertEqual("PASS", findings.status("workbook"))

    def test_declared_table_part_requires_a_table_xml_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wrong-table-root.xlsx"
            _write_workbook_fixture(
                path,
                table_part_ids={"Contents": ("rIdTable",)},
                worksheet_relationships={
                    "Contents": (
                        ("rIdTable", TABLE_RELATIONSHIP, "../tables/table1.xml"),
                    )
                },
                table_members={
                    "xl/tables/table1.xml": _table_xml(
                        "Table_S1", root="notTable"
                    )
                },
            )

            result, findings = _parse(path)

            self.assertEqual(set(), result["table_identifiers"])
            self.assertIn("invalid_excel_table_root", _codes(findings))
            self.assertEqual("FAIL", findings.status("workbook"))

    def test_valid_declared_table_part_adds_its_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "valid-table.xlsx"
            _write_workbook_fixture(
                path,
                table_part_ids={"Contents": ("rIdTable",)},
                worksheet_relationships={
                    "Contents": (
                        ("rIdTable", TABLE_RELATIONSHIP, "../tables/table1.xml"),
                    )
                },
                table_members={
                    "xl/tables/table1.xml": _table_xml("Table_S1")
                },
            )

            result, findings = _parse(path)

            self.assertEqual(["Table_S1"], result["excel_tables"])
            self.assertEqual({"S1"}, result["table_identifiers"])
            self.assertEqual("PASS", findings.status("workbook"))


class WorkbookRootAndDeclarationTests(unittest.TestCase):
    def test_wrong_workbook_root_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wrong-workbook-root.xlsx"
            _write_workbook_fixture(path, workbook_root="notWorkbook")

            result, findings = _parse(path)

            self.assertEqual([], result["sheets"])
            self.assertIn("invalid_workbook_root", _codes(findings))
            self.assertEqual("FAIL", findings.status("workbook"))

    def test_wrong_worksheet_root_does_not_count_as_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "wrong-worksheet-root.xlsx"
            _write_workbook_fixture(
                path,
                worksheet_roots={"Contents": "notWorksheet"},
            )

            result, findings = _parse(path)

            self.assertEqual([], result["sheets"])
            self.assertIn("invalid_worksheet_root", _codes(findings))
            self.assertIn("no_resolved_worksheets", _codes(findings))
            self.assertEqual("FAIL", findings.status("workbook"))

    def test_empty_workbook_has_no_resolved_worksheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.xlsx"
            _write_workbook_fixture(path, sheet_names=())

            result, findings = _parse(path)

            self.assertEqual([], result["sheets"])
            self.assertIn("no_resolved_worksheets", _codes(findings))
            self.assertEqual("FAIL", findings.status("workbook"))

    def test_prose_table_mention_is_a_reference_not_a_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "prose-reference.xlsx"
            _write_workbook_fixture(
                path,
                cell_texts={"Contents": "Table S9 contains processed values."},
            )

            result, findings = _parse(path)

            self.assertEqual(set(), result["table_identifiers"])
            self.assertEqual({"S9"}, result["table_references"])
            self.assertEqual("PASS", findings.status("workbook"))

    def test_unambiguous_cell_caption_can_declare_a_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "caption.xlsx"
            _write_workbook_fixture(
                path,
                cell_texts={"Contents": "Table S9: Processed values."},
            )

            result, findings = _parse(path)

            self.assertEqual({"S9"}, result["table_identifiers"])
            self.assertEqual(set(), result["table_references"])
            self.assertEqual("PASS", findings.status("workbook"))


@unittest.skipUnless(HAS_PILLOW, PILLOW_REASON)
class SplitWorkbookInventoryTests(unittest.TestCase):
    def test_contents_sheet_and_split_shared_s7_form_eight_unique_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(
                root,
                main_body=(
                    "Evidence [1] is detailed in Tables S1, S2, S3, S4, S5, "
                    "S6, S7 and S8 and illustrated by Figure 1."
                ),
                workbook_table_ids=("S1", "S2", "S5", "S7"),
                si_table_captions=("S3", "S4", "S6", "S7", "S8"),
            )
            workbook_path = root / config.workbook
            _write_workbook_fixture(
                workbook_path,
                sheet_names=(
                    "Contents",
                    "Table S1",
                    "Table S2",
                    "Table S5",
                    "Table S7",
                    "Data",
                ),
                cell_texts={"Contents": "Supporting table inventory"},
                formulas={"Table S1": "'Data'!A1"},
            )
            qa_path = root / config.qa_receipt
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            qa["artifact_hashes"][config.workbook] = _file_hash(workbook_path)
            qa_path.write_text(json.dumps(qa, indent=2), encoding="utf-8")
            _write_manifest(root)

            receipt = audit_bundle(config)

            workbook_ids = set(receipt["workbook"]["table_identifiers"])
            docx_ids = {
                caption["id"]
                for document in receipt["documents"]
                for caption in document["table_captions"]
            }
            self.assertEqual({"S1", "S2", "S5", "S7"}, workbook_ids)
            self.assertEqual({"S3", "S4", "S6", "S7", "S8"}, docx_ids)
            self.assertEqual({f"S{index}" for index in range(1, 9)}, workbook_ids | docx_ids)
            self.assertIn("S7", workbook_ids & docx_ids)
            self.assertEqual(
                ["Contents", "Table S1", "Table S2", "Table S5", "Table S7", "Data"],
                receipt["workbook"]["sheets"],
            )
            codes = {finding["code"] for finding in receipt["findings"]}
            self.assertNotIn("dangling_table_reference", codes)
            self.assertNotIn("dangling_workbook_table_reference", codes)
            self.assertEqual("PASS", receipt["structural_machine_audit"])


if __name__ == "__main__":
    unittest.main()
