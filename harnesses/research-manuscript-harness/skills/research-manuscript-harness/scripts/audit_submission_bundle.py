#!/usr/bin/env python3
"""Read-only structural audit for a manuscript submission bundle.

The auditor intentionally uses only the Python standard library for package,
XML, JSON, path, and checksum work. Pillow is imported at run time for pixel
inspection; when it is unavailable, image verification is explicitly
UNVERIFIED and the bundle cannot be submission-ready.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

AUDIT_SCHEMA = "aaalab.submission-bundle-audit/v1"
QA_SCHEMA = "aaalab.external-qa/v1"
QA_SCOPES = ("visual_readability", "citation_semantics", "scientific_review")
STRUCTURAL_GATES = (
    "path_safety",
    "docx_packages",
    "bibliography",
    "figure_table_references",
    "workbook",
    "images",
    "toc",
    "manifest",
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?:supplementary\s+)?(?:figure|fig\.?)[\s_.-]*(S?\s*\d+)\s*(?:[.:|–—-]|$)",
    re.IGNORECASE,
)
TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:supplementary\s+)?table[\s_.-]*(S?\s*\d+)\s*(?:[.:|–—-]|$)",
    re.IGNORECASE,
)
BIBLIOGRAPHY_ENTRY_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)[.)])\s*")
CITATION_GRAMMAR_RE = re.compile(
    r"\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*"
)
LABEL_TOKEN_RE = re.compile(r"S?\s*\d+", re.IGNORECASE)
SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
A1_RANGE_RE = re.compile(r"\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?$")


@dataclass(frozen=True)
class BundleConfig:
    bundle_root: Path
    main_docx: str
    si_docx: str
    workbook: str
    tiff_dir: str
    toc: str
    checksum_manifest: str
    qa_receipt: str | None = None


class FindingLog:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(
        self,
        gate: str,
        severity: str,
        code: str,
        message: str,
        *,
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        finding: dict[str, Any] = {
            "gate": gate,
            "severity": severity,
            "code": code,
            "message": message,
        }
        if path is not None:
            finding["path"] = path
        if details:
            finding["details"] = details
        self.items.append(finding)

    def status(self, gate: str) -> str:
        relevant = [item for item in self.items if item["gate"] == gate]
        if any(item["severity"] == "blocker" for item in relevant):
            return "FAIL"
        if any(item["severity"] == "unverified" for item in relevant):
            return "UNVERIFIED"
        return "PASS"


def _canonical_label(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _is_office_lock(relative_path: str) -> bool:
    return PurePosixPath(relative_path).name.startswith("~$")


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _normalize_relative(value: str, *, allow_root: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("path must be a non-empty text value")
    portable = value.replace("\\", "/")
    if portable.startswith("/") or portable.startswith("//") or re.match(r"^[A-Za-z]:", portable):
        raise ValueError("absolute paths and drive-qualified paths are forbidden")
    raw_parts = portable.split("/")
    if any(part == ".." for part in raw_parts):
        raise ValueError("parent traversal is forbidden")
    if any(part == "" for part in raw_parts):
        raise ValueError("empty path components are forbidden")
    parts = [part for part in raw_parts if part != "."]
    if not parts:
        if allow_root:
            return "."
        raise ValueError("the bundle root is not a file path")
    if any(":" in part for part in parts):
        raise ValueError("colon-bearing path components are not portable")
    return "/".join(parts)


def _resolve_bundle_path(root: Path, relative_path: str) -> Path:
    if relative_path == ".":
        return root
    current = root
    for component in relative_path.split("/"):
        current = current / component
        if os.path.lexists(current) and _is_link(current):
            raise ValueError("symbolic links and junctions are forbidden")
    candidate = root.joinpath(*relative_path.split("/"))
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("path resolves outside the bundle") from exc
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _scan_error_path(root: Path, error: OSError) -> str | None:
    filename = error.filename
    if not isinstance(filename, (str, os.PathLike)):
        return None
    try:
        error_path = Path(filename)
        if not error_path.is_absolute():
            error_path = root / error_path
        relative = error_path.relative_to(root).as_posix()
    except (OSError, TypeError, ValueError):
        return None
    return relative or "."


def _inventory_bundle(root: Path, findings: FindingLog) -> tuple[dict[str, dict[str, Any]], list[str]]:
    inventory: dict[str, dict[str, Any]] = {}
    excluded_locks: list[str] = []

    def record_scan_error(error: OSError) -> None:
        findings.add(
            "path_safety",
            "blocker",
            "bundle_directory_scan_failed",
            "A bundle directory could not be completely enumerated.",
            path=_scan_error_path(root, error),
        )

    for directory, directory_names, file_names in os.walk(
        root, topdown=True, onerror=record_scan_error, followlinks=False
    ):
        directory_path = Path(directory)
        safe_directories: list[str] = []
        for name in sorted(directory_names):
            child = directory_path / name
            relative = child.relative_to(root).as_posix()
            if _is_link(child):
                findings.add(
                    "path_safety",
                    "blocker",
                    "symlink_rejected",
                    "A symbolic link or junction was found inside the bundle.",
                    path=relative,
                )
            else:
                safe_directories.append(name)
        directory_names[:] = safe_directories
        for name in sorted(file_names):
            child = directory_path / name
            relative = child.relative_to(root).as_posix()
            if _is_link(child):
                findings.add(
                    "path_safety",
                    "blocker",
                    "symlink_rejected",
                    "A symbolic link or junction was found inside the bundle.",
                    path=relative,
                )
                continue
            try:
                file_stat = child.stat()
                if not stat.S_ISREG(file_stat.st_mode):
                    findings.add(
                        "path_safety",
                        "blocker",
                        "non_regular_bundle_entry",
                        "A non-regular filesystem entry was found inside the bundle.",
                        path=relative,
                    )
                    continue
                if _is_office_lock(relative):
                    excluded_locks.append(relative)
                    continue
                inventory[relative] = {
                    "path": relative,
                    "bytes": file_stat.st_size,
                    "sha256": _sha256_file(child),
                }
            except OSError:
                findings.add(
                    "path_safety",
                    "blocker",
                    "unreadable_bundle_file",
                    "A regular bundle file could not be read.",
                    path=relative,
                )
    return inventory, sorted(excluded_locks)


def _safe_archive_member(name: str) -> bool:
    if "\\" in name or any(ord(character) < 32 or ord(character) == 127 for character in name):
        return False
    decoded = unquote(name).replace("\\", "/")
    if not decoded or decoded.startswith("/") or re.match(r"^[A-Za-z]:", decoded):
        return False
    parts = decoded.split("/")
    return all(part not in {"", ".", ".."} for part in parts if part != "") and ".." not in parts


def _inspect_zip_members(zf: zipfile.ZipFile, package_path: str, gate: str, findings: FindingLog) -> set[str]:
    names: set[str] = set()
    duplicates: set[str] = set()
    for info in zf.infolist():
        name = info.filename
        if not _safe_archive_member(name):
            findings.add(
                gate,
                "blocker",
                "unsafe_archive_member",
                "An Office package contains an unsafe member path.",
                path=package_path,
                details={"member": name},
            )
            continue
        if name in names:
            duplicates.add(name)
        names.add(name)
        mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_IFMT(mode) == stat.S_IFLNK:
            findings.add(
                gate,
                "blocker",
                "archive_symlink_rejected",
                "An Office package contains a symbolic-link member.",
                path=package_path,
                details={"member": name},
            )
    for name in sorted(duplicates):
        findings.add(
            gate,
            "blocker",
            "duplicate_archive_member",
            "An Office package contains duplicate member names.",
            path=package_path,
            details={"member": name},
        )
    return names


def _read_xml(
    zf: zipfile.ZipFile,
    member: str,
    package_path: str,
    gate: str,
    findings: FindingLog,
) -> ET.Element | None:
    try:
        return ET.fromstring(zf.read(member))
    except KeyError:
        findings.add(
            gate,
            "blocker",
            "missing_package_part",
            "A required Office XML part is missing.",
            path=package_path,
            details={"member": member},
        )
    except (ET.ParseError, OSError, RuntimeError, ValueError):
        findings.add(
            gate,
            "blocker",
            "invalid_package_xml",
            "An Office XML part could not be parsed.",
            path=package_path,
            details={"member": member},
        )
    return None


def _relationship_member(source_member: str) -> str:
    directory, base = posixpath.split(source_member)
    return posixpath.join(directory, "_rels", base + ".rels")


def _resolve_archive_target(source_member: str, target: str) -> str:
    decoded = unquote(target).replace("\\", "/")
    parsed = urlsplit(decoded)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or decoded.startswith("//")
        or re.match(r"^[A-Za-z]:", decoded)
    ):
        raise ValueError("external relationship target")
    if decoded.startswith("/"):
        # OPC permits package-root-relative part names such as
        # /xl/worksheets/sheet1.xml. This leading slash is a package URI
        # marker, not a host-filesystem root.
        package_relative = parsed.path[1:]
        combined = posixpath.normpath(package_relative)
    else:
        combined = posixpath.normpath(
            posixpath.join(posixpath.dirname(source_member), parsed.path)
        )
    if (
        combined in {"", ".", ".."}
        or combined.startswith("../")
        or combined.startswith("/")
        or re.match(r"^[A-Za-z]:", combined)
        or any(":" in part for part in combined.split("/"))
    ):
        raise ValueError("relationship target escapes the package")
    return combined


def _read_relationships(
    zf: zipfile.ZipFile,
    source_member: str,
    package_path: str,
    gate: str,
    findings: FindingLog,
) -> dict[str, dict[str, Any]]:
    rel_member = _relationship_member(source_member)
    if rel_member not in zf.namelist():
        return {}
    root = _read_xml(zf, rel_member, package_path, gate, findings)
    if root is None:
        return {}
    relationships: dict[str, dict[str, Any]] = {}
    for element in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
        rel_id = element.get("Id")
        target = element.get("Target")
        rel_type = element.get("Type", "")
        external = element.get("TargetMode", "").casefold() == "external"
        if not rel_id or target is None:
            findings.add(
                gate,
                "blocker",
                "invalid_relationship",
                "An Office relationship is missing its identifier or target.",
                path=package_path,
                details={"source": source_member},
            )
            continue
        if rel_id in relationships:
            findings.add(
                gate,
                "blocker",
                "duplicate_relationship_id",
                "An Office relationship identifier is duplicated.",
                path=package_path,
                details={"source": source_member, "relationship_id": rel_id},
            )
            continue
        resolved: str | None = None
        if not external:
            try:
                resolved = _resolve_archive_target(source_member, target)
            except ValueError:
                findings.add(
                    gate,
                    "blocker",
                    "unsafe_relationship_target",
                    "An Office relationship target escapes or addresses outside its package.",
                    path=package_path,
                    details={"source": source_member, "relationship_id": rel_id},
                )
        relationships[rel_id] = {
            "target": resolved,
            "external": external,
            "type": rel_type,
        }
    return relationships


def _paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{W_NS}}}t" and node.text:
            pieces.append(node.text)
        elif node.tag == f"{{{W_NS}}}tab":
            pieces.append("\t")
        elif node.tag in {f"{{{W_NS}}}br", f"{{{W_NS}}}cr"}:
            pieces.append("\n")
    return "".join(pieces)


def _owned_image_occurrences(
    paragraph: ET.Element,
    relative_path: str,
    gate: str,
    findings: FindingLog,
) -> list[tuple[ET.Element, tuple[int, int] | None]]:
    blip_tag = f"{{{A_NS}}}blip"
    owner_tags = {
        f"{{{WP_NS}}}inline",
        f"{{{WP_NS}}}anchor",
    }
    extent_tag = f"{{{WP_NS}}}extent"
    blips = list(paragraph.iter(blip_tag))
    owners = [element for element in paragraph.iter() if element.tag in owner_tags]
    owners_by_blip: dict[int, list[ET.Element]] = {id(blip): [] for blip in blips}
    blip_count_by_owner: dict[int, int] = {}
    for owner in owners:
        owned_blips = list(owner.iter(blip_tag))
        blip_count_by_owner[id(owner)] = len(owned_blips)
        for blip in owned_blips:
            candidates = owners_by_blip.get(id(blip))
            if candidates is not None:
                candidates.append(owner)

    occurrences: list[tuple[ET.Element, tuple[int, int] | None]] = []
    for image_index, blip in enumerate(blips, start=1):
        candidate_owners = owners_by_blip[id(blip)]
        if not candidate_owners:
            findings.add(
                gate,
                "unverified",
                "missing_image_drawing_owner",
                "An image occurrence has no owning Word inline or anchor drawing.",
                path=relative_path,
                details={"paragraph_image_index": image_index},
            )
            occurrences.append((blip, None))
            continue
        if len(candidate_owners) != 1:
            findings.add(
                gate,
                "unverified",
                "ambiguous_image_drawing_owner",
                "An image occurrence is nested in more than one Word inline or anchor drawing.",
                path=relative_path,
                details={
                    "paragraph_image_index": image_index,
                    "owner_count": len(candidate_owners),
                },
            )
            occurrences.append((blip, None))
            continue

        owner = candidate_owners[0]
        if blip_count_by_owner[id(owner)] != 1:
            findings.add(
                gate,
                "unverified",
                "ambiguous_image_drawing_owner",
                "A Word inline or anchor drawing contains multiple image occurrences.",
                path=relative_path,
                details={
                    "paragraph_image_index": image_index,
                    "owner_image_count": blip_count_by_owner[id(owner)],
                },
            )
            occurrences.append((blip, None))
            continue

        extents = owner.findall(f"./{extent_tag}")
        if not extents:
            findings.add(
                gate,
                "unverified",
                "missing_image_drawing_extent",
                "An image occurrence's owning drawing has no extent.",
                path=relative_path,
                details={"paragraph_image_index": image_index},
            )
            occurrences.append((blip, None))
            continue
        if len(extents) != 1:
            findings.add(
                gate,
                "unverified",
                "ambiguous_image_drawing_extent",
                "An image occurrence's owning drawing has multiple extents.",
                path=relative_path,
                details={
                    "paragraph_image_index": image_index,
                    "extent_count": len(extents),
                },
            )
            occurrences.append((blip, None))
            continue

        try:
            extent = (int(extents[0].get("cx", "")), int(extents[0].get("cy", "")))
        except ValueError:
            extent = (0, 0)
        if extent[0] <= 0 or extent[1] <= 0:
            findings.add(
                gate,
                "unverified",
                "invalid_image_drawing_extent",
                "An image occurrence's owning drawing has no usable positive extent.",
                path=relative_path,
                details={"paragraph_image_index": image_index},
            )
            occurrences.append((blip, None))
            continue
        occurrences.append((blip, extent))
    return occurrences


def _caption_label(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.match(text)
    return _canonical_label(match.group(1)) if match else None


def _parse_citation_inner(inner: str) -> tuple[set[int], str | None]:
    if not CITATION_GRAMMAR_RE.fullmatch(inner.strip()):
        return set(), "malformed"
    values: set[int] = set()
    for item in re.split(r"[,;]", inner):
        item = item.strip()
        range_match = re.fullmatch(r"(\d+)\s*[-–—]\s*(\d+)", item)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if end < start:
                return values, "descending"
            if end - start > 10000:
                return values, "too_large"
            values.update(range(start, end + 1))
        else:
            values.add(int(item))
    return values, None


def _numeric_citations(
    text: str,
    document_path: str,
    block_index: int,
    findings: FindingLog,
) -> set[int]:
    citations: set[int] = set()
    for match in re.finditer(r"\[([^\[\]]{1,200})\]", text):
        inner = match.group(1).strip()
        if not re.search(r"\d", inner):
            continue
        numeric_punctuation_only = bool(re.fullmatch(r"[\d\s,;\-–—]+", inner))
        if not numeric_punctuation_only:
            # SMARTS and other bracketed scientific notation contain letters,
            # hashes, atom operators, or other non-citation syntax.
            continue
        values, error = _parse_citation_inner(inner)
        if error:
            findings.add(
                "bibliography",
                "blocker",
                "invalid_numeric_citation_range",
                "A numeric citation bracket is malformed, descending, or unreasonably large.",
                path=document_path,
                details={"block_index": block_index, "reason": error},
            )
        citations.update(values)
    return citations


def _label_references(
    text: str,
    kind: str,
    findings: FindingLog,
    relative_path: str,
) -> set[str]:
    if kind == "figure":
        leader = r"(?:figures?|figs?\.?)"
    else:
        leader = r"tables?"
    phrase = re.compile(
        rf"\b{leader}\s+(S?\s*\d+(?:[A-Za-z])?(?:\s*(?:,|;|&|and|to|[-–—])\s*S?\s*\d+(?:[A-Za-z])?)*)",
        re.IGNORECASE,
    )
    references: set[str] = set()
    for phrase_match in phrase.finditer(text):
        body = phrase_match.group(1)
        tokens = list(LABEL_TOKEN_RE.finditer(body))
        labels: list[str] = []
        for token_index, token in enumerate(tokens):
            label = _canonical_label(token.group(0))
            if token_index and not label.startswith("S") and labels[-1].startswith("S"):
                separator = body[tokens[token_index - 1].end() : token.start()]
                if re.search(r"(?:to|[-–—])", separator, re.IGNORECASE):
                    label = f"S{label}"
            labels.append(label)
            references.add(label)
        for token_index, (left, right) in enumerate(zip(tokens, tokens[1:])):
            separator = body[left.end() : right.start()]
            if not re.search(r"(?:to|[-–—])", separator, re.IGNORECASE):
                continue
            left_label = labels[token_index]
            right_label = labels[token_index + 1]
            left_prefix = "S" if left_label.startswith("S") else ""
            right_prefix = "S" if right_label.startswith("S") else ""
            if left_prefix != right_prefix:
                findings.add(
                    "figure_table_references",
                    "blocker",
                    f"invalid_{kind}_reference_range",
                    f"A {kind} reference range crosses the main and Supporting Information series.",
                    path=relative_path,
                    details={
                        "reference": phrase_match.group(0),
                        "start": left_label,
                        "end": right_label,
                        "reason": "mixed_series",
                    },
                )
                continue
            start = int(left_label.lstrip("S"))
            end = int(right_label.lstrip("S"))
            reason: str | None = None
            if end < start:
                reason = "descending"
            elif end - start > 10000:
                reason = "too_large"
            if reason:
                findings.add(
                    "figure_table_references",
                    "blocker",
                    f"invalid_{kind}_reference_range",
                    f"A {kind} reference range is descending or unreasonably large.",
                    path=relative_path,
                    details={
                        "reference": phrase_match.group(0),
                        "start": left_label,
                        "end": right_label,
                        "reason": reason,
                    },
                )
                continue
            references.update(f"{left_prefix}{number}" for number in range(start, end + 1))
    return references


def _associate_caption_images(blocks: list[dict[str, Any]], caption_index: int) -> list[dict[str, Any]]:
    associated = list(blocks[caption_index]["images"])
    if associated:
        return associated
    for index in range(caption_index - 1, max(-1, caption_index - 5), -1):
        block = blocks[index]
        if _caption_label(FIGURE_CAPTION_RE, block["text"]) is not None:
            break
        if block["images"]:
            associated[0:0] = block["images"]
            continue
        if block["text"].strip():
            break
    return associated


def _parse_docx(path: Path, relative_path: str, kind: str, findings: FindingLog) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": relative_path,
        "kind": kind,
        "bibliography_numbers": [],
        "citations": set(),
        "figure_captions": [],
        "table_captions": [],
        "figure_references": set(),
        "table_references": set(),
        "images": [],
    }
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = _inspect_zip_members(zf, relative_path, "docx_packages", findings)
            document = _read_xml(zf, "word/document.xml", relative_path, "docx_packages", findings)
            if document is None:
                return result
            relationships = _read_relationships(
                zf, "word/document.xml", relative_path, "docx_packages", findings
            )
            body = document.find(f"{{{W_NS}}}body")
            if body is None:
                findings.add(
                    "docx_packages",
                    "blocker",
                    "missing_document_body",
                    "A DOCX package has no document body.",
                    path=relative_path,
                )
                return result
            blocks: list[dict[str, Any]] = []
            image_counter = 0
            for paragraph in body.iter(f"{{{W_NS}}}p"):
                block_images: list[dict[str, Any]] = []
                for blip, extent in _owned_image_occurrences(
                    paragraph,
                    relative_path,
                    "images",
                    findings,
                ):
                    image_counter += 1
                    rel_id = blip.get(f"{{{R_NS}}}embed")
                    linked_id = blip.get(f"{{{R_NS}}}link")
                    if linked_id and not rel_id:
                        findings.add(
                            "docx_packages",
                            "blocker",
                            "external_docx_image",
                            "A DOCX image is linked rather than embedded.",
                            path=relative_path,
                            details={"relationship_id": linked_id},
                        )
                        continue
                    relationship = relationships.get(rel_id or "")
                    if relationship is None:
                        findings.add(
                            "docx_packages",
                            "blocker",
                            "missing_image_relationship",
                            "An embedded drawing has no resolvable image relationship.",
                            path=relative_path,
                            details={"relationship_id": rel_id or ""},
                        )
                        continue
                    if relationship["external"] or relationship["target"] is None:
                        findings.add(
                            "docx_packages",
                            "blocker",
                            "external_or_unsafe_docx_image",
                            "An embedded drawing resolves outside the DOCX package.",
                            path=relative_path,
                            details={"relationship_id": rel_id or ""},
                        )
                        continue
                    target = relationship["target"]
                    if target not in names:
                        findings.add(
                            "docx_packages",
                            "blocker",
                            "missing_embedded_image",
                            "An image relationship points to a missing package member.",
                            path=relative_path,
                            details={"member": target},
                        )
                        continue
                    try:
                        blob = zf.read(target)
                    except (KeyError, OSError, RuntimeError):
                        findings.add(
                            "docx_packages",
                            "blocker",
                            "unreadable_embedded_image",
                            "An embedded image could not be read.",
                            path=relative_path,
                            details={"member": target},
                        )
                        continue
                    image = {
                        "occurrence": f"{relative_path}#image-{image_counter}",
                        "relationship_id": rel_id,
                        "member": target,
                        "encoded_sha256": hashlib.sha256(blob).hexdigest(),
                        "extent_emu": extent,
                        "blob": blob,
                    }
                    block_images.append(image)
                    result["images"].append(image)
                num_element = paragraph.find(
                    f"./{{{W_NS}}}pPr/{{{W_NS}}}numPr/{{{W_NS}}}numId"
                )
                blocks.append(
                    {
                        "text": _paragraph_text(paragraph),
                        "images": block_images,
                        "num_id": num_element.get(f"{{{W_NS}}}val") if num_element is not None else None,
                    }
                )
    except (zipfile.BadZipFile, OSError):
        findings.add(
            "docx_packages",
            "blocker",
            "invalid_docx_package",
            "A DOCX input is not a readable ZIP package.",
            path=relative_path,
        )
        return result

    heading_index: int | None = None
    for index, block in enumerate(blocks):
        if block["text"].strip().casefold() in {"references", "bibliography"}:
            heading_index = index
            break

    bibliography_entry_indexes: set[int] = set()
    if heading_index is not None:
        literal_numbers: list[int] = []
        automatic_entries = 0
        for index, block in enumerate(blocks[heading_index + 1 :], start=heading_index + 1):
            match = BIBLIOGRAPHY_ENTRY_RE.match(block["text"])
            if match:
                bibliography_entry_indexes.add(index)
                literal_numbers.append(int(match.group(1) or match.group(2)))
            elif block["num_id"] and block["text"].strip():
                bibliography_entry_indexes.add(index)
                automatic_entries += 1
        if literal_numbers:
            result["bibliography_numbers"] = literal_numbers
        if automatic_entries:
            findings.add(
                "bibliography",
                "unverified",
                "automatic_bibliography_numbering_unresolved",
                "Word-generated bibliography labels were not resolved from numbering definitions and overrides.",
                path=relative_path,
                details={"entry_count": automatic_entries},
            )
        elif not literal_numbers:
            findings.add(
                "bibliography",
                "blocker",
                "empty_numeric_bibliography",
                "A bibliography heading exists but no numeric entries were found.",
                path=relative_path,
            )

    for index, block in enumerate(blocks, start=1):
        if index - 1 in bibliography_entry_indexes:
            continue
        result["citations"].update(
            _numeric_citations(block["text"], relative_path, index, findings)
        )

    if heading_index is None and result["citations"]:
        # Resolution can still succeed against the main document bibliography,
        # which is common for a separate Supporting Information document.
        findings.add(
            "bibliography",
            "warning",
            "local_bibliography_absent",
            "Numeric citations will be resolved against the main-document bibliography.",
            path=relative_path,
        )
    elif heading_index is None and kind == "main":
        findings.add(
            "bibliography",
            "warning",
            "numeric_bibliography_not_detected",
            "No numeric bibliography was detected; author-date citation semantics are outside this audit.",
            path=relative_path,
        )

    numbers = result["bibliography_numbers"]
    if numbers:
        duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
        if duplicates:
            findings.add(
                "bibliography",
                "blocker",
                "duplicate_bibliography_number",
                "Bibliography entry numbers are duplicated.",
                path=relative_path,
                details={"numbers": duplicates},
            )
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            findings.add(
                "bibliography",
                "blocker",
                "bibliography_noncontiguous",
                "Bibliography numbers are not the ordered contiguous sequence 1..N.",
                path=relative_path,
                details={"observed": numbers, "expected": expected},
            )

    for index, block in enumerate(blocks):
        figure_label = _caption_label(FIGURE_CAPTION_RE, block["text"])
        table_label = _caption_label(TABLE_CAPTION_RE, block["text"])
        if figure_label:
            associated = _associate_caption_images(blocks, index)
            caption = {
                "id": figure_label,
                "block_index": index + 1,
                "images": associated,
            }
            result["figure_captions"].append(caption)
            if not associated:
                findings.add(
                    "figure_table_references",
                    "blocker",
                    "figure_caption_without_image",
                    "A figure caption has no nearby embedded-image association.",
                    path=relative_path,
                    details={"figure_id": figure_label, "block_index": index + 1},
                )
        if table_label:
            result["table_captions"].append({"id": table_label, "block_index": index + 1})
        figure_references = _label_references(
            block["text"], "figure", findings, relative_path
        )
        table_references = _label_references(
            block["text"], "table", findings, relative_path
        )
        if figure_label:
            figure_references.discard(figure_label)
        if table_label:
            table_references.discard(table_label)
        result["figure_references"].update(figure_references)
        result["table_references"].update(table_references)
    return result


def _xlsx_cell_text(cell: ET.Element, shared_strings: list[str]) -> str | None:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        pieces = [node.text or "" for node in cell.findall(f".//{{{SHEET_NS}}}t")]
        return "".join(pieces)
    value = cell.find(f"{{{SHEET_NS}}}v")
    if value is None or value.text is None:
        return None
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (ValueError, IndexError):
            return None
    if cell_type == "str":
        return value.text
    return None


def _identifier_from_text(text: str, kind: str) -> str | None:
    if kind == "figure":
        pattern = re.compile(r"^\s*(?:supplementary\s+)?(?:figure|fig\.?)?[\s_.-]*(S?\d+)\b", re.IGNORECASE)
        if not re.match(r"^\s*(?:supplementary\s+)?(?:figure|fig\.?)", text, re.IGNORECASE):
            return None
    else:
        pattern = re.compile(r"^\s*(?:supplementary\s+)?table[\s_.-]*(S?\d+)\b", re.IGNORECASE)
    match = pattern.match(text)
    return _canonical_label(match.group(1)) if match else None


def _formula_sheet_references(formula: str) -> tuple[set[str], bool, bool, bool]:
    has_ref_error = "#REF!" in formula.upper()
    # String literals may contain text that looks like a sheet reference but is
    # only dynamically interpreted at runtime (for example by INDIRECT).
    scrubbed = re.sub(r'"(?:[^"]|"")*"', '""', formula)
    has_dynamic_dependency = bool(
        re.search(r"(?<![A-Za-z0-9_.])(?:_xlfn\.)?INDIRECT\s*\(", scrubbed, re.IGNORECASE)
    )
    external = bool(re.search(r"\[[^\]]+\][^!]*!", scrubbed))
    references: set[str] = set()

    quoted_pattern = re.compile(r"'((?:[^']|'')+)'!")
    quoted_spans: list[tuple[int, int]] = []
    for match in quoted_pattern.finditer(scrubbed):
        quoted_spans.append(match.span())
        value = match.group(1).replace("''", "'")
        if value.startswith("["):
            external = True
            continue
        references.update(part for part in value.split(":") if part)

    characters = list(scrubbed)
    for start, end in quoted_spans:
        characters[start:end] = " " * (end - start)
    unquoted_source = "".join(characters)
    unquoted_pattern = re.compile(
        r"(?<![\w.\]'\]])([A-Za-z_\\][A-Za-z0-9_.]*)(?::([A-Za-z_\\][A-Za-z0-9_.]*))?!"
    )
    for match in unquoted_pattern.finditer(unquoted_source):
        references.add(match.group(1))
        if match.group(2):
            references.add(match.group(2))
    return references, external, has_ref_error, has_dynamic_dependency


def _parse_workbook(path: Path, relative_path: str, findings: FindingLog) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": relative_path,
        "sheets": [],
        "excel_tables": [],
        "figure_identifiers": set(),
        "table_identifiers": set(),
        "figure_references": set(),
        "table_references": set(),
        "formula_count": 0,
    }
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = _inspect_zip_members(zf, relative_path, "workbook", findings)
            workbook = _read_xml(zf, "xl/workbook.xml", relative_path, "workbook", findings)
            if workbook is None:
                return result
            if workbook.tag != f"{{{SHEET_NS}}}workbook":
                findings.add(
                    "workbook",
                    "blocker",
                    "invalid_workbook_root",
                    "The workbook part does not have the required workbook XML root.",
                    path=relative_path,
                    details={"member": "xl/workbook.xml", "root": workbook.tag},
                )
                return result
            relationships = _read_relationships(zf, "xl/workbook.xml", relative_path, "workbook", findings)
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                shared_root = _read_xml(
                    zf, "xl/sharedStrings.xml", relative_path, "workbook", findings
                )
                if shared_root is not None:
                    for item in shared_root.findall(f"{{{SHEET_NS}}}si"):
                        shared_strings.append(
                            "".join(node.text or "" for node in item.findall(f".//{{{SHEET_NS}}}t"))
                        )

            seen_sheet_names: dict[str, str] = {}
            seen_sheet_ids: set[str] = set()
            sheet_records: list[dict[str, Any]] = []
            sheets_element = workbook.find(f"{{{SHEET_NS}}}sheets")
            sheet_elements = (
                sheets_element.findall(f"{{{SHEET_NS}}}sheet")
                if sheets_element is not None
                else []
            )
            for sheet in sheet_elements:
                name = sheet.get("name", "")
                sheet_id = sheet.get("sheetId", "")
                rel_id = sheet.get(f"{{{R_NS}}}id", "")
                if (
                    not name
                    or not sheet_id.isdigit()
                    or int(sheet_id) < 1
                    or not rel_id
                ):
                    findings.add(
                        "workbook",
                        "blocker",
                        "invalid_sheet_identifier",
                        "A workbook sheet is missing its name, sheetId, or relationship identifier.",
                        path=relative_path,
                    )
                    continue
                folded = name.casefold()
                if folded in seen_sheet_names:
                    findings.add(
                        "workbook",
                        "blocker",
                        "duplicate_sheet_name",
                        "Workbook sheet names are duplicated case-insensitively.",
                        path=relative_path,
                        details={"sheet": name},
                    )
                seen_sheet_names[folded] = name
                if sheet_id in seen_sheet_ids:
                    findings.add(
                        "workbook",
                        "blocker",
                        "duplicate_sheet_id",
                        "Workbook sheet identifiers are duplicated.",
                        path=relative_path,
                        details={"sheet_id": sheet_id},
                    )
                seen_sheet_ids.add(sheet_id)
                relationship = relationships.get(rel_id)
                target = relationship.get("target") if relationship else None
                if (
                    not relationship
                    or relationship["external"]
                    or relationship["type"] != f"{R_NS}/worksheet"
                    or target not in names
                ):
                    findings.add(
                        "workbook",
                        "blocker",
                        "unresolved_worksheet_relationship",
                        "A workbook sheet relationship does not resolve to an internal worksheet.",
                        path=relative_path,
                        details={"sheet": name},
                    )
                    continue
                sheet_root = _read_xml(
                    zf, target, relative_path, "workbook", findings
                )
                if sheet_root is None:
                    continue
                if sheet_root.tag != f"{{{SHEET_NS}}}worksheet":
                    findings.add(
                        "workbook",
                        "blocker",
                        "invalid_worksheet_root",
                        "A worksheet part does not have the required worksheet XML root.",
                        path=relative_path,
                        details={"sheet": name, "member": target, "root": sheet_root.tag},
                    )
                    continue
                sheet_records.append(
                    {
                        "name": name,
                        "sheet_id": sheet_id,
                        "member": target,
                        "root": sheet_root,
                    }
                )
                result["sheets"].append(name)
                figure_id = _identifier_from_text(name, "figure")
                table_id = _identifier_from_text(name, "table")
                if figure_id:
                    result["figure_identifiers"].add(figure_id)
                if table_id:
                    result["table_identifiers"].add(table_id)

            if not sheet_records:
                findings.add(
                    "workbook",
                    "blocker",
                    "no_resolved_worksheets",
                    "The workbook contains no successfully resolved worksheet.",
                    path=relative_path,
                )

            known_sheets = {record["name"].casefold() for record in sheet_records}
            for defined_name in workbook.findall(f".//{{{SHEET_NS}}}definedName"):
                formula = defined_name.text
                if not formula:
                    continue
                result["formula_count"] += 1
                references, external, has_ref_error, has_dynamic_dependency = (
                    _formula_sheet_references(formula)
                )
                details = {"defined_name": defined_name.get("name", "")}
                if has_dynamic_dependency:
                    findings.add(
                        "workbook",
                        "unverified",
                        "dynamic_formula_dependency_unverified",
                        "A workbook defined name uses INDIRECT; its runtime-resolved dependencies were not evaluated.",
                        path=relative_path,
                        details=details,
                    )
                if has_ref_error:
                    findings.add(
                        "workbook",
                        "blocker",
                        "broken_formula_reference",
                        "A workbook defined name contains #REF!.",
                        path=relative_path,
                        details=details,
                    )
                if external:
                    findings.add(
                        "workbook",
                        "blocker",
                        "external_formula_reference",
                        "A workbook defined name references an external workbook.",
                        path=relative_path,
                        details=details,
                    )
                for referenced_sheet in sorted(references):
                    if referenced_sheet.casefold() not in known_sheets:
                        findings.add(
                            "workbook",
                            "blocker",
                            "broken_cross_sheet_formula_reference",
                            "A workbook defined name names a sheet that does not exist.",
                            path=relative_path,
                            details={**details, "missing_sheet": referenced_sheet},
                        )
                local_sheet_id = defined_name.get("localSheetId")
                if local_sheet_id is not None and (
                    not local_sheet_id.isdigit()
                    or int(local_sheet_id) >= len(sheet_elements)
                ):
                    findings.add(
                        "workbook",
                        "blocker",
                        "invalid_defined_name_scope",
                        "A workbook defined name has an invalid local sheet scope.",
                        path=relative_path,
                        details=details,
                    )
            table_names: dict[str, str] = {}
            table_ids: set[str] = set()
            for record in sheet_records:
                sheet_root = record["root"]
                for cell in sheet_root.findall(f".//{{{SHEET_NS}}}c"):
                    text = _xlsx_cell_text(cell, shared_strings)
                    if text:
                        figure_id = _caption_label(FIGURE_CAPTION_RE, text)
                        table_id = _caption_label(TABLE_CAPTION_RE, text)
                        if figure_id:
                            result["figure_identifiers"].add(figure_id)
                        if table_id:
                            result["table_identifiers"].add(table_id)
                        figure_references = _label_references(
                            text, "figure", findings, relative_path
                        )
                        table_references = _label_references(
                            text, "table", findings, relative_path
                        )
                        if figure_id:
                            figure_references.discard(figure_id)
                        if table_id:
                            table_references.discard(table_id)
                        result["figure_references"].update(figure_references)
                        result["table_references"].update(table_references)
                    formula = cell.find(f"{{{SHEET_NS}}}f")
                    if formula is None or not formula.text:
                        continue
                    result["formula_count"] += 1
                    references, external, has_ref_error, has_dynamic_dependency = (
                        _formula_sheet_references(formula.text)
                    )
                    cell_reference = cell.get("r", "")
                    if has_dynamic_dependency:
                        findings.add(
                            "workbook",
                            "unverified",
                            "dynamic_formula_dependency_unverified",
                            "A workbook formula uses INDIRECT; its runtime-resolved dependencies were not evaluated.",
                            path=relative_path,
                            details={"sheet": record["name"], "cell": cell_reference},
                        )
                    if has_ref_error:
                        findings.add(
                            "workbook",
                            "blocker",
                            "broken_formula_reference",
                            "A workbook formula contains #REF!.",
                            path=relative_path,
                            details={"sheet": record["name"], "cell": cell_reference},
                        )
                    if external:
                        findings.add(
                            "workbook",
                            "blocker",
                            "external_formula_reference",
                            "A workbook formula references an external workbook.",
                            path=relative_path,
                            details={"sheet": record["name"], "cell": cell_reference},
                        )
                    for referenced_sheet in sorted(references):
                        if referenced_sheet.casefold() not in known_sheets:
                            findings.add(
                                "workbook",
                                "blocker",
                                "broken_cross_sheet_formula_reference",
                                "A workbook formula names a sheet that does not exist.",
                                path=relative_path,
                                details={
                                    "sheet": record["name"],
                                    "cell": cell_reference,
                                    "missing_sheet": referenced_sheet,
                                },
                            )

                sheet_relationships = _read_relationships(
                    zf, record["member"], relative_path, "workbook", findings
                )
                table_parts = sheet_root.findall(
                    f"./{{{SHEET_NS}}}tableParts/{{{SHEET_NS}}}tablePart"
                )
                declared_table_relationships: set[str] = set()
                for table_part in table_parts:
                    rel_id = table_part.get(f"{{{R_NS}}}id", "")
                    if not rel_id:
                        findings.add(
                            "workbook",
                            "blocker",
                            "invalid_excel_table_part",
                            "A worksheet tablePart is missing its relationship identifier.",
                            path=relative_path,
                            details={"sheet": record["name"]},
                        )
                        continue
                    if rel_id in declared_table_relationships:
                        findings.add(
                            "workbook",
                            "blocker",
                            "duplicate_excel_table_part",
                            "A worksheet declares the same tablePart relationship more than once.",
                            path=relative_path,
                            details={"sheet": record["name"], "relationship_id": rel_id},
                        )
                        continue
                    declared_table_relationships.add(rel_id)
                    relationship = sheet_relationships.get(rel_id)
                    target = relationship.get("target") if relationship else None
                    if (
                        not relationship
                        or relationship["external"]
                        or relationship["type"] != f"{R_NS}/table"
                        or target not in names
                    ):
                        findings.add(
                            "workbook",
                            "blocker",
                            "unresolved_excel_table_relationship",
                            "A declared worksheet tablePart does not resolve through an internal table relationship.",
                            path=relative_path,
                            details={
                                "sheet": record["name"],
                                "relationship_id": rel_id,
                            },
                        )
                        continue
                    table_root = _read_xml(zf, target, relative_path, "workbook", findings)
                    if table_root is None:
                        continue
                    if table_root.tag != f"{{{SHEET_NS}}}table":
                        findings.add(
                            "workbook",
                            "blocker",
                            "invalid_excel_table_root",
                            "A declared table relationship does not target a valid table XML root.",
                            path=relative_path,
                            details={
                                "sheet": record["name"],
                                "relationship_id": rel_id,
                                "member": target,
                                "root": table_root.tag,
                            },
                        )
                        continue
                    name = table_root.get("displayName") or table_root.get("name") or ""
                    table_id = table_root.get("id", "")
                    table_ref = table_root.get("ref", "")
                    if (
                        not name
                        or not table_id.isdigit()
                        or int(table_id) < 1
                        or not A1_RANGE_RE.fullmatch(table_ref)
                    ):
                        findings.add(
                            "workbook",
                            "blocker",
                            "invalid_excel_table_identifier",
                            "An Excel table lacks a valid name, id, or cell range.",
                            path=relative_path,
                            details={"sheet": record["name"]},
                        )
                        continue
                    folded_name = name.casefold()
                    if folded_name in table_names:
                        findings.add(
                            "workbook",
                            "blocker",
                            "duplicate_excel_table_name",
                            "Excel table names are duplicated case-insensitively.",
                            path=relative_path,
                            details={"table": name},
                        )
                    table_names[folded_name] = name
                    if table_id in table_ids:
                        findings.add(
                            "workbook",
                            "blocker",
                            "duplicate_excel_table_id",
                            "Excel table numeric identifiers are duplicated.",
                            path=relative_path,
                            details={"table_id": table_id},
                        )
                    table_ids.add(table_id)
                    result["excel_tables"].append(name)
                    logical_id = _identifier_from_text(name, "table")
                    if logical_id:
                        result["table_identifiers"].add(logical_id)
    except (zipfile.BadZipFile, OSError):
        findings.add(
            "workbook",
            "blocker",
            "invalid_workbook_package",
            "The workbook is not a readable XLSX ZIP package.",
            path=relative_path,
        )
    return result


def _check_sequence(
    captions: list[dict[str, Any]],
    series_prefix: str,
    findings: FindingLog,
) -> None:
    labels = [caption["id"] for caption in captions if caption["id"].startswith("S") == bool(series_prefix)]
    counts = {label: labels.count(label) for label in set(labels)}
    for label in sorted(label for label, count in counts.items() if count > 1):
        findings.add(
            "figure_table_references",
            "blocker",
            "duplicate_figure_caption",
            "A figure identifier has more than one caption.",
            details={"figure_id": label, "count": counts[label]},
        )
    if not labels:
        return
    numbers = sorted({int(label.lstrip("S")) for label in labels})
    expected = list(range(1, max(numbers) + 1))
    if numbers != expected:
        findings.add(
            "figure_table_references",
            "blocker",
            "figure_sequence_noncontiguous",
            "Figure captions are not a contiguous 1..N sequence within their main or SI series.",
            details={"series": "SI" if series_prefix else "main", "observed": numbers, "expected": expected},
        )


def _discover_tiffs(
    root: Path,
    tiff_directory: Path,
    findings: FindingLog,
) -> tuple[dict[str, Path], list[str]]:
    by_identifier: dict[str, Path] = {}
    relative_paths: list[str] = []
    if not tiff_directory.is_dir():
        return by_identifier, relative_paths

    def record_scan_error(error: OSError) -> None:
        findings.add(
            "images",
            "blocker",
            "tiff_directory_scan_failed",
            "The configured TIFF directory could not be completely enumerated.",
            path=_scan_error_path(root, error),
        )

    for directory, directory_names, file_names in os.walk(
        tiff_directory,
        topdown=True,
        onerror=record_scan_error,
        followlinks=False,
    ):
        directory_path = Path(directory)
        safe_directories: list[str] = []
        for name in sorted(directory_names):
            child = directory_path / name
            if _is_link(child):
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories
        for name in sorted(file_names):
            path = directory_path / name
            if _is_link(path):
                continue
            relative = path.relative_to(root).as_posix()
            try:
                file_stat = path.stat()
            except OSError:
                findings.add(
                    "images",
                    "blocker",
                    "unreadable_tiff_directory_entry",
                    "A filesystem entry in the configured TIFF directory could not be read.",
                    path=relative,
                )
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                findings.add(
                    "images",
                    "blocker",
                    "non_regular_tiff_directory_entry",
                    "A non-regular filesystem entry was found in the configured TIFF directory.",
                    path=relative,
                )
                continue
            if path.suffix.casefold() not in {".tif", ".tiff"}:
                findings.add(
                    "images",
                    "warning",
                    "non_tiff_in_tiff_directory",
                    "A non-TIFF file is present in the configured TIFF directory.",
                    path=relative,
                )
                continue
            relative_paths.append(relative)
            stem = path.stem
            match = re.search(
                r"(?:^|[^A-Za-z0-9])(?:figure|fig)?[\s_.-]*(S?\d+)(?:[^A-Za-z0-9]|$)",
                stem,
                re.IGNORECASE,
            )
            if not match:
                findings.add(
                    "images",
                    "blocker",
                    "unrecognized_tiff_identifier",
                    "A TIFF filename does not contain a portable figure identifier.",
                    path=relative,
                )
                continue
            identifier = _canonical_label(match.group(1))
            if identifier in by_identifier:
                findings.add(
                    "images",
                    "blocker",
                    "duplicate_tiff_identifier",
                    "Multiple TIFF files map to the same figure identifier.",
                    path=relative,
                    details={"figure_id": identifier},
                )
            else:
                by_identifier[identifier] = path
    return by_identifier, sorted(relative_paths)


def _check_images(
    captions: list[dict[str, Any]],
    tiffs_by_identifier: dict[str, Path],
    root: Path,
    findings: FindingLog,
) -> list[dict[str, Any]]:
    expected_ids = {caption["id"] for caption in captions}
    for identifier in sorted(expected_ids - set(tiffs_by_identifier)):
        findings.add(
            "images",
            "blocker",
            "missing_tiff",
            "A captioned figure has no corresponding TIFF file.",
            details={"figure_id": identifier},
        )
    for identifier in sorted(set(tiffs_by_identifier) - expected_ids):
        findings.add(
            "images",
            "blocker",
            "extraneous_tiff",
            "A TIFF file has no corresponding figure caption.",
            path=tiffs_by_identifier[identifier].relative_to(root).as_posix(),
            details={"figure_id": identifier},
        )

    relevant = bool(captions or tiffs_by_identifier)
    if not relevant:
        return []
    try:
        from PIL import Image
    except ImportError:
        findings.add(
            "images",
            "unverified",
            "pillow_unavailable",
            "Pillow is unavailable; native image mode, pixels, dimensions, and aspect ratios were not verified.",
        )
        return []

    image_receipts: list[dict[str, Any]] = []
    for caption in captions:
        identifier = caption["id"]
        tiff_path = tiffs_by_identifier.get(identifier)
        images = caption["images"]
        if tiff_path is None or not images:
            continue
        if len(images) != 1:
            findings.add(
                "images",
                "blocker",
                "ambiguous_docx_image_association",
                "A figure caption is associated with multiple DOCX images, so one TIFF cannot be matched safely.",
                details={"figure_id": identifier, "image_count": len(images)},
            )
            continue
        embedded = images[0]
        tiff_relative = tiff_path.relative_to(root).as_posix()
        try:
            with Image.open(io.BytesIO(embedded["blob"])) as docx_image:
                docx_image.load()
                docx_format = (docx_image.format or "").upper()
                docx_mode = docx_image.mode
                docx_size = tuple(docx_image.size)
                docx_frames = int(getattr(docx_image, "n_frames", 1))
                docx_native_pixels = hashlib.sha256(docx_image.tobytes()).hexdigest()
                alpha_extrema: tuple[int, int] | None = None
                if "A" in docx_image.getbands() or "transparency" in docx_image.info:
                    rgba_image = docx_image.convert("RGBA")
                    alpha_extrema = tuple(rgba_image.getchannel("A").getextrema())
                else:
                    rgba_image = None
                if alpha_extrema is not None and alpha_extrema[0] < 255:
                    white_page = Image.new("RGBA", docx_size, (255, 255, 255, 255))
                    canonical_docx = Image.alpha_composite(
                        white_page, rgba_image
                    ).convert("RGB")
                    docx_conversion = "white_page_alpha_composite"
                else:
                    canonical_docx = docx_image.convert("RGB")
                    docx_conversion = {
                        "RGB": "identity_rgb",
                        "RGBA": "drop_opaque_alpha",
                        "L": "grayscale_to_rgb",
                        "LA": "grayscale_alpha_to_rgb",
                        "P": "palette_to_rgb",
                        "1": "bilevel_to_rgb",
                    }.get(docx_mode, "unsupported_mode_to_rgb")
                docx_canonical_pixels = hashlib.sha256(
                    canonical_docx.tobytes()
                ).hexdigest()
            with Image.open(tiff_path) as tiff_image:
                tiff_image.load()
                tiff_format = (tiff_image.format or "").upper()
                tiff_mode = tiff_image.mode
                tiff_size = tuple(tiff_image.size)
                tiff_frames = int(getattr(tiff_image, "n_frames", 1))
                tiff_native_pixels = hashlib.sha256(tiff_image.tobytes()).hexdigest()
                tiff_canonical_pixels = hashlib.sha256(
                    tiff_image.convert("RGB").tobytes()
                ).hexdigest()
        except Exception as exc:  # Pillow exposes format-specific exception classes.
            findings.add(
                "images",
                "blocker",
                "unreadable_raster_image",
                "A corresponding DOCX or TIFF raster could not be decoded by Pillow.",
                path=tiff_relative,
                details={"figure_id": identifier, "error_type": type(exc).__name__},
            )
            continue

        if tiff_format not in {"TIFF", "TIF"}:
            findings.add(
                "images",
                "blocker",
                "tiff_format_mismatch",
                "A .tif or .tiff file does not decode as TIFF.",
                path=tiff_relative,
                details={"figure_id": identifier, "decoded_format": tiff_format},
            )
        if docx_mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
            findings.add(
                "images",
                "blocker",
                "unsupported_docx_image_mode",
                "A DOCX image mode has no declared canonical RGB conversion policy.",
                path=tiff_relative,
                details={"figure_id": identifier, "docx_mode": docx_mode},
            )
        if alpha_extrema is not None and alpha_extrema[0] < 255:
            findings.add(
                "images",
                "warning",
                "docx_alpha_composited_on_white",
                "A nonopaque DOCX image was composited onto a declared white page before RGB pixel comparison.",
                path=tiff_relative,
                details={
                    "figure_id": identifier,
                    "alpha_extrema": list(alpha_extrema),
                    "background_rgb": [255, 255, 255],
                    "resampling": "none",
                },
            )
        if tiff_mode != "RGB":
            findings.add(
                "images",
                "blocker",
                "non_rgb_native_tiff",
                "A corresponding TIFF must contain native RGB pixels.",
                path=tiff_relative,
                details={"figure_id": identifier, "tiff_mode": tiff_mode},
            )
        if docx_frames != 1 or tiff_frames != 1:
            findings.add(
                "images",
                "blocker",
                "multi_frame_figure_image",
                "Corresponding figure images must be single-frame rasters.",
                path=tiff_relative,
                details={"figure_id": identifier, "docx_frames": docx_frames, "tiff_frames": tiff_frames},
            )
        if docx_size != tiff_size:
            findings.add(
                "images",
                "blocker",
                "image_native_dimension_mismatch",
                "Corresponding DOCX and TIFF images have different native pixel dimensions.",
                path=tiff_relative,
                details={"figure_id": identifier, "docx_pixels": list(docx_size), "tiff_pixels": list(tiff_size)},
            )
        if docx_size[1] and tiff_size[1]:
            docx_aspect = docx_size[0] / docx_size[1]
            tiff_aspect = tiff_size[0] / tiff_size[1]
            if abs(docx_aspect - tiff_aspect) / max(docx_aspect, tiff_aspect) > 0.001:
                findings.add(
                    "images",
                    "blocker",
                    "image_native_aspect_mismatch",
                    "Corresponding DOCX and TIFF images have different native aspect ratios.",
                    path=tiff_relative,
                    details={"figure_id": identifier},
                )
            extent = embedded.get("extent_emu")
            if extent and extent[0] > 0 and extent[1] > 0:
                display_aspect = extent[0] / extent[1]
                if abs(display_aspect - docx_aspect) / max(display_aspect, docx_aspect) > 0.01:
                    findings.add(
                        "images",
                        "blocker",
                        "docx_display_aspect_mismatch",
                        "A DOCX drawing extent stretches its embedded image by more than one percent.",
                        details={"figure_id": identifier},
                    )
            else:
                findings.add(
                    "images",
                    "unverified",
                    "docx_display_extent_unavailable",
                    "A DOCX image has no usable drawing extent, so displayed aspect ratio was not verified.",
                    details={"figure_id": identifier},
                )
        if docx_canonical_pixels != tiff_canonical_pixels:
            findings.add(
                "images",
                "blocker",
                "image_pixel_mismatch",
                "Corresponding DOCX and TIFF images differ after the declared, no-resampling RGB canonicalization.",
                path=tiff_relative,
                details={
                    "figure_id": identifier,
                    "docx_conversion": docx_conversion,
                    "background_rgb": [255, 255, 255]
                    if docx_conversion == "white_page_alpha_composite"
                    else None,
                    "resampling": "none",
                },
            )
        image_receipts.append(
            {
                "figure_id": identifier,
                "docx_occurrence": embedded["occurrence"],
                "docx_member": embedded["member"],
                "docx_encoded_sha256": embedded["encoded_sha256"],
                "docx_format": docx_format,
                "docx_mode": docx_mode,
                "docx_native_pixel_sha256": docx_native_pixels,
                "tiff_path": tiff_relative,
                "tiff_sha256": _sha256_file(tiff_path),
                "tiff_format": tiff_format,
                "tiff_mode": tiff_mode,
                "tiff_native_pixel_sha256": tiff_native_pixels,
                "native_pixels": list(docx_size),
                "canonical_rgb_pixel_sha256": docx_canonical_pixels,
                "pixel_sha256": docx_canonical_pixels,
                "canonicalization": {
                    "docx_conversion": docx_conversion,
                    "target_mode": "RGB",
                    "source_alpha_extrema": list(alpha_extrema)
                    if alpha_extrema is not None
                    else None,
                    "background_rgb": [255, 255, 255]
                    if docx_conversion == "white_page_alpha_composite"
                    else None,
                    "resampling": "none",
                },
                "canonical_pixels_match": (
                    docx_canonical_pixels == tiff_canonical_pixels
                ),
            }
        )
    return image_receipts


def _read_json_file(path: Path, gate: str, relative_path: str, findings: FindingLog) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        findings.add(
            gate,
            "blocker",
            f"invalid_{gate}_json",
            "A required JSON file could not be decoded.",
            path=relative_path,
        )
        return None


def _decode_toc_raster(
    source: bytes | Path,
    member: str,
    extent: tuple[int, int] | None,
    *,
    require_extent: bool,
    Image: Any,
    relative_path: str,
    findings: FindingLog,
) -> dict[str, Any] | None:
    try:
        with Image.open(
            io.BytesIO(source) if isinstance(source, bytes) else source
        ) as image:
            image.load()
            image_format = (image.format or "").upper()
            image_mode = image.mode
            image_size = tuple(image.size)
            frame_count = int(getattr(image, "n_frames", 1))
            native_pixel_sha256 = hashlib.sha256(image.tobytes()).hexdigest()
            alpha_extrema: tuple[int, int] | None = None
            if "A" in image.getbands() or "transparency" in image.info:
                rgba_image = image.convert("RGBA")
                alpha_extrema = tuple(rgba_image.getchannel("A").getextrema())
            else:
                rgba_image = None
            if alpha_extrema is not None and alpha_extrema[0] < 255:
                canonical_image = Image.alpha_composite(
                    Image.new("RGBA", image_size, (255, 255, 255, 255)),
                    rgba_image,
                ).convert("RGB")
                conversion = "white_page_alpha_composite"
            else:
                canonical_image = image.convert("RGB")
                conversion = {
                    "RGB": "identity_rgb",
                    "RGBA": "drop_opaque_alpha",
                    "L": "grayscale_to_rgb",
                    "LA": "grayscale_alpha_to_rgb",
                    "P": "palette_to_rgb",
                    "1": "bilevel_to_rgb",
                }.get(image_mode, "unsupported_mode_to_rgb")
            canonical_pixel_sha256 = hashlib.sha256(
                canonical_image.tobytes()
            ).hexdigest()
    except Exception as exc:  # Pillow exposes format-specific exception classes.
        findings.add(
            "toc",
            "blocker",
            "unreadable_toc_raster",
            "A graphical TOC raster could not be decoded by Pillow.",
            path=relative_path,
            details={"member": member, "error_type": type(exc).__name__},
        )
        return None

    if isinstance(source, Path) and source.suffix.lower() in {".tif", ".tiff"}:
        if image_format != "TIFF":
            findings.add(
                "toc",
                "blocker",
                "invalid_toc_tiff_format",
                "A standalone TOC with a TIFF extension must contain decoded TIFF data.",
                path=relative_path,
                details={"member": member, "format": image_format},
            )
        if image_mode != "RGB":
            findings.add(
                "toc",
                "blocker",
                "non_rgb_native_toc_tiff",
                "A standalone TOC TIFF must be native RGB, not merely RGB-convertible.",
                path=relative_path,
                details={"member": member, "mode": image_mode},
            )
    if image_mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
        findings.add(
            "toc",
            "blocker",
            "unsupported_toc_image_mode",
            "A graphical TOC raster mode has no declared canonical RGB conversion policy.",
            path=relative_path,
            details={"member": member, "mode": image_mode},
        )
    if alpha_extrema is not None and alpha_extrema[0] < 255:
        findings.add(
            "toc",
            "warning",
            "toc_alpha_composited_on_white",
            "A nonopaque graphical TOC raster was composited onto a declared white page before comparison.",
            path=relative_path,
            details={
                "member": member,
                "alpha_extrema": list(alpha_extrema),
                "background_rgb": [255, 255, 255],
                "resampling": "none",
            },
        )
    if frame_count != 1:
        findings.add(
            "toc",
            "blocker",
            "multi_frame_toc_raster",
            "A graphical TOC must use a single-frame raster.",
            path=relative_path,
            details={"member": member, "frames": frame_count},
        )
    if image_size[0] <= 0 or image_size[1] <= 0:
        findings.add(
            "toc",
            "blocker",
            "invalid_toc_dimensions",
            "A graphical TOC raster has invalid native dimensions.",
            path=relative_path,
            details={"member": member},
        )
    if require_extent and (
        extent is None or extent[0] <= 0 or extent[1] <= 0
    ):
        findings.add(
            "toc",
            "unverified",
            "toc_display_extent_unavailable",
            "A DOCX graphical TOC occurrence has no unambiguous positive display extent.",
            path=relative_path,
            details={"member": member},
        )
    elif extent is not None and image_size[0] > 0 and image_size[1] > 0:
        native_aspect = image_size[0] / image_size[1]
        display_aspect = extent[0] / extent[1]
        if (
            abs(native_aspect - display_aspect)
            / max(native_aspect, display_aspect)
            > 0.01
        ):
            findings.add(
                "toc",
                "blocker",
                "toc_display_aspect_mismatch",
                "A DOCX graphical TOC occurrence stretches its embedded raster by more than one percent.",
                path=relative_path,
                details={"member": member},
            )
    return {
        "member": member,
        "format": image_format,
        "mode": image_mode,
        "native_pixels": list(image_size),
        "native_pixel_sha256": native_pixel_sha256,
        "canonical_rgb_pixel_sha256": canonical_pixel_sha256,
        "display_extent_emu": list(extent) if extent is not None else None,
        "canonicalization": {
            "conversion": conversion,
            "target_mode": "RGB",
            "source_alpha_extrema": list(alpha_extrema)
            if alpha_extrema is not None
            else None,
            "background_rgb": [255, 255, 255]
            if conversion == "white_page_alpha_composite"
            else None,
            "resampling": "none",
        },
    }


def _check_graphical_toc(
    path: Path,
    relative_path: str,
    main_document: dict[str, Any] | None,
    findings: FindingLog,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": relative_path,
        "kind": None,
        "sha256": _sha256_file(path),
        "embedded_images": [],
        "association": None,
    }
    finding_start = len(findings.items)
    is_container = path.suffix.casefold() == ".docx"
    raster_inputs: list[tuple[str, bytes | Path, tuple[int, int] | None]] = []
    if is_container:
        result["kind"] = "docx_container"
        result["association"] = {
            "mode": "designated_docx_container",
            "main_docx_pairing_required": False,
            "status": "DESIGNATED_CONTAINER",
        }
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = _inspect_zip_members(zf, relative_path, "toc", findings)
                document = _read_xml(
                    zf, "word/document.xml", relative_path, "toc", findings
                )
                if document is None:
                    result["association"]["status"] = "INVALID_CONTAINER"
                    return result
                relationships = _read_relationships(
                    zf, "word/document.xml", relative_path, "toc", findings
                )
                occurrence_index = 0
                for paragraph in document.iter(f"{{{W_NS}}}p"):
                    for blip, extent in _owned_image_occurrences(
                        paragraph, relative_path, "toc", findings
                    ):
                        occurrence_index += 1
                        rel_id = blip.get(f"{{{R_NS}}}embed")
                        linked_id = blip.get(f"{{{R_NS}}}link")
                        if linked_id and not rel_id:
                            findings.add(
                                "toc",
                                "blocker",
                                "external_toc_image",
                                "The graphical TOC container links an image instead of embedding it.",
                                path=relative_path,
                            )
                            continue
                        relationship = relationships.get(rel_id or "")
                        target = (
                            relationship.get("target") if relationship else None
                        )
                        if (
                            relationship is None
                            or relationship["external"]
                            or target is None
                            or target not in names
                        ):
                            findings.add(
                                "toc",
                                "blocker",
                                "unresolved_toc_image",
                                "A graphical TOC image relationship does not resolve inside its DOCX container.",
                                path=relative_path,
                                details={"occurrence": occurrence_index},
                            )
                            continue
                        try:
                            blob = zf.read(target)
                        except (KeyError, OSError, RuntimeError):
                            findings.add(
                                "toc",
                                "blocker",
                                "unreadable_toc_image",
                                "An embedded graphical TOC image could not be read.",
                                path=relative_path,
                                details={"member": target},
                            )
                            continue
                        raster_inputs.append((target, blob, extent))
        except (zipfile.BadZipFile, OSError):
            findings.add(
                "toc",
                "blocker",
                "invalid_toc_docx",
                "The graphical TOC DOCX is not a readable Office package.",
                path=relative_path,
            )
            result["association"]["status"] = "INVALID_CONTAINER"
            return result
        if not raster_inputs:
            findings.add(
                "toc",
                "blocker",
                "toc_graphic_missing",
                "The graphical TOC DOCX contains no resolvable embedded image.",
                path=relative_path,
            )
            result["association"]["status"] = "MISSING"
            return result
    elif path.suffix.casefold() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
    }:
        result["kind"] = "raster_image"
        result["association"] = {
            "mode": "standalone_raster_to_main_docx",
            "main_docx_pairing_required": True,
            "status": "PENDING",
        }
        raster_inputs.append((relative_path, path, None))
    else:
        result["kind"] = "unsupported"
        findings.add(
            "toc",
            "unverified",
            "unsupported_toc_format",
            "The graphical TOC must be a DOCX container or a PNG, JPEG, TIFF, or BMP raster for structural inspection.",
            path=relative_path,
        )
        return result

    try:
        from PIL import Image
    except ImportError:
        findings.add(
            "toc",
            "unverified",
            "pillow_unavailable_for_toc",
            "Pillow is unavailable; graphical TOC pixels and aspect ratio were not verified.",
            path=relative_path,
        )
        if result["association"] is not None:
            result["association"]["status"] = "UNVERIFIED"
        return result

    for member, source, extent in raster_inputs:
        raster = _decode_toc_raster(
            source,
            member,
            extent,
            require_extent=is_container,
            Image=Image,
            relative_path=relative_path,
            findings=findings,
        )
        if raster is not None:
            result["embedded_images"].append(raster)

    if not is_container:
        assigned_occurrences = {
            image["occurrence"]
            for caption in (main_document or {}).get("figure_captions", [])
            for image in caption.get("images", [])
            if image.get("occurrence")
        }
        all_main_images = list((main_document or {}).get("images", []))
        designated = [
            image
            for image in all_main_images
            if str(image.get("role", "")).casefold()
            in {"main_toc", "graphical_toc"}
        ]
        candidates = designated or [
            image
            for image in all_main_images
            if image.get("occurrence") not in assigned_occurrences
        ]
        if not candidates:
            findings.add(
                "toc",
                "blocker",
                "main_toc_image_missing",
                "The standalone graphical TOC has no unassigned or designated image occurrence in the main DOCX.",
                path=relative_path,
            )
            result["association"]["status"] = "MISSING"
        elif len(candidates) != 1:
            findings.add(
                "toc",
                "blocker",
                "main_toc_image_ambiguous",
                "The standalone graphical TOC cannot be paired uniquely with an unassigned or designated main-DOCX image.",
                path=relative_path,
                details={"candidate_count": len(candidates)},
            )
            result["association"]["status"] = "AMBIGUOUS"
        elif not result["embedded_images"]:
            result["association"]["status"] = "UNVERIFIED"
        else:
            candidate = candidates[0]
            main_raster = _decode_toc_raster(
                candidate["blob"],
                candidate["member"],
                candidate.get("extent_emu"),
                require_extent=True,
                Image=Image,
                relative_path=main_document["path"],
                findings=findings,
            )
            if main_raster is not None:
                result["main_docx_image"] = {
                    **main_raster,
                    "occurrence": candidate["occurrence"],
                }
                standalone = result["embedded_images"][0]
                standalone_size = tuple(standalone["native_pixels"])
                main_size = tuple(main_raster["native_pixels"])
                if standalone_size != main_size:
                    findings.add(
                        "toc",
                        "blocker",
                        "toc_native_dimension_mismatch",
                        "The standalone and main-DOCX graphical TOC images have different native dimensions.",
                        path=relative_path,
                        details={
                            "standalone_pixels": list(standalone_size),
                            "main_docx_pixels": list(main_size),
                        },
                    )
                if (
                    min(*standalone_size, *main_size) > 0
                    and abs(
                        standalone_size[0] / standalone_size[1]
                        - main_size[0] / main_size[1]
                    )
                    / max(
                        standalone_size[0] / standalone_size[1],
                        main_size[0] / main_size[1],
                    )
                    > 0.001
                ):
                    findings.add(
                        "toc",
                        "blocker",
                        "toc_native_aspect_mismatch",
                        "The standalone and main-DOCX graphical TOC images have different native aspect ratios.",
                        path=relative_path,
                    )
                if (
                    standalone["canonical_rgb_pixel_sha256"]
                    != main_raster["canonical_rgb_pixel_sha256"]
                ):
                    findings.add(
                        "toc",
                        "blocker",
                        "toc_pixel_mismatch",
                        "The standalone and main-DOCX graphical TOC images differ after declared no-resampling RGB canonicalization.",
                        path=relative_path,
                    )
                result["association"].update(
                    {
                        "candidate_source": "designated"
                        if designated
                        else "unique_unassigned",
                        "main_docx_occurrence": candidate["occurrence"],
                        "canonical_pixels_match": (
                            standalone["canonical_rgb_pixel_sha256"]
                            == main_raster["canonical_rgb_pixel_sha256"]
                        ),
                    }
                )

    toc_findings = findings.items[finding_start:]
    if result["association"]["status"] not in {"MISSING", "AMBIGUOUS"}:
        if any(item["severity"] == "blocker" for item in toc_findings):
            result["association"]["status"] = "MISMATCH"
        elif any(item["severity"] == "unverified" for item in toc_findings):
            result["association"]["status"] = "UNVERIFIED"
        elif is_container:
            result["association"]["status"] = "DESIGNATED_CONTAINER"
        else:
            result["association"]["status"] = "MATCH"
    return result


def _parse_manifest_entries(
    path: Path, relative_path: str, findings: FindingLog
) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        findings.add("manifest", "blocker", "unreadable_manifest", "The checksum manifest is not readable UTF-8 text.", path=relative_path)
        return {}
    entries: list[tuple[Any, Any]] = []
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            findings.add("manifest", "blocker", "invalid_manifest_json", "The JSON checksum manifest cannot be decoded.", path=relative_path)
            return {}
        algorithm = data.get("algorithm", "sha256") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(algorithm, str) or algorithm.casefold() != "sha256" or not isinstance(data.get("files"), list):
            findings.add("manifest", "blocker", "invalid_manifest_schema", "A JSON manifest must declare sha256 and a files array.", path=relative_path)
            return {}
        for item in data["files"]:
            if isinstance(item, dict):
                entries.append((item.get("path"), item.get("sha256")))
            else:
                entries.append((None, None))
    else:
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            bsd_match = re.fullmatch(r"SHA256 \((.+)\) = ([0-9a-fA-F]{64})", line)
            gnu_match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
            if bsd_match:
                entries.append((bsd_match.group(1), bsd_match.group(2)))
            elif gnu_match:
                entries.append((gnu_match.group(2), gnu_match.group(1)))
            else:
                findings.add(
                    "manifest", "blocker", "malformed_manifest_line", "A checksum-manifest line is malformed.", path=relative_path, details={"line": line_number}
                )
    parsed: dict[str, str] = {}
    for index, (entry_path, digest) in enumerate(entries):
        try:
            normalized = _normalize_relative(entry_path)
        except (TypeError, ValueError):
            findings.add(
                "manifest", "blocker", "unsafe_manifest_path", "A checksum-manifest entry contains an invalid or escaping path.", path=relative_path, details={"entry_index": index}
            )
            continue
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            findings.add(
                "manifest", "blocker", "invalid_manifest_checksum", "A checksum-manifest digest is not SHA-256.", path=relative_path, details={"listed_path": normalized}
            )
            continue
        if normalized in parsed:
            findings.add(
                "manifest", "blocker", "duplicate_manifest_path", "A checksum-manifest path is listed more than once.", path=relative_path, details={"listed_path": normalized}
            )
        parsed[normalized] = digest.casefold()
    return parsed


def _check_manifest(
    path: Path,
    relative_path: str,
    inventory: dict[str, dict[str, Any]],
    excluded_locks: list[str],
    findings: FindingLog,
) -> dict[str, Any]:
    entries = _parse_manifest_entries(path, relative_path, findings)
    expected = set(inventory) - {relative_path}
    listed = set(entries)
    missing = sorted(expected - listed)
    extra = sorted(listed - expected)
    if missing:
        findings.add(
            "manifest", "blocker", "incomplete_manifest", "The checksum manifest omits regular bundle files.", path=relative_path, details={"missing_paths": missing}
        )
    if extra:
        findings.add(
            "manifest", "blocker", "extraneous_manifest_entry", "The checksum manifest includes itself, an Office lock, or a nonexistent file.", path=relative_path, details={"extra_paths": extra}
        )
    for listed_path in sorted(expected & listed):
        if entries[listed_path] != inventory[listed_path]["sha256"]:
            findings.add(
                "manifest", "blocker", "stale_manifest_checksum", "A checksum-manifest digest does not match the current file.", path=relative_path, details={"listed_path": listed_path}
            )
    return {
        "path": relative_path,
        "algorithm": "sha256",
        "listed_files": len(entries),
        "expected_files": len(expected),
        "excluded_manifest": relative_path,
        "excluded_office_locks": excluded_locks,
    }


def _check_qa_receipt(
    path: Path | None,
    relative_path: str | None,
    required_artifacts: set[str],
    inventory: dict[str, dict[str, Any]],
    findings: FindingLog,
) -> dict[str, Any]:
    base_result: dict[str, Any] = {
        "path": relative_path,
        "status": "MISSING",
        "schema_version": None,
        "reviewer": None,
        "reviewed_at": None,
        "outcome": None,
        "scope_outcomes": {},
        "artifact_hash_count": 0,
        "artifact_hashes": {},
        "required_artifacts": sorted(required_artifacts),
        "hash_binding_current": False,
        "identity_authenticated": False,
        "scientific_claim_verified_by_machine": False,
    }
    if path is None or relative_path is None:
        findings.add(
            "external_qa", "blocker", "external_qa_missing", "No external QA receipt was supplied; the bundle cannot be submission-ready."
        )
        return base_result
    data = _read_json_file(path, "external_qa", relative_path, findings)
    if not isinstance(data, dict):
        if data is not None:
            findings.add("external_qa", "blocker", "invalid_qa_schema", "The external QA receipt root must be a JSON object.", path=relative_path)
        base_result["status"] = "INVALID"
        return base_result

    valid = True
    stale = False
    approved = True
    base_result["schema_version"] = data.get("schema_version")
    if data.get("schema_version") != QA_SCHEMA:
        findings.add("external_qa", "blocker", "unsupported_qa_schema", f"The QA schema_version must be {QA_SCHEMA}.", path=relative_path)
        valid = False

    reviewer = data.get("reviewer")
    required_reviewer_fields = ("identity", "role", "independence_basis")
    if not isinstance(reviewer, dict) or any(
        not isinstance(reviewer.get(field), str) or not reviewer[field].strip()
        for field in required_reviewer_fields
    ):
        findings.add(
            "external_qa", "blocker", "incomplete_qa_reviewer", "Reviewer identity, role, and a descriptive independence_basis are required.", path=relative_path
        )
        valid = False
    else:
        base_result["reviewer"] = {field: reviewer[field] for field in required_reviewer_fields}

    reviewed_at = data.get("reviewed_at")
    base_result["reviewed_at"] = reviewed_at if isinstance(reviewed_at, str) else None
    try:
        parsed_time = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError("timezone required")
        if parsed_time.astimezone(timezone.utc) > datetime.now(timezone.utc):
            findings.add("external_qa", "blocker", "future_qa_timestamp", "The external QA reviewed_at timestamp is in the future.", path=relative_path)
            valid = False
    except (AttributeError, OverflowError, TypeError, ValueError):
        findings.add("external_qa", "blocker", "invalid_qa_timestamp", "The external QA reviewed_at value must be an ISO-8601 timestamp with timezone.", path=relative_path)
        valid = False

    outcome = data.get("outcome")
    base_result["outcome"] = outcome
    if outcome != "approved":
        findings.add("external_qa", "blocker", "qa_not_approved", "The external QA outcome is not approved.", path=relative_path)
        approved = False

    scope = data.get("scope")
    if not isinstance(scope, dict):
        findings.add("external_qa", "blocker", "missing_qa_scope", "The external QA scope object is missing.", path=relative_path)
        valid = False
        scope = {}
    for scope_name in QA_SCOPES:
        scope_item = scope.get(scope_name)
        scope_outcome = scope_item.get("outcome") if isinstance(scope_item, dict) else None
        base_result["scope_outcomes"][scope_name] = scope_outcome
        if scope_outcome != "approved":
            findings.add(
                "external_qa", "blocker", "qa_scope_not_approved", "A required independent QA scope is missing or not approved.", path=relative_path, details={"scope": scope_name}
            )
            approved = False

    unresolved = data.get("unresolved_blockers")
    if not isinstance(unresolved, list):
        findings.add("external_qa", "blocker", "invalid_qa_blockers", "unresolved_blockers must be an array.", path=relative_path)
        valid = False
    elif unresolved:
        findings.add(
            "external_qa", "blocker", "qa_unresolved_blockers", "The external QA receipt records unresolved blockers.", path=relative_path, details={"count": len(unresolved)}
        )
        approved = False

    artifact_hashes = data.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        findings.add("external_qa", "blocker", "missing_qa_artifact_hashes", "artifact_hashes must map bundle-relative paths to SHA-256 digests.", path=relative_path)
        valid = False
        artifact_hashes = {}
    normalized_hashes: dict[str, str] = {}
    for stated_path, digest in artifact_hashes.items():
        try:
            normalized = _normalize_relative(stated_path)
        except (TypeError, ValueError):
            findings.add("external_qa", "blocker", "unsafe_qa_artifact_path", "A QA artifact hash uses an invalid or escaping path.", path=relative_path)
            valid = False
            continue
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            findings.add("external_qa", "blocker", "invalid_qa_artifact_hash", "A QA artifact hash is not SHA-256.", path=relative_path, details={"artifact": normalized})
            valid = False
            continue
        normalized_hashes[normalized] = digest.casefold()
        current = inventory.get(normalized)
        if current is None or current["sha256"] != digest.casefold():
            findings.add("external_qa", "blocker", "stale_qa_artifact_hash", "A QA artifact hash is missing from or stale against the current bundle.", path=relative_path, details={"artifact": normalized})
            stale = True
    missing_hashes = sorted(required_artifacts - set(normalized_hashes))
    if missing_hashes:
        findings.add(
            "external_qa", "blocker", "incomplete_qa_artifact_hashes", "The QA receipt is not tied to every current document, graphical TOC, workbook, and TIFF.", path=relative_path, details={"missing_paths": missing_hashes}
        )
        stale = True
    base_result["artifact_hash_count"] = len(normalized_hashes)
    base_result["artifact_hashes"] = dict(sorted(normalized_hashes.items()))
    base_result["hash_binding_current"] = valid and not stale

    if isinstance(data.get("scientifically_verified"), bool):
        findings.add(
            "external_qa", "warning", "boolean_scientific_claim_ignored", "A scientifically_verified boolean was ignored; scientific validity is never machine-proven by assertion.", path=relative_path
        )

    if not valid:
        base_result["status"] = "INVALID"
    elif stale:
        base_result["status"] = "STALE"
    elif not approved:
        base_result["status"] = "NOT_APPROVED"
    else:
        base_result["status"] = "CURRENT_APPROVED"
    return base_result


def _public_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": document["path"],
        "kind": document["kind"],
        "bibliography_numbers": document["bibliography_numbers"],
        "numeric_citations": sorted(document["citations"]),
        "figure_captions": [
            {"id": caption["id"], "block_index": caption["block_index"], "image_count": len(caption["images"])}
            for caption in document["figure_captions"]
        ],
        "table_captions": document["table_captions"],
        "figure_references": sorted(document["figure_references"]),
        "table_references": sorted(document["table_references"]),
        "embedded_image_count": len(document["images"]),
    }


def _finalize_receipt(
    findings: FindingLog,
    inputs: dict[str, str | None],
    inventory: dict[str, dict[str, Any]],
    excluded_locks: list[str],
    documents: list[dict[str, Any]],
    workbook: dict[str, Any] | None,
    image_receipts: list[dict[str, Any]],
    tiff_paths: list[str],
    toc_result: dict[str, Any] | None,
    manifest_result: dict[str, Any] | None,
    qa_result: dict[str, Any] | None,
) -> dict[str, Any]:
    gate_statuses = {gate: findings.status(gate) for gate in STRUCTURAL_GATES}
    if any(status == "FAIL" for status in gate_statuses.values()):
        structural_status = "FAIL"
    elif any(status == "UNVERIFIED" for status in gate_statuses.values()):
        structural_status = "UNVERIFIED"
    else:
        structural_status = "PASS"
    qa_status = qa_result["status"] if qa_result else "MISSING"
    eligible = structural_status == "PASS" and qa_status == "CURRENT_APPROVED"
    exit_code = 0 if eligible else (1 if structural_status != "PASS" else 2)
    unresolved = [
        {key: item[key] for key in ("gate", "code", "path") if key in item}
        for item in findings.items
        if item["severity"] in {"blocker", "unverified"}
    ]
    public_workbook = None
    if workbook is not None:
        public_workbook = {
            "path": workbook["path"],
            "sheets": workbook["sheets"],
            "excel_tables": workbook["excel_tables"],
            "figure_identifiers": sorted(workbook["figure_identifiers"]),
            "table_identifiers": sorted(workbook["table_identifiers"]),
            "figure_references": sorted(workbook["figure_references"]),
            "table_references": sorted(workbook["table_references"]),
            "formula_count": workbook["formula_count"],
        }
    return {
        "schema_version": AUDIT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "bundle_root": ".",
        "inputs": inputs,
        "verdict": "SUBMISSION_READY" if eligible else ("STRUCTURAL_UNVERIFIED" if structural_status == "UNVERIFIED" else "BLOCKED"),
        "structural_machine_audit": structural_status,
        "publication_eligibility": "ELIGIBLE" if eligible else "NOT_ELIGIBLE",
        "exit_code": exit_code,
        "source_writes_performed": False,
        "gates": {
            **{gate: {"status": status} for gate, status in gate_statuses.items()},
            "external_qa": {"status": qa_status},
        },
        "inventory": [inventory[path] for path in sorted(inventory)],
        "excluded_office_locks": excluded_locks,
        "documents": [_public_document(document) for document in documents],
        "workbook": public_workbook,
        "images": {
            "tiff_paths": tiff_paths,
            "verified_pairs": image_receipts,
        },
        "toc": toc_result,
        "manifest": manifest_result,
        "external_qa": qa_result,
        "findings": findings.items,
        "unresolved_blockers": unresolved,
        "limitations": [
            "The machine audit checks package structure, identifiers, relationships, hashes, and decodable raster properties; it does not judge visual readability.",
            "Numeric citation syntax and target existence are checked, but citation meaning and source support require independent semantic review.",
            "Scientific correctness is outside the machine audit. External QA is a hash-bound attestation whose reviewer identity, independence, and scientific claims are not authenticated by this tool.",
            "DOCX figure association is inferred from the caption paragraph and up to four immediately preceding paragraphs; unusual layouts require visual review.",
        ],
    }


def audit_bundle(config: BundleConfig) -> dict[str, Any]:
    findings = FindingLog()
    inputs: dict[str, str | None] = {
        "main_docx": None,
        "si_docx": None,
        "workbook": None,
        "tiff_dir": None,
        "toc": None,
        "checksum_manifest": None,
        "qa_receipt": None,
    }
    inventory: dict[str, dict[str, Any]] = {}
    excluded_locks: list[str] = []
    documents: list[dict[str, Any]] = []
    workbook_result: dict[str, Any] | None = None
    image_receipts: list[dict[str, Any]] = []
    tiff_paths: list[str] = []
    toc_result: dict[str, Any] | None = None
    manifest_result: dict[str, Any] | None = None
    qa_result: dict[str, Any] | None = None

    root_input = Path(config.bundle_root)
    if not os.path.lexists(root_input) or not root_input.is_dir() or _is_link(root_input):
        findings.add(
            "path_safety", "blocker", "invalid_bundle_root", "The bundle root must be an existing, non-symlink directory."
        )
        qa_result = _check_qa_receipt(None, None, set(), inventory, findings)
        return _finalize_receipt(
            findings, inputs, inventory, excluded_locks, documents, workbook_result,
            image_receipts, tiff_paths, toc_result, manifest_result, qa_result
        )
    try:
        root = root_input.resolve(strict=True)
    except OSError:
        findings.add("path_safety", "blocker", "invalid_bundle_root", "The bundle root could not be resolved safely.")
        qa_result = _check_qa_receipt(None, None, set(), inventory, findings)
        return _finalize_receipt(
            findings, inputs, inventory, excluded_locks, documents, workbook_result,
            image_receipts, tiff_paths, toc_result, manifest_result, qa_result
        )

    path_specs = {
        "main_docx": (config.main_docx, "file", False, "path_safety"),
        "si_docx": (config.si_docx, "file", False, "path_safety"),
        "workbook": (config.workbook, "file", False, "path_safety"),
        "tiff_dir": (config.tiff_dir, "directory", True, "path_safety"),
        "toc": (config.toc, "file", False, "path_safety"),
        "checksum_manifest": (config.checksum_manifest, "file", False, "path_safety"),
    }
    if config.qa_receipt is not None:
        path_specs["qa_receipt"] = (config.qa_receipt, "file", False, "external_qa")
    resolved: dict[str, Path] = {}
    for key, (raw_value, expected_kind, allow_root, gate) in path_specs.items():
        try:
            relative = _normalize_relative(raw_value, allow_root=allow_root)
            candidate = _resolve_bundle_path(root, relative)
        except (TypeError, ValueError):
            findings.add(
                gate, "blocker", "unsafe_relative_path", "An input path is absolute, escaping, non-portable, or traverses a symlink.", details={"input": key}
            )
            continue
        inputs[key] = relative
        if expected_kind == "file" and not candidate.is_file():
            findings.add(gate, "blocker", "missing_input_file", "A configured bundle input is not a regular file.", path=relative, details={"input": key})
            continue
        if expected_kind == "directory" and not candidate.is_dir():
            findings.add(gate, "blocker", "missing_input_directory", "A configured bundle input is not a directory.", path=relative, details={"input": key})
            continue
        resolved[key] = candidate

    inventory, excluded_locks = _inventory_bundle(root, findings)

    if "main_docx" in resolved and inputs["main_docx"]:
        documents.append(_parse_docx(resolved["main_docx"], inputs["main_docx"], "main", findings))
    if "si_docx" in resolved and inputs["si_docx"]:
        documents.append(_parse_docx(resolved["si_docx"], inputs["si_docx"], "si", findings))
    if "workbook" in resolved and inputs["workbook"]:
        workbook_result = _parse_workbook(resolved["workbook"], inputs["workbook"], findings)

    main_bibliography = set()
    for document in documents:
        if document["kind"] == "main":
            main_bibliography = set(document["bibliography_numbers"])
            break
    all_bibliography = set().union(*(set(document["bibliography_numbers"]) for document in documents)) if documents else set()
    fallback_bibliography = main_bibliography or all_bibliography
    for document in documents:
        available = set(document["bibliography_numbers"]) or fallback_bibliography
        for number in sorted(document["citations"] - available):
            findings.add(
                "bibliography", "blocker", "dangling_literature_reference", "A numeric citation does not resolve to a bibliography entry.", path=document["path"], details={"reference_number": number}
            )

    all_captions = [caption for document in documents for caption in document["figure_captions"]]
    _check_sequence(all_captions, "", findings)
    _check_sequence(all_captions, "S", findings)
    table_caption_ids = [caption["id"] for document in documents for caption in document["table_captions"]]
    for identifier in sorted({item for item in table_caption_ids if table_caption_ids.count(item) > 1}):
        findings.add(
            "figure_table_references", "blocker", "duplicate_table_caption", "A table identifier has more than one DOCX caption.", details={"table_id": identifier}
        )

    available_figures = {caption["id"] for caption in all_captions}
    available_tables = set(table_caption_ids)
    if workbook_result:
        available_figures.update(workbook_result["figure_identifiers"])
        available_tables.update(workbook_result["table_identifiers"])
    for document in documents:
        for identifier in sorted(document["figure_references"] - available_figures):
            findings.add(
                "figure_table_references", "blocker", "dangling_figure_reference", "A figure reference does not resolve across the DOCX files or workbook.", path=document["path"], details={"figure_id": identifier}
            )
        for identifier in sorted(document["table_references"] - available_tables):
            findings.add(
                "figure_table_references", "blocker", "dangling_table_reference", "A table reference does not resolve across the DOCX files or workbook.", path=document["path"], details={"table_id": identifier}
            )
    if workbook_result:
        for identifier in sorted(workbook_result["figure_references"] - available_figures):
            findings.add(
                "figure_table_references", "blocker", "dangling_workbook_figure_reference", "A workbook figure reference does not resolve across the DOCX files or workbook.", path=workbook_result["path"], details={"figure_id": identifier}
            )
        for identifier in sorted(workbook_result["table_references"] - available_tables):
            findings.add(
                "figure_table_references", "blocker", "dangling_workbook_table_reference", "A workbook table reference does not resolve across the DOCX files or workbook.", path=workbook_result["path"], details={"table_id": identifier}
            )

    tiffs_by_identifier: dict[str, Path] = {}
    if "tiff_dir" in resolved and inputs["tiff_dir"]:
        tiffs_by_identifier, tiff_paths = _discover_tiffs(
            root, resolved["tiff_dir"], findings
        )
    image_receipts = _check_images(all_captions, tiffs_by_identifier, root, findings)

    if "toc" in resolved and inputs["toc"]:
        main_document = next(
            (
                document
                for document in documents
                if document.get("kind") == "main"
            ),
            None,
        )
        toc_result = _check_graphical_toc(
            resolved["toc"], inputs["toc"], main_document, findings
        )
    if "checksum_manifest" in resolved and inputs["checksum_manifest"]:
        manifest_result = _check_manifest(
            resolved["checksum_manifest"], inputs["checksum_manifest"], inventory, excluded_locks, findings
        )

    required_qa_artifacts = {
        path
        for path in (
            inputs["main_docx"],
            inputs["si_docx"],
            inputs["workbook"],
            inputs["toc"],
        )
        if path
    }
    required_qa_artifacts.update(tiff_paths)
    qa_result = _check_qa_receipt(
        resolved.get("qa_receipt"), inputs.get("qa_receipt"), required_qa_artifacts, inventory, findings
    )
    return _finalize_receipt(
        findings, inputs, inventory, excluded_locks, documents, workbook_result,
        image_receipts, tiff_paths, toc_result, manifest_result, qa_result
    )


class _ArgumentParsingError(Exception):
    pass


class _StructuredArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ArgumentParsingError(message)


def _invalid_input_receipt(message: str) -> dict[str, Any]:
    return {
        "schema_version": AUDIT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "verdict": "INVALID_INPUT",
        "structural_machine_audit": "NOT_RUN",
        "publication_eligibility": "NOT_ELIGIBLE",
        "exit_code": 1,
        "source_writes_performed": False,
        "error": {
            "code": "invalid_cli_arguments",
            "message": message,
        },
    }


def _write_stdout_receipt(receipt: dict[str, Any]) -> None:
    json.dump(receipt, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _StructuredArgumentParser(
        description="Read-only structural audit of a complete manuscript submission bundle."
    )
    parser.add_argument("--bundle-root", required=True, type=Path, help="Bundle directory (the only permitted filesystem scope).")
    parser.add_argument("--main-docx", required=True, help="Bundle-relative main-manuscript DOCX path.")
    parser.add_argument("--si-docx", required=True, help="Bundle-relative Supporting Information DOCX path.")
    parser.add_argument("--workbook", required=True, help="Bundle-relative Supporting Information workbook path.")
    parser.add_argument("--tiff-dir", required=True, help="Bundle-relative directory containing figure TIFF files.")
    parser.add_argument("--toc", required=True, help="Bundle-relative graphical TOC raster or DOCX path.")
    parser.add_argument("--checksum-manifest", required=True, help="Bundle-relative SHA-256 manifest path.")
    parser.add_argument("--qa-receipt", help="Optional bundle-relative external-QA JSON receipt path.")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentParsingError as error:
        receipt = _invalid_input_receipt(str(error))
        _write_stdout_receipt(receipt)
        return 1
    receipt = audit_bundle(
        BundleConfig(
            bundle_root=args.bundle_root,
            main_docx=args.main_docx,
            si_docx=args.si_docx,
            workbook=args.workbook,
            tiff_dir=args.tiff_dir,
            toc=args.toc,
            checksum_manifest=args.checksum_manifest,
            qa_receipt=args.qa_receipt,
        )
    )
    _write_stdout_receipt(receipt)
    return int(receipt["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
