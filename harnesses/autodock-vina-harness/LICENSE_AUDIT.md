# License Audit

Audit date: 2026-05-29

This is an engineering license review, not legal advice. It documents what is included in this repository, what is merely referenced, and the license choice that best fits the current contents.

## Repository Contents

The repository currently contains:

- Codex skill instructions under `skills/autodock-vina/` and `skills/autodock-vina-harness/`.
- Markdown role definitions and trigger tests.
- PowerShell and Bash installer/validator scripts.
- GitHub Actions workflow YAML.

The repository does not include external docking binaries, chemistry libraries, Docker images, model weights, molecular structures, receptor files, ligand files, or generated docking results.

## Upstream License Findings

| Item | Finding | Source checked |
|---|---|---|
| AutoDock Vina | Apache-2.0. The upstream repository states AutoDock Vina is distributed under Apache License 2.0. | https://github.com/ccsb-scripps/AutoDock-Vina |
| gnina | Dual Apache-2.0 / GPL-2.0. Upstream explains GPL is needed because of Open Babel usage; Apache-only requires removing Open Babel references from source. | https://github.com/gnina/gnina |
| Meeko | LGPL-2.1 in current GitHub repository. PyPI metadata for older releases varied, so use the repository LICENSE as controlling for source. | https://github.com/forlilab/Meeko |
| RDKit | BSD-3-Clause. | https://github.com/rdkit/rdkit |
| Datamol | Apache-2.0. | https://github.com/datamol-io/datamol |
| PDBFixer | MIT-style license. | https://github.com/openmm/pdbfixer |
| Open Babel | GPL-2.0. | https://github.com/openbabel/openbabel |
| 3Dmol.js | BSD-style permissive license per upstream README. | https://github.com/3dmol/3Dmol.js |
| py3Dmol | MIT. | https://pypi.org/project/py3Dmol/ |
| MGLTools / AutoDockTools | Official pages describe availability but this audit did not find a concise current license statement suitable for vendoring decisions. Treat as optional external tooling only. | https://ccsb.scripps.edu/mgltools/ and https://autodocksuite.scripps.edu/adt/ |

## License Compatibility Analysis

Because this repository does not bundle, link, import, or redistribute the external tools, their licenses do not force this repository to adopt GPL, LGPL, BSD, or MIT. The repo can use a permissive license for its own original skill text and scripts.

The main risk is future scope creep:

- If Open Babel code or GPL gnina source is copied into the repo, an Apache-only repository would no longer be appropriate without a GPL compatibility review.
- If Meeko code is copied or modified in the repo, LGPL obligations must be followed.
- If Datamol or PDBFixer code is copied into the repo, retain their upstream notices.
- If 3Dmol.js or py3Dmol code is embedded into generated templates, their notices should be included.
- If the repo starts distributing Docker images or binaries, each bundled component must be re-audited.

## Recommended Repository License

Recommendation: Apache License 2.0.

Rationale:

- It matches AutoDock Vina's upstream Apache-2.0 licensing.
- It is permissive enough for academic and internal industry reuse.
- It includes an express patent grant, which is useful for a workflow/tooling repository that colleagues may extend.
- It avoids imposing copyleft obligations on original harness text while preserving clear reuse rights.

MIT would also be legally simple for original code, but Apache-2.0 is a better fit here because of alignment with Vina and the patent grant.

GPL-2.0 or GPL-3.0 is not recommended for this repository's own license because the current repo does not include GPL code and copyleft would unnecessarily limit reuse.

## Repository Files Added For Release

- `LICENSE`: Apache License 2.0 text.
- `NOTICE`: project copyright and no-vendored-third-party statement.
- `THIRD_PARTY_NOTICES.md`: dependency/reference license notes and future vendoring policy.

## Release Gate

Before publishing to GitHub:

- Keep `skills/` free of vendored third-party software.
- Keep installer scripts from downloading or redistributing Vina, gnina, Open Babel, Meeko, RDKit, Datamol, PDBFixer, MGLTools, 3Dmol.js, or py3Dmol automatically.
- If adding examples that fetch CDN scripts or install packages, document that those tools remain separately licensed.
- Re-run `skills/autodock-vina-harness/scripts/validate_harness.ps1`.
