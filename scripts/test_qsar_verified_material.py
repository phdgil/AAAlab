"""Synthetic mechanics only: these fixtures do not certify any real material."""
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts'
sys.path.insert(0, str(SCRIPTS))
import qsar_verified_material as q
from rdkit import rdBase
from qsar_data import chemistry


class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.contract = {'contract_version': 'synthetic-v4', 'synthetic': True,
                         'scientific_policy': {'verified_tested_material_v4': {
                             'execution': {
                                 'schema_version': 1,
                                 'adapter': 'reviewed_material_full_graph_v1',
                                 'rdkit_version': rdBase.rdkitVersion,
                                 'mass_algorithm': 'rdkit_descriptors_molwt',
                                 'predictor_policy': 'complete_verified_material_graph',
                                 'grouping_algorithm': 'sha256_utf8_rdkit_canonical_nonisomeric_smiles',
                             },
                             'limitations': ['Synthetic mechanics only'],
                         }}}
        self.env = {'rdkit_version': rdBase.rdkitVersion}

    def write(self, name, value, lines=False):
        path = self.root / name
        path.write_text(('\n'.join(json.dumps(x) for x in value) if lines else json.dumps(value)), encoding='utf-8')
        return q.file_binding(path)

    def fixture(self, raw='[Na+].[Cl-]', verified=None, status='verified'):
        verified = verified or raw
        body = {'smiles': raw, 'source_formula': None, 'study_id': None,
                'dose_basis_declaration': None, 'value': 3550, 'unit': 'mg/kg'}
        source = self.write('synthetic-native.jsonl', [body], True)
        obs = {'source_id': 'synthetic', 'source_sha256': source['sha256'],
               'native_locator': {'line': 1}, 'candidate_key': 'measurement-1',
               'body': body, 'raw_smiles': raw, 'raw_smiles_field': 'smiles'}
        item = {'observation': obs, 'support_candidates': [], 'blocked_support': []}
        key = q.observation_id(obs)
        facts = q.graph_facts(verified)
        decision = {'observation_id': key, 'status': status,
                    'material_class': 'inorganic', 'review_evidence': ['synthetic source row 1'],
                    'limitations': ['SYNTHETIC: not scientific acceptance', 'Optional metadata absent'],
                    'reopening_conditions': ['new source evidence'], 'critical_conflicts': [],
                    'support_resolutions': [], 'dose_basis': 'reported_material_mass',
                    'verified_smiles': verified, 'formula_unit': facts,
                    'fixed_composition': True, 'hydration_interpretation': 'synthetic reviewed interpretation',
                    'correction': None}
        if verified != raw:
            decision['correction'] = {'observation_id': key, 'raw_smiles': raw,
                                      'verified_smiles': verified, 'evidence': ['synthetic source proof']}
        def replay(entry, locator):
            return json.loads(Path(entry['path']).read_text(encoding='utf-8').splitlines()[locator['line'] - 1])
        registry = {'files': [dict(source, source_id='synthetic')], 'replay_native': replay,
                    'replay_provider': {'implementation': q.file_binding(__file__), 'callable': replay.__qualname__}}
        self.registry = registry
        return obs, item, decision, registry

    def bind(self, items, decisions):
        inventory = self.write('synthetic-inventory.jsonl', items, True)
        decisions_file = self.write('synthetic-decisions.jsonl', decisions, True)
        context = {'contract_sha256': q.digest(self.contract),
                   'implementation_sha256': q.file_binding(q.__file__)['sha256'],
                   'environment': self.env, 'inventory': inventory, 'decisions': decisions_file,
                   'replay_provider': self.registry['replay_provider'], 'sources': self.registry['files']}
        receipt = {'schema_version': 1, 'synthetic': True, 'result': 'PASS',
                   'producer_execution_id': 'synthetic-producer', 'reviewer_execution_id': 'synthetic-reviewer',
                   'context': context, 'scope_ids': sorted(q.observation_id(x['observation']) for x in items),
                   'native_review': self.write('synthetic-return.json', {'synthetic': True, 'text': 'synthetic independent return'})}
        receipt_file = self.write('synthetic-receipt.json', receipt)
        return {'inventory': inventory, 'decisions': decisions_file, 'receipt': receipt_file,
                'actual_review': dict(receipt, receipt=receipt_file)}

    def load(self, item, decision, registry):
        binding = self.bind([item], [decision])
        return q.load_reviewed_materials(binding, self.contract, registry, self.env)

    def result(self, raw, verified=None):
        obs, item, decision, registry = self.fixture(raw, verified)
        index = self.load(item, decision, registry)
        return q.resolve_material(obs, [], [], index), index

    def test_execution_policy_requires_exact_supported_fields(self):
        baseline = copy.deepcopy(self.contract)
        execution = baseline['scientific_policy']['verified_tested_material_v4']['execution']
        for field in execution:
            for value in ('missing', None, '', False, {}, [], 'unsupported'):
                with self.subTest(field=field, value=value):
                    self.contract = copy.deepcopy(baseline)
                    current = self.contract['scientific_policy']['verified_tested_material_v4']['execution']
                    if value == 'missing':
                        current.pop(field)
                    else:
                        current[field] = value
                    obs, item, decision, registry = self.fixture()
                    # Rebind receipts to each invalid contract: rejection must be policy-based.
                    with self.assertRaisesRegex(q.MaterialError, 'execution policy'):
                        self.load(item, decision, registry)
        for version in (True, 1.0, 2):
            self.contract = copy.deepcopy(baseline)
            self.contract['scientific_policy']['verified_tested_material_v4']['execution']['schema_version'] = version
            obs, item, decision, registry = self.fixture()
            with self.assertRaisesRegex(q.MaterialError, 'execution policy: schema_version'):
                self.load(item, decision, registry)
        self.contract = copy.deepcopy(baseline)
        self.contract['scientific_policy']['verified_tested_material_v4']['execution']['fragment_parent'] = True
        obs, item, decision, registry = self.fixture()
        with self.assertRaisesRegex(q.MaterialError, 'Exact material execution policy'):
            self.load(item, decision, registry)

    def test_missing_or_malformed_policy_containers_fail_closed(self):
        baseline = copy.deepcopy(self.contract)
        for path in (('scientific_policy',),
                     ('scientific_policy', 'verified_tested_material_v4'),
                     ('scientific_policy', 'verified_tested_material_v4', 'execution')):
            for value in ('missing', None, {}, [], '', True):
                with self.subTest(path=path, value=value):
                    self.contract = copy.deepcopy(baseline)
                    container = self.contract
                    for field in path[:-1]:
                        container = container[field]
                    if value == 'missing':
                        container.pop(path[-1])
                    else:
                        container[path[-1]] = value
                    obs, item, decision, registry = self.fixture()
                    with self.assertRaises(q.MaterialError):
                        self.load(item, decision, registry)

    def test_policy_rdkit_must_match_runtime_even_if_environment_agrees(self):
        self.contract['scientific_policy']['verified_tested_material_v4']['execution']['rdkit_version'] = 'unsupported'
        self.env['rdkit_version'] = 'unsupported'
        obs, item, decision, registry = self.fixture()
        with self.assertRaisesRegex(q.MaterialError, 'execution policy: rdkit_version'):
            self.load(item, decision, registry)

    def test_optional_scientific_policy_metadata_remains_bound(self):
        policy = self.contract['scientific_policy']['verified_tested_material_v4']
        policy.update(source_formula=None, study_id=None, evidence=['synthetic'], notes={'scope': 'test'})
        row, index = self.result('[Na+].[Cl-]')
        self.assertEqual(row['status'], 'VerifiedMaterial')
        self.assertEqual(json.loads(index.payload)['context']['contract_sha256'], q.digest(self.contract))

    def test_nacl_full_graph_and_permutation_and_scaling(self):
        first, _ = self.result('[Na+].[Cl-]')
        second, _ = self.result('[Cl-].[Na+]')
        doubled, _ = self.result('[Cl-].[Na+].[Cl-].[Na+]')
        self.assertEqual(first['status'], 'VerifiedMaterial')
        self.assertAlmostEqual(first['formula_unit_mw'], 58.443)
        self.assertEqual(first['material_group_id'], second['material_group_id'])
        self.assertNotEqual(first['material_group_id'], doubled['material_group_id'])
        self.assertAlmostEqual(doubled['formula_unit_mw'], 2 * first['formula_unit_mw'])
        targets = [-math.log10(3550 / (1000 * row['formula_unit_mw'])) for row in (first, doubled)]
        self.assertAlmostEqual(targets[1] - targets[0], math.log10(2))

    def test_carbon_inorganics_and_single_fragment(self):
        for smi in ('[Na+].[Na+].[O-]C([O-])=O', '[Na+].[C-]#N', '[Cu]'):
            row, _ = self.result(smi)
            self.assertEqual(row['status'], 'VerifiedMaterial')
            self.assertEqual(row['predictor_graph'], q.graph_facts(smi)['canonical_smiles'])

    def test_explicit_three_waters_and_isotopes(self):
        row, _ = self.result('[Cu+2].[O-][N+](=O)[O-].[O-][N+](=O)[O-].O.O.O')
        self.assertEqual(row['formula_unit']['components']['O'], 3)
        self.assertEqual(row['formula_unit']['atoms']['H:0'], 6)
        facts = q.graph_facts('[2H]O[2H]')
        self.assertEqual(facts['atoms']['H:2'], 2)
        self.assertNotEqual(facts['atoms'], q.graph_facts('O')['atoms'])

    def test_copper_requires_scoped_correction_and_preserves_raw(self):
        obs, item, decision, registry = self.fixture('O.[Cu].[Cu]', '[Cu+].[Cu+].[O-2]')
        decision['correction'] = None
        with self.assertRaises(ValueError):
            self.load(item, decision, registry)
        obs, item, decision, registry = self.fixture('O.[Cu].[Cu]', '[Cu+].[Cu+].[O-2]')
        index = self.load(item, decision, registry)
        row = q.resolve_material(obs, [], [], index)
        self.assertAlmostEqual(row['raw_mw'], 145.107)
        self.assertAlmostEqual(row['formula_unit_mw'], 143.091)
        self.assertEqual(row['original_structure'], row['verified_material_graph'])
        self.assertEqual(row['parent_smiles'], row['predictor_graph'])
        self.assertTrue(q.validate_material_projection(row, index, self.env))
        forged = dict(row, original_mw=row['raw_mw'])
        with self.assertRaises(ValueError):
            q.validate_material_projection(forged, index, self.env)

    def test_material_certificate_never_repairs_basis_or_endpoint(self):
        for conflict in ('elemental_dose', 'range_endpoint', 'hydrate_unknown'):
            obs, item, decision, registry = self.fixture('O.[Cu].[Cu]', '[Cu+].[Cu+].[O-2]')
            decision['critical_conflicts'] = [conflict]
            index = self.load(item, decision, registry)
            self.assertEqual(q.resolve_material(obs, [], [], index)['status'], 'ReviewedHold')
        obs, item, decision, registry = self.fixture()
        decision['dose_basis'] = 'elemental_mass'
        self.assertEqual(q.resolve_material(obs, [], [], self.load(item, decision, registry))['status'], 'ReviewedHold')

    def test_variable_formula_and_composition_mismatch_rejected(self):
        for mutate in (lambda d: d.update(fixed_composition=False),
                       lambda d: d['formula_unit']['atoms'].update({'H:0': 2}),
                       lambda d: d['formula_unit'].update(mw=float('inf')),
                       lambda d: d['formula_unit'].update(formula='Na2(Sx)')):
            obs, item, decision, registry = self.fixture()
            mutate(decision)
            with self.assertRaises(ValueError):
                self.load(item, decision, registry)

    def test_optional_null_metadata_and_organic_legacy(self):
        row, index = self.result('[Na+].[Cl-]')
        self.assertEqual(row['status'], 'VerifiedMaterial')
        obs = {'source_id': 'other', 'source_sha256': 'a'*64, 'native_locator': {'line': 2},
               'candidate_key': 'other', 'body': {'smiles': 'CC(=O)[O-].[Na+]'}, 'raw_smiles': 'CC(=O)[O-].[Na+]'}
        before = chemistry(obs['raw_smiles'], '', 'fragment_parent')
        self.assertEqual(q.resolve_material(obs, [], [], index)['status'], 'NotApplicable')
        self.assertEqual(before, chemistry(obs['raw_smiles'], '', 'fragment_parent'))
        self.assertNotIn('[Na+]', before['parent_smiles'])

    def test_missing_duplicate_and_wrong_scope_decisions(self):
        obs, item, decision, registry = self.fixture()
        for decisions in ([], [decision, decision], [dict(decision, observation_id='wrong')]):
            with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                q.load_reviewed_materials(self.bind([item], decisions), self.contract, registry, self.env)

    def test_contract_receipt_source_and_environment_tampering(self):
        for mutation in ('contract', 'receipt', 'descriptor', 'source', 'environment'):
            obs, item, decision, registry = self.fixture()
            binding = self.bind([item], [decision])
            contract, env = copy.deepcopy(self.contract), dict(self.env)
            if mutation == 'contract': contract['unrelated_policy'] = 'changed'
            if mutation == 'receipt': Path(binding['receipt']['path']).write_text('{}')
            if mutation == 'descriptor': binding['actual_review']['scope_ids'] = []
            if mutation == 'source': Path(registry['files'][0]['path']).write_text('{}')
            if mutation == 'environment': env['rdkit_version'] = 'stale'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                q.load_reviewed_materials(binding, contract, registry, env)

    def test_native_membership_and_affected_scope_no_fallback(self):
        obs, item, decision, registry = self.fixture()
        index = self.load(item, decision, registry)
        altered = copy.deepcopy(obs)
        altered['body']['value'] = 1
        self.assertEqual(q.resolve_material(altered, [], [], index)['status'], 'PendingMaterialReview')
        item['observation'] = altered
        decision['observation_id'] = q.observation_id(altered)
        with self.assertRaises(ValueError): self.load(item, decision, registry)

    def test_support_conflicts_cannot_be_omitted_or_globally_waived(self):
        obs, item, decision, registry = self.fixture()
        item['blocked_support'] = [{'reason': 'identity_conflict', 'id': 'a'}]
        with self.assertRaises(ValueError): self.load(item, decision, registry)
        decision['support_resolutions'] = [{'support_sha256': q.digest(item['blocked_support'][0]),
                                            'disposition': 'unresolved', 'evidence': ['synthetic']}]
        index = self.load(item, decision, registry)
        self.assertEqual(q.resolve_material(obs, [], item['blocked_support'], index)['status'], 'ReviewedHold')
        self.assertEqual(q.resolve_material(obs, [], [], index)['status'], 'PendingMaterialReview')

    def test_unresolved_and_reviewed_hold_are_not_acceptance(self):
        for status, expected in [('unresolved', 'PendingMaterialReview'), ('reviewed_hold', 'ReviewedHold')]:
            obs, item, decision, registry = self.fixture(status=status)
            self.assertEqual(q.resolve_material(obs, [], [], self.load(item, decision, registry))['status'], expected)

    def test_missing_replay_provider_and_wrong_binding_reject(self):
        for field in ('replay_native', 'replay_provider'):
            obs, item, decision, registry = self.fixture()
            binding = self.bind([item], [decision])
            registry.pop(field)
            with self.assertRaises(ValueError):
                q.load_reviewed_materials(binding, self.contract, registry, self.env)

    def test_source_formula_contradiction_cannot_hide_in_graph_facts(self):
        obs, item, decision, registry = self.fixture('O.[Cu].[Cu]')
        obs['body']['source_formula'] = 'Cu2O'
        source = self.write('synthetic-native.jsonl', [obs['body']], True)
        registry['files'][0].update(source)
        obs['source_sha256'] = source['sha256']
        decision['observation_id'] = q.observation_id(obs)
        with self.assertRaises(ValueError): self.load(item, decision, registry)
        decision['source_formula_interpretation'] = {'source_formula': 'Cu2O', 'formula': 'Cu2O', 'evidence': ['synthetic']}
        with self.assertRaises(ValueError): self.load(item, decision, registry)

    def test_changed_candidate_key_cannot_fall_back(self):
        obs, item, decision, registry = self.fixture()
        index = self.load(item, decision, registry)
        obs['candidate_key'] = 'swapped'
        self.assertEqual(q.resolve_material(obs, [], [], index)['status'], 'PendingMaterialReview')

    def test_target_projection_and_no_endpoint_grant(self):
        row, index = self.result('[Na+].[Cl-]')
        self.assertNotIn('route', row)
        self.assertNotIn('target_pLD50', row)
        row.update(target_mg_kg=3550, target_mol_kg=3550/(1000*row['formula_unit_mw']))
        row['target_pLD50'] = -math.log10(row['target_mol_kg'])
        self.assertTrue(q.validate_material_projection(row, index, self.env))
        row['target_pLD50'] += 1
        with self.assertRaises(ValueError): q.validate_material_projection(row, index, self.env)

    def test_sulfate_stoichiometries_do_not_merge(self):
        three, _ = self.result('[Cu+2].[Cu+2].[Cu+2].[OH-].[OH-].[O-]S(=O)(=O)[O-].[O-]S(=O)(=O)[O-]')
        four, _ = self.result('[Cu+2].[Cu+2].[Cu+2].[Cu+2].[OH-].[OH-].[OH-].[OH-].[OH-].[OH-].[O-]S(=O)(=O)[O-]')
        self.assertNotEqual(three['material_group_id'], four['material_group_id'])
        self.assertNotEqual(three['formula_unit_mw'], four['formula_unit_mw'])

    def test_scoped_material_support_resolution_only(self):
        obs, item, decision, registry = self.fixture()
        support = {'reason': 'material_identity_conflict', 'locator': 'synthetic:2'}
        item['blocked_support'] = [support]
        decision['support_resolutions'] = [{'support_sha256': q.digest(support),
                                            'disposition': 'resolved_material_identity', 'evidence': ['synthetic review']}]
        index = self.load(item, decision, registry)
        self.assertEqual(q.resolve_material(obs, [], [support], index)['status'], 'VerifiedMaterial')
        support['reason'] = 'unparsed_required_support'
        decision['support_resolutions'][0]['support_sha256'] = q.digest(support)
        with self.assertRaises(ValueError): self.load(item, decision, registry)

    def test_projection_and_receipt_mutations(self):
        row, index = self.result('[Na+].[Cl-]')
        for field, value in [('raw_structure', '[K+].[Cl-]'), ('predictor_graph', '[Na+]'),
                             ('material_group_id', 'wrong'), ('formula_unit_mw', 1),
                             ('parent_mw', 1), ('normalization', 'fragment_parent')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                q.validate_material_projection(dict(row, **{field: value}), index, self.env)
        obs, item, decision, registry = self.fixture()
        binding = self.bind([item], [decision])
        binding['actual_review']['reviewer_execution_id'] = 'synthetic-producer'
        with self.assertRaises(ValueError):
            q.load_reviewed_materials(binding, self.contract, registry, self.env)

    def test_material_group_is_exact_pinned_utf8_hash(self):
        import hashlib
        row, _ = self.result('[Na+].[Cl-]')
        self.assertEqual(row['material_group_id'], hashlib.sha256(b'[Cl-].[Na+]').hexdigest())

    def test_component_charge_and_isotope_claim_tampering(self):
        for mutate in (lambda f: f['components'].update({'O': 3}),
                       lambda f: f.update(formal_charge=1),
                       lambda f: f['atom_formal_charges'].update({'Na:0:1': 2}),
                       lambda f: f['atoms'].update({'Na:23': 1})):
            obs, item, decision, registry = self.fixture()
            mutate(decision['formula_unit'])
            with self.assertRaises(ValueError): self.load(item, decision, registry)

    def test_stale_loaded_index_and_no_bound_replay_fallback(self):
        obs, item, decision, registry = self.fixture()
        index = self.load(item, decision, registry)
        Path(registry['files'][0]['path']).write_text('{}')
        with self.assertRaises(ValueError): q.resolve_material(obs, [], [], index)
        obs, item, decision, registry = self.fixture()
        registry['replay_provider']['callable'] = 'unbound'
        with self.assertRaises(ValueError): self.load(item, decision, registry)

    def test_native_body_membership_is_recursively_type_exact(self):
        for native_value, inventory_value in ((0, False), (3550, 3550.0),
                                               ({'nested': [0]}, {'nested': [False]}),
                                               ({'nested': [3550]}, {'nested': [3550.0]})):
            with self.subTest(native=native_value, inventory=inventory_value):
                obs, item, decision, registry = self.fixture()
                native_body = dict(obs['body'], native_extra=native_value)
                source = self.write('synthetic-native.jsonl', [native_body], True)
                registry['files'][0].update(source)
                obs['source_sha256'] = source['sha256']
                obs['body']['native_extra'] = inventory_value
                decision['observation_id'] = q.observation_id(obs)
                with self.assertRaises(q.MaterialError):
                    self.load(item, decision, registry)

    def test_support_resolutions_requires_explicit_array_even_without_conflicts(self):
        for invalid in ('missing', None, {}, ''):
            with self.subTest(invalid=invalid):
                obs, item, decision, registry = self.fixture()
                if invalid == 'missing':
                    decision.pop('support_resolutions')
                else:
                    decision['support_resolutions'] = invalid
                with self.assertRaises(q.MaterialError):
                    self.load(item, decision, registry)



class OrganicV1BoundaryTests(unittest.TestCase):
    def test_isolated_v1_cannot_accept_organic_schema3(self):
        f=MaterialTests(); f.setUp(); self.addCleanup(f.doCleanups)
        obs,item,decision,registry=f.fixture(raw='CC[NH3+].[Cl-]')
        decision.update(schema_version=3,material_class='organic',principal_component=dict(canonical_smiles='CC[NH3+]',multiplicity=1,
            source_evidence=[],supplemental_evidence=[],selection_rationale='SYNTHETIC'))
        with self.assertRaises(q.MaterialError): f.load(item,decision,registry)


if __name__ == '__main__':
    unittest.main()
