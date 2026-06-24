#!/usr/bin/env bash
set -euo pipefail

AGENT_HOME="${1:-${AAALAB_AGENT_HOME:-${CODEX_HOME:-$HOME/.codex}}}"
PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SKILLS="$PACK_ROOT/skills"
TARGET_SKILLS="$AGENT_HOME/skills"

for skill in qsar-autoresearch qsar-autoresearch-harness; do
  if [[ ! -d "$SOURCE_SKILLS/$skill" ]]; then
    echo "Missing source skill: $SOURCE_SKILLS/$skill" >&2
    exit 1
  fi
done

mkdir -p "$TARGET_SKILLS"

for skill in qsar-autoresearch qsar-autoresearch-harness; do
  rm -rf "$TARGET_SKILLS/$skill"
  cp -R "$SOURCE_SKILLS/$skill" "$TARGET_SKILLS/"
  echo "Installed $skill -> $TARGET_SKILLS/$skill"
done

for notice in LICENSE NOTICE LICENSE_AUDIT.md THIRD_PARTY_NOTICES.md; do
  if [[ -f "$PACK_ROOT/$notice" ]]; then
    cp "$PACK_ROOT/$notice" "$TARGET_SKILLS/qsar-autoresearch-harness/"
  fi
done

if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -File "$TARGET_SKILLS/qsar-autoresearch-harness/scripts/validate_harness.ps1"
elif command -v powershell >/dev/null 2>&1; then
  powershell -NoProfile -ExecutionPolicy Bypass -File "$TARGET_SKILLS/qsar-autoresearch-harness/scripts/validate_harness.ps1"
else
  echo "PowerShell was not found; skipped validation script."
fi

echo "Restart your agent runtime so its skill registry reloads."
