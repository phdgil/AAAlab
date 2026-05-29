# Third-Party License Notes

This repository does not vendor third-party docking or chemistry software. It only references external tools that users may install separately.

| Tool or project | How this repo uses it | Upstream license found during audit | Bundled here? | Notes |
|---|---|---|---|---|
| AutoDock Vina | Referenced as the primary docking engine | Apache-2.0 | No | Compatible with this repo's Apache-2.0 license if future examples remain invocation-only. |
| gnina | Optional follow-up rescoring/redocking engine | Dual Apache-2.0 / GPL-2.0, with GPL required by Open Babel references | No | Do not vendor gnina into this repo without re-auditing GPL implications. |
| Meeko | Optional receptor/ligand PDBQT preparation path | LGPL-2.1 | No | Invocation or optional installation guidance is acceptable; vendoring requires LGPL compliance. |
| RDKit | Optional conformer and chemistry handling path | BSD-3-Clause | No | Permissive; keep notices if any code is copied in the future. |
| Datamol | Optional molecule standardization/conformer helper built on RDKit | Apache-2.0 | No | Optional helper only; keep Apache notice if code is copied. |
| PDBFixer | Optional receptor structure repair helper | MIT-style | No | Optional helper only; keep MIT notice if code is copied. |
| Open Babel | Optional format conversion / PDBQT preparation path | GPL-2.0 | No | Do not vendor Open Babel or link against it in repo code without GPL review. |
| MGLTools / AutoDockTools | Optional legacy PDBQT preparation path | License not conclusively determined from official pages in this audit | No | Keep as optional user-installed tooling only unless license terms are reviewed directly. |
| 3Dmol.js | Possible HTML pose viewer dependency | BSD-style permissive license | No | If viewer HTML embeds 3Dmol.js code instead of loading from CDN, include its notice. |
| py3Dmol | Possible HTML viewer generation helper | MIT | No | If copied or bundled, include MIT notice. |
| actions/checkout | Used by GitHub Actions workflow | MIT | No | GitHub Action is referenced by workflow, not redistributed in this repo. |

## Policy For Future Changes

- Keep this repository free of vendored GPL/LGPL tools unless the repository license strategy is re-reviewed.
- Prefer instructions that call user-installed tools on `PATH`.
- If adding code copied from third-party projects, add the upstream license text or required notices before merging.
- If adding dependency installation files, list dependency licenses in this file.
