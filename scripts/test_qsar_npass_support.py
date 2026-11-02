"""NPASS-format support regressions using independently authored synthetic records."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from rdkit import Chem

SCRIPT = Path(__file__).resolve().parents[1] / 'harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts/qsar_data.py'
spec = importlib.util.spec_from_file_location('npass_data', SCRIPT)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

class NPASSTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'structure.txt'
        self.policy = {'chemistry_policy': 'largest_organic', 'identity_sources': [{'source_id': 'structure', 'adapter': 'npass_structure_v1'}]}
    def tearDown(self):
        self.tmp.cleanup()
    def row(self, npid='NPC1', smiles='CCO', **changes):
        mol = Chem.MolFromSmiles(smiles)
        row = dict(np_id=npid, SMILES=smiles, InChI=Chem.MolToInchi(mol), InChIKey=Chem.MolToInchiKey(mol))
        row.update(changes)
        return row
    def write(self, records):
        with self.path.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(records[0]), delimiter='\t')
            w.writeheader(); w.writerows(records)
        self.registry = {'files': [{'source_id': 'structure', 'path': str(self.path), 'sha256': q.digest(self.path)}]}
    def run_index(self):
        self.ledger, self.blocked = [], {}
        return q.identity_index(self.policy, self.registry, self.ledger, self.blocked)
    def resolve(self, index, npid):
        mapped = dict.fromkeys(q.FIELDS, ''); mapped['npid'] = npid
        return q.resolve_identity(mapped, index, 'largest_organic', self.blocked)
    def test_synthetic_stereo_conflict_does_not_abort_later_valid(self):
        # Synthetic mismatch: InChI specifies chirality while SMILES omits it.
        conflicting = __import__('json').loads((Path(__file__).parent / 'fixtures/npass-row19.json').read_text())
        later = __import__('json').loads((Path(__file__).parent / 'fixtures/npass-row37.json').read_text())
        self.write([conflicting, later]); index = self.run_index()
        self.assertEqual(self.resolve(index, conflicting['np_id'])[1], 'unresolved_support_identity')
        self.assertEqual(self.resolve(index, later['np_id'])[1], 'exact_identifier_mapping')
        self.assertIn('conflicting_structure_representations', self.ledger[0]['reasons'])
        self.assertEqual(self.ledger[0]['native_record'], conflicting)
        self.assertEqual(self.ledger[1]['native_row'], 3)
        self.assertEqual(self.ledger[1]['sha256'], self.registry['files'][0]['sha256'])
    def test_row_local_invalid_missing_key_multiorganic(self):
        records = [self.row('invalid', SMILES='invalid'), self.row('invalid_inchi', InChI='invalid'),
                   self.row('missing', SMILES='', InChI=''), self.row('missing_inchi', InChI=''),
                   self.row('missing_smiles', SMILES=''), self.row('missing_key', InChIKey=''),
                   self.row('key', InChIKey='AAAAAAAAAAAAAA-BBBBBBBBBB-C'),
                   self.row('multi', 'CCO.C'), self.row('valid')]
        self.write(records); index = self.run_index()
        self.assertEqual(len(self.ledger), len(records))
        for row in records[:-1]: self.assertEqual(self.resolve(index, row['np_id'])[1], 'unresolved_support_identity')
        self.assertEqual(self.resolve(index, 'valid')[1], 'exact_identifier_mapping')
    def test_duplicates_and_poisoned_clean_candidate(self):
        self.write([self.row('same'), self.row('same'), self.row('conflict'), self.row('conflict', 'C'), self.row('poison'), self.row('poison', SMILES='invalid')])
        index = self.run_index()
        self.assertEqual(len(index[('npid', 'same')]['CCO']), 2)
        self.assertEqual(self.resolve(index, 'same')[1], 'exact_identifier_mapping')
        for key in ('conflict', 'poison'):
            self.assertEqual(self.resolve(index, key)[1], 'unresolved_support_identity')
        self.assertTrue(all(r['status'] == 'quarantined' for r in self.ledger[2:]))
    def test_poison_blocks_other_source(self):
        self.write([self.row('poison', SMILES='invalid')])
        clean = self.path.with_name('clean.csv'); clean.write_text('npid,smiles\npoison,CCO\n')
        self.registry['files'].append({'source_id':'clean','path':str(clean),'sha256':q.digest(clean)})
        self.policy['identity_sources'].append({'source_id':'clean','columns':{'npid':'npid','smiles':'smiles'}})
        self.assertEqual(self.resolve(self.run_index(), 'poison')[1], 'unresolved_support_identity')
    def test_schema_and_integrity_fail_closed(self):
        self.write([self.row()]); self.path.write_text('np_id\n')
        with self.assertRaisesRegex(q.DataError, 'digest mismatch'): self.run_index()
        self.registry['files'][0]['sha256'] = q.digest(self.path)
        with self.assertRaisesRegex(q.DataError, 'NPASS.*header'): self.run_index()
        self.write([self.row()])
        with patch.object(q, 'digest', side_effect=[self.registry['files'][0]['sha256'], 'changed']):
            with self.assertRaisesRegex(q.DataError, 'changed'): self.run_index()
    def test_malformed_rows_headers_and_overrides_fail_closed(self):
        for content in ('np_id\tSMILES\tInChI\tInChIKey\nNPC1\tCCO\n',
                        'np_id\tSMILES\tInChI\tInChIKey\tSMILES\n',
                        'np_id\tSMILES\tInChI\tInChIKey\nNPC1\tCCO\tI\tK\textra\n'):
            self.write([self.row()]); self.path.write_text(content)
            self.registry['files'][0]['sha256'] = q.digest(self.path)
            with self.assertRaises(q.DataError): self.run_index()
        self.write([self.row()]); self.policy['identity_sources'][0]['header_row'] = 2
        with self.assertRaisesRegex(q.DataError, 'overrides'): self.run_index()
    def test_missing_npid_is_accounted_without_index(self):
        self.write([self.row('')]); index = self.run_index()
        self.assertFalse(index)
        self.assertEqual(self.ledger[0]['reasons'], ['missing_npid'])
        self.assertFalse(self.ledger[0]['blocked_key'])
    def test_missing_route_stays_missing(self):
        self.write([self.row()]); index=self.run_index()
        native={'np_id':'NPC1','activity_type':'LD50','assay_organism':'Rattus norvegicus','activity_value':'100','activity_units':'mg/kg','activity_relation':'='}
        mapped={f:q.text(native.get(q.ADAPTERS['npass'].get(f,''))) for f in q.FIELDS}
        self.assertEqual(q.resolve_identity(mapped,index,'largest_organic',self.blocked)[1], 'exact_identifier_mapping')
        self.assertEqual(mapped['route'], '')
        toxicity = self.path.with_name('toxicity.txt')
        with toxicity.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(native), delimiter='\t')
            writer.writeheader()
            writer.writerow(native)
        self.registry['files'].append({'source_id': 'toxicity', 'path': str(toxicity), 'sha256': q.digest(toxicity)})
        self.policy.update(relation_policy='exact_only', replicate_policy='preserve_flag',
                           sources=[{'source_id': 'toxicity', 'adapter': 'npass'}],
                           tasks=[{'id': 'rat_oral', 'species': 'Rattus norvegicus', 'route': 'oral', 'endpoint': 'LD50'}])
        base = self.path.parent
        q.dump(base / 'contract.json', {'version': 'fixture', 'data_curation': self.policy})
        q.dump(base / 'registry.json', self.registry)
        q.curate(base / 'contract.json', base / 'registry.json', base / 'out')
        with (base / 'out/dispositions.csv').open(encoding='utf-8') as stream:
            disposition = next(csv.DictReader(stream))
        self.assertIn('outside_declared_task_or_missing_stratum', disposition['reasons'])
        with (base / 'out/curated.csv').open(encoding='utf-8') as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])

if __name__ == '__main__': unittest.main()
