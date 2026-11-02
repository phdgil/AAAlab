"""Synthetic accepted-runtime release tests; no research sources or reviews fabricated."""
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_qsar_runtime as gates

SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
sys.path.insert(0, str(SCRIPTS))
import qsar_release as release


def accepted_fixture(contract_update=None, extra_preprocessing=None, external=None,
                     producer_fields=None, reviewer_fields=None):
    fixture = gates.RuntimeTests()
    fixture.setUp()
    outputs = {}
    try:
        if contract_update is not None:
            contract = json.loads(fixture.contract.read_text())
            contract.update(contract_update)
            fixture.contract.write_text(json.dumps(contract), encoding="utf-8")
            fixture.rt.replace_contract(fixture.contract, "Synthetic reporting configuration")
        external_source = None
        if external is not None:
            external_source = fixture.base / "external.csv"
            external_source.write_text("output_row_id,task_id,group_id,partition,target\next-1,oral,ge1,external,4\next-2,oral,ge2,external,6\n")
        for stage in gates.STAGES[:gates.STAGES.index("release")]:
            source = outputs.get("curation")
            inputs = [str(source)] + ([str(external_source)] if external_source else []) if stage == "preprocessing" else []
            dispatch = fixture.bind(fixture.prepare(stage, inputs=inputs))
            filename = {"preprocessing": "preprocessing_manifest.json", "features": "feature_manifest.json",
                        "training": "search_coverage.json", "final-fit": "final_fit.json",
                        "terminal-evaluation": "terminal_evaluation.json", "development-evaluation": "evaluation.json",
                        "curation": "result.csv"}.get(stage, "result.json")
            output = Path(dispatch["owned_output_dir"]) / filename
            content = {"stage": stage, "fixture_only": True, "limitations": ["<script>alert('science')</script>"],
                       "environment": {"fixture": "1"}, "metrics": {"rmse": 1.25, "n": 12},
                       "development_rows": 24, "terminal_rows": 12, "feature_names": ["alpha", "beta"]}
            if stage == "preprocessing":
                prepared = fixture.preprocessing(dispatch, source, external_source)
                content = {**json.loads(prepared.read_text()), **content}
                content["task_counts"] = {"oral": {"development": 24, "terminal": 12}}
                if external_source:
                    content["task_counts"]["oral"]["external"] = 2
            elif stage == "training":
                content.update(status="complete", cells=[{"task_id": "oral", "scope": "final-selection",
                    "candidate_id": "ridge-descriptors", "expected_search_trials": 2, "expected_anchor_trials": 1,
                    "complete": 3, "failed": 0, "pending": 0}])
            elif stage == "final-fit":
                content["models"] = {"oral": {"family": "RDKit2D", "candidate_id": "ridge-descriptors"}}
            elif stage in {"development-evaluation", "terminal-evaluation"}:
                content.update(mode="development" if stage == "development-evaluation" else "terminal",
                               results=[{"task_id": "oral", "candidate_id": "selection_procedure" if stage == "development-evaluation" else "ridge-descriptors",
                                         "task_type": "regression", "metrics": {"rmse": 1.25, "n": 24 if stage == "development-evaluation" else 12}}])
            output.write_text("output_row_id,task_id,group_id,target\ndev,oral,g1,1\nheld,oral,g2,5\n" if stage == "curation"
                              else json.dumps(content), encoding="utf-8")
            outputs[stage] = output
            stage_outputs = [str(output)]
            if stage == "training":
                selection = output.with_name("final_selection.json")
                selection.write_text(json.dumps({"fixture_only": True, "status": "complete", "tasks": {
                    "oral": {"family": "RDKit2D", "algorithm": "Ridge", "candidate_id": "ridge-descriptors"}}}), encoding="utf-8")
                stage_outputs.append(str(selection))
                outputs["selection"] = selection
            if stage == "preprocessing":
                stage_outputs.extend(str(path) for path in output.parent.iterdir() if path.is_file() and path != output)
                for name, value in (extra_preprocessing or {}).items():
                    extra_output = output.parent / name
                    extra_output.parent.mkdir(parents=True, exist_ok=True)
                    extra_output.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
                    stage_outputs.append(str(extra_output))
                    outputs[name] = extra_output
            extra = {}
            if stage == "improvement":
                extra = {"decision": "STOP", "reason": "Fixture STOP", "model_lock": [str(outputs["selection"])]}
            fixture.rt.submit(dispatch["dispatch_id"], {"outputs": stage_outputs, "summary": "Scientific <endpoint>",
                           "checks": ["Fixture check"], "limitations": ["Limited source <coverage>"],
                           "coverage": {"rows": 24}, **extra, **(producer_fields or {}).get(stage, {})})
            fixture.accept(stage, **{"limitations": ["Reviewer-only <applicability> limitation"],
                                     **(reviewer_fields or {}).get(stage, {})})
        if external is not None:
            external_dispatch = fixture.bind(fixture.prepare("terminal-evaluation", evaluation_mode="external"))
            output = Path(external_dispatch["owned_output_dir"]) / "external_evaluation.json"
            output.write_text(json.dumps({"mode": "external", "fixture_only": True,
                "results": [{"task_id": "oral", "candidate_id": "ridge-descriptors", "task_type": "regression", "metrics": {"rmse": 9.75, "n": 2}}],
                "prior_exposure": {"status": "unknown"}, "claim": "Synthetic external evidence; exposure requires review"}), encoding="utf-8")
            fixture.rt.submit(external_dispatch["dispatch_id"], {"outputs": [str(output)], "summary": "Synthetic external evaluation",
                "checks": ["Fixture shape"], "limitations": ["External exposure remains unknown"],
                **(producer_fields or {}).get("external-evaluation", {})})
            outputs["external-evaluation"] = output
            if external in {"accepted", "blocked"}:
                fixture.accept("terminal-evaluation", evaluation_mode="external", verdict="PASS" if external == "accepted" else "BLOCK",
                               **(reviewer_fields or {}).get("external-evaluation", {}))
        dispatch = fixture.bind(fixture.prepare("release", code=[str(SCRIPTS / "qsar_release.py")]))
        return fixture, outputs, dispatch
    except BaseException:
        fixture.tearDown()
        raise


def accepted_source_bytes(state):
    bindings = [state["contract"]]
    for registry in ("sealed_inputs", "external_inputs", "custodian_only"):
        bindings.extend(state.get(registry, []))
    entries = [*state["stages"].values(), state.get("external_evaluation", {})]
    for entry in entries:
        if entry.get("submission"):
            bindings.extend(entry["submission"]["outputs"] + entry["submission"]["bindings"])
        if entry.get("review"):
            bindings.extend(entry["review"]["evidence_bindings"] + [entry["review"]["native_return_evidence"]])
    return {item["path"]: Path(item["path"]).read_bytes() for item in bindings}


class Tables(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tables = []
        self.links = []
        self.current = self.row = self.cell = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs)["href"])
        elif tag == "table":
            self.current = []
        elif tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr":
            self.current.append(self.row)
        elif tag == "table":
            self.tables.append(self.current)
            self.current = None


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture, self.outputs, self.dispatch = accepted_fixture()
        self.rt = self.fixture.rt
        self.run = self.fixture.run

    def tearDown(self):
        self.fixture.tearDown()

    def package(self):
        return release.package_release(self.run, self.dispatch["dispatch_id"])

    def assert_omission(self, result, published, value, stage, source, source_hash, pointer):
        receipt = next(item for item in result["embedded_omissions"] if item["id"] == published["release_omission"])
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(receipt, {"id": published["release_omission"], "stage": stage, "source": source,
                                   "source_hash": source_hash, "pointer": pointer, "reason": "row-level-data",
                                   "encoding": "qsar-runtime-canonical-json-utf8",
                                   "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)})

    def test_real_minimal_package_escaped_counts_metrics_and_lineage(self):
        result = self.package()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["review_required"])
        self.assertEqual(len(result["stages"]), len(gates.STAGES) - 1)
        out = Path(self.dispatch["owned_output_dir"])
        content = (out / "training_summary.html").read_text()
        self.assertEqual(result["reporting"], {"language": "en"})
        self.assertIn('<html lang="en">', content)
        self.assertLess(content.index("Task / endpoint summary"), content.index("Stage evidence"))
        endpoint = dict(zip(Tables(content).tables[0][0], Tables(content).tables[0][1]))
        self.assertEqual(endpoint["Task / endpoint"], "oral")
        self.assertIn("Development: 24", endpoint["Recorded cohort rows"])
        self.assertIn("Terminal evaluation: 12", endpoint["Recorded cohort rows"])
        self.assertIn("Algorithm: Ridge", endpoint["Chosen representation / model"])
        self.assertIn("External evaluation: Unavailable", endpoint["Recorded cohort rows"])
        self.assertEqual(endpoint["External evaluation"], "Unavailable")
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertIn("1.25", content)
        self.assertIn("development_rows", content)
        self.assertIn("feature_names", content)
        self.assertIn("Reviewer-only &lt;applicability&gt; limitation", content)
        for stage in result["stages"]:
            self.assertEqual(stage["review"], self.rt.status()["stages"][stage["stage"]]["review"])
        self.assertTrue(any(a["storage"] == "external-reference" for a in result["artifacts"]))
        self.assertTrue(any(a["stage"] == "terminal-evaluation" and a["relative_path"] for a in result["artifacts"]))
        self.assertFalse(any(a["source"]["path"] == str(self.fixture.holdout) for a in result["artifacts"]))
        protected = release.protected_paths(self.rt.status())
        self.assertFalse(any(a["source"]["path"] in protected for a in result["artifacts"]))
        self.assertFalse(any(b["path"] in protected for s in result["stages"] for b in s["input_bindings"]))
        self.assertTrue(all(link == "release_manifest.json" or link.startswith("./summaries/") for link in Tables(content).links))
        self.assertIn("provenance references, not promises", content)
        self.assertEqual(self.rt.status()["stages"]["release"]["status"], "prepared")
        for artifact in result["artifacts"]:
            if artifact["relative_path"]:
                self.assertEqual(release.snapshot(out / artifact["relative_path"])["sha256"], artifact["source"]["sha256"])

    def test_package_uses_hash_bound_contract_language_without_changing_science(self):
        fixture, outputs, dispatch = accepted_fixture({"reporting": {"language": "ko"}})
        self.addCleanup(fixture.tearDown)
        before = {name: release.snapshot(path) for name, path in outputs.items()}
        result = release.package_release(fixture.run, dispatch["dispatch_id"])
        content = (Path(dispatch["owned_output_dir"]) / "training_summary.html").read_text(encoding="utf-8")
        self.assertEqual(result["reporting"], {"language": "ko"})
        self.assertIn('<html lang="ko">', content)
        self.assertIn("선택된 분자 표현·모델", content)
        self.assertIn("개발 교차검증 지표", content)
        self.assertIn("RDKit2D", content)
        self.assertEqual(before, {name: release.snapshot(path) for name, path in outputs.items()})
        self.assertEqual(result["identity"]["contract"], release.snapshot(fixture.contract))

    def test_invalid_contract_language_rejects_before_writing_package(self):
        fixture, _, dispatch = accepted_fixture({"reporting": {"language": "fr"}})
        self.addCleanup(fixture.tearDown)
        with self.assertRaisesRegex(release.GateError, "reporting.language"):
            release.package_release(fixture.run, dispatch["dispatch_id"])
        out = Path(dispatch["owned_output_dir"])
        self.assertFalse(any((out / name).exists() for name in ("summaries", "release_manifest.json", "training_summary.html")))

    def test_all_protected_registries_exclude_summaries_before_json_parsing(self):
        names = ("sealed/exclusion_summary.json", "external/source_manifest.json", "grouping/curation_manifest.json")
        fixture, outputs, dispatch = accepted_fixture(extra_preprocessing={name: "Not JSON: custodian-only rows" for name in names})
        self.addCleanup(fixture.tearDown)
        state = fixture.rt.status()
        for registry, name in zip(("sealed_inputs", "external_inputs", "custodian_only"), names):
            state.setdefault(registry, []).append(release.snapshot(outputs[name]))
        with patch.object(release.Runtime, "status", return_value=state):
            result = release.package_release(fixture.run, dispatch["dispatch_id"])
        protected = {str(outputs[name].resolve()) for name in names} | {str(fixture.holdout.resolve())}
        self.assertFalse(any(a["source"]["path"] in protected for a in result["artifacts"]))
        self.assertFalse(any(b["path"] in protected for s in result["stages"] for b in s["input_bindings"]))
        out = Path(dispatch["owned_output_dir"])
        content = (out / "training_summary.html").read_text(encoding="utf-8")
        self.assertNotIn("custodian-only rows", content)
        for name in names:
            self.assertNotIn(Path(name).name, content)
            self.assertFalse(any(p.name.endswith(Path(name).name) for p in (out / "summaries").rglob("*.json")))

    def test_row_bearing_recognized_json_is_referenced_not_published(self):
        raw = {"rows/training_summary.json": {"payload": {"rows": [{"target": "fixture-only-secret"}]}},
               "source/source_manifest.json": {"source_records": [{"target": "fixture-only-secret"}]},
               "identity/exclusion_summary.json": {"observations": [{"output_row_id": "fixture-row", "target": "fixture-only-secret"}]}}
        fixture, outputs, dispatch = accepted_fixture(extra_preprocessing=raw)
        self.addCleanup(fixture.tearDown)
        result = release.package_release(fixture.run, dispatch["dispatch_id"])
        out = Path(dispatch["owned_output_dir"])
        content = (out / "training_summary.html").read_text(encoding="utf-8")
        self.assertNotIn("fixture-only-secret", content)
        for name in raw:
            record = next(a for a in result["artifacts"] if a["source"]["path"] == str(outputs[name].resolve()))
            self.assertEqual(record["storage"], "external-reference")
            self.assertIsNone(record["relative_path"])
            self.assertIn("Row-level JSON", record["copy_omitted"])
            self.assertEqual(record["source"], release.snapshot(outputs[name]))
        self.assertTrue(any(a["stage"] == "preprocessing" and a["relative_path"] for a in result["artifacts"]))

    def test_terminal_and_external_inline_coverage_rows_are_omitted_with_receipts(self):
        producer_fields = {stage: {"coverage": {"rows": [{"row_id": stage + "-private-row",
                            "y_true": stage + "-private-target"}], "evaluated_rows": 12 if stage == "terminal-evaluation" else 2}}
                           for stage in ("terminal-evaluation", "external-evaluation")}
        fixture, _, dispatch = accepted_fixture(external="accepted", producer_fields=producer_fields)
        self.addCleanup(fixture.tearDown)
        before = fixture.rt.status()
        source_bytes = accepted_source_bytes(before)
        result = release.package_release(fixture.run, dispatch["dispatch_id"])
        out = Path(dispatch["owned_output_dir"])
        manifest = (out / "release_manifest.json").read_text(encoding="utf-8")
        content = (out / "training_summary.html").read_text(encoding="utf-8")
        self.assertEqual(json.loads(manifest), result)
        self.assertEqual(len(result["embedded_omissions"]), 2)
        for stage in producer_fields:
            original = before["external_evaluation"] if stage == "external-evaluation" else before["stages"][stage]
            published = next(record for record in result["stages"] if record["stage"] == stage)
            self.assert_omission(result, published["coverage"], producer_fields[stage]["coverage"], stage,
                                 "candidate", original["submission"]["candidate_hash"], "/coverage")
            self.assertEqual(published["candidate_hash"], original["submission"]["candidate_hash"])
            self.assertEqual(published["review"], original["review"])
            for output in (manifest, content):
                self.assertNotIn(stage + "-private-row", output)
                self.assertNotIn(stage + "-private-target", output)
        token = {"submission": before["external_evaluation"]["submission"]["candidate_hash"],
                 "review": before["external_evaluation"]["review"]["review_hash"]}
        self.assertEqual(result["identity"]["external_acceptance"], token)
        self.assertEqual(result["external_evaluation"], {"status": "accepted", "packaged": True, "acceptance": token})
        self.assertEqual(result["identity"]["upstream_tokens"], dispatch["upstream_tokens"])
        endpoint = dict(zip(Tables(content).tables[0][0], Tables(content).tables[0][1]))
        self.assertIn("RMSE: 1.25", endpoint["Terminal evaluation"])
        self.assertIn("RMSE: 9.75", endpoint["External evaluation"])
        self.assertIn("Development: 24", endpoint["Recorded cohort rows"])
        training = next(row for row in Tables(content).tables[1][1:] if row[0] == "training")
        self.assertEqual(json.loads(training[2]), {"rows": 24})
        for receipt in result["embedded_omissions"]:
            self.assertIn(receipt["id"], content)
            self.assertIn(receipt["sha256"], content)
        self.assertEqual(source_bytes, accepted_source_bytes(fixture.rt.status()))
        self.assertEqual(before["stages"], fixture.rt.status()["stages"])
        self.assertEqual(before["external_evaluation"], fixture.rt.status()["external_evaluation"])

    def test_nested_inline_checks_limitations_and_review_extras_do_not_publish_rows(self):
        producer_fields = {"training": {
            "checks": [{"nested": {"source_records": [{"target": "producer-check-private-target"}]}}],
            "limitations": [{"nested": [{"output_row_id": "producer-limit-private-row", "target": "producer-limit-private-target"}]}],
            "coverage": {"rows": 24, "metrics": {"n": 24}}}}
        reviewer_fields = {"training": {
            "checks": [{"nested": {"rows": [{"target": "review-check-private-target"}]}}],
            "limitations": [{"nested": [{"row_id": "review-limit-private-row", "target": "review-limit-private-target"}]}],
            "findings": [{"severity": "info", "resolved": True, "details": {"source_records": [{"target": "review-finding-private-target"}]}}],
            "review/<detail>~": {"nested": {"rows": [{"row_id": "검토-비공개-행", "target": "review-extra-private-target"}]}},
            "coverage": {"rows": 24, "metrics": {"n": 24}}}}
        aggregates = {"aggregate/training_summary.json": {"rows": 77, "source_records": 9, "metrics": {"n": 77},
                      "zero": {"rows": 0}, "empty": {"rows": [], "source_records": {}}}}
        fixture, outputs, dispatch = accepted_fixture({"reporting": {"language": "ko"}}, aggregates,
            producer_fields=producer_fields, reviewer_fields=reviewer_fields)
        self.addCleanup(fixture.tearDown)
        before = fixture.rt.status()
        source_bytes = accepted_source_bytes(before)
        result = release.package_release(fixture.run, dispatch["dispatch_id"])
        published = next(record for record in result["stages"] if record["stage"] == "training")
        original = before["stages"]["training"]
        self.assertEqual(len(result["embedded_omissions"]), 6)
        for source, fields, record in (("candidate", producer_fields["training"], published),
                                       ("review", reviewer_fields["training"], published["review"])):
            source_hash = original["submission"]["candidate_hash"] if source == "candidate" else original["review"]["review_hash"]
            for key, value in fields.items():
                if key == "coverage":
                    self.assertEqual(record[key], value)
                else:
                    self.assert_omission(result, record[key], value, "training", source, source_hash,
                                         "/" + key.replace("~", "~0").replace("/", "~1"))
        out = Path(dispatch["owned_output_dir"])
        manifest = (out / "release_manifest.json").read_text(encoding="utf-8")
        content = (out / "training_summary.html").read_text(encoding="utf-8")
        for output in (manifest, content):
            for private in ("producer-check-private-target", "producer-limit-private-row", "producer-limit-private-target",
                            "review-check-private-target", "review-limit-private-row", "review-limit-private-target",
                            "review-finding-private-target", "검토-비공개-행", "review-extra-private-target"):
                self.assertNotIn(private, output)
        self.assertIn('<html lang="ko">', content)
        self.assertIn("내장된 행 수준 데이터의 제외 기록", content)
        self.assertIn("review~1&lt;detail&gt;~0", content)
        self.assertNotIn("<detail>", content)
        self.assertIn("&lt;script&gt;", content)
        facts = [row for table in Tables(content).tables for row in table if len(row) == 2]
        self.assertIn(["rows", "77"], facts)
        self.assertIn(["source_records", "9"], facts)
        self.assertIn(["zero.rows", "0"], facts)
        self.assertEqual(published["review"]["review_hash"], original["review"]["review_hash"])
        self.assertEqual(published["review"]["verdict"], "PASS")
        self.assertEqual(published["review"]["evidence_bindings"], original["review"]["evidence_bindings"])
        preprocessing = next(record for record in result["stages"] if record["stage"] == "preprocessing")
        self.assertEqual(preprocessing["review"]["preprocessing_evidence_bindings"],
                         before["stages"]["preprocessing"]["review"]["preprocessing_evidence_bindings"])
        artifact = next(item for item in result["artifacts"] if item["source"]["path"] == str(outputs["aggregate/training_summary.json"].resolve()))
        self.assertEqual(artifact["storage"], "copied-summary")
        self.assertEqual((out / artifact["relative_path"]).read_bytes(), outputs["aggregate/training_summary.json"].read_bytes())
        self.assertTrue(all(link == "release_manifest.json" or link.startswith("./summaries/") for link in Tables(content).links))
        self.assertEqual(source_bytes, accepted_source_bytes(fixture.rt.status()))
        self.assertEqual(before["stages"], fixture.rt.status()["stages"])

    def test_row_shaped_review_keeps_acceptance_identity_but_omits_all_details(self):
        for fields in ({"row_id": "root-review-private-row", "y_true": "root-review-private-target"},
                       {"rows": [{"target": "root-review-private-target"}], "detail": "root-review-private-row"},
                       {"source_records": [{"target": "root-review-private-target"}], "detail": "root-review-private-row"}):
            with self.subTest(fields=fields):
                fixture, _, dispatch = accepted_fixture(reviewer_fields={"terminal-evaluation": fields})
                self.addCleanup(fixture.tearDown)
                before = fixture.rt.status()
                source_bytes = accepted_source_bytes(before)
                result = release.package_release(fixture.run, dispatch["dispatch_id"])
                original = before["stages"]["terminal-evaluation"]["review"]
                published = next(record for record in result["stages"] if record["stage"] == "terminal-evaluation")["review"]
                self.assert_omission(result, published, original, "terminal-evaluation", "review", original["review_hash"], "")
                self.assertEqual(set(published), {"release_omission", "candidate_hash", "producer_execution_id",
                                                  "reviewer_execution_id", "verdict", "review_hash"})
                for key in set(published) - {"release_omission"}:
                    self.assertEqual(published[key], original[key])
                out = Path(dispatch["owned_output_dir"])
                for name in ("release_manifest.json", "training_summary.html"):
                    content = (out / name).read_text(encoding="utf-8")
                    self.assertNotIn("root-review-private-row", content)
                    self.assertNotIn("root-review-private-target", content)
                self.assertIn("Row-level details omitted", (out / "training_summary.html").read_text(encoding="utf-8"))
                self.assertEqual(source_bytes, accepted_source_bytes(fixture.rt.status()))
                self.assertEqual(before["stages"], fixture.rt.status()["stages"])

    def test_separately_reviewed_external_results_are_hash_bound_and_not_terminal_metrics(self):
        fixture, outputs, dispatch = accepted_fixture(external="accepted")
        self.addCleanup(fixture.tearDown)
        result = release.package_release(fixture.run, dispatch["dispatch_id"])
        accepted = fixture.rt.status()["external_evaluation"]
        token = {"submission": accepted["submission"]["candidate_hash"], "review": accepted["review"]["review_hash"]}
        self.assertEqual(result["identity"]["external_acceptance"], token)
        self.assertEqual(result["external_evaluation"], {"status": "accepted", "packaged": True, "acceptance": token})
        stage = next(s for s in result["stages"] if s["stage"] == "external-evaluation")
        self.assertEqual(stage["review"], accepted["review"])
        self.assertEqual(stage["evaluation_mode"], "external")
        artifact = next(a for a in result["artifacts"] if a["stage"] == "external-evaluation")
        self.assertEqual(artifact["source"], release.snapshot(outputs["external-evaluation"]))
        out = Path(dispatch["owned_output_dir"])
        self.assertEqual(release.snapshot(out / artifact["relative_path"])["sha256"], artifact["source"]["sha256"])
        content = (out / "training_summary.html").read_text(encoding="utf-8")
        table = Tables(content).tables[0]
        endpoint = dict(zip(table[0], table[1]))
        self.assertIn("RMSE: 1.25", endpoint["Terminal evaluation"])
        self.assertNotIn("9.75", endpoint["Terminal evaluation"])
        self.assertIn("RMSE: 9.75", endpoint["External evaluation"])
        self.assertIn("External exposure remains unknown", content)

    def test_unreviewed_or_blocked_external_results_are_explicit_but_not_copied(self):
        for status in ("produced", "blocked"):
            with self.subTest(status=status):
                fixture, _, dispatch = accepted_fixture(external=status)
                self.addCleanup(fixture.tearDown)
                result = release.package_release(fixture.run, dispatch["dispatch_id"])
                self.assertEqual(result["external_evaluation"], {"status": status, "packaged": False, "acceptance": None})
                self.assertFalse(any(a["stage"] == "external-evaluation" for a in result["artifacts"]))
                self.assertFalse(any(s["stage"] == "external-evaluation" for s in result["stages"]))
                content = (Path(dispatch["owned_output_dir"]) / "training_summary.html").read_text(encoding="utf-8")
                self.assertIn("External evaluation evidence status (run-level): " + status, content)
                self.assertNotIn("external_evaluation.json", content)
                self.assertNotIn("9.75", content)
                table = Tables(content).tables[0]
                self.assertEqual(dict(zip(table[0], table[1]))["External evaluation"], "Unavailable")

    def test_stale_accepted_external_result_blocks_token_bound_release(self):
        fixture, outputs, dispatch = accepted_fixture(external="accepted")
        self.addCleanup(fixture.tearDown)
        outputs["external-evaluation"].write_text("changed", encoding="utf-8")
        with self.assertRaises(release.GateError):
            release.package_release(fixture.run, dispatch["dispatch_id"])

    def test_external_review_must_match_current_independent_candidate(self):
        fixture, _, dispatch = accepted_fixture(external="accepted")
        self.addCleanup(fixture.tearDown)
        state = fixture.rt.status()
        state["external_evaluation"]["review"]["candidate_hash"] = "foreign-candidate"
        with patch.object(release.Runtime, "status", return_value=state):
            with self.assertRaisesRegex(release.GateError, "independent acceptance"):
                release.package_release(fixture.run, dispatch["dispatch_id"])

    def test_mid_package_custody_registry_change_never_completes(self):
        state = self.rt.status()
        original = release.render_summary
        def changed(*args):
            content = original(*args)
            state["custodian_only"].append(release.snapshot(self.outputs["selection"]))
            return content
        with patch.object(release.Runtime, "status", return_value=state), patch.object(release, "render_summary", side_effect=changed):
            with self.assertRaisesRegex(release.GateError, "Protected artifact registry changed"):
                self.package()
        manifest = json.loads((Path(self.dispatch["owned_output_dir"]) / "release_manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")

    def test_stale_accepted_artifact_blocks(self):
        self.outputs["features"].write_text("changed", encoding="utf-8")
        with self.assertRaises(release.GateError):
            self.package()

    def test_missing_final_fit_acceptance_blocks(self):
        self.rt.invalidate("final-fit", "Synthetic test invalidation")
        with self.assertRaises(release.GateError):
            self.package()

    def test_foreign_output_rejected(self):
        state = self.rt.status()
        state["dispatches"][self.dispatch["dispatch_id"]]["owned_output_dir"] = str(self.fixture.base / "foreign")
        with patch.object(release.Runtime, "status", return_value=state):
            with self.assertRaisesRegex(release.GateError, "ownership"):
                self.package()
        self.assertFalse((self.fixture.base / "foreign").exists())

    def test_unbound_or_wrong_stage_rejected(self):
        state = self.rt.status()
        state["dispatches"][self.dispatch["dispatch_id"]]["status"] = "prepared"
        with patch.object(release.Runtime, "status", return_value=state):
            with self.assertRaisesRegex(release.GateError, "bound/running"):
                self.package()

    def test_no_overwrite_of_previous_package(self):
        first = self.package()
        with self.assertRaisesRegex(release.GateError, "already contains"):
            self.package()
        self.assertEqual(release.snapshot(first["summary"]["path"]), first["summary"])

    def test_mid_package_input_change_never_completes(self):
        original = release.render_summary
        def changed(*args):
            result = original(*args)
            self.outputs["terminal-evaluation"].write_text("changed", encoding="utf-8")
            return result
        with patch.object(release, "render_summary", side_effect=changed):
            with self.assertRaises(release.GateError):
                self.package()
        manifest = json.loads((Path(self.dispatch["owned_output_dir"]) / "release_manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")

    def test_cli_uses_live_dispatch(self):
        import subprocess
        result = subprocess.run([sys.executable, str(SCRIPTS / "qsar_release.py"), "--run-dir", str(self.run),
                                 "--dispatch-id", self.dispatch["dispatch_id"]], capture_output=True, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "complete")


class RenderSummaryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"run_id": "synthetic-render", "contract_version": "fixture-v1", "stages": [], "artifacts": []}

    def summary(self, stage, name, content, path=None):
        return {"stage": stage, "name": name, "relative_path": path or f"summaries/{stage}/fixture-{name}", "content": content}

    def endpoint_rows(self, content):
        table = Tables(content).tables[0]
        self.assertEqual(table[0][0], "Task / endpoint")
        return {row[0]: dict(zip(table[0], row)) for row in table[1:]}

    def test_rendering_rejects_unsanitized_row_data_before_producing_html(self):
        for value in ({"rows": [{"target": "private-target"}]},
                      {"nested": [{"row_id": "private-row", "y_true": 7.1}]},
                      {"source_records": [{"target": "private-target"}]}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(release.GateError, "row-data exclusion"):
                    release.render_summary({**self.manifest, "stages": [{"coverage": value}]}, [])
                with self.assertRaisesRegex(release.GateError, "row-data exclusion"):
                    release.render_summary(self.manifest, [self.summary("training", "training_summary.json", value)])

    def test_language_validation_and_translated_columns(self):
        summaries = [self.summary("preprocessing", "preprocessing_manifest.json", {"task_counts": {"oral": {"development": 4}}})]
        for reporting, language, header in (({}, "en", "Chosen representation / model"),
                                             ({"language": "en"}, "en", "Chosen representation / model"),
                                             ({"language": "ko"}, "ko", "선택된 분자 표현·모델")):
            with self.subTest(reporting=reporting):
                content = release.render_summary({**self.manifest, "reporting": reporting}, summaries)
                self.assertIn(f'<html lang="{language}">', content)
                self.assertIn(header, Tables(content).tables[0][0])
                self.assertIn("oral", content)
        content = release.render_summary(self.manifest, summaries)
        self.assertIn('<html lang="en">', content)
        self.assertIn("Unavailable", content)
        for invalid in (None, "", "fr", "EN", "en-US", "ko-KR", " ko", "ko ", True, 1, [], {}):
            with self.subTest(language=invalid), self.assertRaisesRegex(release.GateError, "reporting.language"):
                release.render_summary({**self.manifest, "reporting": {"language": invalid}}, summaries)
        for invalid in (None, [], "ko", True):
            with self.subTest(reporting=invalid), self.assertRaisesRegex(release.GateError, "reporting must be an object"):
                release.render_summary({**self.manifest, "reporting": invalid}, summaries)

    def test_endpoint_cells_and_links_escape_untrusted_strings_in_both_languages(self):
        task = '<script>alert("endpoint")</script>'
        path = 'summaries/" onmouseover="fixture"/<x>.json'
        summaries = [self.summary("preprocessing", "preprocessing_manifest.json", {"task_counts": {task: {"development": 2}}}),
                     self.summary("training", "final_selection.json", {"tasks": {task: {"family": "<b>Morgan</b>", "algorithm": "A&B"}}}),
                     self.summary("development-evaluation", "evaluation.json", {"mode": "development", "results": [
                         {"task_id": task, "candidate_id": '<img src=x onerror="fixture">', "task_type": "classification",
                          "positive_class": 1, "threshold": 0.5, "metrics": {"<metric>": "<svg onload=fixture>"}}]}, path)]
        for language in ("en", "ko"):
            with self.subTest(language=language):
                manifest = {**self.manifest, "run_id": "<run>", "reporting": {"language": language}, "limitations": ["<limit>"]}
                content = release.render_summary(manifest, summaries)
                self.assertNotIn("<script>", content)
                self.assertNotIn("<img ", content)
                self.assertNotIn("<svg ", content)
                self.assertNotIn("<b>Morgan</b>", content)
                self.assertNotIn('" onmouseover="fixture"', content)
                self.assertIn("&lt;script&gt;", content)
                self.assertIn("A&amp;B", content)
                self.assertIn("&lt;limit&gt;", content)
                self.assertIn('href="./summaries/%22%20onmouseover%3D%22fixture%22/%3Cx%3E.json"', content)
                self.assertEqual(Tables(content).tables[0][1][0], task)

    def test_absent_optional_fields_and_undefined_metrics_are_not_fabricated(self):
        summaries = [self.summary("preprocessing", "preprocessing_manifest.json", {"task_counts": {"counts-only": {}}}),
                     self.summary("development-evaluation", "evaluation.json", {"mode": "development", "results": [
                         {"task_id": "metrics-only", "metrics": {"n": 2, "r2": None, "r2_undefined_reason": "constant_target_or_single_row"}}]}),
                     self.summary("final-fit", "final_fit.json", {"models": {"model-only": {}}}),
                     self.summary("training", "final_selection.json", {"tasks": None}),
                     self.summary("terminal-evaluation", "terminal_evaluation.json", {"mode": "terminal", "results": None}),
                     self.summary("external-evaluation", "external_evaluation.json", {"mode": "external", "results": None}),
                     self.summary("features", "feature_manifest.json", None)]
        content = release.render_summary(self.manifest, summaries)
        rows = self.endpoint_rows(content)
        self.assertEqual(set(rows), {"counts-only", "metrics-only", "model-only"})
        self.assertIn("Development: Unavailable", rows["counts-only"]["Recorded cohort rows"])
        self.assertEqual(rows["counts-only"]["Chosen representation / model"], "Unavailable")
        metrics = rows["metrics-only"]["Development CV metrics"]
        self.assertIn("Evaluated rows (n): 2", metrics)
        self.assertIn("R²: Unavailable", metrics)
        self.assertIn("constant_target_or_single_row", metrics)
        self.assertIn("Task type: Unavailable", metrics)
        self.assertNotIn("regression", metrics)
        self.assertNotIn("0.0", metrics)
        self.assertEqual(rows["metrics-only"]["Recorded cohort rows"], "Unavailable")
        self.assertEqual(rows["metrics-only"]["Terminal evaluation"], "Unavailable")
        self.assertEqual(rows["metrics-only"]["External evaluation"], "Unavailable")
        self.assertIn("Representation: Unavailable", rows["model-only"]["Chosen representation / model"])

    def test_report_level_metrics_status_and_wrong_scopes_are_not_endpoint_evidence(self):
        self.manifest["stages"] = [{"stage": "training", "summary": "Run complete", "limitations": [],
            "coverage": {"task_counts": {"coverage-only": 9999}, "metrics": {"rmse": 9999}},
            "review": {"summary": "Synthetic review"}}]
        summaries = [self.summary("preprocessing", "preprocessing_manifest.json", {"development_rows": 9999, "task_counts": {"oral": {"development": 8}}}),
                     self.summary("training", "search_coverage.json", {"status": "complete", "completed_trials": 9999}),
                     self.summary("training", "training_summary.json", {"tasks": {"report-only": {"metrics": {"rmse": 9999}}}}),
                     self.summary("training", "evaluation.json", {"mode": "development", "results": [{"task_id": "wrong-stage", "metrics": {"rmse": 9999}}]}),
                     self.summary("development-evaluation", "evaluation.json", {"mode": "terminal", "results": [{"task_id": "wrong-mode", "metrics": {"rmse": 9999}}]}),
                     self.summary("development-evaluation", "evaluation.json", {"mode": "development", "metrics": {"rmse": 9999},
                         "results": [{"task_id": "oral", "candidate_id": "selection_procedure", "metrics": {"rmse": 1.125}}]})]
        content = release.render_summary(self.manifest, summaries)
        rows = self.endpoint_rows(content)
        self.assertEqual(set(rows), {"oral"})
        self.assertIn("RMSE: 1.125", rows["oral"]["Development CV metrics"])
        self.assertEqual(rows["oral"]["Chosen representation / model"], "Unavailable")
        self.assertEqual(rows["oral"]["Search coverage / recorded status"], "Unavailable")
        self.assertNotIn("9999", content.split("<h2>Stage evidence</h2>")[0])
        self.assertIn("9999", content.split("<h2>Stage evidence</h2>")[1])
        self.assertIn("not scientific completion", content)
        self.assertNotIn("scientifically complete", content)

    def test_classification_cv_terminal_external_metadata_and_metrics_remain_separate(self):
        cv = {"task_id": "binary", "candidate_id": "selection_procedure", "task_type": "classification", "positive_class": 1,
              "fold_thresholds": {"0": 0.4, "1": 0.6}, "threshold_policy": "outer-training-only",
              "metrics": {"n": 20, "roc_auc": 0.71, "pr_auc": None, "accuracy": 0.65}}
        terminal = {"task_id": "binary", "candidate_id": "locked-classifier", "task_type": "classification", "positive_class": 1,
                    "threshold": 0.6, "metrics": {"n": 8, "roc_auc": 0.82, "accuracy": 0.75}}
        external = {**terminal, "metrics": {"n": 3, "roc_auc": 0.93, "accuracy": 1.0}}
        report = {"mode": "external", "results": [external], "prior_exposure": {"status": "unknown"},
                  "claim": "External provenance requires independent review"}
        summaries = [self.summary("preprocessing", "preprocessing_manifest.json", {"task_counts": {"binary": {"development": 20, "terminal": 8, "external": 3}}}),
                     self.summary("development-evaluation", "evaluation.json", {"mode": "development", "results": [cv]}),
                     self.summary("terminal-evaluation", "terminal_evaluation.json", {"mode": "terminal", "results": [terminal]}),
                     self.summary("external-evaluation", "external_evaluation.json", report)]
        content = release.render_summary(self.manifest, summaries)
        row = self.endpoint_rows(content)["binary"]
        for scope, expected, excluded in (("Development CV metrics", "0.71", "0.82"),
                                          ("Terminal evaluation", "0.82", "0.93"),
                                          ("External evaluation", "0.93", "0.71")):
            self.assertIn("ROC AUC: " + expected, row[scope])
            self.assertNotIn(excluded, row[scope])
            self.assertIn("Task type: classification", row[scope])
            self.assertIn("Positive class: 1", row[scope])
        self.assertIn("Per-fold thresholds", row["Development CV metrics"])
        self.assertIn("outer-training-only", row["Development CV metrics"])
        self.assertIn("PR AUC: Unavailable", row["Development CV metrics"])
        self.assertIn("Decision threshold: 0.6", row["Terminal evaluation"])
        self.assertIn("External evaluation: 3", row["Recorded cohort rows"])
        self.assertIn("prior_exposure.status", content)
        self.assertIn("External provenance requires independent review", content)
        summaries.pop()
        row = self.endpoint_rows(release.render_summary(self.manifest, summaries))["binary"]
        self.assertIn("External evaluation: 3", row["Recorded cohort rows"])
        self.assertEqual(row["External evaluation"], "Unavailable")

    def test_external_report_requires_its_own_stage_and_mode(self):
        result = {"task_id": "oral", "metrics": {"rmse": 9999}}
        summaries = [self.summary("preprocessing", "preprocessing_manifest.json", {"task_counts": {"oral": {"external": 2}}}),
                     self.summary("terminal-evaluation", "terminal_evaluation.json", {"mode": "terminal", "external_evaluation": {"mode": "external", "results": [result]}}),
                     self.summary("terminal-evaluation", "external_evaluation.json", {"mode": "external", "results": [result]}),
                     self.summary("external-evaluation", "external_evaluation.json", {"mode": "terminal", "results": [result]})]
        self.manifest["external_evaluation"] = {"status": "failed", "packaged": False}
        content = release.render_summary(self.manifest, summaries)
        self.assertEqual(self.endpoint_rows(content)["oral"]["External evaluation"], "Unavailable")
        self.assertIn("External evaluation evidence status (run-level): failed", content)
        self.assertNotIn("9999", content.split("<h2>Stage evidence</h2>")[0])

    def test_search_coverage_sums_only_complete_task_cell_fields(self):
        cells = [{"task_id": "oral", "scope": "outer-0", "expected_search_trials": 2, "expected_anchor_trials": 1,
                  "complete": 3, "failed": 0, "pending": 0},
                 {"task_id": "oral", "scope": "final-selection", "expected_search_trials": 4, "expected_anchor_trials": 1,
                  "failed": 0, "pending": 5},
                 {"task_id": "dermal", "scope": "final-selection", "expected_search_trials": 1, "expected_anchor_trials": 1,
                  "complete": 2, "failed": 0, "pending": 0}]
        summaries = [self.summary("training", "search_coverage.json", {"status": "budget_exhausted", "completed_trials": 9999, "cells": cells})]
        rows = self.endpoint_rows(release.render_summary(self.manifest, summaries))
        oral = rows["oral"]["Search coverage / recorded status"]
        self.assertIn("Search report status (run-level): budget_exhausted", oral)
        self.assertIn("Expected search trials: 6", oral)
        self.assertIn("Expected anchor trials: 2", oral)
        self.assertIn("Completed trials (anchor + search): Unavailable", oral)
        self.assertIn("Pending trials (anchor + search): 5", oral)
        self.assertNotIn("9999", oral)
        dermal = rows["dermal"]["Search coverage / recorded status"]
        self.assertIn("Completed trials (anchor + search): 2", dermal)
        self.assertIn("Pending trials (anchor + search): 0", dermal)

    def test_no_endpoint_evidence_does_not_create_a_synthetic_task(self):
        summaries = [self.summary("training", "training_summary.json", {"status": "complete", "metrics": {"rmse": 1.2}})]
        content = release.render_summary(self.manifest, summaries)
        self.assertIn("Tasks / endpoints with recorded evidence: 0", content)
        self.assertIn("No task / endpoint-level evidence", content)
        self.assertNotIn("<th>Task / endpoint</th>", content)
        self.assertIn("metrics.rmse", content)


if __name__ == "__main__":
    unittest.main()
