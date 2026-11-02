# Grouped preparation and fixed features

`scripts/qsar_prepare.py` exposes:

```python
prepare_splits(curated_csv, contract_path, output_dir, curation_manifest=None)
generate_features(development_csv, split_manifest_path, contract_path, output_dir)
```

Run the corresponding `split --curated FILE` or `features --development FILE
--split-manifest FILE` CLI command with `--contract FILE --output-dir DIR`.
The output directory must be empty and cannot overlap inputs. These are producer
adapters, not independent scientific acceptance. Dispatch them through the stage
runtime, listing this module as a code dependency. Existing NumPy and RDKit are
required; Avalon must be present when requested. No packages are installed.

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

All input rows are eligible curated records. Required fields are unique nonempty
`output_row_id`, `task_id`, `group_id`, valid `parent_smiles`, and finite numeric
target in the configured column. Additional columns survive unchanged. Conflicting
group assignments for the same non-stereochemical canonical parent fail. An
existing `partition` column fails to prevent accidental benchmark reshuffling.

Groups are ordered by SHA-256 of the seed, scope and group ID, without examining
targets. The first `ceil(terminal_fraction * global_group_count)` groups form
the terminal set across all tasks. Within each task, remaining groups are
assigned round-robin to outer folds; each outer training set gets separately
seeded inner group folds. All row axes preserve input order. There is no row cap,
label stratification, fitted transformation, or silent sparse-task exclusion.
Insufficient groups or tasks lacking either partition produce a blocked
`feasibility.json`; the scientific contract must adjudicate another design.
Group counts are balanced, not record counts or target distributions.

Outputs:

- `development.csv` and `sealed_terminal.csv`: original columns plus `partition`.
- `membership.csv`: only row/task/group identifiers and partition, no labels.
- `folds.json`: exact nested development row membership.
- `exposure_ledger.json`, `unfitted_pipeline.json`, `preprocessing_manifest.json`.

The manifest binds source, contract, code, development, terminal, membership and
fold artifacts by SHA-256. It contains terminal counts and an artifact locator,
but no terminal targets. The custodian must seal the terminal file and restrict
access to it and the original curated table before handing development files to
modeling agents. This script provides no OS access control.

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

Fingerprints are separate binary-bit families. Morgan uses explicit radius 2/3,
bond types and ring membership. RDKit path uses hydrogen/branched paths/bond
orders and two bits per feature. Atom-pair uses topological distances. Count
simulation is disabled for these generators. MACCS includes its unused bit zero
and has 167 dimensions. Avalon uses its standard bit flags. Exact generator
arguments/info strings, dimensions and toolkit version are recorded; alternative
count or learned representations require another reviewed implementation.

Each `<family>.npz` opens with `np.load(path, allow_pickle=False)` and contains
`X` (float64 descriptors or uint8 fingerprint bits), `row_ids`, `task_ids`, and
`feature_names` (Unicode arrays). Axes match development file order exactly.
`feature_manifest.json` includes `families[name].artifact` path/hash, shape,
dtype, ordered names, parameters, replay recipe and nonfinite count, plus the
full row axis and planned/generated family accounting. Consumers must validate
these hashes/axes before training and fit handling of nonfinite columns only
within the permitted training folds.
