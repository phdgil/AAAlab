"""Synthetic material integration mechanics; never scientific approval."""
import copy
import sys
from pathlib import Path
import unittest
import json
import tempfile
import csv
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts'
sys.path.insert(0, str(SCRIPTS))
import qsar_data as data
import qsar_material_curation as bridge
import qsar_verified_material as material
from scripts import test_qsar_verified_material as material_fixtures
from scripts import test_qsar_curation_evidence as curation_fixtures
from scripts import test_qsar_native_sources as native_fixtures
from scripts import test_qsar_archive_sources as archive_fixtures
from scripts import test_qsar_candidate_integration as candidate_fixtures


class PreIdentityRegressionTests(unittest.TestCase):
    def fixture(self):
        raw = dict(source_record_id='one', casrn='', species='rat', route='oral',
                   endpoint_type='LD50', numeric_value='100', numeric_unit='mg/kg',
                   smiles='CCO', qualifier='=')
        config = dict(source_id='synthetic', adapter='oecd', columns={'relation': 'qualifier'})
        policy = dict(tasks=[dict(id='rat', species='rat', route='oral', endpoint='LD50')],
                      chemistry_policy='single_component', relation_policy='exact_only')
        return raw, config, policy, dict(path='synthetic.csv', sha256='a' * 64)

    def test_ordinary_observation_regression(self):
        for changes, reasons, count in [({}, [], 1), ({'route': 'dermal'}, ['outside_declared_task_or_missing_stratum'], 0),
                ({'qualifier': '<'}, ['nonexact_or_unknown_relation'], 0),
                ({'numeric_value': 'NaN'}, ['invalid_positive_numeric_value'], 0),
                ({'numeric_unit': 'unsupported'}, ['unsupported_or_unknown_unit'], 0)]:
            raw, config, policy, entry = self.fixture()
            raw.update(changes)
            disposition, identity, rows = data.curate_observation(raw, config, policy, entry, 2, '', {}, {})
            self.assertEqual(disposition['reasons'], reasons)
            self.assertEqual(len(rows), count)
            self.assertEqual(identity['status'], 'direct_source')
            if rows:
                self.assertEqual(rows[0]['target_mg_kg'], 100)
                self.assertEqual(rows[0]['output_row_id'], 'synthetic::2:rat')

    def test_preidentity_never_calls_chemistry(self):
        raw, config, policy, entry = self.fixture()
        raw['smiles'] = 'not a graph'
        with patch.object(data, 'chemistry', side_effect=AssertionError('chemistry called')):
            pre = data.pre_identity_observation(raw, config, policy, entry, 2, '')
        self.assertTrue(pre.requires_classification)
        self.assertEqual(pre.applicable_task_ids, ('rat',))
        raw['route'] = 'dermal'
        pre = data.pre_identity_observation(raw, config, policy, entry, 2, '')
        self.assertFalse(pre.requires_classification)
        self.assertEqual(pre.nonmaterial_reasons, ('outside_declared_task_or_missing_stratum',))

    def test_unsupported_integration_filter_rejects(self):
        raw, config, policy, entry = self.fixture()
        config['native_field_filters'] = {'smiles': {'allowed_values': ['CCO'], 'evidence': 'synthetic'}}
        with self.assertRaisesRegex(data.DataError, 'Unsupported'):
            data.pre_identity_observation(raw, config, policy, entry, 2, '')


class NativeMaterialTests(unittest.TestCase):
    def test_npass_missing_key_column_keeps_native_validator_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'support.tsv'
            path.write_text('np_id\tSMILES\tInChI\nNPC1\t[Na+].[Cl-]\tInChI=1S/ClH.Na/h1H;/q;+1/p-1\n', encoding='utf-8')
            entry = dict(source_id='support.tsv', **material.file_binding(path))
            policy = dict(chemistry_policy='single_component', identity_sources=[dict(source_id='support.tsv', adapter='npass_structure_v1')])
            for observer in (None, lambda raw: None):
                with self.assertRaisesRegex(data.DataError, 'NPASS structure required header missing'):
                    data.identity_index(policy, dict(files=[entry]), raw_support_observer=observer)

    def test_npass_native_identifier_errors_survive_raw_support(self):
        from rdkit import Chem
        graph = '[Na+].[Cl-]'
        inchi = Chem.MolToInchi(Chem.MolFromSmiles(graph))
        correct = Chem.MolToInchiKey(Chem.MolFromSmiles(graph))
        mismatch = Chem.MolToInchiKey(Chem.MolFromSmiles('CCO'))
        for key, reason in [('INVALID', 'missing_or_invalid_native_inchikey'),
                            ('', 'missing_or_invalid_native_inchikey'),
                            (mismatch, 'native_inchikey_disagreement'), (correct, None)]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'support.tsv'
                path.write_text(f'np_id\tSMILES\tInChI\tInChIKey\nNPC1\t{graph}\t{inchi}\t{key}\n', encoding='utf-8')
                entry = dict(source_id='support.tsv', **material.file_binding(path))
                policy = dict(chemistry_policy='single_component', identity_sources=[dict(source_id='support.tsv', adapter='npass_structure_v1')])
                observed, blocked = [], {}
                data.identity_index(policy, dict(files=[entry]), blocked_keys=blocked, raw_support_observer=observed.append)
                raw, config, observation_policy, observation_entry = PreIdentityRegressionTests().fixture()
                raw['npid'] = 'NPC1'
                config['columns']['npid'] = 'npid'
                pre = data.pre_identity_observation(raw, config, observation_policy, observation_entry, 2, '')
                store = bridge.NativeReplayStore(root)
                try:
                    store.observe_support(observed[0])
                    linked = store.supports(pre)
                finally:
                    store.close()
                self.assertNotIn('multiple_components', observed[0]['native_reasons'])
                if reason:
                    self.assertIn(reason, observed[0]['native_reasons'])
                    self.assertTrue(any(reason in b['original_reasons'] and b['reason'] != 'material_identity_conflict'
                                        for b in linked['blocked_support']))
                else:
                    self.assertEqual(observed[0]['native_reasons'], [])
                    self.assertEqual(linked['blocked_support'], [])

    def observation(self, smiles='[Na+].[Cl-]', inchi=None, include_smiles=True):
        fixture = PreIdentityRegressionTests()
        raw, config, policy, entry = fixture.fixture()
        if include_smiles:
            raw['smiles'] = smiles
        else:
            raw.pop('smiles')
        if inchi is not None:
            raw['inchi'] = inchi
            config['columns']['inchi'] = 'inchi'
        return bridge.observation(raw, config, entry, 2, '')

    def test_literal_inchi_and_absent_smiles(self):
        inchi = 'InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3'
        obs = self.observation(inchi=inchi, include_smiles=False)
        reps = obs['body']['representations']
        self.assertIsNone(reps['smiles'])
        self.assertEqual(reps['inchi']['value'], inchi)
        self.assertEqual(bridge.graph_diagnostics(reps)['input_graph_smiles'], 'CCO')

    def test_malformed_representation_does_not_fallback(self):
        obs = self.observation('bad(', 'InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3')
        diag = bridge.graph_diagnostics(obs['body']['representations'])
        self.assertEqual(diag['agreement'], 'invalid')
        self.assertIsNone(diag['input_graph_smiles'])
        obs = self.observation('CC', 'InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3')
        self.assertEqual(bridge.graph_diagnostics(obs['body']['representations'])['agreement'], 'conflict')

    def test_null_blank_absent_and_physical_siblings(self):
        missing = self.observation(include_smiles=False)
        blank = self.observation('')
        null = self.observation(None)
        self.assertIsNone(missing['body']['representations']['smiles'])
        self.assertEqual(blank['body']['representations']['smiles']['value'], '')
        self.assertIsNone(null['body']['representations']['smiles']['value'])
        sibling = copy.deepcopy(missing)
        sibling['candidate_key'] = 'v2:second'
        self.assertEqual(bridge.slot_id(missing), bridge.slot_id(sibling))
        self.assertNotEqual(bridge.member_key(missing), bridge.member_key(sibling))
        self.assertEqual(len({material.digest(x) for x in (missing, blank, null)}), 3)

    def test_forged_fields_schema_and_coordinates(self):
        obs = self.observation()
        for attack in ('value', 'record', 'extra', 'ordinal'):
            mutated = copy.deepcopy(obs)
            if attack == 'value': mutated['body']['representations']['smiles']['value'] = 'CCO'
            elif attack == 'record': mutated['body']['records'][0]['native_record']['smiles'] = 'CCO'
            elif attack == 'extra': mutated['native_locator']['sha256'] = 'b' * 64
            else: mutated['native_locator']['row'] = True
            with self.subTest(attack=attack), self.assertRaises(material.MaterialError):
                bridge.validate_observation(mutated)
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}'):
            with self.assertRaises(ValueError): bridge.decode(value)

    def test_raw_support_observer_precedes_filtering_and_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'support.csv'
            path.write_text('cas,smiles,inchi\n50-00-0,broken,\n64-17-5,CC,"InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"\n', encoding='utf-8')
            entry = dict(source_id='support', **material.file_binding(path))
            policy = dict(chemistry_policy='single_component', identity_sources=[dict(source_id='support',
                columns=dict(cas='cas', smiles='smiles', inchi='inchi'))])
            observed = []
            with self.assertRaises(data.DataError):
                data.identity_index(policy, {'files': [entry]}, raw_support_observer=observed.append)
            self.assertEqual(len(observed), 2)
            self.assertEqual(observed[0]['representations']['smiles']['value'], 'broken')
            for raw in observed: bridge.validate_raw_support(raw)

    def test_sql_support_only_uses_physical_statement_tuple(self):
        fixture = native_fixtures.NativeSourcesTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        path = fixture.toxval_sql('=', '=')
        raw = next(data.native_sources().iter_toxval(path))
        obs = bridge.observation(raw, dict(source_id='sql', adapter='toxval'), material.file_binding(path), 999, 'toxval')
        self.assertEqual(obs['native_locator']['line'], raw['native_lineage']['line'])
        self.assertEqual(obs['native_locator']['tuple'], raw['native_lineage']['tuple'])
        self.assertNotIn('row', obs['native_locator'])
        self.assertEqual(obs['body']['kind'], 'support_only')
        self.assertEqual(obs['body']['representations'], dict(smiles=None, inchi=None))

    def test_oft_exact_and_ambiguous_joins_preidentity(self):
        fixture = native_fixtures.NativeSourcesTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        config = dict(source_id='oft', adapter='openfoodtox', allowed_test_materials=['mono-constituent substance'],
                      exact_unit_aliases={'mg/kg bw': dict(unit='mg/kg', evidence='synthetic')})
        _, _, policy, _ = PreIdentityRegressionTests().fixture()
        policy['tasks'][0]['species'] = 'mouse'
        for options, kind, representations in [({}, 'joined', True), ({'duplicate_ref': True}, 'support_only', False),
                                               ({'missing_sub': True}, 'support_only', False)]:
            with self.subTest(options=options):
                path = fixture.workbook([fixture.study(lq='=')], **options)
                raw = next(data.native_sources().iter_openfoodtox(path))
                entry = material.file_binding(path)
                obs = bridge.observation(raw, config, entry, 1, 'END_STUDY_REC.HumanHealth')
                self.assertEqual(obs['body']['kind'], kind)
                self.assertEqual(bridge.nonblank(obs['body']['representations']['smiles']), representations)
                pre = data.pre_identity_observation(raw, config, policy, dict(entry, path=str(path)), 1, 'END_STUDY_REC.HumanHealth')
                self.assertTrue(pre.requires_classification)
                if options.get('missing_sub'):
                    self.assertIn('missing_sub_join', pre.identity_reasons)
                    self.assertNotIn('test_material_not_allowed', pre.nonmaterial_reasons)
                if options.get('duplicate_ref'):
                    refs = [r for r in obs['body']['records'] if r['locator']['sheet'] == 'REF_SUB']
                    self.assertEqual(len(refs), 2)

    def test_hsdb_literal_document_support_and_siblings(self):
        fixture = archive_fixtures.ArchiveTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        path = fixture.hsdb('<ntxv>LD50 Rat oral 822 mg/kg</ntxv>')
        raw = next(data.archive_sources().iter_hsdb(path))
        doc = next(data.archive_sources().iter_hsdb_documents(path))
        entry = material.file_binding(path)
        line = doc['native_lineage']
        record = bridge.native_record('hsdb', entry['sha256'], dict(kind='xml_document', archive_member=line['archive_member'],
            document_ordinal=line['document_ordinal'], record_id=doc['record_id']), doc['native_record'], line)
        config = dict(source_id='hsdb', adapter='hsdb')
        obs = bridge.observation(raw, config, entry, 1, 'hsdb_narratives', document_record=record)
        self.assertEqual(obs['body']['kind'], 'support_only')
        self.assertEqual(obs['body']['native_identifiers']['cas'][0]['value'], '64-17-5')
        self.assertEqual(obs['native_locator']['record_id'], '30')
        sibling_raw = dict(raw, candidate_id='synthetic-sibling', record_id='synthetic-sibling')
        sibling = bridge.observation(sibling_raw, config, entry, 2, 'hsdb_narratives', adjudicated=True, document_record=record)
        self.assertEqual(obs['body'], sibling['body'])
        self.assertEqual(bridge.slot_id(obs), bridge.slot_id(sibling))
        self.assertNotEqual(bridge.member_key(obs), bridge.member_key(sibling))

    def test_chemid_raw_complete_candidates_and_orphans(self):
        fixture = archive_fixtures.ArchiveTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        path = fixture.chemid(smiles='X1\tCCO\nX1\tCC\nORPHAN\t[Na+].[Cl-]\n')
        entry = material.file_binding(path)
        rows = list(data.archive_sources().iter_chemid_identity(path))
        supports = [bridge.raw_chemid_support(entry, dict(source_id='chemid', adapter='chemid_identity'), row) for row in rows]
        for support in supports: bridge.validate_raw_support(support)
        self.assertIsNone(supports[0]['representations']['smiles'])
        self.assertEqual(len(supports[0]['records']), 4)
        self.assertTrue(supports[0]['identifiers']['cas'])
        self.assertIn('orphan_structure_superid', supports[-1]['native_reasons'])


class MaterialV2BatchTests(unittest.TestCase):
    def test_illegal_preentry_and_caught_collecting_reentry_cannot_reopen(self):
        batch = self.batch()
        with self.assertRaises(material.MaterialError): batch.finish()
        self.assertEqual(batch.state, 'ABORTED')
        with self.assertRaises(material.MaterialError): batch.__enter__()
        batch = self.batch()
        original = material.load_reviewed_materials
        def reentrant(*args, **kwargs):
            with self.assertRaises(material.MaterialError): batch.__enter__()
            return original(*args, **kwargs)
        with patch.object(material, 'load_reviewed_materials', reentrant):
            with self.assertRaises(material.MaterialError): batch.__enter__()
        self.assertEqual(batch.state, 'ABORTED')
        with self.assertRaises(material.MaterialError): batch.finish()

    def test_active_reentry_aborts_and_cannot_finish(self):
        batch = self.batch()
        batch.__enter__()
        with self.assertRaises(material.MaterialError): batch.__enter__()
        self.assertEqual(batch.state, 'ABORTED')
        batch.abort()
        for operation in (lambda: batch.resolve(self.obs, [], []), batch.finish, batch.__enter__):
            with self.assertRaises(material.MaterialError): operation()
        self.assertEqual(batch.state, 'ABORTED')

    def setUp(self):
        self.fixture = material_fixtures.MaterialTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.obs = NativeMaterialTests().observation()
        self.source = self.fixture.write('native.json', self.obs['candidate_projection'])
        self.obs['source_sha256'] = self.source['sha256']
        # Rebuild literal records after binding the actual synthetic source bytes.
        raw, config, policy, entry = PreIdentityRegressionTests().fixture()
        raw['smiles'] = '[Na+].[Cl-]'
        self.obs = bridge.observation(raw, config, dict(entry, sha256=self.source['sha256']), 2, '')
        self.item = dict(observation=self.obs, support_candidates=[], blocked_support=[],
            source_metadata=dict(source_formula=None, study_id=None, dose_basis_declaration=None))
        facts = material.graph_facts('[Na+].[Cl-]')
        self.decision = dict(schema_version=2, observation_id=material.digest(self.obs), status='verified',
            material_class='inorganic', review_evidence=['SYNTHETIC source review'], reopening_conditions=['source changes'],
            limitations=['SYNTHETIC mechanics only'], critical_conflicts=[], support_resolutions=[],
            dose_basis='reported_material_mass', fixed_composition=True, hydration_interpretation='synthetic anhydrous',
            verified_smiles='[Na+].[Cl-]', formula_unit=facts, source_formula_interpretation=None,
            resolution=dict(kind='unchanged_native', native_representations_sha256=material.digest(self.obs['body']['representations']),
                selected_support_ids=[], source_evidence=[], representation_dispositions=[dict(representation='smiles',
                    disposition='agrees_with_verified', evidence=['synthetic literal'])], evidence=['synthetic source']))
        def replay(source, locator):
            self.replays += 1
            self.assertEqual(locator, self.obs['native_locator'])
            return self.obs['body']
        self.replays = 0
        self.registry = dict(files=[dict(self.source, source_id='synthetic')], replay_native=replay,
            replay_provider=dict(implementation=material.file_binding(__file__), callable=replay.__qualname__, dependencies=[]))
        scope = dict(observation_count=1, eligible_count=1, nonmaterial_count=0, narrative_field_count=0,
            **{k: 'a' * 64 for k in ('physical_slots_sha256', 'observations_sha256', 'eligible_sha256',
                                    'nonmaterial_sha256', 'narrative_fields_sha256')})
        context = dict(full_contract=self.fixture.write('contract.json', self.fixture.contract),
            canonical_contract_sha256=material.digest(self.fixture.contract),
            accepted_contract_review=self.fixture.write('contract-review.json', {'synthetic': True}),
            source_registry=self.fixture.write('registry.json', {'files': self.registry['files']}),
            adjudication_config=None, source_files=self.registry['files'],
            classification=self.fixture.write('classification.jsonl', [], True), environment=self.fixture.env,
            code=bridge.code_bindings(), predicate_id='pre_identity_v1', scope=scope)
        self.integration = dict(schema_version=1, classification_context=context,
            classification_review=self.fixture.write('classification-review.json', {'synthetic': True}))

    def bind(self):
        fixture = self.fixture
        inventory = fixture.write('inventory.jsonl', [self.item], True)
        decisions = fixture.write('decisions.jsonl', [self.decision], True)
        context = dict(contract_sha256=material.digest(fixture.contract), implementation_sha256=material.file_binding(material.__file__)['sha256'],
            environment=fixture.env, inventory=inventory, decisions=decisions, replay_provider=self.registry['replay_provider'],
            sources=self.registry['files'], integration=self.integration)
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-producer',
            reviewer_execution_id='synthetic-reviewer', context=context, scope_ids=[material.digest(self.obs)],
            native_review=fixture.write('native-review.json', {'synthetic': True, 'result': 'synthetic return'}))
        receipt_file = fixture.write('receipt.json', receipt)
        return dict(inventory=inventory, decisions=decisions, receipt=receipt_file, actual_review=dict(receipt, receipt=receipt_file))

    def batch(self, binding=None):
        return material.material_batch(binding or self.bind(), self.fixture.contract, self.registry,
                                       self.fixture.env, integration=self.integration)

    def test_verified_projection_and_no_per_row_replay(self):
        with self.batch() as batch:
            initial_replays = self.replays
            with patch.object(material, 'file_binding', side_effect=AssertionError('per-row hash')):
                result = batch.resolve(self.obs, [], [])
                batch.validate_projection(result)
            self.assertEqual(self.replays, initial_replays)
            self.assertEqual(result['raw_structure'], '[Na+].[Cl-]')
            self.assertEqual(result['material_schema_version'], 2)
            batch.finish()
        self.assertEqual(batch.state, 'FINISHED')
        with self.assertRaises(material.MaterialError): batch.resolve(self.obs, [], [])

    def test_mutation_during_lifetime_and_unfinished_exit(self):
        with self.assertRaises(material.MaterialError):
            with self.batch() as batch:
                batch.resolve(self.obs, [], [])
                Path(self.source['path']).write_text('mutation', encoding='utf-8')
                batch.finish()
        self.assertEqual(batch.state, 'ABORTED')
        with self.assertRaises(material.MaterialError):
            with self.batch(): pass

    def test_omission_duplicate_and_projection_forgery(self):
        with self.assertRaises(material.MaterialError):
            with self.batch() as batch: batch.finish()
        with self.assertRaises(material.MaterialError):
            with self.batch() as batch:
                batch.resolve(self.obs, [], [])
                batch.resolve(self.obs, [], [])
        for field, value in (('parent_smiles', 'CCO'), ('raw_mw', 999), ('material_group_id', 'a' * 64)):
            with self.subTest(field=field), self.assertRaises(material.MaterialError):
                with self.batch() as batch:
                    result = batch.resolve(self.obs, [], [])
                    result[field] = value
                    batch.validate_projection(result)

    def test_optional_metadata_and_nonwaivable_blocker(self):
        bridge.validate_decision(self.decision, self.item)
        blocker = dict(category='ingestion', reason='material_identity_conflict', evidence_record_ids=[], original_reasons=['read failure'])
        blocker = dict(blocker_id=material.digest(blocker), **blocker)
        self.item['blocked_support'] = [blocker]
        self.decision['support_resolutions'] = [dict(support_sha256=material.digest(blocker),
            disposition='resolved_material_identity', evidence=['synthetic'])]
        with self.assertRaisesRegex(material.MaterialError, 'cannot waive'):
            bridge.validate_decision(self.decision, self.item)

    def test_support_only_and_explicit_invalid_representation_correction(self):
        obs = NativeMaterialTests().observation(include_smiles=False)
        item = dict(self.item, observation=obs)
        decision = copy.deepcopy(self.decision)
        decision['observation_id'] = material.digest(obs)
        resolution = decision['resolution']
        resolution.update(kind='support_resolution', native_representations_sha256=material.digest(obs['body']['representations']),
            representation_dispositions=[], source_evidence=[bridge.field_evidence(obs['body']['records'][0], '/source_record_id')])
        bridge.validate_decision(decision, item)
        data_index = dict(inventory={decision['observation_id']: item}, decisions={decision['observation_id']: decision}, context={})
        result = bridge.resolve_v2(obs, [], [], data_index)
        self.assertIsNone(result['raw_mw'])
        self.assertIsNone(result['raw_structure'])
        self.assertIsNone(result['raw_graph_diagnostics']['input_graph_smiles'])
        self.assertGreater(result['formula_unit_mw'], 0)
        obs = NativeMaterialTests().observation('broken', 'InChI=1S/ClH.Na/h1H;/q;+1/p-1')
        item = dict(self.item, observation=obs)
        decision = copy.deepcopy(self.decision)
        decision['observation_id'] = material.digest(obs)
        resolution = decision['resolution']
        resolution['native_representations_sha256'] = material.digest(obs['body']['representations'])
        resolution['representation_dispositions'].append(dict(representation='inchi', disposition='agrees_with_verified', evidence=['synthetic']))
        with self.assertRaises(material.MaterialError): bridge.validate_decision(decision, item)
        resolution.update(kind='native_correction', source_evidence=[obs['body']['representations']['smiles']])
        resolution['representation_dispositions'][0]['disposition'] = 'source_reviewed_correction'
        bridge.validate_decision(decision, item)

    def test_native_unit_kind_cannot_change_with_material_review(self):
        raw, config, policy, entry = PreIdentityRegressionTests().fixture()
        result = bridge.resolve_v2(self.obs, [], [], dict(inventory={material.digest(self.obs): self.item},
            decisions={material.digest(self.obs): self.decision}, context={}))
        result['reviewed_dose_basis'] = 'explicit_formula_unit_molar'
        with self.assertRaisesRegex(data.DataError, 'Native unit kind'):
            data.curate_observation(raw, config, policy, entry, 2, '', {}, {}, material_route=dict(
                route='verified_material', member_key='a' * 64, material_result=result, classification={}))


class MaterialCurationTests(unittest.TestCase):
    def npass_fixture(self, native_key, *, duplicate=False, missing_representation=None, waive=False):
        """Full packet attack reconstructed from the independent review probe."""
        f = self.fixture()
        packet = data.load(f.packet['path'])
        support = f.raw / 'support.tsv'
        from rdkit import Chem
        graph = '[Na+].[Cl-]'
        inchi = Chem.MolToInchi(Chem.MolFromSmiles(graph))
        valid_key = Chem.MolToInchiKey(Chem.MolFromSmiles(graph))
        native_rows = [('NPC1', '' if missing_representation == 'smiles' else graph,
                       '' if missing_representation == 'inchi' else inchi, native_key)]
        if duplicate:
            native_rows.insert(0, ('NPC1', graph, inchi, valid_key))
        support.write_text('np_id\tSMILES\tInChI\tInChIKey\n' + ''.join('\t'.join(row) + '\n' for row in native_rows), encoding='utf-8')
        with f.source.open(newline='', encoding='utf-8') as stream: rows = list(csv.DictReader(stream))
        for i, row in enumerate(rows): row['npid'] = 'NPC1' if i == 0 else ''
        with f.source.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
        policy = f.config['data_curation']
        config = policy['sources'][0]
        config['columns']['npid'] = 'npid'
        policy['identity_sources'] = [dict(source_id='support.tsv', adapter='npass_structure_v1')]
        data.dump(f.contract, f.config)
        f.collection = f.root / 'collection-npass'
        data.collect(f.raw, f.collection)
        f.registry_path = f.collection / 'source_registry.json'
        registry = data.load(f.registry_path)
        entry = next(e for e in registry['files'] if e['source_id'] == 'oecd.csv')
        packet.update(full_contract=material.file_binding(f.contract), source_registry=material.file_binding(f.registry_path))
        acceptance = data.load(packet['accepted_contract_review']['path'])
        data.dump(acceptance['candidate']['path'], {'contract': packet['full_contract']})
        acceptance.update(candidate=material.file_binding(acceptance['candidate']['path']), contract_sha256=packet['full_contract']['sha256'])
        data.dump(packet['accepted_contract_review']['path'], acceptance)
        packet['accepted_contract_review'] = material.file_binding(packet['accepted_contract_review']['path'])
        item = bridge.decode(Path(packet['materials']['inventory']['path']).read_bytes().splitlines()[0])
        decision = bridge.decode(Path(packet['materials']['decisions']['path']).read_bytes().splitlines()[0])
        classes = []
        with tempfile.TemporaryDirectory(dir=f.root) as scratch:
            store = bridge.NativeReplayStore(scratch)
            f.legacy_blocked = {}
            data.identity_index(policy, registry, blocked_keys=f.legacy_blocked, raw_support_observer=store.observe_support)
            for number, raw in data.rows(entry['path']):
                pre = data.pre_identity_observation(raw, config, policy, entry, number, '')
                obs = bridge.observation(raw, config, entry, number, '')
                linked = store.supports(pre)
                store.stage(obs, raw, pre, number, '', False)
                if pre.requires_classification:
                    classes.append(dict(schema_version=1, member_key=bridge.member_key(obs),
                        **{k: obs[k] for k in ('source_id', 'source_sha256', 'native_locator', 'candidate_key')},
                        observation_id=material.digest(obs), support_sha256=material.digest(linked),
                        classification='affected' if number == 2 else 'unaffected_organic', review_evidence=['synthetic NPASS repair probe']))
                if number == 2:
                    item.update(observation=obs, **linked)
                    decision['observation_id'] = material.digest(obs)
                    decision['resolution']['native_representations_sha256'] = material.digest(obs['body']['representations'])
            scope = store.scope()
            store.close()
        if waive:
            decision['support_resolutions'] = [dict(support_sha256=material.digest(b), disposition='resolved_material_identity',
                                                  evidence=['synthetic attempted identifier waiver']) for b in item['blocked_support']]
        def lines(binding, records):
            Path(binding['path']).write_bytes(b''.join(material.canonical(row) + b'\n' for row in records))
            return material.file_binding(binding['path'])
        packet['classification'] = lines(packet['classification'], sorted(classes, key=lambda c: c['member_key']))
        context = bridge.classification_context(packet, f.config, registry, scope)
        receipt = data.load(packet['classification_review']['receipt']['path'])
        receipt.update(context=context, scope_ids=sorted(c['member_key'] for c in classes))
        data.dump(packet['classification_review']['receipt']['path'], receipt)
        packet['classification_review']['receipt'] = material.file_binding(packet['classification_review']['receipt']['path'])
        packet['classification_review']['actual_review'] = dict(receipt, receipt=packet['classification_review']['receipt'])
        packet['materials']['inventory'] = lines(packet['materials']['inventory'], [item])
        packet['materials']['decisions'] = lines(packet['materials']['decisions'], [decision])
        receipt = data.load(packet['materials']['receipt']['path'])
        receipt['context'].update(contract_sha256=material.digest(f.config), sources=registry['files'],
            inventory=packet['materials']['inventory'], decisions=packet['materials']['decisions'],
            integration=dict(schema_version=1, classification_context=context, classification_review=packet['classification_review']['receipt']))
        receipt['scope_ids'] = [decision['observation_id']]
        data.dump(packet['materials']['receipt']['path'], receipt)
        packet['materials']['receipt'] = material.file_binding(packet['materials']['receipt']['path'])
        packet['materials']['actual_review'] = dict(receipt, receipt=packet['materials']['receipt'])
        data.dump(f.packet['path'], packet)
        f.native_material_blockers = item['blocked_support']
        return f

    def test_full_npass_identifier_failure_packets_cannot_publish(self):
        from rdkit import Chem
        mismatch = Chem.MolToInchiKey(Chem.MolFromSmiles('CCO'))
        correct = Chem.MolToInchiKey(Chem.MolFromSmiles('[Na+].[Cl-]'))
        cases = [('INVALID', {}), (mismatch, {}), ('', {}), ('INVALID', {'duplicate': True}),
                 (correct, {'missing_representation': 'smiles'}), (correct, {'missing_representation': 'inchi'})]
        for key, options in cases:
            with self.subTest(key=key, options=options):
                f = self.npass_fixture(key, waive=True, **options)
                self.assertTrue(f.native_material_blockers)
                with self.assertRaisesRegex(material.MaterialError, 'cannot waive'):
                    data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
                self.assertFalse(f.manifest.exists())
                self.assertFalse((f.out / 'manifest.json').exists())

    def test_valid_npass_full_material_remains_allowed(self):
        from rdkit import Chem
        key = Chem.MolToInchiKey(Chem.MolFromSmiles('[Na+].[Cl-]'))
        f = self.npass_fixture(key)
        self.assertEqual(f.native_material_blockers, [])
        self.assertIn('multiple_components', f.legacy_blocked[('npid', 'NPC1')]['reasons'])
        data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        self.assertTrue(f.check())
        self.assertEqual(data.load(f.manifest)['output_rows'], 11)

    def test_guard_reconstructs_npass_failure_from_forged_clean_inventory(self):
        # Deliberately bypass producer checks in memory to construct the old bad
        # artifact. The guard must reconstruct after all monkeypatches are gone.
        with patch.object(bridge, 'npass_native_reasons', return_value=[]), patch.object(bridge, 'NATIVE_SUPPORT_IDENTITY_REASONS', frozenset()):
            f = self.npass_fixture('INVALID')
            data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        with self.assertRaises(curation_fixtures.evidence.EvidenceError): f.check()

    def fixture(self, material_count=1, *, organic_graph=None, principal=None, legacy_sibling=False, feature_families=None):
        f = curation_fixtures.CoverageTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        base = MaterialV2BatchTests()
        base.setUp()
        self.addCleanup(base.doCleanups)
        f.config.update(contract_version='synthetic-material-integration', synthetic=True,
                        scientific_policy=base.fixture.contract['scientific_policy'])
        if organic_graph is not None:
            enable_organic(f.config)
        f.config['features'] = {'families': feature_families or {'RDKit2D': {}}}
        with f.source.open(newline='', encoding='utf-8') as stream:
            source_rows = list(csv.DictReader(stream))
        for row in source_rows[:material_count]:
            row['smiles'] = organic_graph or '[Na+].[Cl-]'
        if legacy_sibling:
            source_rows[material_count]['smiles'] = principal
        source_rows[-1]['route'] = 'dermal'
        with f.source.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(source_rows[0]))
            writer.writeheader()
            writer.writerows(source_rows)
        data.dump(f.contract, f.config)
        data.collect(f.raw, f.collection)
        registry_path = f.collection / 'source_registry.json'
        registry = data.load(registry_path)
        entry = registry['files'][0]
        config, policy = f.config['data_curation']['sources'][0], f.config['data_curation']
        def write(name, value, lines=False):
            path = f.root / name
            path.write_bytes(b'\n'.join(material.canonical(v) for v in value) + b'\n' if lines and value else
                             b'' if lines else material.canonical(value))
            return material.file_binding(path)
        contract_binding = material.file_binding(f.contract)
        acceptance = dict(schema_version=2, status='PASS', attestation='local_leader_attested_not_authenticated',
            producer_execution_id='synthetic-contract-producer', reviewer_execution_id='synthetic-contract-reviewer',
            receipt_writer='synthetic-leader', review_evidence=write('contract-native-return.json', {'synthetic': True}),
            findings=[], stage='contract', contract_sha256=contract_binding['sha256'],
            candidate=write('contract-candidate.json', {'contract': contract_binding}))
        packet = dict(schema_version=1, kind='qsar_material_curation_v1', full_contract=contract_binding,
            accepted_contract_review=write('accepted-contract.json', acceptance), source_registry=material.file_binding(registry_path),
            adjudication_config=None, environment=base.fixture.env,
            replay_provider=dict(implementation=material.file_binding(bridge.__file__), callable='NativeReplayStore.replay_native',
                dependencies=[b for b in bridge.code_bindings() if b['path'] != str(Path(bridge.__file__).resolve())]))
        with tempfile.TemporaryDirectory(dir=f.root) as scratch:
            store = bridge.NativeReplayStore(scratch)
            observations, classifications = [], []
            for number, raw in data.rows(entry['path']):
                pre = data.pre_identity_observation(raw, config, policy, entry, number, '')
                obs = bridge.observation(raw, config, entry, number, '')
                store.stage(obs, raw, pre, number, '', False)
                observations.append(obs)
                if pre.requires_classification:
                    classifications.append(dict(schema_version=1, member_key=bridge.member_key(obs),
                        **{k: obs[k] for k in ('source_id', 'source_sha256', 'native_locator', 'candidate_key')},
                        observation_id=material.digest(obs), support_sha256=material.digest(dict(support_candidates=[], blocked_support=[])),
                        classification='affected' if number <= material_count + 1 else 'unaffected_organic', review_evidence=['SYNTHETIC classification']))
            scope = store.scope()
            store.close()
        packet['classification'] = write('classification.jsonl', sorted(classifications, key=lambda c: c['member_key']), True)
        context = bridge.classification_context(packet, f.config, registry, scope)
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-classification-producer',
            reviewer_execution_id='synthetic-classification-reviewer', context=context,
            scope_ids=sorted(c['member_key'] for c in classifications), native_review=write('classification-native-return.json', {'synthetic': True}))
        receipt_binding = write('classification-receipt.json', receipt)
        packet['classification_review'] = dict(receipt=receipt_binding, actual_review=dict(receipt, receipt=receipt_binding))
        items, decision_rows = [], []
        for obs in observations[:material_count]:
            items.append(dict(base.item, observation=obs))
            decision = copy.deepcopy(base.decision)
            decision['observation_id'] = material.digest(obs)
            decision['resolution']['native_representations_sha256'] = material.digest(obs['body']['representations'])
            if organic_graph is not None:
                organic_decision(decision, obs, organic_graph, principal)
            decision_rows.append(decision)
        inventory = write('materials.jsonl', items, True)
        decisions = write('decisions.jsonl', decision_rows, True)
        material_context = dict(contract_sha256=material.digest(f.config), implementation_sha256=material.file_binding(material.__file__)['sha256'],
            environment=packet['environment'], inventory=inventory, decisions=decisions, replay_provider=packet['replay_provider'],
            sources=registry['files'], integration=dict(schema_version=1, classification_context=context, classification_review=receipt_binding))
        material_receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-material-producer',
            reviewer_execution_id='synthetic-material-reviewer', context=material_context, scope_ids=sorted(d['observation_id'] for d in decision_rows),
            native_review=write('material-native-return.json', {'synthetic': True}))
        receipt_binding = write('material-receipt.json', material_receipt)
        packet['materials'] = dict(inventory=inventory, decisions=decisions, receipt=receipt_binding,
            actual_review=dict(material_receipt, receipt=receipt_binding))
        f.packet = write('packet.json', packet)
        f.registry_path = registry_path
        f.manifest = f.out / 'curation_manifest.json'
        return f

    def test_end_to_end_native_partition_and_guard(self):
        f = self.fixture()
        result = data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        manifest = data.load(f.manifest)
        self.assertEqual(manifest['output_rows'], 11)
        self.assertEqual(manifest['material_integration']['classification_context']['scope']['nonmaterial_count'], 1)
        self.assertTrue(f.check())
        with (f.out / 'curated.csv').open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        inorganic = next(r for r in rows if r['material_schema_version'])
        self.assertEqual(inorganic['parent_smiles'], '[Cl-].[Na+]')
        self.assertAlmostEqual(float(inorganic['target_mg_kg']), 100)
        organic = next(r for r in rows if not r['material_schema_version'])
        self.assertTrue(all(organic[k] == '' for k in bridge.MATERIAL_COLUMNS))

    def test_material_enabled_cannot_omit_packet(self):
        f = self.fixture()
        with self.assertRaisesRegex(data.DataError, 'explicit material packet'):
            data.curate(f.contract, f.registry_path, f.out)
        self.assertFalse(f.manifest.exists())

    def rebind_reviews(self, f, mutate_classification=None, omit_inventory=False):
        packet = data.load(f.packet['path'])
        path = Path(packet['classification']['path'])
        rows = [bridge.decode(line) for line in path.read_bytes().splitlines()]
        if mutate_classification:
            mutate_classification(rows)
        path.write_bytes(b'\n'.join(material.canonical(row) for row in rows) + b'\n')
        packet['classification'] = material.file_binding(path)
        receipt = data.load(packet['classification_review']['receipt']['path'])
        receipt['context']['classification'] = packet['classification']
        receipt['scope_ids'] = sorted(row['member_key'] for row in rows)
        data.dump(packet['classification_review']['receipt']['path'], receipt)
        packet['classification_review']['receipt'] = material.file_binding(packet['classification_review']['receipt']['path'])
        packet['classification_review']['actual_review'] = dict(receipt, receipt=packet['classification_review']['receipt'])
        mr = data.load(packet['materials']['receipt']['path'])
        mr['context']['integration'] = dict(schema_version=1, classification_context=receipt['context'],
                                          classification_review=packet['classification_review']['receipt'])
        if omit_inventory:
            for key in ('inventory', 'decisions'):
                Path(packet['materials'][key]['path']).write_bytes(b'')
                packet['materials'][key] = material.file_binding(packet['materials'][key]['path'])
                mr['context'][key] = packet['materials'][key]
            mr['scope_ids'] = []
        data.dump(packet['materials']['receipt']['path'], mr)
        packet['materials']['receipt'] = material.file_binding(packet['materials']['receipt']['path'])
        packet['materials']['actual_review'] = dict(mr, receipt=packet['materials']['receipt'])
        data.dump(f.packet['path'], packet)

    def test_rebound_omissions_duplicates_and_false_classification_fail(self):
        for attack in ('omit_classification', 'duplicate', 'false_unaffected', 'omit_inventory'):
            f = self.fixture()
            def mutate(rows):
                if attack == 'omit_classification': rows.pop()
                elif attack == 'duplicate': rows.append(dict(rows[0]))
                elif attack == 'false_unaffected':
                    next(r for r in rows if r['classification'] == 'affected')['classification'] = 'unaffected_organic'
            self.rebind_reviews(f, mutate, omit_inventory=attack == 'omit_inventory')
            with self.subTest(attack=attack), self.assertRaises((ValueError, __import__('sqlite3').IntegrityError)):
                data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
            self.assertFalse(f.manifest.exists())
            self.assertFalse((f.out / 'manifest.json').exists())

    def test_guard_rejects_rebound_target_identity_and_membership(self):
        f = self.fixture()
        data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        original = (f.out / 'curated.csv').read_bytes()
        for field, value in (('target_mg_kg', '999999'), ('parent_smiles', 'CCO'), ('material_group_id', 'a' * 64),
                             ('raw_representations', '{"smiles":null,"smiles":null}')):
            f.rewrite_csv('curated.csv', lambda rows: next(r for r in rows if r['material_schema_version']).update({field: value}))
            with self.subTest(field=field), self.assertRaises(curation_fixtures.evidence.EvidenceError): f.check()
            (f.out / 'curated.csv').write_bytes(original)
            f.rebind_output('curated.csv')

    def test_final_publication_failure_removes_only_owned_markers(self):
        f = self.fixture()
        with patch.object(data, 'handoff', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        self.assertFalse(f.manifest.exists())
        self.assertFalse((f.out / 'manifest.json').exists())
        self.assertTrue((f.out / 'curated.csv').exists())

    def test_late_source_mutation_prevents_success(self):
        f = self.fixture()
        original_finish = bridge.MaterialCurationBatch.finish
        def changed(batch):
            with f.source.open('a', encoding='utf-8') as stream: stream.write('\n')
            return original_finish(batch)
        with patch.object(bridge.MaterialCurationBatch, 'finish', changed):
            with self.assertRaises(material.MaterialError):
                data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        self.assertFalse(f.manifest.exists())
        self.assertFalse((f.out / 'manifest.json').exists())

    def test_whole_source_hash_count_constant_with_material_rows(self):
        counts = []
        original = material.file_binding
        for count in (1, 10):
            f = self.fixture(material_count=count)
            calls = []
            def counted(path):
                if Path(path).resolve() == f.source.resolve(): calls.append(str(path))
                return original(path)
            with patch.object(material, 'file_binding', side_effect=counted):
                data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
            counts.append(len(calls))
        self.assertGreater(counts[0], 0)
        self.assertEqual(counts[0], counts[1])

    def test_downstream_references_and_feature_entry_final_freshness(self):
        from rdkit.Chem import Descriptors
        prepare = curation_fixtures.prepare
        for mutation in ('none', 'entry', 'during'):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
                split_dir = f.root / 'split'
                split = prepare.prepare_splits(f.out / 'curated.csv', f.contract, split_dir, f.manifest)
                refs = {r['path'] for r in split['curation_evidence']}
                self.assertIn(f.packet['path'], refs)
                self.assertIn(str(f.root / 'classification-native-return.json'), refs)
                self.assertIn(str(f.root / 'material-native-return.json'), refs)
                feature_dir = f.root / 'features'
                def mutate():
                    with Path(f.packet['path']).open('a', encoding='utf-8') as stream: stream.write(' ')
                if mutation == 'entry': mutate()
                inventory = list(Descriptors._descList)
                def descriptor(mol):
                    mutate()
                    return inventory[0][1](mol)
                patched = [(inventory[0][0], descriptor), *inventory[1:]] if mutation == 'during' else inventory
                with patch.object(Descriptors, '_descList', patched):
                    if mutation == 'none':
                        result = prepare.generate_features(split_dir / 'development.csv', split_dir / 'preprocessing_manifest.json', f.contract, feature_dir)
                        self.assertEqual(result['curation_evidence'], split['curation_evidence'])
                    else:
                        with self.assertRaises(prepare.PreparationError):
                            prepare.generate_features(split_dir / 'development.csv', split_dir / 'preprocessing_manifest.json', f.contract, feature_dir)
                        self.assertFalse((feature_dir / 'feature_manifest.json').exists())

    def test_hsdb_reviewed_hold_and_organic_sibling_full_pipeline(self):
        fixture = candidate_fixtures.CandidateIntegrationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        f = fixture.f
        base = MaterialV2BatchTests()
        base.setUp()
        self.addCleanup(base.doCleanups)
        f.contract.update(contract_version='synthetic-hsdb-material', synthetic=True)
        f.contract['scientific_policy'].update(base.fixture.contract['scientific_policy'])
        config, contract_file, acceptance = f.bind_v2()
        mapping = f.desc('external.json', {'hsdb.zip': dict(config, full_contract=contract_file, accepted_contract_review=acceptance)})
        data.collect(f.raw, fixture.collection)
        registry_file = material.file_binding(fixture.collection / 'source_registry.json')
        registry = data.load(registry_file['path'])
        entry = next(e for e in registry['files'] if e['source_id'] == 'hsdb.zip')
        packet = dict(schema_version=1, kind='qsar_material_curation_v1', full_contract=contract_file,
            accepted_contract_review=acceptance, source_registry=registry_file, adjudication_config=mapping,
            environment=base.fixture.env, replay_provider=dict(implementation=material.file_binding(bridge.__file__),
                callable='NativeReplayStore.replay_native', dependencies=[b for b in bridge.code_bindings() if b['path'] != str(Path(bridge.__file__).resolve())]))
        def lines(name, values):
            path = f.root / name
            path.write_bytes(b''.join(material.canonical(v) + b'\n' for v in values))
            return material.file_binding(path)
        with tempfile.TemporaryDirectory(dir=f.root) as scratch:
            store = bridge.NativeReplayStore(scratch)
            data.identity_index(f.policy, registry, raw_support_observer=store.observe_support)
            doc = next(data.archive_sources().iter_hsdb_documents(entry['path']))
            line = doc['native_lineage']
            record = bridge.native_record('hsdb.zip', entry['sha256'], dict(kind='xml_document', archive_member=line['archive_member'],
                document_ordinal=line['document_ordinal'], record_id=doc['record_id']), doc['native_record'], line)
            ledger = Path(scratch) / 'fields.jsonl'
            engine = candidate_fixtures.adj.AdjudicationV2(config, entry, contract_file, acceptance, data.UNITS, ledger)
            observations, classifications, items = [], [], []
            for number, raw in enumerate(engine.rows(), 1):
                source_config = f.policy['sources'][0]
                pre = data.pre_identity_observation(raw, source_config, f.policy, entry, number, 'hsdb_narratives', adjudication=True)
                obs = bridge.observation(raw, source_config, entry, number, 'hsdb_narratives', adjudicated=True, document_record=record)
                store.stage(obs, raw, pre, number, 'hsdb_narratives', True)
                support = store.supports(pre)
                observations.append(obs)
                classifications.append(dict(schema_version=1, member_key=bridge.member_key(obs),
                    **{k: obs[k] for k in ('source_id', 'source_sha256', 'native_locator', 'candidate_key')},
                    observation_id=material.digest(obs), support_sha256=material.digest(support),
                    classification='affected' if number == 1 else 'unaffected_organic', review_evidence=['synthetic sibling review']))
                if number == 1:
                    items.append(dict(observation=obs, **support, source_metadata=base.item['source_metadata']))
            fields = {field['field_id']: field for document in candidate_fixtures.adj.native_fields(entry['path'], 'hsdb.zip', entry['sha256']) for field in document}
            for row in candidate_fixtures.adj.jsonl(ledger):
                loc = bridge.physical_locator(dict(native_lineage=fields[row['field_id']]['locator']), 'hsdb', 1, '')
                store.db.execute('INSERT INTO field VALUES(?,?,?,?)', ('hsdb.zip', row['field_id'], material.digest(['hsdb.zip', loc]), material.canonical(row)))
            scope = store.scope()
            store.close()
        packet['classification'] = lines('classification.jsonl', sorted(classifications, key=lambda c: c['member_key']))
        context = bridge.classification_context(packet, f.contract, registry, scope)
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-classifier',
            reviewer_execution_id='synthetic-reviewer', context=context, scope_ids=sorted(c['member_key'] for c in classifications),
            native_review=f.desc('classification-return.json', {'synthetic': True}))
        receipt_file = f.desc('classification-receipt.json', receipt)
        packet['classification_review'] = dict(receipt=receipt_file, actual_review=dict(receipt, receipt=receipt_file))
        decision = dict(base.decision, observation_id=material.digest(observations[0]), status='reviewed_hold', material_class='organic',
            dose_basis=None, fixed_composition=None, hydration_interpretation=None, verified_smiles=None, formula_unit=None, resolution=None)
        inventory = lines('materials.jsonl', items)
        decisions = lines('materials-decisions.jsonl', [decision])
        context = dict(contract_sha256=material.digest(f.contract), implementation_sha256=material.file_binding(material.__file__)['sha256'],
            environment=packet['environment'], inventory=inventory, decisions=decisions, replay_provider=packet['replay_provider'],
            sources=registry['files'], integration=dict(schema_version=1, classification_context=context, classification_review=receipt_file))
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-material-producer',
            reviewer_execution_id='synthetic-material-reviewer', context=context, scope_ids=[decision['observation_id']],
            native_review=f.desc('material-return.json', {'synthetic': True}))
        receipt_file = f.desc('materials-receipt.json', receipt)
        packet['materials'] = dict(inventory=inventory, decisions=decisions, receipt=receipt_file, actual_review=dict(receipt, receipt=receipt_file))
        packet_file = f.desc('material-packet.json', packet)
        data.curate(contract_file['path'], registry_file['path'], fixture.out, mapping['path'], packet_file['path'])
        manifest = data.load(fixture.out / 'curation_manifest.json')
        self.assertEqual(manifest['input_rows'], 2)
        self.assertEqual(manifest['output_rows'], 1)
        self.assertEqual(manifest['dispositions'], {'quarantined': 1, 'retained': 1})
        refs = curation_fixtures.evidence.validate(contract_file['path'], fixture.out / 'curation_manifest.json', fixture.out / 'curated.csv')
        self.assertIn(str(f.root / 'evidence-v2.json'), {r['path'] for r in refs})





# These certificates and receipts exercise mechanics only; no real salt is approved.
def enable_organic(contract):
    contract['scientific_policy']['verified_tested_material_v4']['organic_salt_b_v1'] = dict(execution=dict(
        schema_version=1, adapter='reviewed_organic_principal_component_v1', rdkit_version=bridge.rdBase.rdkitVersion,
        mass_algorithm='rdkit_descriptors_molwt', predictor_policy='reviewed_principal_component',
        grouping_algorithm='sha256_utf8_rdkit_canonical_nonisomeric_smiles'))


def organic_decision(decision, obs, graph, principal):
    from rdkit import Chem
    principal = Chem.MolToSmiles(Chem.MolFromSmiles(principal), canonical=True, isomericSmiles=True)
    facts = material.graph_facts(graph)
    decision.update(schema_version=3, material_class='organic', verified_smiles=graph, formula_unit=facts,
        principal_component=dict(canonical_smiles=principal, multiplicity=facts['components'][principal],
            source_evidence=[obs['body']['representations']['smiles']], supplemental_evidence=[],
            selection_rationale='SYNTHETIC stipulated principal identity; not actual chemical acceptance'))


class OrganicMaterialTests(unittest.TestCase):
    def batch_fixture(self, graph='CC[NH3+].O=C([O-])C(=O)O', principal='CC[NH3+]', basis='__absent__'):
        f = MaterialV2BatchTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        raw, config, policy, entry = PreIdentityRegressionTests().fixture()
        raw['smiles'] = graph
        if basis != '__absent__': raw['dose_basis'] = basis
        f.obs = bridge.observation(raw, config, dict(entry, sha256=f.source['sha256']), 2, '')
        f.item['observation'] = f.obs
        f.decision['observation_id'] = material.digest(f.obs)
        f.decision['resolution']['native_representations_sha256'] = material.digest(f.obs['body']['representations'])
        organic_decision(f.decision, f.obs, graph, principal)
        enable_organic(f.fixture.contract)
        context = f.integration['classification_context']
        context['full_contract'] = f.fixture.write('contract.json', f.fixture.contract)
        context['canonical_contract_sha256'] = material.digest(f.fixture.contract)
        return f

    def test_complete_mass_principal_and_unprefixed_group(self):
        import hashlib, math
        from rdkit import Chem
        from rdkit.Chem import Descriptors
        atropine = 'C[NH+]1[C@@H]2CC[C@H]1C[C@@H](OC(=O)C(CO)c1ccccc1)C2'
        for principal, counterion, count in [('CC[NH3+]', 'O=C([O-])C(=O)O', 1),
                (atropine, 'O=S(=O)([O-])[O-]', 2), ('CC[NH3+]', '[Cl-]', 1),
                ('CC(=O)[O-]', '[Na+]', 1)]:
            graph = '.'.join([principal] * count + [counterion])
            with self.subTest(graph=graph):
                f = self.batch_fixture(graph, principal)
                with f.batch() as batch:
                    result = batch.resolve(f.obs, [], [])
                    mw = count * Descriptors.MolWt(Chem.MolFromSmiles(principal)) + Descriptors.MolWt(Chem.MolFromSmiles(counterion))
                    self.assertAlmostEqual(result['original_mw'], mw)
                    self.assertEqual(result['principal_component']['multiplicity'], count)
                    self.assertAlmostEqual(result['parent_mw'], Descriptors.MolWt(Chem.MolFromSmiles(principal)))
                    group = hashlib.sha256(Chem.MolToSmiles(Chem.MolFromSmiles(principal), isomericSmiles=False).encode()).hexdigest()
                    self.assertEqual(result['connectivity_id'], group)
                    target = 100 / (1000 * mw)
                    result.update(group_id=group, target_mg_kg=100, target_mol_kg=target, target_pLD50=-math.log10(target))
                    batch.validate_projection(result)
                    batch.finish()

    def test_selector_and_certificate_attacks(self):
        for attack in ('no_selector', 'extra_selector', 'bool_count', 'wrong_count', 'nonmember', 'empty_evidence', 'wrong_pointer', 'downgrade'):
            with self.subTest(attack=attack):
                f = self.batch_fixture()
                if attack == 'no_selector': del f.fixture.contract['scientific_policy']['verified_tested_material_v4']['organic_salt_b_v1']
                elif attack == 'extra_selector': f.fixture.contract['scientific_policy']['verified_tested_material_v4']['organic_salt_b_v1']['execution']['extra'] = True
                elif attack == 'bool_count': f.decision['principal_component']['multiplicity'] = True
                elif attack == 'wrong_count': f.decision['principal_component']['multiplicity'] = 2
                elif attack == 'nonmember': f.decision['principal_component']['canonical_smiles'] = 'CCO'
                elif attack == 'empty_evidence': f.decision['principal_component']['source_evidence'] = []
                elif attack == 'wrong_pointer': f.decision['principal_component']['source_evidence'][0]['value'] = 'false'
                else: f.decision['schema_version'] = 2
                context = f.integration['classification_context']
                context['full_contract'] = f.fixture.write('contract.json', f.fixture.contract)
                context['canonical_contract_sha256'] = material.digest(f.fixture.contract)
                with self.assertRaises(material.MaterialError):
                    with f.batch(): pass

    def test_supplemental_drift_rejects_finish(self):
        import os
        f = self.batch_fixture()
        evidence = f.fixture.write('principal-evidence.json', {'synthetic': 'A'})
        f.decision['principal_component']['supplemental_evidence'] = [dict(file=evidence, locator='/synthetic')]
        with self.assertRaises(material.MaterialError):
            with f.batch() as batch:
                batch.resolve(f.obs, [], [])
                path = Path(evidence['path']); stat = path.stat()
                path.write_bytes(path.read_bytes().replace(b'A', b'B'))
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                batch.finish()
        self.assertEqual(batch.state, 'ABORTED')

    def test_ordinary_full_consumer_and_guard(self):
        maker = MaterialCurationTests()
        self.addCleanup(maker.doCleanups)
        f = maker.fixture(organic_graph='CC[NH3+].O=C([O-])C(=O)O', principal='CC[NH3+]')
        data.curate(f.contract, f.registry_path, f.out, material_config_path=f.packet['path'])
        self.assertTrue(f.check())
        with (f.out / 'curated.csv').open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 11)
        organic = next(r for r in rows if r['material_schema_version'])
        self.assertEqual(organic['material_schema_version'], '3')
        self.assertEqual(organic['parent_smiles'], 'CC[NH3+]')
        self.assertGreater(float(organic['original_mw']), float(organic['parent_mw']))

class HSDBMetadataMaterialTests(unittest.TestCase):
    def test_metadata_preserved_with_reviewed_inorganic_and_organic_packets(self):
        from contextlib import closing
        from scripts.test_qsar_observation_metadata import MetadataCurationTests
        import qsar_adjudication as adj
        for organic in (False, True):
            with self.subTest(organic=organic):
                fixture = MetadataCurationTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
                c, f = fixture.coverage, fixture.fixture
                base = MaterialV2BatchTests(); base.setUp(); self.addCleanup(base.doCleanups)
                graph = 'CC[NH3+].[Cl-]' if organic else '[Na+].[Cl-]'
                # Explicit synthetic support and reviews, not a real ETHANOL identity claim.
                (c.raw / 'identity.csv').write_text('cas,smiles\n64-17-5,' + graph + '\n', encoding='utf-8')
                c.config.update(contract_version='synthetic-hsdb-material-metadata', synthetic=True,
                    scientific_policy=copy.deepcopy(base.fixture.contract['scientific_policy']))
                if organic: enable_organic(c.config)
                data.collect(c.raw, c.collection)
                registry_path = c.collection / 'source_registry.json'
                registry = data.load(registry_path)
                entry = next(e for e in registry['files'] if e['source_id'] == 'hsdb.zip')
                def write(name, value, lines=False):
                    path = c.root / name
                    path.write_bytes(b''.join(material.canonical(v)+b'\n' for v in value) if lines else material.canonical(value))
                    return material.file_binding(path)
                baseline_rows = None
                for metadata_enabled in (False, True):
                    config = f.policy['sources'][0]
                    if metadata_enabled: config['narrative_observation_metadata'] = fixture.descriptor
                    else: config.pop('narrative_observation_metadata', None)
                    config['narrative_adjudication'] = f.bind()
                    data.dump(c.contract, c.config)
                    cb = material.file_binding(c.contract)
                    acceptance = write('accepted-contract.json', dict(schema_version=2, status='PASS',
                        attestation='local_leader_attested_not_authenticated', producer_execution_id='synthetic-producer',
                        reviewer_execution_id='synthetic-reviewer', receipt_writer='synthetic-leader', findings=[],
                        review_evidence=write('contract-return.json', {'synthetic':True}), stage='contract',
                        contract_sha256=cb['sha256'], candidate=write('contract-candidate.json', {'contract':cb})))
                    packet = dict(schema_version=1, kind='qsar_material_curation_v1', full_contract=cb,
                        accepted_contract_review=acceptance, source_registry=material.file_binding(registry_path),
                        adjudication_config=None, environment=base.fixture.env,
                        replay_provider=dict(implementation=material.file_binding(bridge.__file__),
                            callable='NativeReplayStore.replay_native', dependencies=[b for b in bridge.code_bindings()
                                if b['path'] != str(Path(bridge.__file__).resolve())]))
                    ledger = c.root / 'fixture-field-ledger.jsonl'
                    engine = adj.Adjudication(f.config, f.policy, entry, data.UNITS, ledger)
                    if metadata_enabled:
                        engine.metadata = adj.ObservationMetadata(fixture.descriptor, f.config, entry, data.UNITS)
                    with tempfile.TemporaryDirectory(dir=c.root) as scratch, closing(bridge.NativeReplayStore(scratch)) as store:
                        data.identity_index(f.policy, registry, raw_support_observer=store.observe_support)
                        doc = next(data.archive_sources().iter_hsdb_documents(entry['path']))
                        line = doc['native_lineage']
                        record = bridge.native_record('hsdb.zip', entry['sha256'], dict(kind='xml_document',
                            archive_member=line['archive_member'], document_ordinal=line['document_ordinal'], record_id=doc['record_id']),
                            doc['native_record'], line)
                        classes, items, decisions = [], [], []
                        for number, raw in enumerate(engine.rows(), 1):
                            pre = data.pre_identity_observation(raw, config, f.policy, entry, number, 'hsdb_narratives', adjudication=True)
                            obs = bridge.observation(raw, config, entry, number, 'hsdb_narratives', adjudicated=True, document_record=record)
                            store.stage(obs, raw, pre, number, 'hsdb_narratives', True)
                            support = store.supports(pre)
                            classes.append(dict(schema_version=1, member_key=bridge.member_key(obs),
                                **{k:obs[k] for k in ('source_id','source_sha256','native_locator','candidate_key')},
                                observation_id=material.digest(obs), support_sha256=material.digest(support), classification='affected',
                                review_evidence=['SYNTHETIC material classification']))
                            item = dict(observation=obs, **support, source_metadata=base.item['source_metadata']); items.append(item)
                            decision = copy.deepcopy(base.decision)
                            decision.update(observation_id=material.digest(obs), verified_smiles=graph, formula_unit=material.graph_facts(graph))
                            evidence = support['support_candidates'][0]['raw']['representations']['smiles']
                            decision['resolution'] = dict(kind='support_resolution', native_representations_sha256=material.digest(obs['body']['representations']),
                                selected_support_ids=[s['support_id'] for s in support['support_candidates']],
                                source_evidence=[bridge.field_evidence(obs['body']['records'][0], '')],
                                representation_dispositions=[], evidence=['SYNTHETIC stipulated support identity'])
                            if organic:
                                decision.update(schema_version=3, material_class='organic', principal_component=dict(canonical_smiles='CC[NH3+]',
                                    multiplicity=1, source_evidence=[evidence], supplemental_evidence=[], selection_rationale='SYNTHETIC stipulated principal'))
                            decisions.append(decision)
                        fields = {field['field_id']:field for document in adj.native_fields(entry['path'], 'hsdb.zip', entry['sha256']) for field in document}
                        for row in adj.jsonl(ledger):
                            loc = bridge.physical_locator(dict(native_lineage=fields[row['field_id']]['locator']), 'hsdb', 1, '')
                            store.db.execute('INSERT INTO field VALUES(?,?,?,?)', ('hsdb.zip',row['field_id'],material.digest(['hsdb.zip',loc]),material.canonical(row)))
                        scope = store.scope(); store.close()
                    packet['classification'] = write('classes.jsonl', sorted(classes,key=lambda x:x['member_key']),True)
                    context = bridge.classification_context(packet,c.config,registry,scope)
                    receipt = dict(schema_version=1,result='PASS',synthetic=True,producer_execution_id='synthetic-producer',reviewer_execution_id='synthetic-reviewer',
                        context=context,scope_ids=sorted(x['member_key'] for x in classes),native_review=write('classification-return.json',{'synthetic':True}))
                    rb=write('classification-receipt.json',receipt); packet['classification_review']=dict(receipt=rb,actual_review=dict(receipt,receipt=rb))
                    inventory=write('materials.jsonl',items,True); db=write('decisions.jsonl',decisions,True)
                    context=dict(contract_sha256=material.digest(c.config),implementation_sha256=material.file_binding(material.__file__)['sha256'],environment=base.fixture.env,
                        inventory=inventory,decisions=db,replay_provider=packet['replay_provider'],sources=registry['files'],
                        integration=dict(schema_version=1,classification_context=context,classification_review=rb))
                    receipt=dict(schema_version=1,result='PASS',synthetic=True,producer_execution_id='synthetic-producer',reviewer_execution_id='synthetic-reviewer',
                        context=context,scope_ids=sorted(d['observation_id'] for d in decisions),native_review=write('material-return.json',{'synthetic':True}))
                    rb=write('material-receipt.json',receipt); packet['materials']=dict(inventory=inventory,decisions=db,receipt=rb,actual_review=dict(receipt,receipt=rb))
                    packet_file=write('material-packet.json',packet)
                    out=c.root / ('with-metadata' if metadata_enabled else 'without-metadata')
                    data.curate(c.contract,registry_path,out,material_config_path=packet_file['path'])
                    curation_fixtures.evidence.validate(c.contract,out/'curation_manifest.json',out/'curated.csv')
                    rows=list(curation_fixtures.evidence.csv_rows(out/'curated.csv'))
                    comparable=[{k:r[k] for k in ('output_row_id','row_id','target_mg_kg','target_mol_kg','target_pLD50','parent_smiles','group_id')} for r in rows]
                    if not metadata_enabled: baseline_rows=comparable
                    else:
                        self.assertEqual(comparable,baseline_rows)
                        self.assertEqual(len(rows),2)
                        self.assertTrue(all(json.loads(r['observation_metadata'])['metadata']['reported_observation_not_unique_study'] for r in rows))
                        expected_metadata = {m['observation_id']:m for m in adj.jsonl(fixture.manifest['metadata']['path'])}
                        for name in ('curated.csv','identity_ledger.csv','dispositions.csv'):
                            for row in curation_fixtures.evidence.csv_rows(out/name):
                                projected = json.loads(row['observation_metadata'])['metadata']
                                self.assertEqual(projected,expected_metadata[projected['observation_id']])
                        manifest=data.load(out/'curation_manifest.json')
                        self.assertEqual(len(manifest['observation_metadata_accounting']['hsdb.zip']['consumed_observation_ids']),2)

class OrganicMaterialAdversarialTests(unittest.TestCase):
    batch_fixture = OrganicMaterialTests.batch_fixture
    def test_repeated_supplemental_files_hash_once_per_batch_check(self):
        counts=[]
        for copies in (1,10):
            f=self.batch_fixture()
            evidence=f.fixture.write('principal-source.json',{'synthetic':True})
            f.decision['principal_component']['supplemental_evidence']=[dict(file=evidence,locator='/synthetic') for _ in range(copies)]
            original=material._file
            calls=[]
            def counted(binding):
                if binding['path']==evidence['path']: calls.append(binding['path'])
                return original(binding)
            with patch.object(material,'_file',side_effect=counted):
                with f.batch() as batch:
                    before=len(calls)
                    batch.resolve(f.obs,[],[])
                    self.assertEqual(len(calls),before)
                    batch.finish()
            counts.append(len(calls))
        self.assertEqual(counts[0],counts[1])
        self.assertGreater(counts[0],0)

    def test_declared_invalid_selector_rejects_even_without_b_rows(self):
        f = MaterialV2BatchTests(); f.setUp(); self.addCleanup(f.doCleanups)
        enable_organic(f.fixture.contract)
        f.fixture.contract['scientific_policy']['verified_tested_material_v4']['organic_salt_b_v1']['execution']['rdkit_version'] = 'unknown'
        context = f.integration['classification_context']
        context['full_contract'] = f.fixture.write('contract.json',f.fixture.contract)
        context['canonical_contract_sha256'] = material.digest(f.fixture.contract)
        with self.assertRaises(material.MaterialError):
            with f.batch(): pass

    def test_fragment_permutation_and_retained_legacy_controls(self):
        for graph, principal in [('CC[NH3+].[Cl-]','CC[NH3+]'), ('CC(=O)[O-].[Na+]','CC(=O)[O-]')]:
            results=[]
            for variant in (graph,'.'.join(reversed(graph.split('.')))):
                f=self.batch_fixture(variant,principal)
                with f.batch() as batch:
                    result=batch.resolve(f.obs,[],[]); batch.finish(); results.append(result)
                legacy=data.chemistry(variant,'','largest_organic')
                for field in ('original_mw','parent_mw','parent_smiles','connectivity_id'):
                    self.assertEqual(result[field],legacy[field])
            for field in ('original_mw','parent_mw','parent_smiles','connectivity_id','material_group_id'):
                if field.endswith('_mw'): self.assertAlmostEqual(results[0][field],results[1][field])
                else: self.assertEqual(results[0][field],results[1][field])

    def test_changed_principal_or_stoichiometry_under_existing_review_rejects(self):
        for attack in ('counterion','formula','water','stereo','charge','isotope'):
            with self.subTest(attack=attack):
                f=self.batch_fixture('C[C@H]([NH3+])CO.O=C([O-])C(=O)O.O','C[C@H]([NH3+])CO')
                binding=f.bind()
                if attack=='counterion': f.decision['principal_component']['canonical_smiles']='O=C([O-])C(=O)O'
                elif attack=='formula': f.decision['formula_unit']['mw']-=18.015
                elif attack=='water': f.decision['verified_smiles']='C[C@H]([NH3+])CO.O=C([O-])C(=O)O'
                elif attack=='stereo': f.decision['principal_component']['canonical_smiles']='C[C@@H]([NH3+])CO'
                elif attack=='charge': f.decision['principal_component']['canonical_smiles']='C[C@H](N)CO'
                else: f.decision['principal_component']['canonical_smiles']='[13CH3][C@H]([NH3+])CO'
                f.fixture.write('decisions.jsonl',[f.decision],True)
                with self.assertRaises(material.MaterialError):
                    with f.batch(binding): pass

    def test_schema3_hold_and_unresolved_cannot_emit_or_finish(self):
        f=self.batch_fixture(); f.decision.update(status='reviewed_hold',principal_component=None)
        with f.batch() as batch:
            self.assertNotIn('original_mw',batch.resolve(f.obs,[],[])); batch.finish()
        f=self.batch_fixture(); f.decision.update(status='unresolved',principal_component=None)
        with self.assertRaises(material.MaterialError):
            with f.batch() as batch:
                self.assertNotIn('original_mw',batch.resolve(f.obs,[],[])); batch.finish()
        self.assertEqual(batch.state,'ABORTED')


class MaterialCSVCompatibilityTests(unittest.TestCase):
    batch_fixture = OrganicMaterialTests.batch_fixture

    def test_blank_only_csv_comparison_preserves_native_and_typed_values(self):
        for basis in ('__absent__',None,'',' ','material'):
            with self.subTest(basis=basis):
                f=self.batch_fixture(basis=basis)
                raw=f.obs['candidate_projection']
                self.assertEqual('dose_basis' in raw,basis!='__absent__')
                if basis!='__absent__': self.assertEqual(raw['dose_basis'],basis)
                literal=f.obs['body']['records'][0]['native_record']
                self.assertEqual('dose_basis' in literal,basis!='__absent__')
                if basis!='__absent__': self.assertEqual(literal['dose_basis'],basis)
                with f.batch() as batch:
                    result=batch.resolve(f.obs,[],[])
                    result['group_id']=result['connectivity_id']
                    bridge.validate_projection_v2(result,batch._data)
                    path=f.fixture.root/'projection.csv'
                    data.write_csv(path,[result],list(result))
                    with path.open(encoding='utf-8',newline='') as stream: csvrow=next(csv.DictReader(stream))
                    typed=bridge.decode_material_csv(csvrow)
                    bridge.validate_projection_v2(typed,batch._data,csv_projection=True)
                    if basis=='':
                        changed=dict(result,source_dose_basis=None)
                        with self.assertRaises(material.MaterialError): bridge.validate_projection_v2(changed,batch._data)
                        for forged in (' ', 'material'):
                            with self.assertRaises(material.MaterialError):
                                bridge.validate_projection_v2(dict(typed,source_dose_basis=forged),batch._data,csv_projection=True)
                    elif basis in (' ', 'material'):
                        with self.assertRaises(material.MaterialError):
                            bridge.validate_projection_v2(dict(typed,source_dose_basis=None),batch._data,csv_projection=True)
                    batch.finish()


if __name__ == '__main__':
    unittest.main()
