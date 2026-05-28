---
name: autodock-vina-harness
description: "Coordinate a multi-agent AutoDock Vina and gnina docking harness for real docking execution, batch docking, receptor and ligand preparation, binding-box validation, literature or user-supplied validation gates, pose checks, gnina rescoring, troubleshooting, reruns, updates, partial reruns, and improvement of previous docking results. Use this skill first for end-to-end docking tasks; load autodock-vina as the protocol source."
metadata:
  protocol_skill: "autodock-vina"
  protocol_relative_path: "../autodock-vina/SKILL.md"
---

# AutoDock Vina Harness

This harness coordinates specialist agents around the `autodock-vina` protocol. The goal is to reduce docking failure modes by separating structure choice, validation design, molecular preparation, execution, and independent QA.

Use this harness when the user asks for an actual docking run, batch docking, gnina follow-up, troubleshooting, rerun, validation improvement, or previous-result update. For a simple conceptual question about Vina, answer directly or use only `autodock-vina`.

## Execution Mode

Use Codex multi-agent fan-out/fan-in with producer-reviewer gates.

This is an instruction harness, not a declarative team manifest. The orchestrator manually calls the available Codex multi-agent tools, such as `multi_agent_v1.spawn_agent`, with the role files below as prompt inputs.

- Spawn independent agents only when the current task needs real execution, validation, or audit. Keep simple questions local.
- Use `explorer` for read-only research or file inspection.
- Use `worker` for file-producing preparation, docking execution, parsing, visualization, and QA.
- Give every spawned agent the relevant role file from `references/agents/` plus the protocol skill path.
- Keep the main agent as orchestrator. The main agent owns final decisions, user communication, and the final report.

If a future runtime provides true team primitives, prefer the same roster as an agent team with shared task tracking. Until then, use parallel sub-agents and file-based handoff.

## Protocol Source

Before any run, load:

1. The sibling skill `$autodock-vina`, normally installed at `../autodock-vina/SKILL.md` relative to this harness skill.
2. `../autodock-vina/references/vina-draft-protocol.md` when preparing commands, selecting structures, validating gates, or writing the report.

Do not duplicate or override the protocol source casually. If the harness exposes a repeated failure pattern, update `autodock-vina` itself so the protocol improves.

## Agent Roster

| Role file | Agent type | Purpose | Primary outputs |
|---|---|---|---|
| `references/agents/structure-box-lead.md` | `explorer` or `worker` | Select structure, define chains, cofactors, pocket basis, and box sanity checks | `structure_selection.md`, box rationale |
| `references/agents/validation-lead.md` | `explorer` | Find literature or user-supplied anchor gates before novel interpretation | `validation_criteria.md` or `validation_gap.md` |
| `references/agents/prep-lead.md` | `worker` | Prepare receptor and ligand files, record retention and protonation decisions | `preflight.md`, receptor/ligand prep artifacts |
| `references/agents/docking-runner.md` | `worker` | Run Vina and optional gnina, parse logs, build score tables and viewers | `scores.csv`, `pose_check.html`, gnina outputs |
| `references/agents/qa-reviewer.md` | `worker` | Cross-check outputs, parser fields, gate logic, red flags, and report claims | `qa_verdict.md`, required fixes |

Use fewer agents for smaller jobs, but keep QA separate from the producer when results will be interpreted biologically.

## Workspace Layout

Create one run directory unless the user gives one:

```text
vina_run_<target>_<ligand_or_batch>/
  _workspace/
    00_input/
    01_structure/
    02_validation/
    03_prep/
    04_docking/
    05_qa/
  input/
  validation/
  docking/
  gnina/
  report.md
  structure_selection.md
```

Intermediate artifacts stay in `_workspace/` for audit. Final user-facing artifacts are copied or written to the top-level domain folders from the `autodock-vina` output template.

## Workflow

### Phase 0: Context Check

Classify the request before touching inputs.

- Initial run: no `_workspace/` exists. Create a fresh run directory.
- Partial rerun: `_workspace/` exists and the user asks to fix a specific stage. Reuse previous artifacts outside that stage and record what changed.
- New run from previous result: `_workspace/` exists and the user provides a new target, ligand, receptor, or validation objective. Archive the previous `_workspace/` as `_workspace_prev_<YYYYMMDD_HHMMSS>/`.
- Simple explanation: no harness run. Use `autodock-vina` directly and answer.

### Phase 1: Blocking Preflight

Do locally before delegation because every later step depends on it.

1. Verify `vina -h` works.
2. Verify at least one PDBQT prep path exists: Meeko, MGLTools/AutoDockTools, or Open Babel.
3. Check RDKit or another conformer path when ligand generation is required.
4. If gnina is requested, check Docker or native gnina. For GPU use, require a real one-ligand CNN-scoring smoke test, not only `nvidia-smi`.
5. Write `preflight.md`.

If Vina or all PDBQT preparation paths are missing, stop and report missing dependencies. Do not improvise a fake docking pipeline.

### Phase 2: Parallel Planning

Spawn independent agents in parallel when the task is non-trivial.

- Structure-box lead: choose or audit the receptor structure, chain, retained components, original ligand, and box basis.
- Validation lead: search for anchor evidence or document why no defensible gate exists.
- Prep lead: inspect input ligand/receptor availability and propose exact prep commands.

Each agent writes a short artifact under `_workspace/` and lists hard stops.

### Phase 3: Gate Before Docking

The main agent reads Phase 2 outputs and decides whether docking can proceed.

Hard stops:

- No working Vina executable.
- No receptor or ligand PDBQT preparation path.
- Box center is not tied to a co-crystallized ligand, known site coordinates, or explicit user-provided pocket.
- Species, mutant, allele, or construct identity is unresolved for comparative docking.
- Required user-specified validation gates are impossible with available inputs.

Allowed proceed states:

- Validation gate exists: proceed with anchor docking before novel ligands.
- No defensible gate exists: proceed only as exploratory triage and write `validation_gap.md`.
- User explicitly requests troubleshooting: proceed with a minimal diagnostic run and label it as diagnostic.

### Phase 4: Preparation and Execution

Run preparation and docking through worker agents or locally when the work is sequential.

Required behavior:

- Save exact commands, configs, logs, receptor PDBQT, ligand PDBQT, poses, and scores.
- Use deterministic seeds for comparative studies.
- Keep anchor outputs separate from novel-ligand outputs.
- Build `pose_check.html` before interpreting scores.
- Build `interaction_analysis.html` for final selected poses.
- If gnina is used, parse Vina affinity, intramolecular term, CNN pose score, and CNN affinity as distinct fields.

### Phase 5: QA Review

Run QA after artifacts exist and before the final report.

The QA reviewer must cross-check:

- Required output files exist and are non-empty.
- `vina_config.txt` box matches the selected pocket basis.
- Known anchors pass or fail according to predeclared gates.
- Novel-ligand claims are blocked when anchors fail.
- gnina parser columns are correct.
- HTML views show receptor, original ligand when available, docked pose, and pocket context.
- The report separates validated evidence, exploratory ranking, and failure analysis.

If QA finds a blocking issue, fix it and rerun only the affected stage.

### Phase 6: Final Verdict

Write `report.md` with:

- Structure and box rationale.
- Receptor and ligand preparation decisions.
- Validation criteria or validation gap.
- Anchor verdict.
- Score table summary.
- Pose and interaction analysis.
- gnina concordance or divergence if used.
- Limitations and whether biological interpretation is allowed.

Never present a Vina or gnina score as experimental affinity. If the pipeline fails available anchor gates, the correct result is failure analysis and revised protocol.

## Data Handoff

Use files for handoff:

- `_workspace/01_structure/structure_box_notes.md`
- `_workspace/02_validation/validation_notes.md`
- `_workspace/03_prep/prep_manifest.md`
- `_workspace/04_docking/run_manifest.md`
- `_workspace/05_qa/qa_verdict.md`

For spawned agents, include:

- The user objective.
- The run directory.
- The relevant role file.
- The protocol source skill or sibling protocol path.
- The exact output path they own.
- A warning not to modify other agents' files unless explicitly asked.

## Error Handling

- Retry a failed command once only after identifying a concrete fix.
- If an agent fails, continue with available artifacts only when the missing role is non-blocking; document the omission.
- Preserve conflicting evidence with sources. Do not delete inconvenient anchor failures.
- If more than half the required roles fail, stop and ask for user input or dependency fixes.
- Treat wrong-pocket poses, failed anchor gates, and malformed gnina parsing as blocking defects.

## Trigger Tests

Should trigger:

- "Run Vina docking for this receptor and ligand and validate the pose."
- "Redo only the gnina rescoring from the previous docking result."
- "Batch dock these ligands and make sure known anchors pass first."
- "Troubleshoot why my Vina pose is outside the binding pocket."
- "Update the previous docking report with a stricter validation gate."
- "Use multi-agent docking harness for AHR ligands."

Should not trigger:

- "What is AutoDock Vina?"
- "Explain what exhaustiveness means."
- "Summarize a docking paper without running anything."
- "Convert this unrelated CSV to Excel."
- "Make a protein cartoon image with no docking task."

## Test Scenarios

Normal flow:

1. User provides a target protein and ligand set.
2. Preflight finds Vina and a prep path.
3. Structure-box and validation agents produce a pocket basis and anchor gates.
4. Prep and runner produce PDBQT files, Vina outputs, score tables, and pose views.
5. QA passes after checking anchors and report claims.

Error flow:

1. User asks for full docking but Vina is unavailable.
2. Preflight writes `preflight.md` with missing dependencies.
3. Harness stops before receptor or ligand preparation.
4. Final response reports the missing executable and next install step.

Partial rerun flow:

1. User asks to rerun only gnina after a completed Vina run.
2. Harness reads previous `_workspace/` and Vina artifacts.
3. Only docking-runner and QA roles are invoked.
4. Report updates gnina tables and engine concordance without changing receptor prep.
