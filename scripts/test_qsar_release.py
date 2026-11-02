"""Synthetic accepted-runtime release tests; no research sources or reviews fabricated."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_qsar_runtime as gates

SCRIPTS = Path(__file__).resolve().parents[1] / "harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts"
sys.path.insert(0, str(SCRIPTS))
import qsar_release as release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = gates.RuntimeTests()
        self.fixture.setUp()
        self.rt = self.fixture.rt
        self.run = self.fixture.run
        self.outputs = {}
        for stage in gates.STAGES[:gates.STAGES.index("release")]:
            dispatch = self.fixture.bind(self.fixture.prepare(stage))
            filename = {"preprocessing": "preprocessing_manifest.json", "features": "feature_manifest.json",
                        "training": "search_coverage.json", "final-fit": "final_fit.json",
                        "terminal-evaluation": "terminal_evaluation.json", "development-evaluation": "evaluation.json"}.get(stage, "result.json")
            output = Path(dispatch["owned_output_dir"]) / filename
            content = {"stage": stage, "fixture_only": True, "limitations": ["<script>alert('science')</script>"],
                       "environment": {"fixture": "1"}, "metrics": {"rmse": 1.25, "n": 12},
                       "development_rows": 24, "terminal_rows": 12, "feature_names": ["alpha", "beta"]}
            output.write_text(json.dumps(content), encoding="utf-8")
            self.outputs[stage] = output
            extra = {}
            if stage == "improvement":
                extra = {"decision": "STOP", "reason": "Fixture STOP", "model_lock": [str(self.outputs["training"])]}
            self.rt.submit(dispatch["dispatch_id"], {"outputs": [str(output)], "summary": "Scientific <endpoint>",
                           "checks": ["Fixture check"], "limitations": ["Limited source <coverage>"],
                           "coverage": {"rows": 24}, **extra})
            self.fixture.accept(stage, limitations=["Reviewer-only <applicability> limitation"])
        self.dispatch = self.fixture.bind(self.fixture.prepare("release", code=[str(SCRIPTS / "qsar_release.py")]))

    def tearDown(self):
        self.fixture.tearDown()

    def package(self):
        return release.package_release(self.run, self.dispatch["dispatch_id"])

    def test_real_minimal_package_escaped_counts_metrics_and_lineage(self):
        result = self.package()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["review_required"])
        self.assertEqual(len(result["stages"]), len(gates.STAGES) - 1)
        out = Path(self.dispatch["owned_output_dir"])
        content = (out / "training_summary.html").read_text()
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
        self.assertEqual(self.rt.status()["stages"]["release"]["status"], "prepared")
        for artifact in result["artifacts"]:
            if artifact["relative_path"]:
                self.assertEqual(release.snapshot(out / artifact["relative_path"])["sha256"], artifact["source"]["sha256"])

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


if __name__ == "__main__":
    unittest.main()
