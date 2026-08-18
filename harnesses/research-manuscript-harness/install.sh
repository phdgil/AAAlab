#!/usr/bin/env bash
set -euo pipefail

AGENT_HOME="${1:-${AAALAB_AGENT_HOME:-${CODEX_HOME:-$HOME/.codex}}}"
PACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SKILLS="$PACK_ROOT/skills"
TARGET_SKILLS="$AGENT_HOME/skills"
SKILLS=(research-manuscript research-manuscript-harness)

for skill in "${SKILLS[@]}"; do
  [[ -d "$SOURCE_SKILLS/$skill" ]] || { echo "Missing source skill: $SOURCE_SKILLS/$skill" >&2; exit 1; }
done

mkdir -p "$TARGET_SKILLS"
for skill in "${SKILLS[@]}"; do
  rm -rf "$TARGET_SKILLS/$skill"
  cp -R "$SOURCE_SKILLS/$skill" "$TARGET_SKILLS/"
  echo "Installed $skill -> $TARGET_SKILLS/$skill"
done

for notice in NOTICE LICENSE_AUDIT.md THIRD_PARTY_NOTICES.md; do
  [[ -f "$PACK_ROOT/$notice" ]] && cp "$PACK_ROOT/$notice" "$TARGET_SKILLS/research-manuscript-harness/"
done

"$TARGET_SKILLS/research-manuscript-harness/scripts/validate_harness.sh"
echo "Restart your agent runtime so its skill registry reloads."
