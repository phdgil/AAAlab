"""Finite ordinary curation integration fixtures; synthetic envelopes only."""
import copy
import csv
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts.test_qsar_ordinary_adjudication import Fixture, adj, data, native as bridge, material
from scripts import test_qsar_curation_evidence as coverage
from scripts import test_qsar_material_integration as material_tests
from scripts import test_qsar_runtime as runtime_tests
from scripts.test_qsar_prepare import MODULE as prepare
from rdkit import Chem, rdBase


class IntegrationFixture(Fixture):
    def __init__(self, root, adapter='npass', count=8, material_enabled=False, default_sheet=False):
        self.material_enabled = material_enabled
        self.default_sheet = default_sheet
        self.count = count
        self.base = root
        packet = root / 'packet'
        packet.mkdir()
        super().__init__(packet, adapter, count)
        if default_sheet:
            replacements = {}
            current = [bridge.observation(raw, self.config, self.entry, number, 'Data') for number, raw in data.rows(self.source, 'Data')]
            for old, new, decision in zip(self.observations, current, self.decisions):
                replacements[old['body']['records'][0]['record_id']] = new['body']['records'][0]['record_id']
                decision.update(member_key=bridge.member_key(new), native_observation_sha256=material.digest(new))
            def rebind(value):
                if type(value) is dict:
                    return {k: rebind(v) for k, v in value.items()}
                if type(value) is list:
                    result = [rebind(v) for v in value]
                    if result and type(result[0]) is dict and ('kind' in result[0] or 'record_id' in result[0]):
                        result.sort(key=material.digest)
                    return result
                return replacements.get(value, value) if type(value) is str else value
            self.decisions = rebind(self.decisions)
            self.observations = current
        self.notices['source_id'] = self.entry['source_id']
        self.seal()
        self.external = root / 'external.json'
        self.write(self.external, {self.entry['source_id']: self.binding})
        self.out = root / 'curation'
        self.manifest = self.out / 'curation_manifest.json'
        self.packet = None
        if material_enabled:
            self.build_material_packet()

    def write(self, path, value):
        if path.name == 'registry.json':
            raw = self.base / 'raw'
            raw.mkdir()
            target = raw / 'source.tsv'
            shutil.copyfile(self.source, target)
            self.source = target
            self.config['source_id'] = 'source.tsv'
            if not self.default_sheet:
                self.config['sheet'] = ''
            else:
                self.config.pop('sheet', None)
            if self.adapter == 'ice_oral':
                self.config['allowed_test_materials'] = ['']
                self.config['columns'] = dict(smiles='fixture_smiles')
                with target.open(encoding='utf-8') as stream:
                    rows = list(csv.DictReader(stream, delimiter='\t'))
                for row in rows:
                    row['fixture_smiles'] = 'CCO'
                with target.open('w', newline='', encoding='utf-8') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t')
                    writer.writeheader()
                    writer.writerows(rows)
                if self.default_sheet:
                    import openpyxl
                    book = openpyxl.Workbook()
                    sheet = book.active
                    sheet.title = 'Data'
                    sheet.append(list(rows[0]))
                    for row in rows:
                        sheet.append([row[k] for k in rows[0]])
                    xlsx = raw / 'source.xlsx'
                    book.save(xlsx)
                    book.close()
                    target.unlink()
                    target = xlsx
                    self.source = target
                    self.config['source_id'] = target.name
            if self.adapter == 'toxric':
                self.config['constants'] = dict(species='Rat', endpoint='LD50', unit='mg/kg')
                self.config['unit_evidence'] = dict(unit='mg/kg', citation='Synthetic documented native mass unit', receipt_source_id='toxric-receipt.json')
                Fixture.write(raw / 'toxric-receipt.json', dict(scope='Synthetic original-value export',
                    files=[dict(filename=target.name, sha256=data.digest(target), url='https://example.invalid/synthetic-toxric')]))
            support = raw / 'support.tsv'
            with support.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream, delimiter='\t')
                writer.writerow(['np_id', 'SMILES', 'InChI', 'InChIKey'])
                for i in [*range(self.count), 0]:
                    smiles = 'C' * (i + 1) + 'O'
                    mol = Chem.MolFromSmiles(smiles)
                    writer.writerow([f'NP{i}', smiles, Chem.MolToInchi(mol), Chem.MolToInchiKey(mol)])
            self.collection = self.base / 'collection'
            data.collect(raw, self.collection)
            self.registry_path = self.collection / 'source_registry.json'
            self.registry = data.load(self.registry_path)
            self.entry = next(e for e in self.registry['files'] if e['source_id'] == target.name)
            self.support = next(e for e in self.registry['files'] if e['source_id'] == 'support.tsv')
            return
        if path.name == 'contract.json':
            value.update(version='synthetic-ordinary-integration', contract_version='synthetic-ordinary-integration', synthetic=True,
                execution_policy=dict(allow_training_with_unresolved_required_source_coverage=False),
                preparation=dict(target_column='target_pLD50', group_column='group_id', seed=9, terminal_fraction=.25,
                    outer_folds=3, inner_folds=2, intended_generalization='synthetic', terminal_custodian='fixture',
                    prior_exposure=dict(status='none_known', evidence=[], description='synthetic'), unfitted_transforms=dict(scaling='fold-local')))
            value['data_curation'].update(chemistry_policy='single_component', relation_policy='reported_numeric',
                replicate_policy='preserve_flag', tasks=[dict(id='rat', species='Rat', route='oral', endpoint='LD50')],
                identity_sources=[dict(source_id='support.tsv', adapter='npass_structure_v1')])
            if self.material_enabled:
                fixture = material_tests.MaterialV2BatchTests()
                fixture.setUp()
                try:
                    value['scientific_policy'].update(fixture.fixture.contract['scientific_policy'])
                finally:
                    fixture.doCleanups()
        Fixture.write(path, value)

    def seal(self):
        super().seal()
        # Full registry entries are bound through the exact finite projection.
        receipt = data.load(self.root / 'receipt.json')
        receipt['context']['source_files'] = sorted(({k: e[k] for k in ('source_id', 'path', 'sha256')}
            for e in self.registry['files']), key=lambda e: e['source_id'])
        self.write(self.root / 'receipt.json', receipt)
        descriptor = material.file_binding(self.root / 'receipt.json')
        self.binding['review'] = dict(receipt=descriptor, actual_review=dict(receipt, receipt=descriptor))

    def descriptor(self, name, value, lines=False):
        path = self.root / name
        path.write_bytes(b''.join(material.canonical(v) + b'\n' for v in value) if lines else material.canonical(value))
        return material.file_binding(path)

    def build_material_packet(self, stale=False):
        packet = dict(schema_version=1, kind='qsar_material_curation_v1', full_contract=self.contract_file,
            accepted_contract_review=self.acceptance_file, source_registry=material.file_binding(self.registry_path),
            adjudication_config=material.file_binding(self.external), environment=dict(rdkit_version=rdBase.rdkitVersion),
            replay_provider=dict(implementation=material.file_binding(bridge.__file__), callable='NativeReplayStore.replay_native',
                dependencies=[b for b in bridge.code_bindings() if b['path'] != str(Path(bridge.__file__).resolve())]))
        engine = self.engine()
        with tempfile.TemporaryDirectory(dir=self.base) as scratch:
            store = bridge.NativeReplayStore(scratch)
            data.identity_index(self.contract['data_curation'], self.registry, raw_support_observer=store.observe_support)
            classifications = []
            self.projected = []
            for number, raw in data.rows(self.entry['path']):
                untouched = bridge.observation(raw, self.config, self.entry, number, '')
                scientific = bridge.ScientificResult(engine, untouched)
                pre = data.pre_identity_observation(raw, self.config, self.contract['data_curation'], self.entry, number, '',
                    scientific_result=None if stale else scientific)
                obs = untouched if stale else bridge.observation(raw, self.config, self.entry, number, '', scientific_result=scientific)
                self.projected.append(obs)
                store.stage(obs, raw, pre, number, '', False)
                if pre.requires_classification:
                    classifications.append(dict(schema_version=1, member_key=bridge.member_key(obs),
                        **{k: obs[k] for k in ('source_id', 'source_sha256', 'native_locator', 'candidate_key')},
                        observation_id=material.digest(obs), support_sha256=material.digest(store.supports(pre)),
                        classification='unaffected_organic', review_evidence=['Synthetic physical row classification']))
            scope = store.scope()
            store.close()
        engine.finish()
        packet['classification'] = self.descriptor('classification.jsonl', sorted(classifications, key=lambda c: c['member_key']), True)
        context = bridge.classification_context(packet, self.contract, self.registry, scope)
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-classifier',
            reviewer_execution_id='synthetic-classification-reviewer', context=context,
            scope_ids=sorted(c['member_key'] for c in classifications), native_review=self.common['review_evidence'])
        file = self.descriptor('classification-review.json', receipt)
        packet['classification_review'] = dict(receipt=file, actual_review=dict(receipt, receipt=file))
        inventory = self.descriptor('materials.jsonl', [], True)
        decisions = self.descriptor('material-decisions.jsonl', [], True)
        context = dict(contract_sha256=material.digest(self.contract), implementation_sha256=material.file_binding(material.__file__)['sha256'],
            environment=packet['environment'], inventory=inventory, decisions=decisions, replay_provider=packet['replay_provider'],
            sources=self.registry['files'], integration=dict(schema_version=1, classification_context=context, classification_review=file))
        receipt = dict(schema_version=1, result='PASS', synthetic=True, producer_execution_id='synthetic-material-producer',
            reviewer_execution_id='synthetic-material-reviewer', context=context, scope_ids=[], native_review=self.common['review_evidence'])
        file = self.descriptor('material-review.json', receipt)
        packet['materials'] = dict(inventory=inventory, decisions=decisions, receipt=file, actual_review=dict(receipt, receipt=file))
        self.packet = self.descriptor('material-packet.json', packet)

    def produce(self):
        data.curate(self.contract_path, self.registry_path, self.out, self.external, self.packet['path'] if self.packet else None)
        return data.load(self.manifest)

    def check(self):
        return coverage.evidence.validate(self.contract_path, self.manifest, self.out / 'curated.csv')

    def rebind_output(self, name):
        manifest = data.load(self.manifest)
        manifest['bound_evidence']['outputs'][name] = material.file_binding(self.out / name)
        data.dump(self.manifest, manifest)

    def rewrite_csv(self, name, change):
        path = self.out / name
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        change(rows)
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        self.rebind_output(name)


class OrdinaryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_eight_rows_ordinary_and_material_paths(self):
        for enabled in (False, True):
            with self.subTest(material=enabled), tempfile.TemporaryDirectory() as directory:
                f = IntegrationFixture(Path(directory), material_enabled=enabled)
                manifest = f.produce()
                self.assertEqual(manifest['input_rows'], 8)
                self.assertEqual(manifest['output_rows'], 3)
                self.assertEqual(manifest['dispositions'], dict(retained=3, excluded=3 if enabled else 2, quarantined=2 if enabled else 3))
                refs = f.check()
                self.assertIn(f.binding['known_evidence_notices'], refs)
                with (f.out / 'curated.csv').open(encoding='utf-8') as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual({r['output_row_id'] for r in rows}, {'source.tsv::2:rat', 'source.tsv::6:rat', 'source.tsv::7:rat'})
                for row in rows:
                    self.assertEqual(float(row['target_mg_kg']), 100)
                if enabled:
                    scope = manifest['material_integration']['classification_context']['scope']
                    self.assertEqual((scope['eligible_count'], scope['nonmaterial_count']), (5, 3))
                    self.assertEqual(f.projected[0]['body'], f.observations[0]['body'])
                    self.assertEqual(bridge.member_key(f.projected[0]), bridge.member_key(f.observations[0]))
                    self.assertNotEqual(material.digest(f.projected[0]), material.digest(f.observations[0]))

    def test_stale_preroute_material_classification_rejects(self):
        f = IntegrationFixture(self.root, material_enabled=True)
        f.build_material_packet(stale=True)
        with self.assertRaises(ValueError):
            f.produce()
        self.assertFalse(f.manifest.exists())

    def test_four_native_adapters(self):
        for adapter in ('npass', 'ice_oral', 'oecd', 'toxric'):
            with self.subTest(adapter=adapter), tempfile.TemporaryDirectory() as directory:
                f = IntegrationFixture(Path(directory), adapter=adapter, count=1)
                self.assertEqual(f.produce()['output_rows'], 1)
                self.assertTrue(f.check())

    def test_final_gate_rejects_issues_even_alongside_holds(self):
        for enabled in (False, True):
            for domain in ('route', 'extraction', 'support', 'join', 'tested_identity', 'dose_basis'):
                with self.subTest(material=enabled, domain=domain), tempfile.TemporaryDirectory() as directory:
                    f = IntegrationFixture(Path(directory), material_enabled=enabled)
                    f.decisions[2]['remaining_issues'] = [dict(domain=domain, reason='Synthetic unresolved work', evidence=[f.ref('/2/question')])]
                    f.seal()
                    f.write(f.external, {f.entry['source_id']: f.binding})
                    if enabled:
                        f.build_material_packet()
                    with self.assertRaisesRegex(ValueError, 'Unresolved ordinary scientific'):
                        f.produce()
                    self.assertFalse(f.manifest.exists())
                    self.assertFalse((f.out / 'manifest.json').exists())

    def test_rebound_output_forgery_and_eligible_sibling_omission(self):
        for enabled in (False, True):
            with self.subTest(material=enabled), tempfile.TemporaryDirectory() as directory:
                f = IntegrationFixture(Path(directory), material_enabled=enabled)
                f.produce()
                originals = {name: (f.out / name).read_bytes() for name in ('curated.csv', 'dispositions.csv', 'identity_ledger.csv')}
                mutations = [('curated.csv', lambda rows: rows[0].update(output_row_id='source.tsv::4:rat', row_id='source.tsv::4')),
                             ('curated.csv', lambda rows: rows.pop()),
                             ('curated.csv', lambda rows: rows[0].update(target_mg_kg='999')),
                             ('dispositions.csv', lambda rows: rows[6].update(native_record=json.dumps(dict(json.loads(rows[6]['native_record']), activity_relation='=')))),
                             ('dispositions.csv', lambda rows: rows[2].update(scientific_dispositions='[]')),
                             ('identity_ledger.csv', lambda rows: rows[0].update(native_record='{}'))]
                for name, mutate in mutations:
                    f.rewrite_csv(name, mutate)
                    with self.subTest(name=name), self.assertRaises(coverage.evidence.EvidenceError):
                        f.check()
                    (f.out / name).write_bytes(originals[name])
                    f.rebind_output(name)

    def test_plain_result_fake_engine_and_wrong_native_cannot_project(self):
        f = IntegrationFixture(self.root)
        number, raw = next(data.rows(f.entry['path']))
        with self.assertRaises(material.MaterialError):
            data.pre_identity_observation(raw, f.config, f.contract['data_curation'], f.entry, number, '', scientific_result={'route_override': 'oral'})
        with self.assertRaises(material.MaterialError):
            bridge.ScientificResult(type('Fake', (), {'resolve': lambda self, obs: {}})(), f.observations[0])
        result = bridge.ScientificResult(f.engine(), f.observations[0])
        with self.assertRaises(AttributeError):
            result._encoded = b'{}'
        changed = dict(raw, activity_relation='=forged')
        with self.assertRaises(material.MaterialError):
            data.pre_identity_observation(changed, f.config, f.contract['data_curation'], f.entry, number, '', scientific_result=result)

    def test_staged_result_mutation_aborts_material_batch(self):
        f = IntegrationFixture(self.root, material_enabled=True)
        original = bridge.MaterialCurationBatch.seal_native
        def mutate(batch):
            original(batch)
            batch.store.db.execute("UPDATE scientific SET body=?", (b'{}',))
        with patch.object(bridge.MaterialCurationBatch, 'seal_native', mutate):
            with self.assertRaisesRegex(material.MaterialError, 'Staged scientific result mismatch'):
                f.produce()
        self.assertFalse(f.manifest.exists())

    def test_publication_failure_cleans_markers(self):
        f = IntegrationFixture(self.root, material_enabled=True)
        with patch.object(data, 'handoff', side_effect=OSError('Synthetic publication failure')):
            with self.assertRaises(OSError):
                f.produce()
        self.assertFalse(f.manifest.exists())
        self.assertFalse((f.out / 'manifest.json').exists())
        self.assertFalse(list(f.out.glob('.material-*')))

    def test_external_unknown_duplicate_and_unsupported_bindings(self):
        f = IntegrationFixture(self.root)
        for sources, mapping in [([f.config], {'unknown': f.binding}),
                                 ([dict(f.config, ordinary_adjudication=f.binding)], {f.entry['source_id']: f.binding}),
                                 ([dict(f.config, adapter='toxval')], {f.entry['source_id']: f.binding})]:
            with self.assertRaises(data.DataError):
                data.apply_adjudication_bindings(copy.deepcopy(sources), mapping, f.contract_path)

    def test_ice_default_sheet_end_to_end_guard(self):
        f = IntegrationFixture(self.root, adapter='ice_oral', count=1, default_sheet=True)
        self.assertNotIn('sheet', f.config)
        self.assertEqual(f.produce()['output_rows'], 1)
        self.assertTrue(f.check())
        with (f.out / 'curated.csv').open(encoding='utf-8') as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['output_row_id'], 'source.xlsx:Data:2:rat')

    def runtime_fixture(self, wrong_review=False, material_enabled=False):
        f = IntegrationFixture(self.root, material_enabled=material_enabled)
        runtime = runtime_tests.RuntimeTests()
        runtime.setUp()
        self.addCleanup(runtime.tearDown)
        runtime.rt.replace_contract(str(f.contract_path), 'Synthetic ordinary integration')
        dispatch = runtime.bind(runtime.prepare('contract'))
        candidate = Path(dispatch['owned_output_dir']) / 'contract-candidate.json'
        data.dump(candidate, dict(contract=f.contract_file))
        runtime.rt.submit(dispatch['dispatch_id'], dict(outputs=[str(candidate)], summary='Synthetic contract', checks=['fixture']))
        review = runtime.accept('contract')
        acceptance = data.load(f.acceptance_file['path'])
        acceptance.update(candidate=material.file_binding(candidate), producer_execution_id=dispatch['native_execution_id'],
            reviewer_execution_id=review['reviewer_execution_id'], review_evidence=material.file_binding(review['evidence_bindings'][0]['path']))
        if wrong_review:
            acceptance['review_evidence'] = f.common['review_evidence']
        f.acceptance_file = f.descriptor('acceptance.json', acceptance)
        dispatch = runtime.bind(runtime.prepare('collection'))
        f.collection = Path(dispatch['owned_output_dir']) / 'collected'
        data.collect(f.base / 'raw', f.collection)
        f.registry_path = f.collection / 'source_registry.json'
        f.registry = data.load(f.registry_path)
        runtime.rt.submit(dispatch['dispatch_id'], dict(outputs=[str(p) for p in f.collection.iterdir() if p.is_file()],
            summary='Synthetic collection', checks=['fixture']))
        runtime.accept('collection')
        f.seal()
        f.write(f.external, {f.entry['source_id']: f.binding})
        if material_enabled:
            f.build_material_packet()
        dispatch = runtime.bind(runtime.prepare('curation'))
        f.out = Path(dispatch['owned_output_dir']) / 'curated'
        f.manifest = f.out / 'curation_manifest.json'
        f.produce()
        runtime.rt.submit(dispatch['dispatch_id'], dict(outputs=[str(p) for p in f.out.iterdir() if p.is_file()],
            summary='Synthetic ordinary curation', checks=['fixture'], coverage=data.load(f.manifest)))
        return f, runtime

    def test_runtime_pass_cached_reuse_and_new_notice_invalidation(self):
        f, runtime = self.runtime_fixture()
        runtime.accept('curation')
        state = runtime.rt.status()
        self.assertEqual(state['stages']['curation']['status'], 'accepted')
        # Cached validation must keep every transitive ordinary evidence binding.
        refs = state['stages']['curation']['review']['curation_evidence_bindings']
        self.assertIn(f.binding['known_evidence_notices']['path'], {r['path'] for r in refs})
        with patch.object(adj.OrdinaryAdjudication, '__init__', side_effect=AssertionError('Cached reuse must not reparse native sources')):
            dispatch = runtime.bind(runtime.prepare('preprocessing'))
        output = Path(dispatch['owned_output_dir']) / 'untrusted.json'
        data.dump(output, dict(synthetic=True))
        notices = Path(f.binding['known_evidence_notices']['path'])
        notices.write_bytes(notices.read_bytes() + b' ')
        with self.assertRaises(runtime_tests.GateError):
            runtime.rt.submit(dispatch['dispatch_id'], dict(outputs=[str(output)], summary='Must reject stale notice', checks=['fixture']))
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'stale')

    def test_runtime_rejects_structurally_valid_unaccepted_contract_review(self):
        f, runtime = self.runtime_fixture(wrong_review=True)
        self.assertTrue(f.check())
        with self.assertRaises(runtime_tests.GateError):
            runtime.accept('curation')

    def test_runtime_material_path_pass_and_freshness(self):
        f, runtime = self.runtime_fixture(material_enabled=True)
        runtime.accept('curation')
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'accepted')
        context = Path(f.context['content']['path'])
        context.write_bytes(context.read_bytes() + b' ')
        with self.assertRaises(runtime_tests.GateError):
            runtime.prepare('preprocessing')

    def test_secondary_only_report_without_primary_context_retained(self):
        f = IntegrationFixture(self.root, count=1)
        old = f.context['context_id']
        f.context['source_role'] = 'secondary'
        f.context['context_id'] = material.digest({k: v for k, v in f.context.items() if k != 'context_id'})
        f.decisions = json.loads(json.dumps(f.decisions).replace(old, f.context['context_id']))
        def sort_evidence(value):
            if type(value) is dict:
                return {k: sort_evidence(v) for k, v in value.items()}
            if type(value) is list:
                result = [sort_evidence(v) for v in value]
                if result and type(result[0]) is dict and result[0].get('kind') in {'native', 'context'}:
                    result.sort(key=material.digest)
                return result
            return value
        f.decisions = sort_evidence(f.decisions)
        f.seal()
        f.write(f.external, {f.entry['source_id']: f.binding})
        self.assertTrue(all(c['source_role'] != 'primary' for c in f.contexts))
        self.assertEqual(f.produce()['output_rows'], 1)
        self.assertTrue(f.check())

    def test_concurrent_material_iteration_aborts_and_cleans_staging(self):
        f = IntegrationFixture(self.root, material_enabled=True)
        original = bridge.MaterialCurationBatch.iter_observations
        def concurrent(batch):
            first = original(batch)
            next(first)
            second = original(batch)
            try:
                with self.assertRaises(material.MaterialError):
                    next(second)
                self.assertEqual(batch.state, 'ABORTED')
            finally:
                first.close()
                second.close()
            raise material.MaterialError('Synthetic stopped aborted iterator')
            yield
        with patch.object(bridge.MaterialCurationBatch, 'iter_observations', concurrent):
            with self.assertRaises(material.MaterialError):
                f.produce()
        self.assertFalse(f.manifest.exists())
        self.assertFalse(list(f.out.glob('.material-*')))

    def test_preparation_inherits_evidence_and_rejects_stale_context(self):
        f = IntegrationFixture(self.root, count=16, material_enabled=True)
        f.produce()
        split = prepare.prepare_splits(f.out / 'curated.csv', f.contract_path, self.root / 'split', f.manifest)
        self.assertIn(f.context['content'], split['curation_evidence'])
        context = Path(f.context['content']['path'])
        context.write_bytes(context.read_bytes() + b' ')
        with self.assertRaises(prepare.PreparationError):
            prepare.prepare_splits(f.out / 'curated.csv', f.contract_path, self.root / 'stale-split', f.manifest)
        self.assertFalse((self.root / 'stale-split' / 'preprocessing_manifest.json').exists())

    def test_rebound_held_emission_with_consistent_counts_rejects(self):
        f = IntegrationFixture(self.root, material_enabled=True)
        f.produce()
        with (f.out / 'dispositions.csv').open(encoding='utf-8') as stream:
            held = list(csv.DictReader(stream))[2]
        def emit_hold(rows):
            row = rows[0]
            row.update({k: v for k, v in held.items() if k in row})
            row.update(row_id='source.tsv::4', output_row_id='source.tsv::4:rat')
        f.rewrite_csv('curated.csv', emit_hold)
        def swap_dispositions(rows):
            rows[0].update(disposition='excluded', reasons='["invented_exclusion"]', output_row_ids='[]')
            rows[2].update(disposition='retained', reasons='[]', output_row_ids='["source.tsv::4:rat"]')
        f.rewrite_csv('dispositions.csv', swap_dispositions)
        manifest = data.load(f.manifest)
        manifest['dispositions'] = dict(retained=3, excluded=4, quarantined=1)
        data.dump(f.manifest, manifest)
        with self.assertRaises(coverage.evidence.EvidenceError):
            f.check()

    def test_source_notice_change_during_publication_has_no_success(self):
        f = IntegrationFixture(self.root)
        original_write = data.write_csv
        changed = False
        def mutate(*args, **kwargs):
            nonlocal changed
            result = original_write(*args, **kwargs)
            if not changed:
                changed = True
                notices = Path(f.binding['known_evidence_notices']['path'])
                notices.write_bytes(notices.read_bytes() + b' ')
            return result
        with patch.object(data, 'write_csv', mutate):
            with self.assertRaises(ValueError):
                f.produce()
        self.assertFalse(f.manifest.exists())


if __name__ == '__main__':
    unittest.main()
