"""Bounded synthetic metadata packets; test receipts are not scientific approval."""
import copy
import csv
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import test_qsar_adjudication as narrative
from scripts import test_qsar_curation_evidence as coverage

adj, data = narrative.adj, narrative.data


def packet(fixture, root, metadata_transform=None):
    root.mkdir()
    def write(name, value, lines=False):
        path = root / name
        path.write_text(''.join(json.dumps(v) + '\n' for v in value) if lines else json.dumps(value), encoding='utf-8')
        return dict(path=str(path.resolve()), sha256=adj.digest(path))
    def binding(path):
        return dict(path=str(path.resolve()), sha256=adj.digest(path))
    def mapping(*items):
        return {item['path']: item['sha256'] for item in items}
    source = {k: fixture.entry[k] for k in ('path', 'sha256')}
    decisions = fixture.config['decisions']
    scope = write('scope.jsonl', fixture.decisions, True)
    metadata, links = [], []
    for decision in fixture.decisions:
        for ordinal, observation in enumerate(decision['observations'], 1):
            fields = observation['fields']
            fid = decision['field_id']
            field = next(f for f in fixture.fields if f['field_id'] == fid)
            oid = adj.stable([fixture.entry['source_id'], source['sha256'], fid, fields['value'], fields['sex']])
            meta = dict(observation_id=oid, field_id=fid, value_anchor=fields['value'], population_anchor=fields['sex'],
                        native_value=fields['value']['quote'], native_unit=fields['unit']['quote'], native_modifier=None,
                        modifier_semantics='reported_numeric_estimate_not_proven_exact', normalized_mg_kg=float(fields['value']['quote']),
                        sex=fields['sex']['quote'] if fields['sex'] else None, strain=None, exposure_duration=None,
                        study_identity=None, study_independence=observation['dependence']['status'],
                        same_report_group=adj.stable([fixture.entry['source_id'], field['locator']['docno'], fields['reference']['quote']]),
                        reported_observation_not_unique_study=True)
            for condition, kind in (('14 day', 'observation_follow_up'), ('48 hour', 'observation_follow_up'), ('olive oil', 'vehicle')):
                if condition in field['native_record']['text']:
                    meta.update(native_condition=fixture.evidence(field, condition), condition_kind=kind)
            if observation['confidence_interval']:
                meta['confidence_interval'] = observation['confidence_interval']
            if metadata_transform is not None:
                metadata_transform(meta)
            metadata.append(meta)
            links.append(dict(field_id=fid, observation_ordinal=ordinal, observation_id=oid,
                              decision_observation=observation, observation_metadata=meta, scope='All synthetic scoped observations'))
    original = write('source-metadata.jsonl', metadata, True)
    integrated = write('metadata.jsonl', metadata, True)
    link_binding = write('links.jsonl', links, True)
    context = write('context.json', {'native_context': 'frozen leaf; no recursive discovery'})
    source_artifact = write('source-check.json', {'synthetic': True})
    identity = dict(schema_version=1, run_id='test', contract_version='test-v1', verdict='PASS', bounded_decision='APPROVE',
                    producer_execution_id='producer', reviewer_execution_id='reviewer', native_returned_review_locator='synthetic-only')
    source_review = write('source-review.json', dict(identity, stage='curation_adjudication',
        input_output_bindings=mapping(source, scope, original, context), review_artifact_bindings=mapping(source_artifact)))
    integration = write('integration.json', dict(input_bindings=mapping(scope, original, source_review, source_artifact, context),
                                               output_bindings=mapping(decisions, integrated, link_binding)))
    review_artifact = write('integration-check.json', {'synthetic': True})
    erratum = write('erratum.json', {'synthetic': True})
    integration_review = write('integration-review.json', dict(identity, stage='bounded_candidate_integration',
        candidate_bindings=mapping(integration, decisions, integrated, link_binding),
        input_bindings=mapping(source, scope, original, source_review, source_artifact, context),
        review_artifact_bindings=mapping(review_artifact), append_only_erratum=erratum))
    manifest = dict(schema_version=1, kind='hsdb_observation_metadata', source_id=fixture.entry['source_id'],
                    source=source, decisions=decisions, scope_decisions=scope, source_metadata=original,
                    metadata=integrated, links=link_binding, source_review=source_review,
                    integration_manifest=integration, integration_review=integration_review,
                    scope=dict(field_ids=sorted(d['field_id'] for d in fixture.decisions),
                               observation_ids=sorted(m['observation_id'] for m in metadata)))
    return write('manifest.json', manifest), manifest


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = narrative.AdjudicationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        f = self.fixture
        f.fixture(['ETHANOL LD50 Rat oral male 3160 mg/kg 14 day Study',
                   'ETHANOL LD50 Rat oral female 3160 mg/kg 48 hour Study',
                   'ETHANOL LD50 Rat oral female 5520 mg/kg CI 4500-6800 Study',
                   'ETHANOL LD50 Rat oral male 1110 mg/kg olive oil Study'])
        for n, (value, sex) in enumerate((('3160', 'male'), ('3160', 'female'), ('5520', 'female'), ('1110', 'male'))):
            observation = f.observation(f.inventory[n], value, sex)
            observation['dependence'] = dict(status='unknown', evidence=None)
            if n == 2:
                observation['confidence_interval'] = {k: f.evidence(f.inventory[n], q) for k, q in
                    (('lower', '4500'), ('upper', '6800'), ('context', 'CI 4500-6800'))}
            f.expand(n, [observation])
        f.bind()
        self.descriptor, self.manifest = packet(f, f.root / 'packet')

    def consumer(self):
        return adj.ObservationMetadata(self.descriptor, self.fixture.config, self.fixture.entry, data.UNITS)

    def rebind_manifest(self):
        path = Path(self.descriptor['path'])
        path.write_text(json.dumps(self.manifest), encoding='utf-8')
        self.descriptor['sha256'] = adj.digest(path)

    def test_nonseven_packet_exact_spans_nulls_ci_vehicle_and_unchanged_labels(self):
        f = self.fixture
        expected = list(f.engine(bind=False).rows())
        engine = f.engine(bind=False)
        engine.metadata = self.consumer()
        actual = list(engine.rows())
        engine.metadata.finish()
        self.assertEqual(expected, [{k: v for k, v in r.items() if k != 'observation_metadata'} for r in actual])
        self.assertEqual(len(engine.metadata.consumed), 4)
        self.assertEqual([r['value'] for r in actual], ['3160', '3160', '5520', '1110'])
        self.assertTrue(all(r['observation_metadata']['metadata']['exposure_duration'] is None for r in actual))
        self.assertEqual(actual[2]['observation_metadata']['metadata']['confidence_interval']['lower']['quote'], '4500')
        self.assertEqual(actual[3]['observation_metadata']['metadata']['condition_kind'], 'vehicle')
        # Acyclic: neither prior review nor metadata manifest depends on the future narrative receipt.
        self.assertNotIn(f.config['review_receipt']['path'], [b['path'] for b in engine.metadata.references])

    def test_reviewed_condition_pair_has_exact_nonnull_enum(self):
        # Each variant receives its own internally consistent synthetic source and
        # integration receipts. Rejection must come from the consumer wire rule,
        # not a stale digest or disagreement with an unchanged original copy.
        cases = (
            ('absent', lambda m: (m.pop('native_condition', None), m.pop('condition_kind', None)), False),
            ('null', lambda m: m.update(condition_kind=None) if 'native_condition' in m else None, True),
            ('invalid', lambda m: m.update(condition_kind='exposure_duration') if 'native_condition' in m else None, True),
            ('false', lambda m: m.update(condition_kind=False) if 'native_condition' in m else None, True),
            ('missing_kind', lambda m: m.pop('condition_kind', None), True),
            ('missing_condition', lambda m: m.pop('native_condition', None), True),
            ('valid', lambda m: None, False),
        )
        for name, transform, rejects in cases:
            with self.subTest(case=name):
                descriptor, manifest = packet(self.fixture, self.fixture.root / ('condition-' + name), transform)
                consumer = adj.ObservationMetadata(descriptor, self.fixture.config, self.fixture.entry, data.UNITS)
                engine = self.fixture.engine(bind=False)
                engine.metadata = consumer
                if rejects:
                    with self.assertRaisesRegex(ValueError, 'condition pair'):
                        list(engine.rows())
                else:
                    rows = list(engine.rows())
                    consumer.finish()
                    self.assertEqual(len(rows), 4)
                    values = [row['observation_metadata']['metadata'] for row in rows]
                    if name == 'absent':
                        self.assertTrue(all('native_condition' not in m and 'condition_kind' not in m for m in values))
                    else:
                        self.assertEqual([m.get('condition_kind') for m in values],
                                         ['observation_follow_up', 'observation_follow_up', None, 'vehicle'])

    def test_finite_dependency_cache_and_fresh_end_check(self):
        calls = []
        real = adj.digest
        with patch.object(adj, 'digest', side_effect=lambda p: (calls.append(str(Path(p).resolve())), real(p))[1]):
            consumer = self.consumer()
        self.assertEqual(len(calls), len(set(calls)))
        engine = self.fixture.engine(bind=False)
        engine.metadata = consumer
        list(engine.rows())
        (self.fixture.root / 'packet/context.json').write_text('changed', encoding='utf-8')
        with self.assertRaises(adj.AdjudicationError):
            consumer.finish()

    def test_rehashed_manifest_cannot_replace_accepted_metadata_or_links(self):
        for key in ('metadata', 'links', 'source_metadata', 'source_review', 'integration_manifest'):
            with self.subTest(key=key):
                original = copy.deepcopy(self.manifest)
                path = self.fixture.root / ('replacement-' + key)
                path.write_bytes(Path(self.manifest[key]['path']).read_bytes())
                self.manifest[key] = dict(path=str(path), sha256=adj.digest(path))
                self.rebind_manifest()
                with self.assertRaises(adj.AdjudicationError):
                    self.consumer()
                self.manifest = original
                self.rebind_manifest()

    def test_scope_unknown_keys_and_external_collision_reject(self):
        for mutate in (lambda m: m['scope']['observation_ids'].pop(), lambda m: m.update(context_id='unsupported'),
                       lambda m: m.update(source_metadata=None)):
            original = copy.deepcopy(self.manifest)
            mutate(self.manifest)
            self.rebind_manifest()
            with self.assertRaises((adj.AdjudicationError, TypeError)):
                self.consumer()
            self.manifest = original
            self.rebind_manifest()
        config = dict(source_id='hsdb', adapter='hsdb', narrative_observation_metadata=self.descriptor)
        with self.assertRaises(data.DataError):
            data.apply_adjudication_bindings([config], {'hsdb': self.fixture.config}, self.fixture.root / 'unused')
        for external in (dict(narrative_observation_metadata=self.descriptor),
                         dict(self.fixture.config, narrative_observation_metadata=self.descriptor),
                         dict(narrative_adjudication=self.fixture.config, narrative_observation_metadata=self.descriptor)):
            with self.assertRaises(ValueError):
                data.apply_adjudication_bindings([dict(source_id='hsdb', adapter='hsdb')], {'hsdb': external}, self.fixture.root / 'unused')

    def test_rehashed_link_swap_and_missing_sibling_rejected(self):
        path = Path(self.manifest['links']['path'])
        rows = list(adj.jsonl(path))
        rows[0]['decision_observation'], rows[1]['decision_observation'] = rows[1]['decision_observation'], rows[0]['decision_observation']
        path.write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        self.manifest['links']['sha256'] = adj.digest(path)
        # Rebind candidate integration only. The independent source metadata remains fixed.
        integration_path = Path(self.manifest['integration_manifest']['path'])
        integration = adj.read_json(integration_path)
        integration['output_bindings'][str(path)] = adj.digest(path)
        integration_path.write_text(json.dumps(integration), encoding='utf-8')
        self.manifest['integration_manifest']['sha256'] = adj.digest(integration_path)
        review_path = Path(self.manifest['integration_review']['path'])
        review = adj.read_json(review_path)
        review['candidate_bindings'][str(path)] = adj.digest(path)
        review['candidate_bindings'][str(integration_path)] = adj.digest(integration_path)
        review_path.write_text(json.dumps(review), encoding='utf-8')
        self.manifest['integration_review']['sha256'] = adj.digest(review_path)
        self.rebind_manifest()
        with self.assertRaisesRegex(ValueError, 'source-span link'):
            self.consumer()

    def test_invalid_maps_conflicts_aliases_erratum_and_finite_leaves(self):
        path = Path(self.manifest['integration_review']['path'])
        original = adj.read_json(path)
        mutations = [lambda r: r.update(input_bindings={}),
                     lambda r: r['input_bindings'].update({self.manifest['source']['path']: '0' * 64}),
                     lambda r: r['input_bindings'].update({'relative.json': '0' * 64}),
                     lambda r: r['input_bindings'].update({str(Path(self.manifest['source']['path']).parent / '.' / 'nested' / '..' / 'hsdb.zip'): self.manifest['source']['sha256']}),
                     lambda r: r.update(append_only_erratum=None)]
        for mutate in mutations:
            candidate = copy.deepcopy(original)
            mutate(candidate)
            path.write_text(json.dumps(candidate), encoding='utf-8')
            self.manifest['integration_review']['sha256'] = adj.digest(path)
            self.rebind_manifest()
            with self.assertRaises(ValueError):
                self.consumer()
        path.write_text(json.dumps(original), encoding='utf-8')
        self.manifest['integration_review']['sha256'] = adj.digest(path)
        self.rebind_manifest()
        consumer = self.consumer()
        self.assertIn(str(path.with_name('erratum.json')), [b['path'] for b in consumer.references])


class MetadataCurationTests(unittest.TestCase):
    def setUp(self):
        self.coverage = coverage.CoverageTests()
        self.coverage.setUp()
        self.addCleanup(self.coverage.doCleanups)
        c = self.coverage
        self.fixture = c.narrative_fixture()
        self.descriptor, self.manifest = packet(self.fixture, c.root / 'packet')
        self.fixture.policy['sources'][0]['narrative_observation_metadata'] = self.descriptor
        # Metadata is part of policy; refresh the synthetic narrative receipt only, not prior packet evidence.
        self.fixture.policy['sources'][0]['narrative_adjudication'] = self.fixture.bind()

    def test_projection_accounting_guard_and_nonemission(self):
        c = self.coverage
        manifest = c.generate()
        self.assertEqual(manifest['output_rows'], 2)
        self.assertTrue(c.check())
        accounting = manifest['observation_metadata_accounting']['hsdb.zip']
        self.assertEqual(len(accounting['consumed_observation_ids']), 2)
        for name in ('dispositions.csv', 'identity_ledger.csv', 'curated.csv'):
            path = c.out / name
            original = path.read_bytes()
            c.rewrite_csv(name, lambda rows: rows[0].update(observation_metadata=''))
            with self.assertRaises(coverage.evidence.EvidenceError):
                c.check()
            path.write_bytes(original)
            c.rebind_output(name)
        c.config['data_curation']['tasks'][0]['species'] = 'Mouse'
        self.fixture.bind()
        c.config['data_curation']['sources'][0]['narrative_adjudication'] = self.fixture.config
        c.out = c.root / 'nonemitting'
        data.dump(c.contract, c.config)
        data.curate(c.contract, c.collection / 'source_registry.json', c.out)
        c.manifest = c.out / 'curation_manifest.json'
        self.assertTrue(c.check())
        self.assertTrue(all(not p['output_row_ids'] for p in data.load(c.manifest)['observation_metadata_accounting']['hsdb.zip']['projections'].values()))

    def test_fresh_policy_receipt_required(self):
        self.fixture.policy['tasks'][0]['species'] = 'Mouse'
        with self.assertRaises(ValueError):
            self.coverage.generate()

    def test_stale_context_rejected_by_guard_and_preparation(self):
        c = self.coverage
        c.generate()
        self.assertTrue(c.check())
        Path(self.manifest['integration_review']['path']).with_name('context.json').write_text('tampered', encoding='utf-8')
        with self.assertRaises(coverage.evidence.EvidenceError):
            c.check()
        with self.assertRaises(coverage.prepare.PreparationError):
            coverage.prepare.prepare_splits(c.out / 'curated.csv', c.contract, c.root / 'split', c.manifest)

    def test_rehashed_nested_projection_tampering_rejected(self):
        c = self.coverage
        c.generate()
        for name in ('curated.csv', 'dispositions.csv', 'identity_ledger.csv'):
            path = c.out / name
            original = path.read_bytes()
            for mutate in (lambda m: m.update(same_report_group='0' * 64),
                           lambda m: m.update(study_identity='invented independent study'),
                           lambda m: m.update(exposure_duration='14 day'),
                           lambda m: m.update(native_modifier='='),
                           lambda m: m.update(normalized_mg_kg=4500)):
                def change(rows):
                    value = json.loads(rows[0]['observation_metadata'])
                    mutate(value['metadata'])
                    rows[0]['observation_metadata'] = json.dumps(value)
                c.rewrite_csv(name, change)
                with self.assertRaises(coverage.evidence.EvidenceError):
                    c.check()
                path.write_bytes(original)
                c.rebind_output(name)

    def test_runtime_reuse_requires_current_transitive_context(self):
        c = self.coverage
        runtime = c.runtime_curated()
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'accepted')
        Path(self.descriptor['path']).with_name('context.json').write_text('changed context', encoding='utf-8')
        with self.assertRaises(coverage.GateError):
            runtime.prepare('preprocessing')
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'stale')

    def test_rehashed_projection_cannot_receive_runtime_pass_or_prepare(self):
        c = self.coverage
        runtime = c.runtime_curated(lambda: c.rewrite_csv('dispositions.csv', lambda rows: rows[0].update(observation_metadata='')))
        with self.assertRaises(coverage.GateError):
            runtime.accept('curation')
        with self.assertRaises(coverage.prepare.PreparationError):
            coverage.prepare.prepare_splits(c.out / 'curated.csv', c.contract, c.root / 'split', c.manifest)

    def test_task_fanout_preserves_single_source_observation_accounting(self):
        c = self.coverage
        self.fixture.policy['tasks'].append(dict(self.fixture.policy['tasks'][0], id='second-rat-task'))
        self.fixture.policy['sources'][0]['narrative_adjudication'] = self.fixture.bind()
        manifest = c.generate()
        self.assertTrue(c.check())
        account = manifest['observation_metadata_accounting']['hsdb.zip']
        self.assertEqual(account['report']['reported_observations'], 2)
        self.assertEqual(account['report']['output_rows'], 4)
        self.assertTrue(all(len(p['output_row_ids']) == 2 for p in account['projections'].values()))

    def test_preparation_has_prior_review_without_runtime_and_unchanged_groups(self):
        c = self.coverage
        self.fixture.policy['sources'].append(dict(source_id='oecd.csv', adapter='oecd', columns={'relation': 'qualifier'}))
        self.fixture.policy['sources'][0]['narrative_adjudication'] = self.fixture.bind()
        c.generate()
        produced = coverage.prepare.prepare_splits(c.out / 'curated.csv', c.contract, c.root / 'split', c.manifest)
        split = data.load(c.root / 'split/preprocessing_manifest.json')
        self.assertIn(self.manifest['integration_review'], split['curation_evidence'])
        with (c.out / 'curated.csv').open(encoding='utf-8', newline='') as stream:
            enabled = list(csv.DictReader(stream))
        del self.fixture.policy['sources'][0]['narrative_observation_metadata']
        self.fixture.policy['sources'][0]['narrative_adjudication'] = self.fixture.bind()
        data.dump(c.contract, c.config)
        baseline = c.root / 'without-metadata'
        data.curate(c.contract, c.collection / 'source_registry.json', baseline)
        with (baseline / 'curated.csv').open(encoding='utf-8', newline='') as stream:
            disabled = list(csv.DictReader(stream))
        self.assertEqual([{k: v for k, v in row.items() if k != 'observation_metadata'} for row in enabled], disabled)

    def test_removing_required_config_and_shortening_accounting_reject(self):
        c = self.coverage
        c.generate()
        manifest = data.load(c.manifest)
        del manifest['policy']['sources'][0]['narrative_observation_metadata']
        data.dump(c.manifest, manifest)
        with self.assertRaises(coverage.evidence.EvidenceError):
            c.check()


if __name__ == '__main__':
    unittest.main()
