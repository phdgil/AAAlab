#!/usr/bin/env bash
set -euo pipefail

HARNESS="${1:-*}"
CODEX_HOME="${2:-${CODEX_HOME:-$HOME/.codex}}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$REPO_ROOT/harnesses"

if [[ ! -d "$HARNESS_ROOT" ]]; then
  echo "Missing harnesses directory: $HARNESS_ROOT" >&2
  exit 1
fi

matched=0
for harness_dir in "$HARNESS_ROOT"/*; do
  [[ -d "$harness_dir" ]] || continue
  harness_name="$(basename "$harness_dir")"
  case "$harness_name" in
    $HARNESS)
      matched=1
      if [[ -x "$harness_dir/install.sh" || -f "$harness_dir/install.sh" ]]; then
        echo "Installing harness: $harness_name"
        bash "$harness_dir/install.sh" "$CODEX_HOME"
      else
        echo "Skipping $harness_name: no install.sh found." >&2
      fi
      ;;
  esac
done

if [[ "$matched" -eq 0 ]]; then
  echo "No harness matched: $HARNESS" >&2
  exit 1
fi

echo "Restart Codex so the skill registry reloads."
