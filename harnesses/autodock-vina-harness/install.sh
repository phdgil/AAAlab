#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${1:-${CODEX_HOME:-$HOME/.codex}}"
PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SKILLS="$PACK_ROOT/skills"
TARGET_SKILLS="$CODEX_HOME/skills"

for skill in autodock-vina autodock-vina-harness; do
  if [[ ! -d "$SOURCE_SKILLS/$skill" ]]; then
    echo "Missing source skill: $SOURCE_SKILLS/$skill" >&2
    exit 1
  fi
done

mkdir -p "$TARGET_SKILLS"

for skill in autodock-vina autodock-vina-harness; do
  rm -rf "$TARGET_SKILLS/$skill"
  cp -R "$SOURCE_SKILLS/$skill" "$TARGET_SKILLS/"
  echo "Installed $skill -> $TARGET_SKILLS/$skill"
done

if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -File "$TARGET_SKILLS/autodock-vina-harness/scripts/validate_harness.ps1"
elif command -v powershell >/dev/null 2>&1; then
  powershell -NoProfile -ExecutionPolicy Bypass -File "$TARGET_SKILLS/autodock-vina-harness/scripts/validate_harness.ps1"
else
  echo "PowerShell was not found; skipped validation script."
fi

echo "Restart Codex so the skill registry reloads."
