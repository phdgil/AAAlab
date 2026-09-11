#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import builtins
import hashlib
import io
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_submission_bundle import (  # noqa: E402
    BundleConfig,
    _resolve_archive_target,
    audit_bundle,
    main,
)

try:
    from PIL import Image
except ImportError:
    Image = None

HAS_PILLOW = Image is not None
PILLOW_REASON = "Pillow is optional and is required only for synthetic raster fixtures"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _paragraph(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _drawing(rel_id: str, cx: int = 400000, cy: int = 200000) -> str:
    return f"""
<w:p><w:r><w:drawing><wp:inline>
  <wp:extent cx="{cx}" cy="{cy}"/>
  <a:graphic><a:graphicData><pic:pic><pic:blipFill>
    <a:blip r:embed="{rel_id}"/>
  </pic:blipFill></pic:pic></a:graphicData></a:graphic>
</wp:inline></w:drawing></w:r></w:p>
"""


def _png_bytes(
    color: tuple[int, int, int] | tuple[int, int, int, int],
    size: tuple[int, int] = (4, 2),
) -> bytes:
    assert Image is not None
    buffer = io.BytesIO()
    mode = "RGBA" if len(color) == 4 else "RGB"
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_docx(
    path: Path,
    *,
    body_text: str,
    figures: tuple[str, ...],
    bibliography_numbers: tuple[int, ...] | None,
    table_captions: tuple[str, ...] = (),
    opaque_rgba_figures: set[str] | None = None,
    transparent_figures: set[str] | None = None,
    unassigned_toc_count: int = 0,
    unassigned_toc_color: tuple[int, int, int] | tuple[int, int, int, int] = (40, 100, 180),
    unassigned_toc_stretched: bool = False,
    stretched_figures: set[str] | None = None,
    malicious_target: bool = False,
    malicious_member: bool = False,
) -> None:
    opaque_rgba_figures = opaque_rgba_figures or set()
    transparent_figures = transparent_figures or set()
    stretched_figures = stretched_figures or set()
    colors = {
        "1": (200, 20, 20),
        "2": (20, 200, 20),
        "3": (20, 20, 200),
        "S1": (120, 80, 200),
        "S2": (30, 140, 180),
    }
    body_parts = [_paragraph(body_text)]
    relationships: list[str] = []
    media: list[tuple[str, bytes]] = []
    for index, label in enumerate(figures, start=1):
        rel_id = f"rIdImage{index}"
        member = f"word/media/image-{index}.png"
        target = "../../../outside.png" if malicious_target and index == 1 else f"media/image-{index}.png"
        relationships.append(
            f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>'
        )
        rgb = colors.get(label, (80, 80, 80))
        if label in transparent_figures:
            png_color = (*rgb, 128)
        elif label in opaque_rgba_figures:
            png_color = (*rgb, 255)
        else:
            png_color = rgb
        media.append((member, _png_bytes(png_color)))
        body_parts.append(_drawing(rel_id, cy=400000 if label in stretched_figures else 200000))
        body_parts.append(_paragraph(f"Figure {label}: Synthetic figure."))
    for label in table_captions:
        qualifier = "Rounded values." if label == "S7" else "Synthetic values."
        body_parts.append(_paragraph(f"Table {label}: {qualifier}"))
    for toc_index in range(unassigned_toc_count):
        image_index = len(figures) + toc_index + 1
        rel_id = f"rIdImage{image_index}"
        member = f"word/media/image-{image_index}.png"
        relationships.append(
            f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image-{image_index}.png"/>'
        )
        media.append(
            (
                member,
                _png_bytes(unassigned_toc_color, size=(6, 3)),
            )
        )
        body_parts.append(
            _drawing(
                rel_id,
                cx=600000,
                cy=600000 if unassigned_toc_stretched else 300000,
            )
        )
    if bibliography_numbers is not None:
        body_parts.append(_paragraph("References"))
        for number in bibliography_numbers:
            body_parts.append(_paragraph(f"[{number}] Synthetic reference {number}."))
    body_parts.append("<w:sectPr/>")
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:pic="{PIC}">
<w:body>{''.join(body_parts)}</w:body></w:document>
"""
    rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL}">{''.join(relationships)}</Relationships>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        for member, blob in media:
            archive.writestr(member, blob)
        if malicious_member:
            archive.writestr("../escape.xml", "<unsafe/>")


def _write_workbook(
    path: Path,
    formula: str = "'Data'!A1",
    *,
    table_ids: tuple[str, ...] = ("S1",),
    root_relative_targets: bool = False,
) -> None:
    sheet_names = [f"Table {identifier}" for identifier in table_ids] + ["Data"]
    sheet_elements = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    first_table_name = sheet_names[0]
    workbook = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="{X}" xmlns:r="{R}"><sheets>
  {sheet_elements}
</sheets><definedNames>
  <definedName name="SyntheticScope" localSheetId="0">'{escape(first_table_name)}'!$A$1</definedName>
</definedNames></workbook>
"""
    relationships = []
    for index in range(1, len(sheet_names) + 1):
        target = f"xl/worksheets/sheet{index}.xml"
        if not root_relative_targets:
            target = target.removeprefix("xl/")
        else:
            target = f"/{target}"
        relationships.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="{target}"/>'
        )
    rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL}">
  {''.join(relationships)}
</Relationships>
"""
    worksheets: list[str] = []
    for index, name in enumerate(sheet_names, start=1):
        if name == "Data":
            cells = '<c r="A1"><v>1</v></c>'
        else:
            detail = "Detailed values" if name == "Table S7" else "Synthetic values"
            formula_cell = (
                f'<c r="B1"><f>{escape(formula)}</f><v>1</v></c>'
                if index == 1
                else ""
            )
            cells = (
                f'<c r="A1" t="inlineStr"><is><t>{escape(name)}: {detail}</t></is></c>'
                f"{formula_cell}"
            )
        worksheets.append(
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<worksheet xmlns="{X}"><sheetData><row r="1">{cells}</row></sheetData></worksheet>'
        )
    content_type_overrides = "\n".join(
        f'  <Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, len(sheet_names) + 1)
    )
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{overrides}
</Types>
""".format(overrides=content_type_overrides)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        for index, worksheet in enumerate(worksheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet)


def _write_toc_docx(path: Path, *, stretched: bool = False) -> None:
    blob = _png_bytes((40, 100, 180), size=(6, 3))
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:pic="{PIC}">
<w:body>{_drawing('rIdToc', cx=600000, cy=600000 if stretched else 300000)}<w:sectPr/></w:body></w:document>
"""
    rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="{REL}">
  <Relationship Id="rIdToc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/toc.png"/>
</Relationships>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/media/toc.png", blob)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(root: Path) -> None:
    manifest = root / "checksums.sha256"
    lines: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "checksums.sha256" or path.name.startswith("~$"):
            continue
        lines.append(f"{_file_hash(path)}  {relative}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _finding_codes(receipt: dict[str, object]) -> set[str]:
    findings = receipt["findings"]
    assert isinstance(findings, list)
    return {item["code"] for item in findings}


def _config(root: Path, toc_path: str = "graphical-toc.tiff") -> BundleConfig:
    return BundleConfig(
        bundle_root=root,
        main_docx="main.docx",
        si_docx="si.docx",
        workbook="supporting.xlsx",
        tiff_dir="figures",
        toc=toc_path,
        checksum_manifest="checksums.sha256",
        qa_receipt="external-qa.json",
    )


def _create_bundle(
    root: Path,
    *,
    main_body: str | None = None,
    main_figures: tuple[str, ...] = ("1",),
    si_figures: tuple[str, ...] = ("S1",),
    si_table_captions: tuple[str, ...] = (),
    workbook_table_ids: tuple[str, ...] = ("S1",),
    root_relative_workbook_targets: bool = False,
    bibliography_numbers: tuple[int, ...] = (1, 2, 3),
    omit_tiffs: set[str] | None = None,
    extra_tiffs: set[str] | None = None,
    mismatched_tiffs: set[str] | None = None,
    opaque_rgba_figures: set[str] | None = None,
    transparent_figures: set[str] | None = None,
    stretched_figures: set[str] | None = None,
    formula: str = "'Data'!A1",
    toc_kind: str = "tiff",
    toc_stretched: bool = False,
    main_toc_count: int | None = None,
    main_toc_color: tuple[int, int, int] | tuple[int, int, int, int] = (40, 100, 180),
    main_toc_stretched: bool = False,
    malicious_target: bool = False,
    malicious_member: bool = False,
    qa_outcome: str = "approved",
) -> BundleConfig:
    assert Image is not None
    omit_tiffs = omit_tiffs or set()
    extra_tiffs = extra_tiffs or set()
    mismatched_tiffs = mismatched_tiffs or set()
    opaque_rgba_figures = opaque_rgba_figures or set()
    transparent_figures = transparent_figures or set()
    stretched_figures = stretched_figures or set()
    (root / "figures").mkdir(parents=True)
    if main_toc_count is None:
        main_toc_count = 1 if toc_kind == "tiff" else 0
    main_text = main_body or (
        "SMARTS [C;H3] and [nH] are chemical notation, while evidence [1–3] "
        "supports Figure 1 and Table S1."
    )
    _write_docx(
        root / "main.docx",
        body_text=main_text,
        figures=main_figures,
        bibliography_numbers=bibliography_numbers,
        opaque_rgba_figures=opaque_rgba_figures,
        transparent_figures=transparent_figures,
        unassigned_toc_count=main_toc_count,
        unassigned_toc_color=main_toc_color,
        unassigned_toc_stretched=main_toc_stretched,
        stretched_figures=stretched_figures,
        malicious_target=malicious_target,
        malicious_member=malicious_member,
    )
    _write_docx(
        root / "si.docx",
        body_text="Supporting evidence [2] appears in Figure S1 and Table S1.",
        figures=si_figures,
        bibliography_numbers=None,
        table_captions=si_table_captions,
        opaque_rgba_figures=opaque_rgba_figures,
        transparent_figures=transparent_figures,
    )
    _write_workbook(
        root / "supporting.xlsx",
        formula=formula,
        table_ids=workbook_table_ids,
        root_relative_targets=root_relative_workbook_targets,
    )

    colors = {
        "1": (200, 20, 20),
        "2": (20, 200, 20),
        "3": (20, 20, 200),
        "S1": (120, 80, 200),
        "S2": (30, 140, 180),
        "9": (90, 90, 20),
    }
    labels = set(main_figures) | set(si_figures) | extra_tiffs
    for label in sorted(labels - omit_tiffs):
        rgb = colors.get(label, (80, 80, 80))
        if label in mismatched_tiffs:
            tiff_image = Image.new("RGB", (4, 2), (1, 2, 3))
        elif label in transparent_figures:
            rgba_image = Image.new("RGBA", (4, 2), (*rgb, 128))
            tiff_image = Image.alpha_composite(
                Image.new("RGBA", rgba_image.size, (255, 255, 255, 255)),
                rgba_image,
            ).convert("RGB")
        else:
            tiff_image = Image.new("RGB", (4, 2), rgb)
        tiff_image.save(root / "figures" / f"fig-{label}.tiff", format="TIFF")

    if toc_kind == "docx":
        toc_path = "graphical-toc.docx"
        _write_toc_docx(root / toc_path, stretched=toc_stretched)
    else:
        toc_path = "graphical-toc.tiff"
        Image.new("RGB", (6, 3), (40, 100, 180)).save(root / toc_path, format="TIFF")

    (root / "notes.txt").write_text("Synthetic bundle note.\n", encoding="utf-8")
    (root / "~$main.docx").write_bytes(b"synthetic Office lock")
    required_hashes = [
        "main.docx",
        "si.docx",
        "supporting.xlsx",
        toc_path,
        *sorted((root / "figures").joinpath(path.name).relative_to(root).as_posix() for path in (root / "figures").glob("*.tiff")),
    ]
    qa = {
        "schema_version": "aaalab.external-qa/v1",
        "reviewer": {
            "identity": "independent-reviewer-1",
            "role": "external QA reviewer",
            "independence_basis": "Separate reviewer with no manuscript editing role.",
        },
        "reviewed_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "outcome": qa_outcome,
        "scope": {
            "visual_readability": {"outcome": "approved"},
            "citation_semantics": {"outcome": "approved"},
            "scientific_review": {"outcome": "approved" if qa_outcome == "approved" else "changes_required"},
        },
        "artifact_hashes": {relative: _file_hash(root / relative) for relative in required_hashes},
        "unresolved_blockers": [] if qa_outcome == "approved" else ["Scientific review requires changes."],
    }
    (root / "external-qa.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")
    _write_manifest(root)
    return _config(root, toc_path)


@unittest.skipUnless(HAS_PILLOW, PILLOW_REASON)
class SubmissionBundleAuditTests(unittest.TestCase):
    def test_positive_bundle_is_submission_ready_and_smarts_is_not_a_citation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(root)
            receipt = audit_bundle(config)
            self.assertEqual("PASS", receipt["structural_machine_audit"])
            self.assertEqual("ELIGIBLE", receipt["publication_eligibility"])
            self.assertEqual("SUBMISSION_READY", receipt["verdict"])
            self.assertNotIn("invalid_numeric_citation_range", _finding_codes(receipt))
            self.assertIn("~$main.docx", receipt["excluded_office_locks"])

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "--bundle-root", str(root),
                        "--main-docx", "main.docx",
                        "--si-docx", "si.docx",
                        "--workbook", "supporting.xlsx",
                        "--tiff-dir", "figures",
                        "--toc", "graphical-toc.tiff",
                        "--checksum-manifest", "checksums.sha256",
                        "--qa-receipt", "external-qa.json",
                    ]
                )
            self.assertEqual(0, exit_code)
            self.assertEqual("SUBMISSION_READY", json.loads(output.getvalue())["verdict"])

    def test_missing_pillow_is_explicitly_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary))
            original_import = builtins.__import__

            def reject_pillow(name: str, *args: object, **kwargs: object) -> object:
                if name == "PIL" or name.startswith("PIL."):
                    raise ImportError("synthetic missing Pillow")
                return original_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=reject_pillow):
                receipt = audit_bundle(config)
            codes = _finding_codes(receipt)
            self.assertIn("pillow_unavailable", codes)
            self.assertIn("pillow_unavailable_for_toc", codes)
            self.assertEqual("UNVERIFIED", receipt["structural_machine_audit"])
            self.assertEqual("NOT_ELIGIBLE", receipt["publication_eligibility"])

    def test_dangling_literature_figure_and_table_references_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary),
                main_body="SMARTS [C;H3] is not a citation; [1,9] cites Figure 9 and Table S9.",
            )
            receipt = audit_bundle(config)
            codes = _finding_codes(receipt)
            self.assertIn("dangling_literature_reference", codes)
            self.assertIn("dangling_figure_reference", codes)
            self.assertIn("dangling_table_reference", codes)
            self.assertEqual("FAIL", receipt["structural_machine_audit"])
            self.assertEqual(1, receipt["exit_code"])

    def test_duplicate_and_renumbered_sequences_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary),
                main_body="Evidence [1] supports Figure 1 and Table S1.",
                main_figures=("1", "1", "3"),
                bibliography_numbers=(1, 3),
            )
            codes = _finding_codes(audit_bundle(config))
            self.assertIn("duplicate_figure_caption", codes)
            self.assertIn("figure_sequence_noncontiguous", codes)
            self.assertIn("bibliography_noncontiguous", codes)

    def test_missing_extra_and_mismatched_tiffs_block(self) -> None:
        cases = (
            ({"omit_tiffs": {"S1"}}, "missing_tiff"),
            ({"extra_tiffs": {"9"}}, "extraneous_tiff"),
        )
        for kwargs, expected_code in cases:
            with self.subTest(expected_code=expected_code), tempfile.TemporaryDirectory() as temporary:
                config = _create_bundle(Path(temporary), **kwargs)
                self.assertIn(expected_code, _finding_codes(audit_bundle(config)))

    def test_opaque_rgba_docx_pixels_match_native_rgb_tiffs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary), opaque_rgba_figures={"1", "S1"}
            )
            receipt = audit_bundle(config)
            self.assertEqual("PASS", receipt["structural_machine_audit"])
            self.assertEqual("ELIGIBLE", receipt["publication_eligibility"])
            self.assertNotIn("image_pixel_mismatch", _finding_codes(receipt))
            policies = {
                pair["figure_id"]: pair["canonicalization"]["docx_conversion"]
                for pair in receipt["images"]["verified_pairs"]
            }
            self.assertEqual({"1": "drop_opaque_alpha", "S1": "drop_opaque_alpha"}, policies)

    def test_nonopaque_rgba_uses_declared_white_page_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), transparent_figures={"1"})
            receipt = audit_bundle(config)
            self.assertEqual("PASS", receipt["structural_machine_audit"])
            self.assertEqual("ELIGIBLE", receipt["publication_eligibility"])
            self.assertIn("docx_alpha_composited_on_white", _finding_codes(receipt))
            figure = next(
                pair
                for pair in receipt["images"]["verified_pairs"]
                if pair["figure_id"] == "1"
            )
            self.assertEqual(
                "white_page_alpha_composite",
                figure["canonicalization"]["docx_conversion"],
            )
            self.assertEqual([255, 255, 255], figure["canonicalization"]["background_rgb"])
            self.assertEqual("none", figure["canonicalization"]["resampling"])
            self.assertEqual(
                figure["canonical_rgb_pixel_sha256"],
                figure["tiff_native_pixel_sha256"],
            )

    def test_genuinely_different_rgb_pixels_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), mismatched_tiffs={"1"})
            receipt = audit_bundle(config)
            self.assertIn("image_pixel_mismatch", _finding_codes(receipt))
            figure = next(
                pair
                for pair in receipt["images"]["verified_pairs"]
                if pair["figure_id"] == "1"
            )
            self.assertEqual("RGB", figure["docx_mode"])
            self.assertEqual("RGB", figure["tiff_mode"])
            self.assertNotEqual(
                figure["canonical_rgb_pixel_sha256"],
                figure["tiff_native_pixel_sha256"],
            )

    def test_docx_image_pixel_and_display_aspect_mismatches_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary),
                mismatched_tiffs={"1"},
                stretched_figures={"1"},
            )
            codes = _finding_codes(audit_bundle(config))
            self.assertIn("image_pixel_mismatch", codes)
            self.assertIn("docx_display_aspect_mismatch", codes)

    def test_stale_and_incomplete_manifest_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(root)
            (root / "notes.txt").write_text("Changed after manifest.\n", encoding="utf-8")
            self.assertIn("stale_manifest_checksum", _finding_codes(audit_bundle(config)))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(root)
            manifest = root / "checksums.sha256"
            lines = [line for line in manifest.read_text(encoding="utf-8").splitlines() if not line.endswith("  notes.txt")]
            manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertIn("incomplete_manifest", _finding_codes(audit_bundle(config)))

    def test_broken_cross_sheet_formula_reference_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), formula="MissingSheet!A1")
            self.assertIn(
                "broken_cross_sheet_formula_reference",
                _finding_codes(audit_bundle(config)),
            )

    def test_root_relative_opc_relationships_and_split_table_locations_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary),
                main_body=(
                    "Evidence [1] is detailed in Tables S1, S2, S3, S4, S5, "
                    "S6, S7 and S8 and illustrated by Figure 1."
                ),
                workbook_table_ids=("S1", "S2", "S5", "S7"),
                si_table_captions=("S3", "S4", "S6", "S7", "S8"),
                root_relative_workbook_targets=True,
            )
            receipt = audit_bundle(config)
            codes = _finding_codes(receipt)
            self.assertEqual("PASS", receipt["structural_machine_audit"])
            self.assertEqual(
                ["Table S1", "Table S2", "Table S5", "Table S7", "Data"],
                receipt["workbook"]["sheets"],
            )
            self.assertEqual(
                ["S1", "S2", "S5", "S7"],
                receipt["workbook"]["table_identifiers"],
            )
            si_document = next(
                document
                for document in receipt["documents"]
                if document["kind"] == "si"
            )
            self.assertEqual(
                ["S3", "S4", "S6", "S7", "S8"],
                [caption["id"] for caption in si_document["table_captions"]],
            )
            self.assertIn("S7", receipt["workbook"]["table_identifiers"])
            self.assertIn("S7", [item["id"] for item in si_document["table_captions"]])
            self.assertFalse(
                {
                    "unsafe_relationship_target",
                    "unresolved_worksheet_relationship",
                    "invalid_defined_name_scope",
                    "duplicate_table_caption",
                    "dangling_table_reference",
                }
                & codes
            )

    def test_malicious_cli_manifest_and_package_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(root)
            escaped = BundleConfig(
                bundle_root=root,
                main_docx="../outside.docx",
                si_docx=config.si_docx,
                workbook=config.workbook,
                tiff_dir=config.tiff_dir,
                toc=config.toc,
                checksum_manifest=config.checksum_manifest,
                qa_receipt=config.qa_receipt,
            )
            self.assertIn("unsafe_relative_path", _finding_codes(audit_bundle(escaped)))

            manifest = root / "checksums.sha256"
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + f"{'0' * 64}  ../escape.txt\n",
                encoding="utf-8",
            )
            self.assertIn("unsafe_manifest_path", _finding_codes(audit_bundle(config)))

        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary), malicious_target=True, malicious_member=True
            )
            codes = _finding_codes(audit_bundle(config))
            self.assertIn("unsafe_relationship_target", codes)
            self.assertIn("unsafe_archive_member", codes)

    def test_standalone_tiff_and_docx_graphical_tocs_are_audited(self) -> None:
        for toc_kind, expected_kind in (("tiff", "raster_image"), ("docx", "docx_container")):
            with self.subTest(toc_kind=toc_kind), tempfile.TemporaryDirectory() as temporary:
                config = _create_bundle(Path(temporary), toc_kind=toc_kind)
                receipt = audit_bundle(config)
                self.assertEqual("PASS", receipt["gates"]["toc"]["status"])
                self.assertEqual(expected_kind, receipt["toc"]["kind"])
                self.assertTrue(receipt["toc"]["embedded_images"])
                if toc_kind == "tiff":
                    self.assertEqual("MATCH", receipt["toc"]["association"]["status"])
                    self.assertEqual(
                        "unique_unassigned",
                        receipt["toc"]["association"]["candidate_source"],
                    )
                    self.assertTrue(
                        receipt["toc"]["association"]["canonical_pixels_match"]
                    )
                else:
                    self.assertEqual(
                        "DESIGNATED_CONTAINER",
                        receipt["toc"]["association"]["status"],
                    )
                    self.assertFalse(
                        receipt["toc"]["association"]["main_docx_pairing_required"]
                    )

    def test_standalone_toc_tiff_requires_native_rgb_and_tiff_encoding(self) -> None:
        for mode, encoding, color, expected_code in (
            ("RGBA", "TIFF", (40, 100, 180, 255), "non_rgb_native_toc_tiff"),
            ("L", "TIFF", 100, "non_rgb_native_toc_tiff"),
            ("RGB", "PNG", (40, 100, 180), "invalid_toc_tiff_format"),
        ):
            with self.subTest(mode=mode, encoding=encoding), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                main_color = (100, 100, 100) if mode == "L" else (40, 100, 180)
                config = _create_bundle(root, main_toc_color=main_color)
                Image.new(mode, (6, 3), color).save(root / config.toc, format=encoding)
                qa_path = root / "external-qa.json"
                qa = json.loads(qa_path.read_text(encoding="utf-8"))
                qa["artifact_hashes"][config.toc] = _file_hash(root / config.toc)
                qa_path.write_text(json.dumps(qa), encoding="utf-8")
                _write_manifest(root)
                receipt = audit_bundle(config)
                self.assertIn(expected_code, _finding_codes(receipt))
                self.assertEqual("FAIL", receipt["gates"]["toc"]["status"])
                self.assertEqual("PASS", receipt["gates"]["manifest"]["status"])
                self.assertEqual(1, receipt["exit_code"])

    def test_native_rgb_toc_accepts_equivalent_opaque_rgba_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary), main_toc_color=(40, 100, 180, 255)
            )
            receipt = audit_bundle(config)
            self.assertEqual(0, receipt["exit_code"])
            self.assertEqual("MATCH", receipt["toc"]["association"]["status"])
            self.assertEqual(
                "drop_opaque_alpha",
                receipt["toc"]["main_docx_image"]["canonicalization"]["conversion"],
            )

    def test_graphical_toc_aspect_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), toc_kind="docx", toc_stretched=True)
            self.assertIn("toc_display_aspect_mismatch", _finding_codes(audit_bundle(config)))

    def test_standalone_toc_pixel_mismatch_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(
                Path(temporary), main_toc_color=(180, 100, 40)
            )
            receipt = audit_bundle(config)
            self.assertIn("toc_pixel_mismatch", _finding_codes(receipt))
            self.assertEqual("MISMATCH", receipt["toc"]["association"]["status"])
            self.assertFalse(
                receipt["toc"]["association"]["canonical_pixels_match"]
            )

    def test_standalone_toc_missing_main_occurrence_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), main_toc_count=0)
            receipt = audit_bundle(config)
            self.assertIn("main_toc_image_missing", _finding_codes(receipt))
            self.assertEqual("MISSING", receipt["toc"]["association"]["status"])

    def test_standalone_toc_ambiguous_main_occurrences_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), main_toc_count=2)
            receipt = audit_bundle(config)
            self.assertIn("main_toc_image_ambiguous", _finding_codes(receipt))
            self.assertEqual("AMBIGUOUS", receipt["toc"]["association"]["status"])

    def test_standalone_toc_stretched_main_occurrence_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), main_toc_stretched=True)
            receipt = audit_bundle(config)
            self.assertIn("toc_display_aspect_mismatch", _finding_codes(receipt))
            self.assertEqual("MISMATCH", receipt["toc"]["association"]["status"])

    def test_stale_qa_hash_and_unapproved_review_cannot_be_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = _create_bundle(root)
            # Re-encode identical native pixels so the structural image checks pass
            # while the QA preimage hash becomes stale.
            Image.new("RGB", (6, 3), (40, 100, 180)).save(
                root / "graphical-toc.tiff", format="TIFF", dpi=(72, 72)
            )
            _write_manifest(root)
            receipt = audit_bundle(config)
            self.assertIn("stale_qa_artifact_hash", _finding_codes(receipt))
            self.assertEqual("PASS", receipt["structural_machine_audit"])
            self.assertEqual("NOT_ELIGIBLE", receipt["publication_eligibility"])
            self.assertEqual(2, receipt["exit_code"])
            self.assertEqual("STALE", receipt["external_qa"]["status"])

        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary))
            missing_qa_config = BundleConfig(
                bundle_root=config.bundle_root,
                main_docx=config.main_docx,
                si_docx=config.si_docx,
                workbook=config.workbook,
                tiff_dir=config.tiff_dir,
                toc=config.toc,
                checksum_manifest=config.checksum_manifest,
            )
            receipt = audit_bundle(missing_qa_config)
            self.assertIn("external_qa_missing", _finding_codes(receipt))
            self.assertEqual("PASS", receipt["structural_machine_audit"])
            self.assertEqual("NOT_ELIGIBLE", receipt["publication_eligibility"])

        with tempfile.TemporaryDirectory() as temporary:
            config = _create_bundle(Path(temporary), qa_outcome="changes_required")
            receipt = audit_bundle(config)
            self.assertIn("qa_not_approved", _finding_codes(receipt))
            self.assertIn("qa_unresolved_blockers", _finding_codes(receipt))
            self.assertEqual("NOT_APPROVED", receipt["external_qa"]["status"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "--bundle-root", str(config.bundle_root),
                        "--main-docx", config.main_docx,
                        "--si-docx", config.si_docx,
                        "--workbook", config.workbook,
                        "--tiff-dir", config.tiff_dir,
                        "--toc", config.toc,
                        "--checksum-manifest", config.checksum_manifest,
                        "--qa-receipt", config.qa_receipt or "",
                    ]
                )
            self.assertEqual(2, exit_code)


class DependencyAndPathUnitTests(unittest.TestCase):
    def test_opc_root_relative_target_stays_inside_package(self) -> None:
        self.assertEqual(
            "xl/worksheets/sheet1.xml",
            _resolve_archive_target(
                "xl/workbook.xml", "/xl/worksheets/sheet1.xml"
            ),
        )
        for target in (
            "//host/share/sheet1.xml",
            "C:/outside/sheet1.xml",
            "/../../outside.xml",
        ):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    _resolve_archive_target("xl/workbook.xml", target)

    def test_bundle_config_keeps_all_paths_explicit(self) -> None:
        config = BundleConfig(
            bundle_root=Path("bundle"),
            main_docx="main.docx",
            si_docx="si.docx",
            workbook="supporting.xlsx",
            tiff_dir="figures",
            toc="graphical-toc.tiff",
            checksum_manifest="checksums.sha256",
        )
        self.assertEqual("figures", config.tiff_dir)
        self.assertIsNone(config.qa_receipt)


if __name__ == "__main__":
    unittest.main()
