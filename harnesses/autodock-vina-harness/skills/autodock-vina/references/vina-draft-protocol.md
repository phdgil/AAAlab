# AutoDock Vina Draft Protocol

This reference captures the initial required workflow. It should be refined after the user provides a step-by-step protocol.

## 1. Protein Structure Selection

Goal: choose a structure that supports reliable pocket definition.

Required checks:

- Query PDB structures for the target protein.
- Prefer structures co-crystallized with a ligand in the relevant binding pocket.
- If multiple ligand-bound structures are available, prioritize higher resolution.
- Consider biological relevance: species, construct, mutation status, chain completeness, cofactors, metal ions, missing residues near pocket, and ligand similarity to the intended compound series.

Must report:

- PDB ID.
- Experimental method.
- Resolution in Angstroms when available.
- Co-crystallized ligand ID/name.
- Chain used for docking.
- Any retained cofactors, metals, catalytic waters, or removed molecules.
- Rationale for structure choice.

Recommended `structure_selection.md` shape:

```markdown
# Structure Selection

- Target:
- Selected PDB:
- Method:
- Resolution:
- Co-crystallized ligand:
- Chain(s):
- Binding pocket basis:
- Retained cofactors/metals/waters:
- Removed molecules/chains:
- Rationale:
- Caveats:
```

## 2. Receptor Preparation

Typical steps:

1. Download selected PDB/mmCIF.
2. Keep target chain(s) and relevant pocket components.
3. Remove unrelated ligands, ions, waters, and alternate chains unless justified.
4. Add hydrogens.
5. Assign charges compatible with Vina/PDBQT preparation.
6. Export receptor PDBQT.

Possible tools depend on local environment:

- Meeko / `mk_prepare_receptor.py`
- AutoDockTools / MGLTools
- Open Babel
- RDKit for some molecule handling
- PDBFixer or similar tools for missing atoms/residues when appropriate

Record all decisions. Do not silently delete cofactors/metals if they are part of binding.

## 3. Ligand Preparation

Rules:

- Use 3D structures for docking.
- If input is SMILES, generate 3D conformers first.
- Assign plausible protonation/tautomer state for assay pH when known.
- Perform geometry optimization if needed to remove unstable geometry.
- Export both:
  - visualization ligand file, such as SDF with 3D coordinates
  - docking ligand file, PDBQT

Possible tools:

- RDKit / Datamol for standardization, conformers, and MMFF/UFF optimization.
- Open Babel for format conversion.
- Meeko / AutoDockTools for PDBQT generation.

Minimum ligand-prep report:

- input source
- selected protonation/tautomer assumption
- 3D conformer generation method
- geometry optimization method
- output files

## 4. Binding Box Definition

Default: derive grid center from the co-crystallized ligand coordinates.

Required validation:

- Original co-crystallized ligand must be visible inside the docking box.
- Docked ligand must be shown against original ligand and receptor before trusting the score.
- If the compound docks outside the known pocket, stop and fix the box or receptor/ligand preparation.

Recommended `vina_config.txt`:

```text
receptor = receptor.pdbqt
ligand = ligand.pdbqt

center_x = <from original ligand centroid or known site>
center_y = <from original ligand centroid or known site>
center_z = <from original ligand centroid or known site>

size_x = <pocket size plus margin>
size_y = <pocket size plus margin>
size_z = <pocket size plus margin>

exhaustiveness = 16
num_modes = 20
energy_range = 4
```

## 5. Run Vina

Example command:

```powershell
vina --config vina_config.txt --out docked_poses.pdbqt --log vina.log
```

Alternative explicit command:

```powershell
vina `
  --receptor receptor.pdbqt `
  --ligand ligand.pdbqt `
  --center_x <x> --center_y <y> --center_z <z> `
  --size_x <sx> --size_y <sy> --size_z <sz> `
  --exhaustiveness 16 `
  --num_modes 20 `
  --out docked_poses.pdbqt `
  --log vina.log
```

Keep:

- receptor input PDB/PDBQT
- ligand input SDF/PDBQT
- Vina config
- Vina log
- docked PDBQT output
- converted docked SDF/PDB for visualization

## 6. Validation Evidence Before Novel Claims

Do not treat docking as a validated insight generator until validation evidence has been checked. A validation gate is not always feasible. In a previous AHR organoid project, it was feasible because research articles provided literature-known TCDD, BaP, and indirubin species-direction anchors. Vina produced internally consistent numbers, but the original protocol inverted the literature-known TCDD species direction. That made the novel-compound scores unsuitable for biological interpretation until validation was redesigned.

Validation evidence can come from two places:

- Research articles, structural databases, and assay literature. Prefer anchors with experimental affinity/direction, co-crystal redocking, mutation/polymorphism evidence, or strong literature consensus.
- User-supplied criteria. Accept them when the user has project-specific assay knowledge, unpublished controls, or a specific operational definition of success.

Gate design when validation evidence exists:

- Define anchor ligands or structural checks before final docking.
- Define hard gates and soft gates separately. Hard gates block validated novel-compound interpretation; soft gates create caveats but may not block.
- Include at least one anchor that should reproduce the target effect and, when possible, one ligand class where the direction is different.
- Write the evidence source for every gate: article citation, PDB/co-crystal basis, assay source, or user-supplied criterion.
- Write the direction convention explicitly. For example, in a human-vs-mouse comparison, define whether `ddG = human - mouse` and which sign means stronger binding.
- Save anchor results separately from test-compound results.
- If a hard gate fails, stop and write `known_anchor_verdict.md`; do not rescue the report by focusing only on novel-ligand scores.

When no validation gate is feasible:

- Do not invent one.
- Write `validation_gap.md`.
- State what was searched or requested: articles, PDB/co-crystal data, known ligands, mutation data, assay controls, and user-supplied criteria.
- Downgrade conclusions to exploratory ranking, triage, or hypothesis generation.
- Recommend the smallest practical experimental or computational control that could create a future gate.

Minimum `validation_criteria.md` shape:

```markdown
# Validation Criteria

- Target:
- Biological comparison:
- Evidence source: literature / structure / assay / user-supplied
- Direction convention:
- Hard gate stop rule:

| ID | Anchor | Required behavior | Quantitative target | Tolerance | Evidence |
|---|---|---|---:|---:|---|
| VC-1 | | | | | |

## Structural sanity checks

- Redock RMSD or centroid threshold:
- Required pocket contacts:
- Clash/drift rejection rule:
- Sequence, allele, mutation, or construct identity checks:
```

Recommended anchor outputs:

```text
results/
  validation_criteria.md
  validation_gap.md
  anchor_scores.csv
  known_anchor_verdict.md
  pose_validation_index.html
  engine_concordance.md
```

Interpretation rule:

- Literature-derived or user-supplied gate passes: proceed to novel compounds, with caveats.
- Gate fails: report methodological failure, inspect receptor preparation, binding box, receptor state, flexible residues, ligand chemistry class, and scoring-function limits.
- No gate available: proceed only as exploratory docking; do not label the prediction as validated.
- Gate passes only under one fragile scoring convention: report the pass and the fragility together; do not present it as robust physical validation.

## 7. HTML Pose Check

Create `pose_check.html` before final interpretation.

Must show:

- receptor structure
- original co-crystallized ligand
- docked ligand pose
- docking box or clear pocket region cue
- labels for original ligand and docked ligand

Purpose:

- Confirm docking was performed in the intended binding pocket.
- Catch the common failure mode where the ligand docks into a wrong external region.

Use any reliable local visualization stack available, such as 3Dmol.js or py3Dmol-generated HTML.

## 8. Interaction Analysis

Create `interaction_analysis.html`.

Must show:

- receptor pocket residues near ligand
- docked ligand
- dotted lines between interacting atoms
- labels for key residues and interaction types

Analyze at least:

- hydrogen bonds
- salt bridges
- pi-pi or pi-cation interactions when applicable
- hydrophobic contacts
- metal coordination if relevant
- steric clashes or suspicious strained poses

The final report should not rely on Vina score alone. Include pose plausibility and interaction quality.

## 9. gnina Follow-Up Rescoring/Redocking

Use gnina when Vina validation is weak, when CNN rescoring is explicitly requested, or when an orthogonal score is useful. gnina is still a docking/scoring model; it does not remove the need to check for literature-derived or user-supplied validation evidence.

Recommended command shape:

```powershell
docker run --rm `
  -v <host_run_dir>:/work `
  gnina/gnina:latest gnina `
  -r /work/input/receptor_prepared.pdbqt `
  -l /work/input/ligand_prepared.pdbqt `
  --center_x <x> --center_y <y> --center_z <z> `
  --size_x <sx> --size_y <sy> --size_z <sz> `
  --exhaustiveness 32 `
  --num_modes 20 `
  --seed 42 `
  --cnn_scoring rescore `
  --cnn default2017 `
  --cpu 8 `
  -o /work/docking/gnina_poses.pdbqt `
  --log /work/docking/gnina.log
```

When GPU is intended, first run a real CNN-scoring smoke docking. A Docker `nvidia-smi` pass only confirms device visibility. In the AHR project, `gnina/gnina:latest` saw an RTX 5060, but CNN scoring crashed because the bundled PyTorch CUDA kernels did not support the newer GPU architecture. CPU mode was slower but worked.

Important parser rule:

With `--cnn_scoring=rescore`, gnina prints four numeric columns per pose:

```text
mode | affinity | intramol | CNN pose score | CNN affinity
```

Keep these as separate fields. Column 3 is a 0-1 pose-confidence score. Column 4 is CNN affinity. Parsing column 3 as affinity is a serious validation bug.

Recommended `gnina_scores.csv` columns:

```text
receptor,ligand,cnn_model,mode,vina_affinity,intramol,cnn_pose_score,cnn_affinity,seconds,selection_rule,status
```

Pose-selection robustness:

- M1: gnina default top-1 by CNN pose score.
- M2: max CNN affinity across all modes.
- M3: CNN affinity of the pose with the best Vina affinity.

If only M1 passes known-anchor gates and M2/M3 fail, mark the result as fragile. The AHR project showed exactly this pattern: two gnina configurations technically passed literature-derived TCDD/indirubin hard gates under default top-1 reporting, but no configuration passed under max-CNN-affinity or best-Vina-pose selection. That is useful evidence about model behavior, not robust physical proof.

Flexible-receptor warning:

- Flexible side chains must fit inside the search box.
- In the AHR project, adding mouse PHE 348 to the flex set required widening the box from 14 A to 22 A because the side chain's CB was about 8 A from the cavity center. A too-small box produced failed or misleading dockings.
- Save both rigid and flex PDBQT components and record the selected residues.

gnina outputs:

```text
results/
  gnina_scores.csv
  gnina_gate_summary.csv
  gnina_pose_selection_robustness.md
logs/
  gnina_<receptor>_<ligand>_<cnn>.log
vina_out/
  gnina_<receptor>_<ligand>_<cnn>.pdbqt
```

## 10. Receptor-State and Induced-Fit Caveats

Vina and gnina can fail when the receptor state is wrong, even if all commands run correctly. Check:

- apo vs holo state
- species, allele, mutation, and construct identity
- missing loops or shifted pocket gates
- collapsed cavities in cryo-EM/chaperone-bound states
- whether side-chain-only flexibility is enough
- whether a full complex, allosteric site, or alternative pocket is biologically relevant

For flexible cavities:

- Prefer ligand-bound structures when available.
- If no holo structure exists, consider induced-fit MD, ensemble docking over MD snapshots, or a mutated homolog template.
- Keep receptor-building outputs and diagnostics. Report cavity volume, closest ligand-protein clash distance, backbone RMSD, and retained lining residues when receptor relaxation is used.
- Use post-docking MD stability or free-energy methods when docking score direction is below the expected scoring-function resolution.

The AHR organoid project provides concrete lessons:

- Vina correctly reproduced some pose sanity checks but inverted TCDD/BaP species direction.
- Induced-fit mouse cavity opening fixed structural clashes but did not make Vina robustly recover TCDD/BaP direction.
- gnina recovered the original hard gate only under default top-1 reporting, and that pass was not robust to alternative pose selection.
- Therefore, downstream claims were scoped to ligand classes and literature anchors that passed validation, while failed anchors were documented rather than hidden.

## Output Folder Template

```text
vina_run_<target>_<ligand>/
  input/
    receptor_original.pdb
    receptor_prepared.pdbqt
    ligand_input.sdf
    ligand_prepared.pdbqt
    original_cocrystal_ligand.sdf
  validation/
    validation_criteria.md
    validation_gap.md
    anchor_scores.csv
    known_anchor_verdict.md
    pose_validation_index.html
  docking/
    vina_config.txt
    docking_command.txt
    vina.log
    docked_poses.pdbqt
    docked_poses.sdf
    scores.csv
    pose_check.html
    interaction_analysis.html
  gnina/
    gnina_scores.csv
    gnina_gate_summary.csv
    gnina_pose_selection_robustness.md
  report.md
  structure_selection.md
```

## Draft Report Checklist

- Structure selection: PDB, resolution, co-crystallized ligand, rationale.
- Receptor prep: retained/removed components.
- Ligand prep: 3D source, protonation/tautomer, optimization.
- Docking box: center, size, pocket basis.
- Validation evidence: literature-derived or user-supplied criteria, hard/soft gate result when available, or explicit validation-gap statement.
- Score table.
- Pose validation: whether docked pose overlaps intended binding site.
- Interaction analysis: key residues and interaction types.
- gnina follow-up, if used: CNN model, parser fields, pose-selection rule, robustness across M1/M2/M3.
- Engine concordance: whether Vina, gnina, structure sanity, and literature anchors agree.
- Caveats and next steps.
