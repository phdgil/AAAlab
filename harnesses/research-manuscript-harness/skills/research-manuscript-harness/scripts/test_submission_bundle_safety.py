#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_submission_bundle as audit  # noqa: E402


def _finding_codes(findings: audit.FindingLog) -> set[str]:
    return {item["code"] for item in findings.items}


def _write_qa_receipt(root: Path, reviewed_at: str) -> Path:
    path = root / "external-qa.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": audit.QA_SCHEMA,
                "reviewer": {
                    "identity": "independent-reviewer-1",
                    "role": "external QA reviewer",
                    "independence_basis": "Separate reviewer with no manuscript editing role.",
                },
                "reviewed_at": reviewed_at,
                "outcome": "approved",
                "scope": {
                    "visual_readability": {"outcome": "approved"},
                    "citation_semantics": {"outcome": "approved"},
                    "scientific_review": {"outcome": "approved"},
                },
                "artifact_hashes": {},
                "unresolved_blockers": [],
                "scientifically_verified": True,
            }
        ),
        encoding="utf-8",
    )
    return path


class FilesystemSafetyTests(unittest.TestCase):
    def test_scan_errors_block_inventory_and_tiff_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tiff_directory = root / "figures"
            tiff_directory.mkdir()

            def inventory_walk(
                _root: object,
                *,
                topdown: bool,
                onerror: object,
                followlinks: bool,
            ) -> tuple[()]:
                self.assertTrue(topdown)
                self.assertFalse(followlinks)
                assert callable(onerror)
                onerror(PermissionError(13, "denied", str(root / "blocked")))
                return ()

            inventory_findings = audit.FindingLog()
            with mock.patch.object(audit.os, "walk", side_effect=inventory_walk):
                inventory, excluded_locks = audit._inventory_bundle(
                    root, inventory_findings
                )
            self.assertEqual({}, inventory)
            self.assertEqual([], excluded_locks)
            self.assertEqual("FAIL", inventory_findings.status("path_safety"))
            self.assertIn(
                "bundle_directory_scan_failed", _finding_codes(inventory_findings)
            )

            def tiff_walk(
                _root: object,
                *,
                topdown: bool,
                onerror: object,
                followlinks: bool,
            ) -> tuple[()]:
                self.assertTrue(topdown)
                self.assertFalse(followlinks)
                assert callable(onerror)
                onerror(
                    PermissionError(
                        13, "denied", str(tiff_directory / "blocked")
                    )
                )
                return ()

            tiff_findings = audit.FindingLog()
            with mock.patch.object(audit.os, "walk", side_effect=tiff_walk):
                by_identifier, relative_paths = audit._discover_tiffs(
                    root, tiff_directory, tiff_findings
                )
            self.assertEqual({}, by_identifier)
            self.assertEqual([], relative_paths)
            self.assertEqual("FAIL", tiff_findings.status("images"))
            self.assertIn(
                "tiff_directory_scan_failed", _finding_codes(tiff_findings)
            )

    def test_fifo_mode_is_rejected_before_hashing_or_opening(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            findings = audit.FindingLog()
            walked = [(str(root), [], ["fig-1.tiff"])]
            fifo_stat = SimpleNamespace(st_mode=stat.S_IFIFO, st_size=0)
            tiff_findings = audit.FindingLog()

            with (
                mock.patch.object(audit.os, "walk", return_value=walked),
                mock.patch.object(audit, "_is_link", return_value=False),
                mock.patch.object(Path, "is_dir", return_value=True),
                mock.patch.object(Path, "stat", return_value=fifo_stat),
                mock.patch.object(audit, "_sha256_file") as hash_file,
            ):
                inventory, _ = audit._inventory_bundle(root, findings)
                by_identifier, relative_paths = audit._discover_tiffs(
                    root, root, tiff_findings
                )

            self.assertEqual({}, inventory)
            self.assertIn("non_regular_bundle_entry", _finding_codes(findings))
            self.assertEqual("FAIL", findings.status("path_safety"))
            hash_file.assert_not_called()
            self.assertEqual({}, by_identifier)
            self.assertEqual([], relative_paths)
            self.assertIn(
                "non_regular_tiff_directory_entry",
                _finding_codes(tiff_findings),
            )
            self.assertEqual("FAIL", tiff_findings.status("images"))


class CommandLineSafetyTests(unittest.TestCase):
    @staticmethod
    def _complete_arguments() -> list[str]:
        return [
            "--bundle-root",
            ".",
            "--main-docx",
            "main.docx",
            "--si-docx",
            "si.docx",
            "--workbook",
            "supporting.xlsx",
            "--tiff-dir",
            "figures",
            "--toc",
            "graphical-toc.tiff",
            "--checksum-manifest",
            "checksums.sha256",
        ]

    def test_missing_and_unknown_arguments_emit_invalid_input_json(self) -> None:
        cases = {
            "missing": [],
            "missing_value": ["--bundle-root"],
            "unknown": [*self._complete_arguments(), "--unknown-option"],
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                    stderr
                ):
                    exit_code = audit.main(arguments)

                receipt = json.loads(stdout.getvalue())
                self.assertEqual(1, exit_code)
                self.assertEqual(audit.AUDIT_SCHEMA, receipt["schema_version"])
                self.assertEqual("INVALID_INPUT", receipt["verdict"])
                self.assertEqual("NOT_RUN", receipt["structural_machine_audit"])
                self.assertEqual("NOT_ELIGIBLE", receipt["publication_eligibility"])
                self.assertEqual(1, receipt["exit_code"])
                self.assertFalse(receipt["source_writes_performed"])
                self.assertEqual(
                    "invalid_cli_arguments", receipt["error"]["code"]
                )
                self.assertIsInstance(receipt["error"]["message"], str)
                self.assertTrue(receipt["error"]["message"])
                self.assertNotIn("bundle_root", receipt)
                self.assertNotIn("inputs", receipt)
                self.assertEqual("", stderr.getvalue())

    def test_help_remains_standard_successful_argparse_output(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                audit.main(["--help"])

        self.assertEqual(0, raised.exception.code)
        self.assertIn("usage:", stdout.getvalue())
        self.assertIn("--bundle-root", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())


class QaTimestampSafetyTests(unittest.TestCase):
    def _check_timestamp(self, reviewed_at: str) -> tuple[dict[str, object], audit.FindingLog]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = _write_qa_receipt(root, reviewed_at)
        findings = audit.FindingLog()
        result = audit._check_qa_receipt(
            path,
            "external-qa.json",
            set(),
            {},
            findings,
        )
        return result, findings

    def test_timezone_aware_same_second_past_timestamp_is_current(self) -> None:
        fixed_now = datetime(2026, 9, 11, 5, 3, 0, 900000, tzinfo=timezone.utc)
        reviewed_at = fixed_now.replace(microsecond=800000).isoformat()
        with mock.patch.object(audit, "datetime", wraps=datetime) as clock:
            clock.now.return_value = fixed_now
            result, findings = self._check_timestamp(reviewed_at)

        self.assertEqual("CURRENT_APPROVED", result["status"])
        self.assertNotIn("future_qa_timestamp", _finding_codes(findings))
        self.assertFalse(result["identity_authenticated"])
        self.assertFalse(result["scientific_claim_verified_by_machine"])

    def test_real_future_timestamp_is_invalid(self) -> None:
        reviewed_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        result, findings = self._check_timestamp(reviewed_at)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("future_qa_timestamp", _finding_codes(findings))
        self.assertFalse(result["identity_authenticated"])
        self.assertFalse(result["scientific_claim_verified_by_machine"])

    def test_utc_conversion_overflow_is_invalid_and_json_serializable(self) -> None:
        result, findings = self._check_timestamp(
            "0001-01-01T00:00:00+23:59"
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("invalid_qa_timestamp", _finding_codes(findings))
        self.assertEqual("INVALID", json.loads(json.dumps(result))["status"])
        self.assertFalse(result["identity_authenticated"])
        self.assertFalse(result["scientific_claim_verified_by_machine"])


if __name__ == "__main__":
    unittest.main()
