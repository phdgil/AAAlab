#!/usr/bin/env bash
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTOCOL_ROOT="$(cd "$SKILL_ROOT/../research-manuscript" && pwd)"
required=(
  "$SKILL_ROOT/SKILL.md"
  "$SKILL_ROOT/README.md"
  "$SKILL_ROOT/agents/openai.yaml"
  "$SKILL_ROOT/references/trigger-tests.md"
  "$SKILL_ROOT/references/agents/evidence-structure-editor.md"
  "$SKILL_ROOT/references/agents/methods-reproducibility-auditor.md"
  "$SKILL_ROOT/references/agents/abbreviation-citation-auditor.md"
  "$SKILL_ROOT/references/agents/figure-table-editor.md"
  "$SKILL_ROOT/references/agents/docx-format-auditor.md"
  "$SKILL_ROOT/references/agents/qa-reviewer.md"
  "$SKILL_ROOT/scripts/audit_docx.py"
  "$PROTOCOL_ROOT/SKILL.md"
  "$PROTOCOL_ROOT/references/manuscript-author-review-guidelines.md"
)
for file in "${required[@]}"; do
  [[ -f "$file" ]] || { echo "Missing required harness file: $file" >&2; exit 1; }
done
for needle in "name: research-manuscript-harness" "Phase 0: Context and authority" "Independent QA" "Test Scenarios"; do
  grep -Fq "$needle" "$SKILL_ROOT/SKILL.md" || { echo "SKILL.md missing required text: $needle" >&2; exit 1; }
done
echo "Research manuscript harness structure OK."
