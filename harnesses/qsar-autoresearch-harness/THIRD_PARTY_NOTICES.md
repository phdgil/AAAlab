# Third-Party Notices - QSAR Autoresearch Harness

This pack contains AAAlab-authored workflow instructions, native agent definitions,
scientific runtime code and supporting tools. External runtimes, libraries,
scientific databases and pretrained models are not distributed with the pack.

## Referenced runtime dependencies

- Python
- NumPy
- SciPy
- scikit-learn
- joblib
- RDKit
- openpyxl
- pandas
- XGBoost, when selected by the research contract
- CatBoost, when selected by the research contract
- LightGBM, when selected by the research contract
- Optuna, when selected as the search backend
- imbalanced-learn, when an explicitly contracted resampling policy requires it
- PyTorch, Transformers and their tokenizer dependencies, when a local
  checkpoint encoder is selected
- Slurm client tools, when selected for shared-filesystem job execution
- psutil, for POSIX process-group quiescence checks before execution receipts
- Node.js
- PowerShell or a POSIX shell for the corresponding installation tools

Use and redistribution of these dependencies are governed by their respective
upstream licenses and notices. Listing a dependency here does not confer rights
to redistribute it.

Checkpoint weights and tokenizers have their own licenses and training-data
provenance. A local file hash establishes identity, not permission to redistribute,
scientific suitability, or freedom from training/evaluation overlap. No model
weights or tokenizers are bundled with this pack.

## Source data and regression fixtures

Adapters may process user-supplied exports from external scientific sources.
Source names and schemas identify supported inputs; they do not grant access,
usage or redistribution rights to the underlying databases.

Repository regression fixtures are independently authored synthetic inputs used
to exercise parsing, identity checks and scientific workflow controls. They are
not native database extracts or evidence of scientific model performance.
