# Synthetic QSAR regression fixtures

These records were independently authored for software tests. They are not NPASS
database extracts, biological observations, or toxicity evidence. Their `np_id`
values are synthetic and are not database identifiers.

- `npass-row19.json`: constructed SMILES `C[C@H](F)Cl` was converted locally to
  InChI and InChIKey with RDKit. The submitted SMILES deliberately omits chirality
  (`CC(F)Cl`), exercising disagreement between structure representations.
- `npass-row37.json`: simple SMILES `CCO` was converted locally to matching InChI
  and InChIKey. This exercises indexing a valid record after a conflicting row.

The filenames remain for fixture compatibility; their numbers do not identify
source rows. No source document, activity measurement, citation, or natural-product
record is bundled. Identifiers were generated using `Chem.MolToInchi` and
`Chem.MolToInchiKey` on the structures above. Tests use the NPASS column schema
and verify quarantine, continued indexing, and exact input/hash lineage.

These independently authored fixtures follow the repository Apache-2.0 license.
