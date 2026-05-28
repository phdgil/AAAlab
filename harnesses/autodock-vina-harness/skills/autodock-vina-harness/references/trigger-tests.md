# AutoDock Vina Harness Trigger Tests

Use these examples when checking whether the harness should activate instead of the base `autodock-vina` protocol skill.

## Should Trigger

- "Run Vina docking for this receptor and ligand, then validate the pose."
- "Use the docking harness for an AHR ligand batch."
- "Redo only gnina rescoring from my previous Vina output."
- "Troubleshoot why the ligand is docking outside the binding pocket."
- "Build validation gates from literature before docking novel ligands."
- "Compare Vina and gnina results and tell me whether interpretation is allowed."
- "Rerun the previous docking with a larger box and keep prior prep files."
- "Make pose_check.html and interaction_analysis.html for these docking results."
- "Batch dock anchors first, then test compounds if the hard gates pass."
- "Audit this docking report for wrong-pocket, parser, or validation errors."

## Should Not Trigger

- "What is AutoDock Vina?"
- "Explain exhaustiveness in Vina."
- "Summarize this docking article."
- "Install RDKit."
- "Convert an unrelated SDF to CSV."
- "Draw a schematic of a protein binding pocket."
- "Find the PDB ID for this protein but do not run docking."
- "Explain CNN pose score versus CNN affinity."
- "Write a generic Bash script."
- "Analyze an MD trajectory without a docking task."

## Boundary Rule

If the user asks only for background, explanation, installation, or a single narrow command, use the base `autodock-vina` skill or answer directly. If the user asks to execute, rerun, validate, troubleshoot, compare engines, create pose viewers, or produce a final docking verdict, use this harness.
