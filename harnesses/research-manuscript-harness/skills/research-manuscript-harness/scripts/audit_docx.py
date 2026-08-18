#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from io import BytesIO
from pathlib import Path

try:
    from docx import Document
    from docx.enum.text import WD_LINE_SPACING
    from docx.oxml.ns import qn
except ImportError as exc:
    raise SystemExit("python-docx is required for DOCX auditing") from exc

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Pillow is required for embedded-image auditing") from exc


def all_paragraphs(document: Document):
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit manuscript DOCX structure and author formatting rules.")
    parser.add_argument("docx", type=Path)
    parser.add_argument("--expect-double-spacing", action="store_true")
    parser.add_argument("--expect-plain-black", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    document = Document(args.docx)
    paragraphs = list(all_paragraphs(document))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    findings: list[dict[str, object]] = []

    if "{" in text or "}" in text:
        findings.append({"severity": "blocker", "check": "unresolved_brace_comment"})

    if args.expect_double_spacing:
        failures = [index for index, paragraph in enumerate(paragraphs) if paragraph.paragraph_format.line_spacing_rule != WD_LINE_SPACING.DOUBLE]
        if failures:
            findings.append({"severity": "blocker", "check": "double_spacing", "paragraph_indices": failures[:50], "failure_count": len(failures)})

    if args.expect_plain_black:
        run_failures = []
        for index, run in enumerate(document.element.body.iter(qn("w:r"))):
            properties = run.find(qn("w:rPr"))
            color = properties.find(qn("w:color")) if properties is not None else None
            bold = properties.find(qn("w:b")) if properties is not None else None
            italic = properties.find(qn("w:i")) if properties is not None else None
            underline = properties.find(qn("w:u")) if properties is not None else None
            if color is None or color.get(qn("w:val")) != "000000":
                run_failures.append(index); continue
            if bold is None or bold.get(qn("w:val")) not in {"0", "false", "off"}:
                run_failures.append(index); continue
            if italic is None or italic.get(qn("w:val")) not in {"0", "false", "off"}:
                run_failures.append(index); continue
            if underline is None or underline.get(qn("w:val")) != "none":
                run_failures.append(index)
        if run_failures:
            findings.append({"severity": "blocker", "check": "plain_black_text", "run_indices": run_failures[:50], "failure_count": len(run_failures)})

    image_rows = []
    for index, shape in enumerate(document.inline_shapes, start=1):
        relationship_id = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
        blob = document.part.related_parts[relationship_id].blob
        with Image.open(BytesIO(blob)) as image:
            pixel_ratio = image.width / image.height
        display_ratio = shape.width / shape.height
        image_rows.append({"index": index, "pixel_ratio": pixel_ratio, "display_ratio": display_ratio, "ratio_error": abs(pixel_ratio - display_ratio)})
        if abs(pixel_ratio - display_ratio) >= 1e-4:
            findings.append({"severity": "blocker", "check": "image_aspect_ratio", "image_index": index, "pixel_ratio": pixel_ratio, "display_ratio": display_ratio})

    captions = [paragraph.text for paragraph in document.paragraphs if re.match(r"^Figure\s+\d+\.", paragraph.text)]
    for caption in captions:
        labels = re.findall(r"\(([A-Z])\)", caption)
        if labels and labels != [chr(ord("A") + offset) for offset in range(len(labels))]:
            findings.append({"severity": "warning", "check": "panel_caption_sequence", "caption": caption})

    result = {
        "document": str(args.docx),
        "paragraph_count": len(paragraphs),
        "table_count": len(document.tables),
        "image_count": len(document.inline_shapes),
        "figure_caption_count": len(captions),
        "images": image_rows,
        "findings": findings,
        "verdict": "PASS" if not any(item["severity"] == "blocker" for item in findings) else "BLOCK",
    }
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(output, encoding="utf-8")
    print(output)
    raise SystemExit(0 if result["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
