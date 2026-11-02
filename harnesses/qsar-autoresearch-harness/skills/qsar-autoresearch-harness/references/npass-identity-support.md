# NPASS structure identity support v1

`npass_structure_v1` is an opt-in identity-source adapter for a new, independently reviewed contract/run version. It does not amend an accepted contract or make NPASS measurements eligible.

Draft configuration fragment (append to a NEW contract's `data_curation.identity_sources`):

```json
{
  "source_id": "npass_toxicity/NPASS3.0_naturalproducts_structure.txt",
  "adapter": "npass_structure_v1"
}
```

The registry must bind the complete native UTF-8 tab-delimited file and SHA-256. The adapter verifies the hash before and after reading all rows. It requires unique, nonblank headers including `np_id`, `SMILES`, `InChI`, and `InChIKey`; field-count errors fail the import. No column mapping, header skip, sheet, or row-limit overrides are accepted. The native `np_id` maps to the existing exact `npid` resolver; there is no identifier rename or InChIKey lookup bridge.

Each physical row preserves its original record, source/hash, row locator, native full InChIKey, generated full key, canonical original structure, reasons, status and blocked-key state in the support ledger. SMILES and InChI must both be present and satisfy the unchanged declared chemistry policy. Their exact isomeric canonical structures must agree. The generated original-material full InChIKey must equal the native key. Missing/invalid pairs, representation disagreements, unsupported chemistry and native-key disagreements quarantine that NPID while unrelated NPIDs continue. No SMILES preference, InChI fallback, stereochemical/tautomer equivalence or external bridge is introduced.

Identical duplicate candidates preserve every physical-row lineage. Different canonical structures or full native keys under the same NPID block that NPID. Any invalid duplicate also poisons the key, even with another clean candidate; earlier rows are marked quarantined during ledger replay. Blank NPIDs are accounted as quarantined rows without a usable key. `candidate` means identity support only, never scientific acceptance. The intended scope is one NPASS structure file; conflicting duplicate NPASS configurations across files must fail evidence validation rather than enter an accepted package.

The collection-evidence guard must explicitly reconcile this adapter's physical rows and dispositions; unsupported adapters are not exempt from guard validation. Generalinfo is not a dependency because its key repeats the structure file's native key and does not resolve disagreements.

NPASS toxicity supplies no route. Recovering identity does not infer oral/acute administration, change qualifiers/units, or resolve generic `ToxVal` references. The 2,545 rat LD50 equality mg/kg records require separate source-record linkage and route adjudication. Unresolved structure cases require separate reviewed adjudication. Do not edit accepted v3, frozen code, original source files, or downstream scientific state to activate this example.

Focused regressions: `python -m unittest discover -s scripts -p test_qsar_npass_support.py`. Fixtures `scripts/fixtures/npass-row19.json` and `npass-row37.json` preserve exact native source rows 19 (NPC469815 conflict) and 37 (NPC259665 valid); fixture filenames describe source provenance, while tests retain their new fixture-file physical row locators.
