# License Audit - QSAR Autoresearch Harness

The AAAlab-authored skills, native agent definitions, reference documentation,
installers, validation tools and scientific runtime code in this harness are
distributed under the repository's Apache-2.0 license.

Repository regression tests use independently authored synthetic fixtures.
They do not distribute native database records, private session transcripts,
research datasets, trained model artifacts or external library implementations.

## External runtime dependencies

Runtime requirements depend on the selected stage:

- Python 3.11+ for scientific scripts and native-agent installation.
- NumPy, SciPy, scikit-learn and joblib for numerical training and model artifacts.
- RDKit for chemistry normalization, molecular representations and identity checks.
- openpyxl for XLSX source adapters.
- pandas for the documented tabular preflight.
- XGBoost and CatBoost only when those algorithms are declared in the search contract.
- Node.js and PowerShell or a POSIX shell for the corresponding package tools.

These runtimes and libraries are not bundled. Their upstream licenses and notices
apply independently; this document does not relicense them or certify license
compatibility for every downstream environment.

## External scientific sources

Source adapters describe how to consume separately obtained scientific databases
and exports. Those databases and their records are not bundled. Users must obtain
source data under the applicable access and usage terms, retain source provenance,
and establish permission before redistributing data or derived packages.

See [third-party notices](THIRD_PARTY_NOTICES.md) and the repository license.
