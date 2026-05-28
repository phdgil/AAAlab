---
name: autodock-vina
description: "Protocol source for preparing, running, validating, and analyzing protein-ligand docking with AutoDock Vina, including gnina follow-up rescoring/redocking. For real end-to-end docking execution, batch docking, reruns, troubleshooting, validation gates, or multi-agent QA, use autodock-vina-harness first and load this skill as the protocol source."
metadata:
  homepage: "https://vina.scripps.edu/"
---

# AutoDock Vina

Use this skill as the protocol source when the user asks about AutoDock Vina, Vina-style docking, gnina/CNN rescoring, PDBQT preparation, binding-box setup, species/variant docking considerations, or docking-pose validation. For actual execution, reruns, troubleshooting, validation-gate design, or final QA, route through `$autodock-vina-harness`.

## Harness Routing

For real docking execution, batch runs, reruns, troubleshooting, validation-gate design, gnina comparison, or final QA, use `$autodock-vina-harness` first. This skill remains the protocol source that the harness loads and enforces.

This is a conservative draft workflow. Prefer the user's step-by-step protocol when provided; update this skill instead of improvising.

## Workflow

0. Run a preflight environment check.
   - Verify `vina -h` works before any docking run.
   - Verify at least one receptor/ligand preparation path is available: Meeko, AutoDockTools/MGLTools, or Open Babel.
   - If `vina` or PDBQT preparation tools are missing, stop and report the missing tools instead of improvising with incomplete docking.
1. Select a protein structure.
   - Search available PDB structures for the target.
   - Prefer structures co-crystallized with a ligand in the relevant binding pocket.
   - If multiple ligand-bound structures exist, prioritize higher resolution.
   - Always report the PDB ID, resolution, co-crystallized ligand ID/name, chain, and why that structure was selected.
2. Prepare the receptor.
   - Keep the biologically relevant chain(s), binding-site residues, cofactors, and waters only when justified.
   - Remove unrelated ligands and crystallographic artifacts.
   - Prepare receptor PDBQT with explicit hydrogens and appropriate charges.
   - Record every deletion/retention decision.
3. Prepare the ligand.
   - Use 3D ligand structures, never flat 2D structures for final docking.
   - Generate or load 3D conformers, assign protonation/tautomer state, and perform geometry optimization when needed.
   - Prepare ligand PDBQT and keep the original SDF/MOL/SDF-with-3D copy for visualization.
4. Define and verify the docking box.
   - Center the box on the co-crystallized ligand or validated binding-pocket coordinates.
   - Check that the original ligand fits inside the box before docking new compounds.
   - Do not dock if the box is outside the known binding pocket.
5. Define validation evidence before interpreting novel ligands.
   - First check research articles and structural databases for usable validation anchors: experimental direction, affinity, pose, mutation/residue evidence, or co-crystal redocking targets.
   - Accept user-supplied validation criteria when literature anchors are unavailable or the user has project-specific assay knowledge.
   - If anchors exist, define hard/soft gates before the final batch: expected direction, tolerated magnitude when defensible, redock RMSD, contact residues, no surface-pocket drift, and receptor-structure sanity checks.
   - If no defensible gate can be built from literature or user input, write `validation_gap.md` and downgrade interpretation to exploratory/triage, not validated biological evidence.
   - Do not use Vina scores from novel compounds as biological evidence if the same pipeline fails required anchor gates that were available or user-specified.
6. Run Vina.
   - Save command, config, logs, receptor PDBQT, ligand PDBQT, output poses, and scores.
   - For batches, use one output folder per ligand and keep a manifest.
   - Use deterministic seeds for comparative studies.
7. Validate the docking region and known-anchor behavior.
   - Create an HTML viewer showing receptor, original co-crystallized ligand, docking box, and docked pose.
   - Explicitly ask the user to confirm the pose is in the intended binding pocket when a new target or box is used.
   - Redock co-crystallized ligands when available; flag RMSD/centroid drift.
   - Compare anchor-ligand directions against literature or user-supplied criteria before proceeding to novel test compounds when such criteria exist.
8. Run gnina follow-up when Vina validation is weak or a CNN orthogonal score is requested.
   - Use gnina as a follow-up engine, not a replacement for validation.
   - Capture Vina affinity, intramolecular term, CNN pose score, and CNN affinity separately; never parse the CNN pose score as CNN affinity.
   - Compare at least three pose-selection conventions when results matter: gnina default top-1 by CNN pose score, max CNN affinity across modes, and CNN affinity of the best Vina-score pose.
   - Treat a gnina gate pass as fragile if it fails under the alternative pose-selection conventions.
   - On new NVIDIA GPUs, include a real CNN-scoring smoke test; `nvidia-smi` inside Docker only proves GPU visibility, not PyTorch kernel compatibility.
9. Analyze interactions.
   - Identify residues interacting with the docked ligand.
   - Create an HTML interaction view with dotted lines between interacting atoms and labels for key residues.
   - Summarize hydrogen bonds, salt bridges, pi interactions, hydrophobic contacts, clashes, and pose caveats.
10. Write the result verdict.
   - Separate "pipeline passed anchor validation", "no validation gate available", and "novel-ligand prediction".
   - Report engine concordance/divergence explicitly.
   - If known anchors fail, the correct output is a failure analysis and revised protocol, not a confident biological claim.

## Required Outputs

- `preflight.md`: Vina version/path, receptor-prep tool, ligand-prep tool, and any missing dependency.
- `structure_selection.md`: PDB ID, resolution, co-crystallized ligand, chain, selection rationale.
- `vina_config.txt`: grid center, grid size, exhaustiveness, seed if used.
- `docking_command.txt`: exact Vina command(s).
- `scores.csv`: ligand, pose rank, affinity, RMSD lower/upper bound if available.
- `validation_criteria.md`: literature-derived or user-supplied anchor compounds, expected directions/poses, hard/soft gate rules, and stop/proceed policy when available.
- `validation_gap.md` when no gate is feasible: literature/user-input search summary, why no defensible anchors were available, and exploratory-use caveat.
- `anchor_scores.csv`: anchor-ligand results separated from novel-ligand results.
- `known_anchor_verdict.md`: pass/fail table and whether interpretation may proceed.
- `pose_check.html`: receptor plus original ligand plus docked pose for binding-box validation.
- `interaction_analysis.html`: pose interactions with dotted interaction lines.
- `gnina_scores.csv` when gnina is used: ligand, receptor, CNN model, Vina affinity, CNN pose score, CNN affinity, selected pose rule, and runtime status.
- `gnina_gate_summary.csv` when gnina is used: gate pass/fail per CNN model and per pose-selection convention.
- `engine_concordance.md`: agreement/disagreement between Vina, gnina, literature anchors, and any structural sanity checks.
- `report.md`: concise summary, selected pose, key residues, and limitations.

## Red Flags

- Protein structure has no co-crystallized ligand and no validated binding-site coordinates.
- Docked pose is outside or only partially inside the known binding pocket.
- Docking box is defined from the full protein centroid instead of the ligand pocket.
- Ligand was docked from a 2D-only structure without 3D conformer generation.
- Protonation, tautomer, metal coordination, covalent ligand behavior, cofactors, or critical waters are ignored.
- Vina score is treated as experimental binding affinity.
- Novel-ligand claims are made after known anchor ligands fail the literature-derived or user-supplied hard gate.
- A validation gate is invented without literature support, structural support, assay data, or explicit user-supplied criteria.
- A species, mutant, or allele comparison is run without confirming the exact modeled sequence/residue identities.
- A collapsed, apo, or non-holo pocket is used for bulky ligands without induced-fit, ensemble, or MD-relaxed receptor justification.
- gnina output is parsed as three numeric columns under `--cnn_scoring=rescore`; the table has four numeric columns and column 4 is CNN affinity.
- gnina "passes" only by top-1 CNN pose score but fails when selecting by max CNN affinity or by best Vina-score pose.
- Docker GPU preflight checks only `nvidia-smi`; a one-ligand CNN-scoring smoke test is required.
- A flexible-residue docking box is too small to contain the flex side chains and ligand poses.

## References

Load [references/vina-draft-protocol.md](references/vina-draft-protocol.md) when preparing commands, selecting structures, or writing the output report.
