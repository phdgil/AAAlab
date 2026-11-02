"""Isolated native role validation and installation regression tests."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "harnesses" / "qsar-autoresearch-harness"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("qsar_native_install", PACK / "scripts" / "install_native_agents.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class NativeAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aaalab-qsar-native-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "native-agents"
        shutil.copytree(PACK / "native-agents", self.source)
        self.skills = PACK / "skills"
        self.home = self.root / "project" / ".codex"

    def tearDown(self):
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("aaalab-qsar-native-"))
        self.temp.cleanup()

    def install(self, replace=False):
        return installer.install(self.home, self.source, self.skills, replace)

    def test_all_roles_and_stage_skill_links(self):
        roles, manifest = installer.validate_definitions(self.source, self.skills)
        self.assertEqual(len(roles), 12)
        self.assertEqual(len(manifest["stages"]), 10)
        for name, raw in roles.items():
            data = tomllib.loads(raw.decode())
            self.assertEqual(data["name"], name)
            self.assertNotIn("model", data)
            self.assertNotIn("sandbox_mode", data)
            self.assertNotIn("{{SKILLS_ROOT}}", data["developer_instructions"])
            self.assertIn(self.skills.as_posix(), data["developer_instructions"])
        for owners in manifest["stages"].values():
            self.assertNotEqual(owners["producer"], owners["reviewer"])

    def test_project_install_preserves_unrelated_and_is_idempotent(self):
        agents = self.home / "agents"
        agents.mkdir(parents=True)
        for name in ["executor.toml", "course-producer.toml", "user-custom.toml"]:
            (agents / name).write_bytes(b"preserve unrelated bytes")
        config = self.home / "config.toml"
        config.write_bytes(b'model = "user-selected"\n')
        result = self.install()
        self.assertEqual(len(result["installed"]), 12)
        self.assertEqual(result["runtime_status"], "files_installed_not_runtime_verified")
        for name in ["executor.toml", "course-producer.toml", "user-custom.toml"]:
            self.assertEqual((agents / name).read_bytes(), b"preserve unrelated bytes")
        self.assertEqual(config.read_bytes(), b'model = "user-selected"\n')
        second = self.install()
        self.assertEqual(second["installed"], [])
        self.assertEqual(len(second["unchanged"]), 12)

    def test_conflict_refuses_all_changes_without_replace(self):
        agents = self.home / "agents"
        agents.mkdir(parents=True)
        conflict = agents / "qsar-trainer.toml"
        conflict.write_bytes(b"existing custom role")
        with self.assertRaisesRegex(installer.InstallError, "Conflicting native roles"):
            self.install()
        self.assertEqual(list(agents.iterdir()), [conflict])
        self.assertEqual(conflict.read_bytes(), b"existing custom role")

    def test_explicit_replacement_backs_up_original(self):
        self.install()
        conflict = self.home / "agents" / "qsar-trainer.toml"
        conflict.write_bytes(b"user edited trainer")
        result = self.install(replace=True)
        backup = Path(result["backup_directory"])
        self.assertEqual((backup / conflict.name).read_bytes(), b"user edited trainer")
        self.assertEqual(result["installed"], ["qsar-trainer"])
        self.assertEqual(tomllib.loads(conflict.read_text(encoding="utf-8"))["name"], "qsar-trainer")

    def test_invalid_toml_prevents_any_install(self):
        (self.source / "qsar-trainer.toml").write_text("not [valid", encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "Invalid TOML"):
            self.install()
        self.assertFalse(self.home.exists())

    def test_pinned_model_is_rejected(self):
        p = self.source / "qsar-trainer.toml"
        p.write_text('model = "unexpected"\n' + p.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "overrides forbidden"):
            self.install()
        self.assertFalse(self.home.exists())

    def test_wrong_stage_skill_is_rejected(self):
        p = self.source / "stage-map.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["roles"]["qsar-trainer"]["skill"] = "qsar-curate"
        p.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "Missing explicit skill read|stage mismatch"):
            self.install()
        self.assertFalse(self.home.exists())

    def test_incomplete_stage_inventory_is_rejected(self):
        p = self.source / "stage-map.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        del data["stages"]["collection"]
        p.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(installer.InstallError, "stage coverage"):
            self.install()
        self.assertFalse(self.home.exists())

    def test_mismatched_dispatch_native_role_is_rejected_without_writes(self):
        self.skills = self.root / "skills"
        shutil.copytree(PACK / "skills", self.skills)
        assignment = self.skills / "qsar-autoresearch-harness" / "references" / "agent-skills.json"
        original = json.loads(assignment.read_text(encoding="utf-8"))
        for kind, wrong_role in (("producer", "qsar-data-reviewer"), ("reviewer", "qsar-trainer")):
            with self.subTest(kind=kind):
                data = json.loads(json.dumps(original))
                training = next(stage for stage in data["stages"] if stage["id"] == "training")
                training[kind]["native_agent_type"] = wrong_role
                assignment.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(installer.InstallError, f"Native agent assignment mismatch: training/{kind}"):
                    self.install()
                self.assertFalse(self.home.exists())

    def test_missing_skill_reference_is_rejected(self):
        with self.assertRaisesRegex(installer.InstallError, "Missing shared skill reference"):
            installer.install(self.home, self.source, self.root / "missing-skills")
        self.assertFalse(self.home.exists())

    def test_native_ownership_index_drift_is_rejected_without_writes(self):
        self.skills = self.root / "skills"
        shutil.copytree(PACK / "skills", self.skills)
        assignment = self.skills / "qsar-autoresearch-harness" / "references" / "agent-skills.json"
        original = json.loads(assignment.read_text(encoding="utf-8"))
        for field, wrong_value in (("skill", "qsar-curate"), ("kind", "reviewer"), ("stages", ["curation"])):
            with self.subTest(field=field):
                data = json.loads(json.dumps(original))
                data["native_roles"]["qsar-trainer"][field] = wrong_value
                assignment.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(installer.InstallError, "ownership index does not match"):
                    self.install()
                self.assertFalse(self.home.exists())

    def test_linked_destination_is_rejected(self):
        agents = self.home / "agents"
        agents.mkdir(parents=True)
        outside = self.root / "unrelated.toml"
        outside.write_bytes(b"keep")
        try:
            (agents / "qsar-trainer.toml").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation unavailable on this test host")
        with self.assertRaisesRegex(installer.InstallError, "linked/reparse"):
            self.install(replace=True)
        self.assertEqual(outside.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
