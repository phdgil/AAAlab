# Collection and curation producer API

`scripts/qsar_data.py` is a stage producer, not an approval service. The leader must
bind native inputs, this script and contract in `Runtime`, accept collection through
a separate reviewer, then dispatch curation. The adapter cannot authenticate a PASS
receipt independently. Run only in a stage-owned empty output directory.

```
python qsar_data.py collect --raw-root PATH --output-dir EMPTY_DIR
python qsar_data.py curate --contract CONTRACT.json --registry source_registry.json --output-dir EMPTY_DIR
```

Requires Python, openpyxl for XLSX, RDKit for curation; no package installation or
network acquisition occurs. No legacy scientific runners are imported.

Collection hashes every supplied file before and after reading. It writes
`source_registry.json`, `source_records.jsonl` (every parsed native tabular row,
including excluded strata and metadata sheets), `collection_manifest.json` and
`collection_gaps.json`. Native originals remain in place, read-only and hash-bound;
these are references, not copied or filesystem-enforced immutable snapshots.
CSV/CSV.gz, TSV/TXT and XLSX are inventoried; JSON has metadata rather than invented
record counts. CSV comment preambles beginning `#` are skipped before the header.
Native XLSX formulas are preserved as formulas, never evaluated into labels.
Known HSDB and ChemID archives are inventoried as complete native documents;
ChemID's native SMILES/InChI TSV members are separately inventoried. Document,
identity and measurement counts are different axes, not interchangeable totals.
Other archives have member metadata when available but no scientific record-count
claim. Invalid ZIP central directories produce explicit parse failures, never an
irrelevance judgment or an in-place repair.
ToxVal SQL is streamed using its CREATE-declared column schema. Other SQL tables
are listed by name with unknown row counts and explicit partial-coverage gaps;
the SQL file's total row count stays null while `inventoried_record_count` counts
all emitted toxval rows. Other unsupported native files remain explicit gaps.
The Agilent formula CSV format uses its exact comment-declared
`# Formula, RT, Mass, Cpd, Comments` header. A filename containing MSReady does not
turn such formula/mass records into molecular structure/identity mappings.
Other malformed headers are failures, not guessed schemas. Missing upstream release and
acquisition date remain unknown. Local file coverage is not upstream completeness.

Both commands write `manifest.json` with `outputs` (absolute file paths), hashes,
`summary`, `checks`, `limitations`, and `coverage` for `Runtime.submit`. The manifest
is a handoff, not independent acceptance. CLI exits 2 for blocked curation or errors;
limited collection still emits its complete file inventory with explicit gaps.

## Contract

The versioned research contract must contain `data_curation`:

```json
{
  "data_curation": {
    "chemistry_policy": "single_component",
    "relation_policy": "reported_numeric",
    "replicate_policy": "preserve_flag",
    "tasks": [
      {"id": "mouse_oral_ld50", "species": "mouse", "route": "oral", "endpoint": "LD50"}
    ],
    "sources": [
      {
        "source_id": "ntp_ice_oral/Acute_Oral_Toxicity.xlsx",
        "adapter": "ice_oral",
        "required": true,
        "sheet": "Data",
        "constants": {"route": "oral"},
        "allowed_test_materials": ["Chemical"]
      }
    ],
    "identity_sources": [
      {
        "source_id": "support/DSSTox_Identifiers_and_CASRN_2021r1.csv",
        "columns": {"dtxsid": "dtxsid", "cas": "casrn", "inchi": "inchi"}
      }
    ]
  }
}
```

This is a schema example, not an approved scientific policy. Every source required
by the research scope must be explicitly listed. Unsupported required sources block
the package. `required` defaults true; optional unsupported sources leave it limited.
Inventory files not selected by `sources` remain listed as
`unselected_inventory_sources`, without being called scientifically irrelevant.
The independent scope reviewer must reconcile these against the research contract.
Support/receipt files are normally unselected measurement sources, but are bound
through `identity_sources` and `unit_evidence` respectively.

Measurement adapters: `ice_oral`, `ice_inhalation`, `oecd`, `toxric`, `npass`,
`toxval`, `openfoodtox`, `hsdb`. Relational adapters load
`scripts/qsar_native_sources.py`; archive adapters load
`scripts/qsar_archive_sources.py`. Include all three Python files in runtime code
dependencies and hash bindings.
Native mappings are in `ADAPTERS`; `columns` may explicitly map canonical field names
to native columns. `sheet` and `header_row` can select an explicit native table.
Species/route/endpoint match case-insensitively with no inferred aliases. Constant
strata must be justified by source evidence and cannot override contradictory native
values. Constants cannot invent values, relations or identities. ICE requires an
explicit `allowed_test_materials` list; native `Mixture`, other test-material fields,
duration and assay conditions remain in each complete `native_record`.
Only LD50 mass/molar target conversion is implemented. Inhalation LC50 is inventoried
and preserved in dispositions but not relabeled LD50; concentration-time conversion
requires a separate reviewed adapter. NPASS route is generally missing: do not set a
constant route without source-specific evidence. OFT joins HumanHealth studies to
SUB and REF_SUB using native UUIDs, preserving an unnamed first index column and
all candidate join rows. Missing/ambiguous joins, multiple effect blocks, ranges
and upper-only limits are quarantined with all native bounds preserved. Neither
interval endpoint becomes an exact label. OFT requires explicit
`allowed_test_materials`, including native composition strings.

ToxVal preserves the normalized numeric/unit pair, both original fields and both
qualifiers; it never pairs a normalized number with an original unit. Contradictory
qualifiers quarantine the row. A normalized `=` with absent original qualifier
retains raw `=` but receives `normalized_relation=unknown`: it fails exact-only
curation and can only be an explicitly permitted reported numeric estimate.

ToxVal curation first joins `chemical_id` exactly to native `source_chemical`,
preserving all matches and quarantining ambiguous/conflicting mappings. Native CAS
or DTXSID can recover an otherwise missing identifier. For selected retained IDs,
`record_source` is joined by `toxval_id`, preserving all references, quality and
QA labels; `toxval_relationship` edges touching selected records become flags,
never automatic aggregation/drop instructions. Native QC failures remain visible;
they are not silently treated as scientific rejection or acceptance. The manifest's
`metadata_coverage` reports join coverage. Other SQL metadata/source tables remain
separately inventoried gaps until their relevance is explicitly reviewed. Unsupported
selected-table SQL operations fail closed rather than silently producing zero rows.
Statement boundaries are resolved before table filtering, including multiple
statements on one physical line; quoted/comment semicolons are not boundaries.

HSDB curation inventories every `DOC`, then accounts every `ntxv` field and every
other narrative field mentioning LD50. Documents without such fields receive an
explicit disposition. The anchored scalar parser accepts only one explicitly
reported rat/rats LD50 expression with an explicit `oral`, `intragastric`, `gavage`,
`ingestion`, or `po` route token, a supported literal unit and reference. Native
route tokens are preserved; mapping a variant to an oral task requires an
evidence-bound `exact_route_aliases` entry in the contract. Mixed routes remain
quarantined.
Raw DOCNO, CAS candidates, tag/field ordinals, complete narrative/XML, reference,
sex notes and document metadata remain traceable. Missing qualifiers stay unknown;
inequalities stay censored. Ranges, multiple observations, other strata and unmatched
narratives are accounted and quarantined/excluded, not assigned guessed numeric
labels. Parser coverage does not mean every potentially recoverable narrative has
been scientifically resolved. Whole native documents remain in the collection.

ChemID is an identity-support adapter, configured separately:

```json
{
  "identity_sources": [
    {"source_id": "chemidplus_archive/chemid-20230222.zip", "adapter": "chemid_identity"}
  ]
}
```

It joins XML `Chemical.id`/CAS to native `SUPERID` SMILES/InChI records, preserving
all candidates, ambiguity, orphan IDs and measurement-bearing notes. It does not
declare the archive scientifically irrelevant. All parseable chemical candidates
enter exact identity comparison, and conflicting candidates cannot be resolved by
first-row choice. Invalid candidates block affected mappings. The
`identity_support_ledger.csv` records unresolved support evidence.
An absent optional structure is recorded as missing support and does not override
a valid direct structure or exact recovery from another source. An explicit
malformed candidate or ambiguous CAS mapping remains a blocking identity finding.

Source-specific unit aliases and OFT administrative route mappings require exact
keys and evidence in the contract. For example (illustrative evidence only):

```json
{
  "exact_unit_aliases": {
    "mg/kg bw": {"unit": "mg/kg", "evidence": "Reviewed body-weight unit definition"}
  },
  "administrative_endpoint_routes": {
    "acute toxicity: oral": {"route": "oral", "evidence": "Reviewed native endpoint definition"}
  },
  "exact_route_aliases": {
    "oral: unspecified": {"route": "oral", "evidence": "Reviewed native route definition"},
    "oral: gavage": {"route": "oral", "evidence": "Reviewed native route definition"}
  },
  "exact_species_aliases": {
    "Rattus norvegicus": {"species": "rat", "evidence": "Reviewed taxonomy citation"}
  }
}
```

These mappings do not use substrings or overwrite contradictory route fields.
Exact route aliases apply before checking administrative endpoint consistency.
The original species/route remain `raw_species`/`raw_route`.
For OFT, literal native `Unit='other:'` selects its explicit `Unit.Other` detail
only when present, preserving both fields as `native_unit_parent` and
`native_unit_other`. It is never globally mapped to mg/kg. Repeated-dose day/week
units remain unsupported by the dose conversion dictionary.
The selected native unit remains `raw_unit`; conversion uses `normalized_unit`.
Native lineage, effect blocks and source qualifier provenance accompany outputs.

Explicit native-field scope decisions can be configured without a hardcoded policy:

```json
{
  "native_field_filters": {
    "study_type": {"allowed_values": ["acute"], "evidence": "Reviewed acute-only contract scope"}
  }
}
```

Each filter matches an exact native value and leaves a named row disposition.
Without such a declared rule, the producer preserves source categories and flags
instead of inventing exclusions. These example evidence strings must be replaced
by the actual reviewed evidence/contract decision.

TOXRIC additionally requires:

```json
{
  "unit_evidence": {
    "receipt_source_id": "toxric_download_20261021/receipt.json",
    "unit": "mg/kg",
    "citation": "https://doi.org/10.1093/nar/gkac1074"
  }
}
```

The registry-bound receipt must state `original-value` in `scope`, and uniquely bind
the CSV filename and SHA-256 plus download URL in `files`. The citation must be
independently reviewed; this adapter does not verify publication content from a URL.
This unit evidence does not establish equality qualifiers. Dimensionless exports
are not supported by this mapping.

## Identity, units, qualifiers and outputs

Identity support maps exact DTXSID, checksum-valid CAS or NPASS IDs to complete
canonical structures. DSSTox `inchi` can recover native structure directly; no
arbitrary MSReady/CAS deduplication or unspecified join is used. Repeated identical
candidates retain their evidence. Multiple distinct candidates or contradiction
with direct structure quarantine the row. Invalid CAS is flagged and never used for
recovery; a valid direct source structure can still survive. Unresolvable records
remain in the identity and disposition ledgers.

`single_component` rejects disconnected structures. `largest_organic` accepts only
one organic fragment and rejects ambiguous multiple-organic mixtures rather than
choosing one by size. It preserves original structure/MW and parent structure/MW.
Mass-to-molar conversion uses original molecular weight. No neutralization,
tautomerization or stereochemistry removal is applied to model structures.
Connectivity grouping ignores stereo/isotope annotations conservatively for split
safety; salts resolving to the same parent share a group. No tautomer-equivalence
guarantee is made.

Only exact dictionary units are converted: mg/kg, ug/kg (including micro symbols),
ng/kg, g/kg, mol/kg, mmol/kg, umol/kg (micro symbols), nmol/kg. No substrings,
concentration conversions or numeric-range unit inference. Values must be finite
and positive to support the logarithmic target; overflow/underflow is excluded.

`exact_only` accepts explicit `=`/`==`. `reported_numeric` also accepts a blank
modifier, preserving `normalized_relation=unknown` and
`target_kind=reported_numeric_estimate`. Unknown nonblank modifiers and censored
relations are excluded under both policies. Missing qualifiers never become `=`.

`curated.csv` exports all eligible measurements (no cap) with targets
`target_mg_kg`, `target_mol_kg`, `target_pLD50`, `task_id`, `group_id`, structures,
original MW, units/relations, references, native identity and full native record.
`target_pLD50` is minus log10 mol/kg. `dispositions.csv` has one terminal disposition
for every parsed measurement row and explicit output memberships for overlapping
tasks. `identity_ledger.csv` retains match evidence and flags.
`conflict_ledger.csv` flags potential within-task parent replicates and duplicate
lineage; all records remain, without means, arbitrary deduplication or claims that
different assay conditions are equivalent. `curation_manifest.json` binds policies,
inputs, counts, zero-row tasks, unsupported sources and scientific limitations.
Candidate tables with unresolved broader source scope are diagnostic artifacts,
not full-scope scientifically accepted datasets.
