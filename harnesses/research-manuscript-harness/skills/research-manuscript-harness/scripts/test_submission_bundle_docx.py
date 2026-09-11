#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_submission_bundle import (  # noqa: E402
    FindingLog,
    _label_references,
    _owned_image_occurrences,
    _parse_docx,
    audit_bundle,
)
from test_submission_bundle import (  # noqa: E402
    A,
    HAS_PILLOW,
    PILLOW_REASON,
    R,
    REL,
    W,
    WP,
    _create_bundle,
    _file_hash,
    _paragraph,
    _write_docx,
    _write_manifest,
)


def _document_xml(body: str) -> bytes:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}">
<w:body>{body}<w:sectPr/></w:body></w:document>
'''.encode("utf-8")


def _replace_zip_members(path: Path, replacements: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    members.update(replacements)
    rewritten = path.with_name(f"{path.name}.rewritten")
    with zipfile.ZipFile(rewritten, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, blob in members.items():
            archive.writestr(member, blob)
    rewritten.replace(path)


def _write_body_docx(
    path: Path,
    body: str,
    *,
    numbering_xml: str | None = None,
) -> None:
    _write_docx(
        path,
        body_text="Synthetic placeholder.",
        figures=(),
        bibliography_numbers=None,
    )
    replacements = {"word/document.xml": _document_xml(body)}
    if numbering_xml is not None:
        replacements["word/numbering.xml"] = numbering_xml.encode("utf-8")
        replacements["word/_rels/document.xml.rels"] = f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL}">
  <Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>
'''.encode("utf-8")
    _replace_zip_members(path, replacements)


def _numbered_paragraph(text: str, num_id: int) -> str:
    return (
        f'<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/>'
        f'</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
    )


def _numbering_xml(num_format: str, nums: str, *, start: int = 1) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<w:numbering xmlns:w="{W}">
  <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">
    <w:start w:val="{start}"/><w:numFmt w:val="{num_format}"/><w:lvlText w:val="%1."/>
  </w:lvl></w:abstractNum>
  {nums}
</w:numbering>
'''


def _inline_drawing(
    relationship_id: str | None,
    extents: tuple[tuple[int, int], ...],
) -> str:
    extent_xml = "".join(
        f'<wp:extent cx="{width}" cy="{height}"/>' for width, height in extents
    )
    graphic_content = (
        f'<a:blip r:embed="{relationship_id}"/>'
        if relationship_id is not None
        else "<a:sp/>"
    )
    return (
        f"<w:r><w:drawing><wp:inline>{extent_xml}<a:graphic><a:graphicData>"
        f"{graphic_content}</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )


def _write_image_docx(path: Path, drawing_paragraph: str) -> None:
    _write_body_docx(
        path,
        _paragraph("Synthetic body.")
        + f"<w:p>{drawing_paragraph}</w:p>"
        + _paragraph("Figure 1: Synthetic figure."),
    )
    relationships = f'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL}">
  <Relationship Id="rIdImage1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image.bin"/>
</Relationships>
'''.encode("utf-8")
    _replace_zip_members(
        path,
        {
            "word/_rels/document.xml.rels": relationships,
            "word/media/image.bin": b"synthetic-image-bytes",
        },
    )


def _finding_codes(findings: FindingLog) -> set[str]:
    return {item["code"] for item in findings.items}


class WordBibliographyParsingTests(unittest.TestCase):
    def test_unsupported_automatic_word_numbering_is_never_invented(self) -> None:
        decimal_num = (
            '<w:num w:numId="10"><w:abstractNumId w:val="0"/>'
            '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="7"/>'
            "</w:num>"
        )
        restarted_nums = (
            '<w:num w:numId="10"><w:abstractNumId w:val="0"/></w:num>'
            '<w:num w:numId="11"><w:abstractNumId w:val="0"/></w:num>'
        )
        alpha_num = '<w:num w:numId="10"><w:abstractNumId w:val="0"/></w:num>'
        cases = {
            "offset": (
                _numbering_xml("decimal", decimal_num),
                [_numbered_paragraph("Offset reference.", 10)],
            ),
            "restarted": (
                _numbering_xml("decimal", restarted_nums),
                [
                    _numbered_paragraph("First sequence.", 10),
                    _numbered_paragraph("Restarted sequence.", 11),
                ],
            ),
            "missing_definition": (
                None,
                [_numbered_paragraph("Missing numbering definition.", 42)],
            ),
            "alphabetic": (
                _numbering_xml("lowerLetter", alpha_num),
                [_numbered_paragraph("Alphabetic reference.", 10)],
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case_name, (numbering_xml, entries) in cases.items():
                with self.subTest(case=case_name):
                    path = root / f"{case_name}.docx"
                    body = _paragraph("References") + "".join(entries)
                    _write_body_docx(path, body, numbering_xml=numbering_xml)
                    findings = FindingLog()
                    result = _parse_docx(path, path.name, "main", findings)
                    self.assertEqual([], result["bibliography_numbers"])
                    self.assertIn(
                        "automatic_bibliography_numbering_unresolved",
                        _finding_codes(findings),
                    )
                    self.assertEqual("UNVERIFIED", findings.status("bibliography"))

    def test_explicit_numeric_bibliography_remains_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "explicit.docx"
            body = (
                _paragraph("Evidence [1–2].")
                + _paragraph("References")
                + _paragraph("[1] First reference.")
                + _paragraph("[2] Second reference.")
            )
            _write_body_docx(path, body)
            findings = FindingLog()
            result = _parse_docx(path, path.name, "main", findings)
            self.assertEqual([1, 2], result["bibliography_numbers"])
            self.assertEqual({1, 2}, result["citations"])
            self.assertEqual("PASS", findings.status("bibliography"))

    def test_post_references_prose_is_scanned_but_entries_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trailing-caption.docx"
            body = (
                _paragraph("Body evidence [1].")
                + _paragraph("References")
                + _paragraph("[1] Entry with year 2024 and nested marker [77].")
                + _paragraph("[2] Second reference.")
                + _paragraph("Table S1: Trailing caption cites [3].")
            )
            _write_body_docx(path, body)
            findings = FindingLog()
            result = _parse_docx(path, path.name, "main", findings)
            self.assertEqual([1, 2], result["bibliography_numbers"])
            self.assertEqual({1, 3}, result["citations"])

    @unittest.skipUnless(HAS_PILLOW, PILLOW_REASON)
    def test_main_bibliography_resolves_si_only_citation_39(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(
                root,
                main_body="Evidence [1–38] supports Figure 1 and Table S1.",
                bibliography_numbers=tuple(range(1, 40)),
            )
            _write_docx(
                root / "si.docx",
                body_text="Supporting evidence [39] appears in Figure S1 and Table S1.",
                figures=("S1",),
                bibliography_numbers=None,
            )
            qa_path = root / "external-qa.json"
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            qa["artifact_hashes"]["si.docx"] = _file_hash(root / "si.docx")
            qa_path.write_text(json.dumps(qa, indent=2), encoding="utf-8")
            _write_manifest(root)

            receipt = audit_bundle(config)
            documents = {item["kind"]: item for item in receipt["documents"]}
            self.assertEqual(list(range(1, 40)), documents["main"]["bibliography_numbers"])
            self.assertEqual(list(range(1, 39)), documents["main"]["numeric_citations"])
            self.assertEqual([39], documents["si"]["numeric_citations"])
            self.assertNotIn(
                "dangling_literature_reference",
                {item["code"] for item in receipt["findings"]},
            )
            self.assertEqual("PASS", receipt["structural_machine_audit"])


class LabelReferenceRangeTests(unittest.TestCase):
    def test_mixed_series_lists_preserve_independent_identifiers(self) -> None:
        for text, kind, expected in (
            ("Figures S1 and 1 are distinct.", "figure", {"S1", "1"}),
            ("Tables S1–3 and 1 are distinct.", "table", {"S1", "S2", "S3", "1"}),
        ):
            with self.subTest(text=text):
                findings = FindingLog()
                self.assertEqual(
                    expected, _label_references(text, kind, findings, "main.docx")
                )
                self.assertEqual([], findings.items)

    def test_inherited_and_explicit_si_ranges_expand_without_bare_endpoint(self) -> None:
        inherited_findings = FindingLog()
        inherited = _label_references(
            "Tables S1–8 contain the measurements.",
            "table",
            inherited_findings,
            "main.docx",
        )
        explicit_findings = FindingLog()
        explicit = _label_references(
            "Tables S1–S8 contain the measurements.",
            "table",
            explicit_findings,
            "main.docx",
        )
        expected = {f"S{number}" for number in range(1, 9)}
        self.assertEqual(expected, inherited)
        self.assertEqual(expected, explicit)
        self.assertNotIn("8", inherited)
        self.assertEqual([], inherited_findings.items)
        self.assertEqual([], explicit_findings.items)

    def test_descending_and_oversized_ranges_are_diagnosed(self) -> None:
        findings = FindingLog()
        table_references = _label_references(
            "Tables S8–1 are invalid.", "table", findings, "main.docx"
        )
        figure_references = _label_references(
            "Figures 1–10002 are invalid.", "figure", findings, "main.docx"
        )
        self.assertEqual({"S1", "S8"}, table_references)
        self.assertEqual({"1", "10002"}, figure_references)
        invalid = [
            item for item in findings.items if item["code"].startswith("invalid_")
        ]
        self.assertEqual({"descending", "too_large"}, {item["details"]["reason"] for item in invalid})
        self.assertEqual("FAIL", findings.status("figure_table_references"))


class DrawingOwnershipTests(unittest.TestCase):
    def test_nonimage_shape_cannot_supply_the_picture_extent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "shape-before-image.docx"
            drawing_paragraph = _inline_drawing(None, ((900000, 900000),)) + _inline_drawing(
                "rIdImage1", ((400000, 200000),)
            )
            _write_image_docx(path, drawing_paragraph)
            findings = FindingLog()
            result = _parse_docx(path, path.name, "main", findings)
            self.assertEqual(1, len(result["images"]))
            self.assertEqual((400000, 200000), result["images"][0]["extent_emu"])
            self.assertEqual("PASS", findings.status("images"))

    def test_ambiguous_owned_extents_are_unverified_in_docx_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ambiguous-extents.docx"
            _write_image_docx(
                path,
                _inline_drawing(
                    "rIdImage1",
                    ((400000, 200000), (800000, 400000)),
                ),
            )
            findings = FindingLog()
            result = _parse_docx(path, path.name, "main", findings)
            self.assertEqual(1, len(result["images"]))
            self.assertIsNone(result["images"][0]["extent_emu"])
            self.assertIn("ambiguous_image_drawing_extent", _finding_codes(findings))
            self.assertEqual("UNVERIFIED", findings.status("images"))

    def test_missing_or_ambiguous_owner_and_missing_extent_are_unverified(self) -> None:
        cases = {
            "missing_owner": (
                '<a:blip r:embed="rId1"/>',
                "missing_image_drawing_owner",
            ),
            "ambiguous_owner": (
                '<wp:inline><wp:extent cx="4" cy="2"/><wp:anchor>'
                '<wp:extent cx="4" cy="2"/><a:blip r:embed="rId1"/>'
                "</wp:anchor></wp:inline>",
                "ambiguous_image_drawing_owner",
            ),
            "missing_extent": (
                '<wp:inline><a:blip r:embed="rId1"/></wp:inline>',
                "missing_image_drawing_extent",
            ),
        }
        for case_name, (content, expected_code) in cases.items():
            with self.subTest(case=case_name):
                paragraph = ET.fromstring(
                    f'<w:p xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" '
                    f'xmlns:wp="{WP}">{content}</w:p>'
                )
                findings = FindingLog()
                occurrences = _owned_image_occurrences(
                    paragraph, "main.docx", "images", findings
                )
                self.assertEqual(1, len(occurrences))
                self.assertIsNone(occurrences[0][1])
                self.assertIn(expected_code, _finding_codes(findings))
                self.assertEqual("UNVERIFIED", findings.status("images"))


if __name__ == "__main__":
    unittest.main()
