"""Validate/install AAAlab QSAR native roles without editing Codex configuration.

Requires Python 3.11+. Agent home is explicit; there is no implicit global install.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import tomllib
import uuid

PACK_ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "{{SKILLS_ROOT}}"
NAME = re.compile(r"qsar-[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class InstallError(ValueError):
    pass


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallError(f"Cannot read JSON {path}: {exc}") from exc


def _reject_link(path: Path):
    if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise InstallError(f"Refusing linked/reparse destination or source: {path}")


def _toml(text: str, path: Path):
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InstallError(f"Invalid TOML {path}: {exc}") from exc


def validate_definitions(source: Path, skills_root: Path):
    """Return rendered definitions after validating every role and stage mapping."""
    source, skills_root = Path(source).resolve(), Path(skills_root).resolve()
    manifest = _load_json(source / "stage-map.json")
    if manifest.get("schema_version") != 1 or manifest.get("skill_root_placeholder") != PLACEHOLDER:
        raise InstallError("Unsupported native role manifest")
    roles, stages = manifest.get("roles"), manifest.get("stages")
    if not isinstance(roles, dict) or not roles or not isinstance(stages, dict):
        raise InstallError("Native role manifest needs roles and stages")
    shared = skills_root / "qsar-autoresearch" / "SKILL.md"
    contract = skills_root / "qsar-autoresearch" / "references" / "artifact-contracts.md"
    for required in (shared, contract):
        if not required.is_file():
            raise InstallError(f"Missing shared skill reference: {required}")
    skill_map = _load_json(skills_root / "qsar-autoresearch-harness" / "references" / "agent-skills.json")
    expected = {item["id"]: item for item in skill_map["stages"]}
    if set(stages) != set(expected):
        raise InstallError("Native stage coverage does not match skill assignments")
    files = {p.name for p in source.glob("*.toml")}
    if files != {f"{name}.toml" for name in roles}:
        raise InstallError("Native role TOML inventory does not match manifest")
    rendered = {}
    for name, role in roles.items():
        if not isinstance(name, str) or not isinstance(role, dict) or not NAME.fullmatch(name) or role.get("file") != f"{name}.toml":
            raise InstallError(f"Invalid native role name/file: {name}")
        skill = role.get("skill")
        if not isinstance(skill, str) or not re.fullmatch(r"qsar-[a-z0-9-]+", skill):
            raise InstallError(f"Invalid skill name: {name}")
        skill_file = skills_root / skill / "SKILL.md"
        if not skill_file.is_file():
            raise InstallError(f"Missing assigned skill: {skill_file}")
        source_file = source / role["file"]
        _reject_link(source_file)
        raw = source_file.read_text(encoding="utf-8")
        parsed = _toml(raw, source_file)
        if set(parsed) != {"name", "description", "model_reasoning_effort", "developer_instructions"}:
            raise InstallError(f"Unexpected native configuration keys (model/permission overrides forbidden): {name}")
        if parsed["name"] != name or not isinstance(parsed["description"], str) or not parsed["description"].strip():
            raise InstallError(f"Native identity does not match manifest: {name}")
        if parsed["model_reasoning_effort"] not in {"medium", "high"}:
            raise InstallError(f"Unsupported reasoning effort: {name}")
        instructions = parsed["developer_instructions"]
        for ref in (f"{PLACEHOLDER}/{skill}/SKILL.md", f"{PLACEHOLDER}/qsar-autoresearch/SKILL.md", f"{PLACEHOLDER}/qsar-autoresearch/references/artifact-contracts.md"):
            if ref not in instructions:
                raise InstallError(f"Missing explicit skill read {ref}: {name}")
        kind = role.get("kind")
        role_stages = role.get("stages")
        if kind not in {"producer", "reviewer"} or not isinstance(role_stages, list) or not role_stages or len(role_stages) != len(set(role_stages)):
            raise InstallError(f"Invalid role stage ownership: {name}")
        for stage in role_stages:
            if stage not in stages or stages[stage].get(kind) != name or expected[stage][kind]["skill"] != skill:
                raise InstallError(f"Role-to-skill/stage mismatch: {name}/{stage}")
        output = raw.replace(PLACEHOLDER, skills_root.as_posix())
        _toml(output, source_file)
        rendered[name] = output.encode("utf-8")
    for stage, owners in stages.items():
        if not isinstance(owners, dict) or set(owners) != {"producer", "reviewer"} or owners["producer"] == owners["reviewer"]:
            raise InstallError(f"Separate producer/reviewer roles required: {stage}")
        for kind, name in owners.items():
            if name not in roles or roles[name]["kind"] != kind or stage not in roles[name]["stages"]:
                raise InstallError(f"Invalid role ownership: {stage}/{kind}")
            if expected[stage][kind].get("native_agent_type") != name:
                raise InstallError(f"Native agent assignment mismatch: {stage}/{kind}")
    ownership_index = {
        name: {key: role[key] for key in ("skill", "kind", "stages")}
        for name, role in roles.items()
    }
    if skill_map.get("native_roles") != ownership_index:
        raise InstallError("Native role ownership index does not match native manifest")
    return rendered, manifest


def _write_atomic(path: Path, content: bytes):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".qsar-install-", delete=False) as handle:
            temp = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def install(agent_home: Path, source: Path, skills_root: Path, replace: bool = False):
    definitions, manifest = validate_definitions(source, skills_root)
    agent_home = Path(agent_home).absolute()
    _reject_link(agent_home)
    agents = agent_home / "agents"
    _reject_link(agents)
    agents = agents.resolve()
    previous = {}
    conflicts = []
    for name, content in definitions.items():
        destination = agents / f"{name}.toml"
        _reject_link(destination)
        if destination.exists() and not destination.is_file():
            raise InstallError(f"Native destination is not a file: {destination}")
        old = destination.read_bytes() if destination.exists() else None
        previous[name] = old
        if old is not None and old != content:
            conflicts.append(name)
    if conflicts and not replace:
        raise InstallError("Conflicting native roles; nothing written. Use --replace to preserve backups and replace: " + ", ".join(conflicts))
    backup = None
    if conflicts:
        backup_root = agents / ".aaalab-qsar-backups"
        _reject_link(backup_root)
        backup = backup_root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        for name in conflicts:
            (backup / f"{name}.toml").write_bytes(previous[name])
        (backup / "manifest.json").write_text(json.dumps({name: hashlib.sha256(previous[name]).hexdigest() for name in conflicts}, indent=2), encoding="utf-8")
    agents.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for name, content in definitions.items():
            if previous[name] != content:
                _write_atomic(agents / f"{name}.toml", content)
                written.append(name)
    except Exception:
        for name in reversed(written):
            destination = agents / f"{name}.toml"
            if previous[name] is None:
                destination.unlink()
            else:
                _write_atomic(destination, previous[name])
        raise
    return {
        "agents_directory": str(agents),
        "installed": written,
        "unchanged": [name for name in definitions if name not in written],
        "backup_directory": str(backup) if backup else None,
        "skills_root": str(Path(skills_root).resolve()),
        "stage_assignments": manifest["stages"],
        "model_policy": "inherit runtime/caller model; definitions set reasoning effort only",
        "runtime_status": "files_installed_not_runtime_verified",
        "next_step": "Reload the native runtime and inspect its actual agent catalog before dispatching these role names. Use fork_turns=none on supported surfaces."
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-home", type=Path, help="Explicit Codex home, e.g. project/.codex; never inferred")
    parser.add_argument("--skills-root", type=Path, help="Existing installed sibling skills; defaults to agent-home/skills")
    parser.add_argument("--source", type=Path, default=PACK_ROOT / "native-agents")
    parser.add_argument("--replace", action="store_true", help="Back up and replace only conflicting QSAR native role files")
    parser.add_argument("--check", action="store_true", help="Validate definitions and skill links without writing")
    args = parser.parse_args(argv)
    if args.agent_home is None and not args.check:
        parser.error("--agent-home is required for installation")
    skills_root = args.skills_root or (args.agent_home / "skills" if args.agent_home else PACK_ROOT / "skills")
    try:
        if args.check:
            definitions, manifest = validate_definitions(args.source, skills_root)
            result = {"status": "valid", "roles": list(definitions), "stages": list(manifest["stages"]), "writes": False}
        else:
            result = install(args.agent_home, args.source, skills_root, args.replace)
        print(json.dumps(result, indent=2))
        return 0
    except (InstallError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
