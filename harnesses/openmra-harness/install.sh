#!/usr/bin/env bash
set -euo pipefail

AGENT_HOME="${1:-${AAALAB_AGENT_HOME:-${CODEX_HOME:-$HOME/.codex}}}"
PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$PACK_ROOT/skills/openmra-harness"
TARGET_SKILLS="$AGENT_HOME/skills"
TARGET="$TARGET_SKILLS/openmra-harness"

if [[ ! -d "$SOURCE" ]]; then
  echo "Missing source skill: $SOURCE" >&2
  exit 1
fi

mkdir -p "$TARGET_SKILLS"
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  echo "Install target already exists: $TARGET. Use 'aaalab install openmra-harness --agent-home <path>' for a managed update, or move the existing installation aside first." >&2
  exit 1
fi
cp -R "$SOURCE" "$TARGET_SKILLS/"

for notice in LICENSE NOTICE LICENSE_AUDIT.md THIRD_PARTY_NOTICES.md; do
  if [[ -f "$PACK_ROOT/$notice" ]]; then
    cp "$PACK_ROOT/$notice" "$TARGET/"
  fi
done

if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -File "$TARGET/scripts/validate_harness.ps1"
elif command -v powershell >/dev/null 2>&1; then
  powershell -NoProfile -ExecutionPolicy Bypass -File "$TARGET/scripts/validate_harness.ps1"
else
  echo "PowerShell was not found; skipped structure validation." >&2
fi

echo "Installed openmra-harness -> $TARGET"
echo "Restart your agent runtime so its skill registry reloads."
