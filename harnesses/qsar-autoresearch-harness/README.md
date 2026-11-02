# QSAR research skill pack

Eleven sibling skills provide coordination/shared rules, eight focused research methods and a harness maintenance workflow. Install the whole `skills/` directory; the installer discovers every skill. Twelve domain roles are supplied in [native-agents](native-agents/README.md), with an explicit installer that preserves unrelated roles. The coordinator checks actual runtime availability and uses declared generic fallbacks when custom roles are unavailable.

| Skill | Responsibility |
|---|---|
| qsar-autoresearch | Shared scientific and artifact contract |
| qsar-autoresearch-harness | Coordinator, assignments and staged handoffs |
| qsar-collect | Source coverage and native acquisition |
| qsar-curate | Endpoint, identity, unit and replicate curation |
| qsar-preprocess | Frozen splits and pre-fit transform specification |
| qsar-features | Declared representation coverage and feature matrices |
| qsar-train | Fold-local model/feature search |
| qsar-evaluate | Development, sealed terminal evaluation, or release |
| qsar-improve | Reviewed intervention or justified STOP |
| qsar-review | Independent stage-specific source and execution review |
| [qsar-evolve](skills/qsar-evolve/SKILL.md) | Improve reusable harness code and instructions from usage feedback |

The producer reads the shared contract, its assigned skill and relevant run inputs. A distinct reviewer uses qsar-review. Modeling uses all scientifically eligible records and declared feature/model coverage; explicit diagnostics are labeled partial. Final holdout evaluation follows the development improvement loop.

## Install and validate

```sh
aaalab install qsar-autoresearch-harness
aaalab validate qsar-autoresearch-harness
aaalab runtime-check qsar-autoresearch-harness
```

For a local checkout use `./install.sh` or `./install.ps1`; provide an isolated agent-home argument to test without modifying installed skills. Manual installation copies every directory under this pack's `skills/` into the target skills directory together. Restart the runtime to discover newly installed skills.

```sh
node skills/qsar-autoresearch-harness/scripts/validate_skill_map.js --dispatch training
node skills/qsar-autoresearch-harness/scripts/validate_skill_map.js --review training
```

These commands produce minimal role/skill dispatch templates. The coordinator supplies actual run inputs, ownership and review evidence and invokes the native role. Use [qsar_runtime.py](skills/qsar-autoresearch-harness/scripts/RUNTIME.md) for durable stage gates, independent execution bindings, reviewed training identity, transitive invalidation and one-time terminal reservation. Native launches remain the coordinator's responsibility; local provenance is leader-attested rather than an authentication or OS isolation boundary.

Python 3.11+ and relevant pandas/scikit-learn/RDKit components are required for research execution, openpyxl for Excel, and optional representations/models need their own feasibility checks. Node is required for package routing validation. Source collection does not require an existing workbook.

## Continuous improvement during use

The bundled `$qsar-evolve` skill captures lessons from real runs and validates
reusable harness changes. Invoke it with, for example:

> Use $qsar-evolve to review this run's failures and my feedback, fix the supported cause, and prepare the harness for the next run.

At a checkpoint, keep a short observation and evidence links in the run's private
maintenance log. Carry reproducible issues into a separate maintenance context,
validate the repair, then adopt a recorded harness version for the next run.
Generalized changes accumulate in [maintenance history](skills/qsar-autoresearch/maintenance/feedback.md).
The coordinator links this workflow without making it a scientific stage or
editing a live run's dependencies. `$qsar-improve` handles research interventions
from development evidence; `$qsar-evolve` maintains the reusable harness.

The namespaced skill installs with the whole pack and preserves any existing
general `evolve` skill. It runs through the agent when invoked or selected for a
matching request; it does not install a background watcher or silently publish
updates. Private run evidence stays outside the distributable pack.

## Scope of this revision

Implemented: focused skills, twelve installable roles, validated assignments, safe installation and executable stage gates. [Collection/curation adapters](skills/qsar-autoresearch-harness/references/source-adapters.md) preserve source hashes, qualifiers and row dispositions. Supported native formats and unresolved source requirements are explicitly documented. Full eligible-data scientific acceptance requires execution and independent review of each stage; package tests alone do not establish it.

Executable stages are documented separately:

- [Grouped preparation and full feature generation](skills/qsar-autoresearch-harness/references/preparation-runtime.md)
- [Nested model/feature search and locked final fitting](skills/qsar-autoresearch-harness/references/training-runtime.md)
- [Evaluation, improvement decisions and terminal replay](skills/qsar-autoresearch-harness/references/evaluation-runtime.md)
- [Accepted-evidence release packaging](skills/qsar-autoresearch-harness/references/release-runtime.md)

Capability choices belong in the versioned research contract. Tabular regression,
binary classification, fitted chemical-rule scoring and frozen local SMILES
encoders are distinct model paths; fixed expert rules and pretrained inference
are not evidence of training. Optional checkpoints require their own provenance
and licenses. Frozen encoding does not imply encoder fine-tuning.

Preparation supports explicit parent/scaffold grouping, count and feature-Morgan
fingerprints, and a separately preserved external cohort. A custodian-only
grouping ledger is not a modeling input. Applicability-domain assessments preserve
out-of-domain and abstained queries instead of silently reducing evaluation
coverage. Curation deltas are advisory comparisons, never replacements for
source review or exact dependency bindings.

Use the runtime execution interfaces for durable local/shared-filesystem Slurm
jobs and preserve the difference between scheduler completion, verified model
artifacts and independent scientific acceptance. Reporting language is an
explicit presentation choice (`en` or `ko`), not a training policy.

Run `npm test` for packaging/routing/isolated-install checks and
`npm run test:qsar-python` for executable adapter, gate and synthetic pipeline
regressions in an environment with the documented scientific dependencies.
Those checks do not train on or modify a user's research originals.

Example: Use $qsar-autoresearch-harness to collect and curate the declared sources, train the planned representation/model matrix, review each stage independently, and execute justified development improvements before final evaluation.

Original package text/scripts remain Apache-2.0. External scientific libraries are not bundled; see LICENSE_AUDIT.md and THIRD_PARTY_NOTICES.md.
