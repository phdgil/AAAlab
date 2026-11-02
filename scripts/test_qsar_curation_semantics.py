"""Source-derived eligibility/value regression tests; synthetic evidence only."""
import csv
import gzip
import json
import math
from pathlib import Path
import unittest
from scripts import test_qsar_curation_evidence as fixtures
from scripts import test_qsar_native_sources as native

data, evidence = fixtures.data, fixtures.evidence


class CurationSemanticsTests(unittest.TestCase):
    def fixture(self, adapter='oecd'):
        fixture = fixtures.CoverageTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if adapter.startswith('ice_'):
            fixture.config['data_curation']['sources'][0].update(adapter=adapter, sheet='',
                allowed_test_materials=[''], columns=dict(
                    {k: v for k, v in data.ADAPTERS['oecd'].items() if k not in {'reference', 'source_url', 'sex'}},
                    relation='qualifier'))
        return fixture

    def exclude(self, fixture, reason):
        fixture.rewrite_csv('dispositions.csv', lambda rows: rows[0].update(
            disposition='excluded', reasons=json.dumps([reason]), output_row_ids='[]'))
        fixture.rewrite_csv('curated.csv', lambda rows: rows.pop(0))
        manifest = data.load(fixture.manifest)
        manifest.update(output_rows=11, groups=11, dispositions={'retained': 11, 'excluded': 1}, task_counts={'rat': 11})
        data.dump(fixture.manifest, manifest)

    def test_public_existing_reasons_cannot_exclude_eligible_native_rows(self):
        for adapter in ('oecd', 'ice_oral', 'ice_inhalation'):
            for reason in ('nonexact_or_unknown_relation', 'unsupported_or_unknown_unit', 'unresolved_identity'):
                with self.subTest(adapter=adapter, reason=reason):
                    fixture = self.fixture(adapter)
                    runtime = fixture.runtime_curated(lambda: self.exclude(fixture, reason))
                    with self.assertRaises(fixtures.GateError): runtime.accept('curation')

    def test_public_coherent_target_and_representation_forgery(self):
        for adapter in ('oecd', 'ice_oral'):
            for attack in ('target', 'representation'):
                with self.subTest(adapter=adapter, attack=attack):
                    fixture = self.fixture(adapter)
                    def mutate():
                        def change(rows):
                            if attack == 'target':
                                rows[0]['target_mg_kg'] = '999999'
                                rows[0]['target_mol_kg'] = str(float(rows[0]['target_mol_kg']) * 999999 / 100)
                                rows[0]['target_pLD50'] = str(float(rows[0]['target_pLD50']) - math.log10(999999 / 100))
                            else:
                                rows[0]['parent_smiles'] = 'N'
                        fixture.rewrite_csv('curated.csv', change)
                    runtime = fixture.runtime_curated(mutate)
                    with self.assertRaises(fixtures.GateError): runtime.accept('curation')

    def check_attacks(self, fixture, check=None):
        check = check or fixture.check
        check()
        path = fixture.out / 'curated.csv'
        original = path.read_bytes()
        for change in (lambda rows: rows[0].update(target_mg_kg='999999'),
                       lambda rows: rows[0].update(parent_smiles='N'),
                       lambda rows: rows[0].update(raw_value='999999'),
                       lambda rows: rows[0].update(species='invented'),
                       lambda rows: rows[0].update(flags='["invented_flag"]'),
                       lambda rows: rows[0].update(replicate_flag=str(rows[0]['replicate_flag'] != 'True')),
                       lambda rows: rows.pop(), lambda rows: rows.append(dict(rows[0]))):
            fixture.rewrite_csv('curated.csv', change)
            with self.assertRaises(evidence.EvidenceError): check()
            path.write_bytes(original)
            fixture.rebind_output('curated.csv')

    def test_native_sql_reconstruction(self):
        fixture = self.fixture()
        native_fixture = native.NativeSourcesTests()
        native_fixture.setUp()
        self.addCleanup(native_fixture.tearDown)
        native_fixture.root = fixture.raw
        source = native_fixture.toxval_sql('=', '=')
        with gzip.open(source, 'rt', encoding='utf-8') as stream:
            sql = stream.read().replace('CREATE TABLE `toxval` (', 'CREATE TABLE `toxval` (`chemical_id` text,')
        sql = sql.replace('INSERT INTO `toxval` VALUES (', "INSERT INTO `toxval` VALUES ('missing',")
        with gzip.open(source, 'wt', encoding='utf-8') as stream:
            stream.write(sql + 'CREATE TABLE `source_chemical` (`chemical_id` text,`casrn` text,`dtxsid` text);\n'
                         'CREATE TABLE `record_source` (`toxval_id` text,`long_ref` text);\n'
                         'CREATE TABLE `toxval_relationship` (`toxval_id_1` text,`toxval_id_2` text,`relationship` text);\n')
        support = fixture.raw / 'identity.csv'
        support.write_text('dtxsid,smiles\nDTX1,CCO\n', encoding='utf-8')
        fixture.config['data_curation'].update(sources=[{'source_id': source.name, 'adapter': 'toxval'}],
            identity_sources=[{'source_id': support.name, 'columns': {'dtxsid': 'dtxsid', 'smiles': 'smiles'}}])
        fixture.config['data_curation']['tasks'][0]['species'] = 'mouse'
        manifest = fixture.generate()
        self.assertEqual(manifest['output_rows'], 1)
        # Native SQL still has unreviewed collection tables. Do not waive that
        # prerequisite to exercise the new source-derived semantic check itself.
        with self.assertRaisesRegex(evidence.EvidenceError, 'required collection source'):
            fixture.check()
        def semantics():
            manifest = data.load(fixture.manifest)
            entries = {e['source_id']: e for e in data.load(fixture.collection / 'source_registry.json')['files']}
            outputs = {name: Path(item['path']) for name, item in manifest['bound_evidence']['outputs'].items()}
            evidence._curation_semantics(manifest, outputs, entries, {}, lambda item: Path(evidence.checked(item)['path']))
        self.check_attacks(fixture, semantics)

    def test_native_oft_reconstruction(self):
        fixture = self.fixture()
        native_fixture = native.NativeSourcesTests()
        native_fixture.setUp()
        self.addCleanup(native_fixture.tearDown)
        native_fixture.root = fixture.raw
        source = native_fixture.workbook([native_fixture.study(lq='=')])
        fixture.config['data_curation']['sources'] = [{'source_id': source.name, 'adapter': 'openfoodtox',
            'allowed_test_materials': ['mono-constituent substance'],
            'exact_unit_aliases': {'mg/kg bw': {'unit': 'mg/kg', 'evidence': 'Synthetic body-weight unit dictionary'}}}]
        fixture.config['data_curation']['tasks'][0]['species'] = 'mouse'
        manifest = fixture.generate()
        self.assertEqual(manifest['output_rows'], 1)
        self.check_attacks(fixture)

    def test_npass_identity_is_rebuilt_from_native_support(self):
        fixture = self.fixture()
        rows = [dict(np_id='NPC1', assay_organism='rat', activity_type='LD50', activity_value=100 + i,
                     activity_units='mg/kg', activity_relation='=', ref_id='synthetic') for i in range(12)]
        source = fixture.raw / 'npass-labels.csv'
        data.write_csv(source, rows, list(rows[0]))
        fixture.config['data_curation']['sources'] = [{'source_id': source.name, 'adapter': 'npass',
                                                       'constants': {'route': 'oral'}}]
        manifest = fixture.npass_support_fixture()
        self.assertEqual(manifest['output_rows'], 12)
        self.check_attacks(fixture)

    def test_toxric_original_value_receipt_reconstruction(self):
        fixture = self.fixture()
        source = fixture.raw / 'toxric.csv'
        rows = [{'TAID': 'T1', 'Canonical SMILES': 'CCO', 'Toxicity Value': '20'}]
        data.write_csv(source, rows, list(rows[0]))
        data.dump(fixture.raw / 'receipt.json', {'scope': 'original-value synthetic download',
            'files': [{'filename': source.name, 'sha256': data.digest(source), 'url': 'https://example.test/original'}]})
        fixture.config['data_curation'].update(relation_policy='reported_numeric', sources=[{
            'source_id': source.name, 'adapter': 'toxric', 'constants': {'species': 'rat', 'route': 'oral', 'endpoint': 'LD50'},
            'unit_evidence': {'unit': 'mg/kg', 'citation': 'Synthetic original-value receipt', 'receipt_source_id': 'receipt.json'}}])
        manifest = fixture.generate()
        self.assertEqual(manifest['output_rows'], 1)
        self.check_attacks(fixture)


if __name__ == '__main__':
    unittest.main()
