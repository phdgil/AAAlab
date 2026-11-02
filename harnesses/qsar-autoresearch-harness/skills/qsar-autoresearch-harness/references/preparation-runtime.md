# Grouped preparation and fixed features

`scripts/qsar_prepare.py` exposes:

```python
prepare_splits(curated_csv, contract_path, output_dir, curation_manifest=None, external_csv=None)
generate_features(development_csv, split_manifest_path, contract_path, output_dir)
```

Run the corresponding `split --curated FILE [--external FILE]` or `features --development FILE
--split-manifest FILE` CLI command with `--contract FILE --output-dir DIR`.
The output directory must be empty and cannot overlap inputs. These are producer
adapters, not independent scientific acceptance. Dispatch them through the stage
runtime, listing this module as a code dependency. Existing NumPy and RDKit are
required; Avalon must be present when requested. Opt-in grouped classification
stratification additionally requires the existing scikit-learn dependency.
No packages are installed.

For a contract with
`execution_policy.allow_training_with_unresolved_required_source_coverage: false`,
pass `--curation-manifest FILE` to `split` (or the fourth Python argument).
The shared read-only `qsar_curation_evidence.py` guard runs before input splitting
or output-directory creation. A bare CSV, old manifest lacking bound evidence,
blocked source, stale file, or inconsistent ledger fails closed. The producer
records the validated bindings in its preprocessing manifest and checks them
again before writing that manifest. List the shared validator and its bound
curation code as dispatch code dependencies.

Current `qsar_data.curate` emits `bound_evidence`: full contract and registry,
collection manifest/gaps/record inventory, curation code, curated table and all
disposition/identity/conflict/narrative ledgers. The validator derives required
sources from the actual contract, reconciles counts and memberships, and checks
reviewed narrative decisions against the native inventory. Runtime curation PASS
and later prerequisite reuse additionally require those collection files to be
current accepted collection outputs. Keep independent curation review in the
runtime; passing the file validator alone does not establish scientific acceptance.

The runtime repeats transitive curation validation when consuming a dependent
dispatch (binding, command execution, submission and review), so a source mutation
after dispatch preparation blocks consumption. These bindings remain internal to
the coordinator; they are not added to training-agent input envelopes.

Native tabular/SQL/OFT dispositions must match exact collection locators and
record fingerprints, with the same lineage in curated and identity rows. OFT
uses its physical study-sheet row; HSDB instead uses reviewed field/observation
accounting, allowing multiple observations per field. Each HSDB observation ID is
reconstructed from its bound v1 decision and native document context using the
existing pure span-validation/ID functions. Disposition, identity and curated rows
must match that observation's exact field ID, native record, locator, source-record
kind and proposal. Parser IDs follow the existing reviewed-parser rule; excluded
fields emit no observations. The checker never calls the ledger-writing producer.
Expected observation fingerprints and per-ledger membership counts use a temporary
SQLite index; matching counts or mutually consistent forged row ledgers alone
cannot establish membership in a reviewed field. Collection records are
streamed through a temporary SQLite index of locators and fingerprints with an
8 MiB page-cache setting; complete record bodies are not accumulated. The scratch
database is closed and removed on success or failure without changing evidence.
Support ledgers account for ChemID documents and orphan structure IDs; conflict
ledgers reconcile every repeated task/group and its output-row membership.
Both ledgers require their documented headers even when empty. Full-run peak
memory and throughput still require measurement; these structural checks do not
certify scientific identity interpretation or authenticity of reviewer assertions.

The explicit identity adapter `npass_structure_v1` has a separate physical-row
support check. Native TSV headers must include `np_id`, `SMILES`, `InChI`, and
`InChIKey`; extra columns are preserved and included in the body fingerprint.
Every bound collection row must have exactly one matching support-ledger row,
including identical duplicate NPIDs at different physical rows. The guard checks
the source hash, row locator, complete native body, canonical structure and full
generated InChIKey, then reconstructs per-NPID reason unions and blocked-key state
using the existing pure chemistry policy. A blank NPID remains quarantined without
creating a usable key. Claimed identity mappings must reference real support rows;
blocked NPIDs cannot resolve identities or produce training rows.

No implicit adapter fallback is allowed for unknown identity adapter names.
Generic identity sources with no named adapter keep their existing behavior.
Multiple NPASS source files with inconsistent cross-source key dispositions fail
closed; the single-file adapter does not automatically reconcile earlier ledgers
after a later file blocks a key. This check does not infer route, exposure context,
or scientific eligibility from a recovered identity. Full-source guard throughput
remains unmeasured; bounded fixtures establish structural compatibility only.

Recorded required-source collection gaps remain blocking, including readable
partial-parser inventories. A collection review's prose cannot automatically
resolve such a gap: a future typed, independently reviewed scope-resolution
artifact would be needed, and this guard does not invent one. The only supported
unreadable-bundle exception matches the exact optional
`toxvaldb/COMPTOX_Public.zip` source, its bound failed archive inventory and the
contract's explicit accepted scope; a global exception flag cannot waive another
source. Historical contracts without the strict false setting retain the existing
non-strict fixture workflow. Regenerate and independently review strict curation
artifacts when their bound code changes; do not rewrite frozen accepted collection
copies or historical diagnostics.

## Contract

This is an example of explicit configuration, not a universal scientific recipe:

```json
{
  "preparation": {
    "target_column": "target_pLD50",
    "group_column": "group_id",
    "grouping_policy": "declared",
    "stratify": false,
    "seed": 92,
    "terminal_fraction": 0.2,
    "outer_folds": 5,
    "inner_folds": 3,
    "intended_generalization": "Specify the reviewed target population",
    "terminal_custodian": "Specify the isolated evaluator",
    "prior_exposure": {
      "status": "unknown",
      "evidence": [],
      "description": "Describe known prior use and evidence gaps"
    },
    "unfitted_transforms": {
      "missingness": "Specify fold-local policy",
      "scaling": "Specify fold-local policy",
      "selection": "Specify fold-local policy"
    }
  },
  "features": {
    "families": {
      "RDKit2D": {},
      "Morgan2": {"n_bits": 2048, "use_chirality": true},
      "Morgan3": {"n_bits": 2048, "use_chirality": true},
      "Morgan2Count": {"n_bits": 2048, "use_chirality": true},
      "Morgan3Count": {"n_bits": 2048, "use_chirality": true},
      "FCFP2": {"n_bits": 2048, "use_chirality": true},
      "FCFP3": {"n_bits": 2048, "use_chirality": true},
      "FCFP2Count": {"n_bits": 2048, "use_chirality": true},
      "FCFP3Count": {"n_bits": 2048, "use_chirality": true},
      "RDKitpath": {"n_bits": 2048, "min_path": 1, "max_path": 7},
      "MACCS": {},
      "atom_pair": {"n_bits": 2048, "min_distance": 1, "max_distance": 30, "use_chirality": true},
      "torsion": {"n_bits": 2048, "torsion_atom_count": 4, "use_chirality": true},
      "Avalon": {"n_bits": 512}
    }
  }
}
```

Prior-exposure status is `unknown`, `none_known`, or `exposed`. The ledger is
copied into an output artifact; random reassignment never resets exposure.
An independent reviewer must substantiate any untouched-benchmark claim.

## Splits and custody

All candidate input rows are eligible curated records. Required fields are unique nonempty
`output_row_id`, `task_id`, `group_id`, valid `parent_smiles`, and finite numeric
target in the configured column. An existing `partition` column fails to prevent
accidental benchmark reshuffling. Additional columns survive unchanged.

`preparation.grouping_policy` is optional and defaults to `declared`: preserve
all supplied `group_id` values and reject conflicting groups for the same
non-stereochemical canonical parent, exactly as before. Explicit `parent` groups
canonical non-stereochemical `parent_smiles` connectivity. Explicit
`bemis_murcko` uses RDKit's `MurckoScaffold.GetScaffoldForMol` with canonical,
non-stereochemical scaffold SMILES (not generic atom/bond scaffolds). Neither
policy performs fragment selection, tautomer normalization or charge changes;
curation must already have established the accepted parent.

Both derived policies **coarsen** the supplied groups by connected-component
union: matching parent/scaffold keys merge declared groups, and every member of
each declared group remains connected, including transitive merges. They never
replace a broader declared group with smaller chemistry-only groups. The derived
`group_id` is `<policy>:<sha256>` of the sorted distinct grouping keys serialized
as compact JSON. Other source columns are untouched. The original group and exact
key/basis for every row are retained in `grouping_mapping.csv`.

Bemis-Murcko requires explicit `preparation.empty_scaffold_policy`:

- `parent`: keep each empty-scaffold/acyclic parent under its own canonical-parent
  key, still respecting declared-group connectivity. The mapping records
  `empty_scaffold_parent_connectivity`; distinct acyclic parents are not one
  shared empty scaffold.
- `error`: block preparation when any candidate has an empty scaffold.

Omitting this field with `bemis_murcko`, using it with another policy, or requesting
an unknown policy fails. There is no implicit scaffold-to-parent substitution.

Groups are ordered by SHA-256 of the seed, scope and group ID, without examining
targets. The first `ceil(terminal_fraction * global_group_count)` groups form
the terminal set across all tasks. Within each task, remaining groups are
assigned round-robin to outer folds; each outer training set gets separately
seeded inner group folds. All row axes preserve input order. There is no row cap,
fitted transformation, or silent sparse-task exclusion.
Insufficient groups or tasks lacking either partition produce a blocked
`feasibility.json`; the scientific contract must adjudicate another design.
Group counts are balanced, not record counts or target distributions.

For binary classification, `training.task_type: "classification"` is the single
task-type authority (absent means regression). Every target, including reserved
and external rows, must be numeric 0 or 1. Set `preparation.stratify: true` to use
`sklearn.model_selection.StratifiedGroupKFold` for each task's development-only
outer folds and each outer training set's inner folds. It uses `shuffle=true`
and a scoped random seed: first four SHA-256 bytes of compact JSON `[seed,scope]`,
interpreted big-endian. Toolkit version and seed derivation are recorded.
Stratification is approximate, group-constrained, and does not guarantee exact
class or row balance. Every classification training/validation fold must contain
both classes; insufficient class-bearing groups or a failed grouped allocation
blocks with `feasibility.json`. No resampling or repeated seed search is used.
`stratify` must be boolean and true is invalid for regression. The terminal
reservation always remains label-blind; terminal class counts do not drive any
split, stratification or model selection and may make later evaluation infeasible.

Outputs:

- `development.csv` and `sealed_terminal.csv`: source columns plus `partition`;
  `group_id` changes only for an explicitly selected derived grouping policy.
- `membership.csv`: only row/task/group identifiers and partition, no labels.
- `grouping_mapping.csv`: all candidate and external rows, including exclusions;
  columns are `cohort`, `output_row_id`, `task_id`, `declared_group_id`,
  `effective_group_id`, `canonical_parent_smiles`, `scaffold_smiles`,
  `grouping_basis`, `grouping_key`, `partition`. This is custodian-only.
- `development_grouping_mapping.csv`: exactly the development subset of that
  mapping in source order, without targets or any protected cohort rows.
- `folds.json`: exact nested development row membership.
- `exposure_ledger.json`, `unfitted_pipeline.json`, `preprocessing_manifest.json`.

The manifest binds source, contract, code, development, terminal, membership,
folds, both grouping ledgers, exposure and unfitted-pipeline artifacts by SHA-256.
It contains terminal counts and artifact locators, but no terminal targets.
`custodian_only` is the exact list of protected path/hash bindings: original
curated input, sealed terminal file, full grouping ledger, and all external
source/copy/exposure/exclusion artifacts when present. Runtime custody must
register these bindings from accepted preprocessing and forbid them as modeling
dispatch inputs. Only the development grouping ledger is suitable for modeling
dispatches. This script itself provides no OS access control; the custodian must
restrict protected files before handing development artifacts to modeling agents.

`folds.json` has this exact structure:

```json
{
  "schema_version": "1.0",
  "tasks": {
    "task-id": {
      "row_ids": ["all development rows for this task, in order"],
      "outer_folds": [
        {
          "fold": 0,
          "train_row_ids": ["outer training rows"],
          "validation_row_ids": ["outer evaluation rows"],
          "inner_folds": [
            {"fold": 0, "train_row_ids": ["inner training rows"], "validation_row_ids": ["inner validation rows"]}
          ]
        }
      ]
    }
  }
}
```

Inner membership never includes outer evaluation rows. The outer membership can
also be reused for separate fresh full-development selection CV after nested
evaluation; outer evaluation scores themselves must not choose the final winner.

## Separate external cohort

`external_csv=` / `split --external FILE` is optional and never replaces the
reserved terminal split. Its presence requires this explicit contract section:

```json
{
  "preparation": {
    "external_cohort": {
      "prior_exposure": {
        "status": "unknown",
        "evidence": [],
        "description": "Describe this external cohort's actual exposure history"
      },
      "exclude_scaffold_overlap": false
    }
  }
}
```

This is a fragment to merge into the full preparation contract. Configuring it
without supplying the external CSV also fails; external exposure cannot inherit
the reserved terminal cohort's exposure. The external table obeys the same
required-field, structure, finite-target, unique-row-ID and declared-connectivity
checks; its tasks must be present among the candidates, and a `partition` column
is rejected. All supplied external bytes, row order, repeated observations,
headers and extra columns are copied unchanged to `sealed_external.csv`.
No partition column is added and no external groups or targets are rewritten.

Before any candidate terminal/development reservation, exclude **only candidate
rows** whose canonical non-stereochemical parent occurs anywhere in the external
cohort, across tasks. Each excluded row records its original/effective groups,
canonical parent, JSON `reasons` and JSON `matched_external_row_ids` in
`external_exclusions.csv`; the default reason is `canonical_parent_overlap`.
Other members of an affected group survive with their existing effective group
assignments. The default is not an entire-group or scaffold exclusion and does
not claim external declared-group/scaffold independence. Explicit
`exclude_scaffold_overlap: true` additionally excludes nonempty Bemis-Murcko
scaffold matches with reason `bemis_murcko_overlap`. Empty scaffolds never match
each other for this exclusion; canonical-parent matches still apply. Both
reasons are recorded when both apply. Surviving candidate/external row-ID
collisions fail. No task is silently dropped when exclusions make it infeasible.

The split manifest adds exactly these external fields (all bindings are
`{"path": "absolute path", "sha256": "hex digest"}`):

```text
external_cohort.input                          original external CSV binding
external_cohort.artifact                       byte-identical sealed_external.csv binding
external_cohort.rows                           external row count, never filtered
external_cohort.prior_exposure                 supplied status/evidence/description
external_cohort.exposure_ledger                external_exposure_ledger.json binding
external_cohort.exclusions                     external_exclusions.csv binding
external_cohort.excluded_development_candidates number of excluded candidate rows
external_cohort.overlap_policy.identity        canonical non-stereochemical parent_smiles
external_cohort.overlap_policy.exclude_scaffold_overlap boolean
```

The external exposure ledger also binds the original input and named custodian.
`input_rows = development_rows + terminal_rows + excluded_rows` counts only
curated candidates. External intake adds per-task `external` and `excluded`
counts to `task_counts`, without labels; `development` and `terminal` keep their
existing meanings. External source and copy hashes are rechecked before writing
the manifest, as are all other source/contract/code/curation bindings. External
rows cannot pass the development-only feature API. External evaluation must use
the locked final models in the isolated evaluator, preserve this exposure ledger,
and report a distinct external result rather than reinterpret it as an untouched
terminal claim. External prior exposure is never reset by candidate exclusions.

## Feature matrices

Features require the exact development file and contract bound by the split
manifest, and every row must declare `partition=development`. Passing the sealed
file or a changed development file fails. Only `parent_smiles` produces features;
identifiers and targets are not predictors. No filtering, imputation, scaling,
selection or fitting occurs.

`RDKit2D` always enumerates the complete installed `Descriptors._descList`, with
exact ordered names and available function version identifiers. Nonfinite values
and descriptor exceptions remain nonfinite in the matrix and are recorded in
`nonfinite_ledger.csv`; no zero substitution or row dropping occurs. Invalid
structures fail the stage. Fingerprint failures produce a failure artifact and
fail the stage. An unavailable requested family is not silently omitted.

Fingerprints are separate bit/count families. Morgan uses explicit radius 2/3,
bond types and ring membership. RDKit path uses hydrogen/branched paths/bond
orders and two bits per feature. Atom-pair uses topological distances. Count
simulation is disabled for these generators. MACCS includes its unused bit zero
and has 167 dimensions. Avalon uses its standard bit flags. Exact generator
arguments/info strings, dimensions and toolkit version are recorded.

`Morgan2` / `Morgan3` remain binary connectivity-Morgan fingerprints.
`Morgan2Count` / `Morgan3Count` call the real RDKit Morgan generator's
`GetCountFingerprint` at the same radius, producing fixed-width hashed counts,
not count-simulation bits or thresholded counts. `FCFP2` / `FCFP3` use the real
`GetMorganFeatureAtomInvGen()` atom-invariant generator, not connectivity-Morgan
aliases; their `Count` counterparts use that feature generator with
`GetCountFingerprint`. Every Morgan/FCFP family accepts exactly `n_bits` (positive
integer, also the count vector's bucket width) and `use_chirality` (boolean).
Unknown families/parameters fail rather than silently substitute another family.
Count buckets accumulate collisions and may exceed 1; binary buckets do not.
No learned representation or fitted feature generation occurs.

Recipes contain `factory`, serializable `arguments`, `method`, `dtype`,
`representation` and native `generator_info` where applicable. FCFP recipes also
contain `atom_invariants_generator: {"factory": "GetMorganFeatureAtomInvGen",
"arguments": {}}`; rebuild this object and pass it as `atomInvariantsGenerator`
before constructing `GetMorganGenerator`. `fingerprint_generator` returns the
exact generator/width/recipe for evaluator replay; `fingerprint_feature_names`
and `fingerprint_dtype` share the value-kind/axis contract. Count vectors use
`GetLength()` rather than the bit-vector-only `GetNumBits()` dimension check.

Each `<family>.npz` opens with `np.load(path, allow_pickle=False)` and contains
`X` (float64 descriptors, uint8 fingerprint bits, or uint32 fingerprint counts), `row_ids`, `task_ids`, and
`feature_names` (Unicode arrays). Axes match development file order exactly.
Fingerprint columns are `<family>:bit:<zero-based bucket>` or
`<family>:count:<zero-based bucket>`; existing binary-family names are unchanged.
`feature_manifest.json` includes `families[name].artifact` path/hash, shape,
dtype, ordered names, parameters, replay recipe and nonfinite count, plus the
full row axis and planned/generated family accounting. Consumers must validate
these hashes/axes before training and fit handling of nonfinite columns only
within the permitted training folds.
