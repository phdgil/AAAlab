"""Synthetic engine-only evidence; no real source or independent acceptance."""
import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'harnesses/qsar-autoresearch-harness/skills/qsar-autoresearch-harness/scripts'
sys.path.insert(0, str(SCRIPTS))
import qsar_adjudication as adj
import qsar_data as data
import qsar_material_curation as native
import qsar_verified_material as material


def identified(value, key):
    return dict(value, **{key: material.digest({k: v for k, v in value.items() if k != key})})


def ordered(values):
    return sorted(values, key=material.digest)


class Fixture:
    def __init__(self, root, adapter='npass', count=8):
        self.root, self.adapter = root, adapter
        self.config = dict(source_id=adapter, adapter=adapter, release='synthetic-release', sheet='')
        self.source = root / 'source.tsv'
        columns = data.ADAPTERS[adapter]
        fields = list(dict.fromkeys([*columns.values(), 'unmapped_native_note']))
        with self.source.open('w', encoding='utf-8', newline='') as out:
            writer = csv.DictWriter(out, fields, delimiter='\t')
            writer.writeheader()
            for i in range(count):
                row = dict.fromkeys(fields, '')
                for k, v in dict(record_id=f'NP{i}', npid=f'NP{i}', cas='64-17-5', smiles='CCO',
                                 endpoint='LD50', species='Rat', value='100', unit='mg/kg', relation='>' if i == 6 else '=',
                                 reference='secondary export', route='oral').items():
                    if k in columns:
                        row[columns[k]] = v
                row['unmapped_native_note'] = f'physical sibling {i}'
                if i == 7:
                    row = dict.fromkeys(fields, '')
                writer.writerow(row)
        self.entry = dict(source_id=adapter, **material.file_binding(self.source))
        support = root / 'support.tsv'
        support.write_text('np_id\tSMILES\tInChI\tInChIKey\nNP0\tCCO\tduplicate\tduplicate\nNP0\tCCO\tduplicate\tduplicate\n', encoding='utf-8')
        unrelated = root / 'unrelated.tsv'
        unrelated.write_text('value\n1\n', encoding='utf-8')
        self.support = dict(source_id='structures', **material.file_binding(support))
        self.registry = dict(files=[self.entry, self.support, dict(source_id='unrelated', **material.file_binding(unrelated))])
        self.registry_path = root / 'registry.json'
        self.write(self.registry_path, self.registry)
        clauses = {
            'v4_nonexperimental_target': dict(disposition='excluded', reason='nonexperimental_target'),
            'v4_unavailable_critical_metadata': dict(disposition='scientific_quarantine', reason='unavailable_critical_metadata'),
            'v4_origin_unknown_bounded_review': dict(disposition='scientific_quarantine', reason='source_anchored_origin_unknown_after_bounded_review')}
        self.contract = dict(data_curation=dict(sources=[self.config], identity_sources=[dict(source_id='structures', adapter='npass_structure_v1')]),
                             scientific_policy=dict(narrative_adjudication_v2=dict(schema_version=2, clauses=clauses)))
        self.contract_path = root / 'contract.json'
        self.write(self.contract_path, self.contract)
        self.contract_file = material.file_binding(self.contract_path)
        review_text = root / 'synthetic-native-return.txt'
        review_text.write_text('SYNTHETIC TEST ONLY: separate-review envelope fixture, not an actual scientific review.', encoding='utf-8')
        self.common = dict(schema_version=2, status='PASS', attestation='local_leader_attested_not_authenticated',
                           producer_execution_id='synthetic-producer', reviewer_execution_id='synthetic-reviewer',
                           receipt_writer='synthetic-leader', review_evidence=material.file_binding(review_text), findings=[])
        candidate = root / 'candidate.json'
        self.write(candidate, dict(contract=self.contract_file))
        acceptance = root / 'acceptance.json'
        self.write(acceptance, dict(self.common, stage='contract', contract_sha256=self.contract_file['sha256'], candidate=material.file_binding(candidate)))
        self.acceptance_file = material.file_binding(acceptance)
        # Explicit distinct primary columns with equal numbers and an export crosswalk.
        context_body = [dict(chemical=f'NP{i}' if adapter == 'npass' else 'CCO' if adapter == 'toxric' else '64-17-5', row=i, experimental=100, predicted=100, route='oral',
                             export_column='predicted' if i == 1 else 'experimental', origin='predicted' if i == 1 else 'experimental report',
                             question='Mixed source columns; exported provenance unresolved' if i in (2, 3) else 'reported') for i in range(count)]
        context_file = root / 'context.json'
        self.write(context_file, context_body)
        self.context = identified(dict(artifact=material.file_binding(context_file), content=material.file_binding(context_file),
            content_format='json', source_role='primary', release='synthetic-release', locator='synthetic table 1', extraction_code=None), 'context_id')
        self.contexts = [self.context]
        self.support_context = identified(dict(artifact=self.support_file(), content=self.support_file(), content_format='utf8',
            source_role='native_support', release='synthetic-release', locator='all physical support rows', extraction_code=None), 'context_id')
        self.contexts.append(self.support_context)
        self.observations = [native.observation(raw, self.config, self.entry, number, '')
                             for number, raw in data.rows(self.source, native_index=True)]
        self.decisions, self.reviews = [], []
        # Only use production mapping helper to form typed native pointers; assertions
        # below separately inspect raw values, full physical row count and outputs.
        mapper = object.__new__(adj.OrdinaryAdjudication)
        mapper.config, mapper.n, mapper.m = self.config, native, material
        for i, obs in enumerate(self.observations):
            bindings = mapper._native_bindings(obs)
            decision = dict(schema_version=1, member_key=native.member_key(obs), native_observation_sha256=material.digest(obs),
                            native_bindings=bindings, target_origin=None, route=None, remaining_issues=[])
            if i != 7:
                origin = dict(classification='experimental', subtype='reported_measurement', evidence=ordered([self.ref(f'/{i}/origin'), self.ref(f'/{i}/chemical')]),
                              primary_attribution=dict.fromkeys(('chemical', 'row', 'column')), experimental_basis=dict(method='none', transformation='none',
                              evidence=[self.ref(f'/{i}/origin')]), treatment=None, bounded_review_id=None)
                if i == 1:
                    origin.update(classification='nonexperimental', subtype='model_prediction', experimental_basis=None,
                                  treatment=dict(clause_id='v4_nonexperimental_target', **clauses['v4_nonexperimental_target']))
                    origin['primary_attribution'] = dict(chemical=self.ref('/1/chemical'), row=self.ref('/1/row'), column=self.ref('/1/export_column'))
                if i in (2, 3):
                    origin.update(classification='origin_unknown', subtype=None, experimental_basis=None,
                                  evidence=ordered([self.ref(f'/{i}/question'), self.ref(f'/{i}/chemical')]), treatment=dict(clause_id='v4_origin_unknown_bounded_review', **clauses['v4_origin_unknown_bounded_review']))
                    review = self.bounded(decision, obs, 'target_origin', i)
                    self.reviews.append(review)
                    origin['bounded_review_id'] = review['review_id']
                if i == 4:
                    origin['subtype'] = 'same_substance_mortality_estimate'
                    origin['experimental_basis']['method'] = 'probit'
                if i == 5:
                    origin['subtype'] = 'validated_experimental_transform'
                    origin['experimental_basis']['transformation'] = 'molar'
                decision['target_origin'] = origin
                decision['route'] = dict(state='established', value='oral', evidence=[self.ref(f'/{i}/route')],
                    candidate_linkage=ordered([dict(kind='native', field=bindings['chemical'][0]), self.ref(f'/{i}/chemical'), self.ref(f'/{i}/export_column')]),
                    treatment=None, bounded_review_id=None)
                if i == 3:
                    review = self.bounded(decision, obs, 'exposure_route', i)
                    self.reviews.append(review)
                    decision['route'].update(state='unavailable', value=None, bounded_review_id=review['review_id'],
                        treatment=dict(clause_id='v4_unavailable_critical_metadata', **clauses['v4_unavailable_critical_metadata']))
            self.decisions.append(identified(decision, 'decision_id'))
        self.notices = dict(schema_version=1, source_id=adapter, source_sha256=self.entry['sha256'], notices=[])
        self.seal()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    def ref(self, pointer):
        value = native.pointer_value(json.loads(Path(self.context['content']['path']).read_text(encoding='utf-8')), pointer)
        return dict(kind='context', context_id=self.context['context_id'], pointer=pointer, start=None, end=None, value=value)

    def support_file(self):
        return {k: self.support[k] for k in ('path', 'sha256')}

    def support_ref(self):
        text = Path(self.support['path']).read_text(encoding='utf-8')
        return dict(kind='context', context_id=self.support_context['context_id'], pointer=None, start=0, end=len(text), value=text)

    def bounded(self, decision, obs, property_name, row):
        evidence = ordered([self.ref(f'/{row}/question'), self.ref(f'/{row}/chemical')])
        targets = [(r['record_id'], 'native_extraction') for r in obs['body']['records']]
        targets += [(self.entry['path'], 'native_extraction'), (self.context['context_id'], 'context'), (self.support_context['context_id'], 'context')]
        if obs['body']['native_identifiers']['npid']:
            targets += [(self.support['path'], 'support')]
        path_evidence = ordered([*evidence, self.support_ref(), dict(kind='native', field=native.field_evidence(obs['body']['records'][0], ''))])
        paths = [identified(dict(category=category, target=target, identified_by=path_evidence, in_scope=True,
            scope_rationale='Synthetic source-linked inventory item', state='reviewed', attempts=[], reviewed_evidence=path_evidence,
            remaining_feasible_actions=[], findings=[]), 'path_id') for target, category in targets]
        return identified(dict(member_key=decision['member_key'], decision_native_sha256=material.digest(obs), property=property_name,
            indispensable_property=property_name, question_evidence=evidence,
            competing_interpretations=ordered([dict(interpretation='experimental source column', evidence=evidence),
                dict(interpretation='prediction source column', evidence=evidence)]), scope_justification='Synthetic complete accessible inventory',
            paths=sorted(paths, key=lambda p: p['path_id']), remaining_issues=[], residual_uncertainty='Source column unresolved',
            reopening_triggers=['New source crosswalk']), 'review_id')

    def seal(self):
        self.decisions = [identified(d, 'decision_id') for d in self.decisions]
        for key, values in [('decisions', self.decisions), ('context_inventory', self.contexts), ('bounded_reviews', self.reviews)]:
            (self.root / (key + '.jsonl')).write_text(''.join(json.dumps(v) + '\n' for v in values), encoding='utf-8')
        self.write(self.root / 'notices.json', self.notices)
        self.binding = dict(schema_version=1, kind=adj.ORDINARY_KIND, full_contract=self.contract_file, accepted_contract_review=self.acceptance_file,
            **{key: material.file_binding(self.root / (key + '.jsonl')) for key in ('decisions', 'context_inventory', 'bounded_reviews')},
            known_evidence_notices=material.file_binding(self.root / 'notices.json'))
        members = sorted([native.member_key(o), material.digest(o)] for o in self.observations)
        context = dict(full_contract=self.contract_file, accepted_contract_review=self.acceptance_file,
            source_registry=material.file_binding(self.registry_path), source_id=self.entry['source_id'], adapter=self.adapter,
            source_release=self.config.get('release'), source_configuration_sha256=material.digest(self.config),
            source_files=sorted(self.registry['files'], key=lambda e: e['source_id']), binding_sha256=material.digest(self.binding),
            **{k: self.binding[k] for k in ('decisions', 'context_inventory', 'bounded_reviews', 'known_evidence_notices')},
            code=adj.ordinary_code_bindings(), environment=adj.ordinary_environment(),
            scope=dict(observation_count=len(members), member_keys=[m[0] for m in members], observations_sha256=material.digest(members)))
        receipt = dict(self.common, kind=adj.ORDINARY_KIND, context=context)
        self.write(self.root / 'receipt.json', receipt)
        file = material.file_binding(self.root / 'receipt.json')
        self.binding['review'] = dict(receipt=file, actual_review=dict(receipt, receipt=file))

    def engine(self):
        return adj.OrdinaryAdjudication(self.binding, self.entry, self.contract_path, self.registry_path, self.root)

    def change_review(self, index, change):
        old = self.reviews[index]['review_id']
        change(self.reviews[index])
        self.reviews[index]['paths'] = sorted([identified(p, 'path_id') for p in self.reviews[index]['paths']], key=lambda p: p['path_id'])
        self.reviews[index] = identified(self.reviews[index], 'review_id')
        for decision in self.decisions:
            for key in ('target_origin', 'route'):
                if decision[key] and decision[key]['bounded_review_id'] == old:
                    decision[key]['bounded_review_id'] = self.reviews[index]['review_id']
        self.seal()


class OrdinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, **kwargs):
        return Fixture(self.root, **kwargs)

    def rejects(self, fixture, message=None):
        with self.assertRaises((adj.AdjudicationError, material.MaterialError), msg=message):
            fixture.engine()

    def test_eight_rows_and_separate_holds_preserve_raw_and_inequality(self):
        f = self.fixture()
        before = copy.deepcopy(f.observations)
        engine = f.engine()
        results = [engine.resolve(o) for o in f.observations]
        summary = engine.finish()['summary']
        self.assertEqual(summary['observation_count'], 8)
        self.assertEqual(results[0]['route_override'], 'oral')
        self.assertEqual(results[1]['target_origin']['primary_attribution']['column']['value'], 'predicted')
        self.assertEqual([d['property'] for d in results[3]['scientific_dispositions']], ['exposure_route', 'target_origin'])
        self.assertEqual(results[4]['target_origin']['experimental_basis']['method'], 'probit')
        self.assertEqual(results[5]['target_origin']['experimental_basis']['transformation'], 'molar')
        self.assertEqual(f.decisions[6]['native_bindings']['modifier']['value'], '>')
        self.assertIsNone(results[7]['target_origin'])
        self.assertEqual(before, f.observations)
        self.assertEqual(summary['scientific_treatments']['source_anchored_origin_unknown_after_bounded_review'], 2)

    def test_four_actual_adapters(self):
        for adapter in ('npass', 'ice_oral', 'oecd', 'toxric'):
            with self.subTest(adapter=adapter), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory), adapter, 1)
                engine = f.engine()
                self.assertEqual(engine.resolve(f.observations[0])['route_override'], 'oral')
                self.assertEqual(engine.finish()['summary']['observation_count'], 1)

    def test_full_u_omission_and_duplicate(self):
        f = self.fixture()
        f.decisions.pop()
        f.seal()
        self.rejects(f)
        f.decisions.append(f.decisions[0])
        f.seal()
        self.rejects(f)

    def test_consumption_omission_duplicate_modified_sibling(self):
        f = self.fixture()
        engine = f.engine()
        with self.assertRaises(adj.AdjudicationError):
            engine.finish()
        engine.resolve(f.observations[0])
        with self.assertRaises(adj.AdjudicationError):
            engine.resolve(f.observations[0])
        changed = copy.deepcopy(f.observations[1])
        changed['candidate_projection']['unmapped_native_note'] = 'forged'
        with self.assertRaises(adj.AdjudicationError):
            engine.resolve(changed)

    def test_native_binding_and_context_span_forgery(self):
        f = self.fixture()
        f.decisions[0]['native_bindings']['value']['value'] = '101'
        f.seal()
        self.rejects(f)
        f = self.fixture()
        f.decisions[0]['route']['evidence'][0]['value'] = 'dermal'
        f.seal()
        self.rejects(f)

    def test_native_primary_attribution_rejected(self):
        f = self.fixture()
        f.decisions[1]['target_origin']['primary_attribution']['column'] = dict(kind='native', field=f.decisions[1]['native_bindings']['value'])
        f.seal()
        self.rejects(f)

    def test_native_route_contradiction(self):
        f = self.fixture(adapter='oecd', count=1)
        f.decisions[0]['route']['value'] = 'dermal'
        f.seal()
        self.rejects(f)

    def test_route_wrong_candidate_crosswalk(self):
        f = self.fixture()
        f.decisions[0]['route']['candidate_linkage'] = ordered([
            dict(kind='native', field=f.decisions[0]['native_bindings']['chemical'][0]), f.ref('/1/chemical')])
        f.seal()
        self.rejects(f)
        f = self.fixture()
        f.decisions[0]['target_origin']['evidence'] = ordered([f.ref('/1/origin'), f.ref('/1/chemical')])
        f.seal()
        self.rejects(f)

    def test_available_evidence_cannot_be_declared_inaccessible(self):
        f = self.fixture()
        def change(review):
            path = next(p for p in review['paths'] if p['target'] == f.entry['path'])
            path.update(state='attempted_access_failure', attempts=[dict(at='2026-10-30T00:00:00Z', target=path['target'],
                outcome='access_failure', evidence=f.common['review_evidence'], context_ids=[])])
        f.change_review(0, change)
        self.rejects(f)

    def test_actual_source_context_and_core_change_invalidates(self):
        for artifact in ('source.tsv', 'context.json', 'support.tsv'):
            with self.subTest(artifact=artifact), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                engine = f.engine()
                for obs in f.observations:
                    engine.resolve(obs)
                with (f.root / artifact).open('a') as stream:
                    stream.write(' ')
                with self.assertRaises(adj.AdjudicationError):
                    engine.finish()
        f = self.fixture()
        engine = f.engine()
        for obs in f.observations:
            engine.resolve(obs)
        with patch.object(adj, 'ordinary_code_bindings', return_value=[]):
            with self.assertRaises(adj.AdjudicationError):
                engine.finish()

    def test_scientific_issues_remain_blocking_alongside_hold(self):
        f = self.fixture()
        f.decisions[2]['remaining_issues'] = [dict(domain='join', reason='Unfinished candidate join', evidence=[f.ref('/2/question')])]
        f.seal()
        engine = f.engine()
        result = engine.resolve(f.observations[2])
        self.assertEqual(result['scientific_remaining_issues'][0]['domain'], 'join')
        self.assertEqual(len(result['scientific_dispositions']), 1)
        # Integration must apply the final gate to this issue even though the
        # origin treatment is validated; finish reports accounting, not approval.

    def test_unknown_hold_cannot_hide_route_or_conflict(self):
        f = self.fixture()
        f.decisions[2]['route'] = None
        f.seal()
        self.rejects(f)
        f = self.fixture()
        f.decisions[2]['target_origin']['classification'] = 'origin_conflict'
        f.seal()
        self.rejects(f)

    def test_applicable_support_omission_unrelated_registry_allowed(self):
        f = self.fixture()
        f.engine()  # unrelated registry file has no bounded-review path
        f.change_review(0, lambda r: r.update(paths=[p for p in r['paths'] if p['target'] != f.support['path']]))
        self.rejects(f)

    def test_unread_unattempted_remaining_actions_and_fabricated_flags(self):
        for mutation in (lambda p: p.update(state='unattempted'), lambda p: p.update(state='retrieved_awaiting_review'),
                         lambda p: p.update(remaining_feasible_actions=['read available paper']), lambda p: p.update(complete=True)):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                f.change_review(0, lambda r: mutation(r['paths'][0]))
                self.rejects(f)

    def test_hidden_extraction_support_join_findings(self):
        for domain in ('extraction', 'support', 'join'):
            with self.subTest(domain=domain), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                f.change_review(0, lambda r: r['paths'][0].update(findings=[dict(domain=domain, reason='unfinished', evidence=[f.ref('/2/question')])]))
                self.rejects(f)

    def test_unconsumed_review_and_wrong_candidate(self):
        f = self.fixture()
        f.change_review(0, lambda r: r.update(member_key=f.decisions[0]['member_key']))
        self.rejects(f)
        f = self.fixture()
        f.reviews.append(identified(dict(f.reviews[0], residual_uncertainty='extra unconsumed'), 'review_id'))
        f.seal()
        self.rejects(f)

    def test_receipt_independence_release_environment_and_code(self):
        for field, value in [('source_release', 'wrong-release'), ('environment', {}), ('code', [])]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                receipt = json.loads((f.root / 'receipt.json').read_text())
                receipt['context'][field] = value
                f.write(f.root / 'receipt.json', receipt)
                descriptor = material.file_binding(f.root / 'receipt.json')
                f.binding['review'] = dict(receipt=descriptor, actual_review=dict(receipt, receipt=descriptor))
                self.rejects(f)
        f = self.fixture()
        f.common['reviewer_execution_id'] = f.common['producer_execution_id']
        f.seal()
        self.rejects(f)

    def test_freshness_changed_notice_and_environment(self):
        f = self.fixture()
        engine = f.engine()
        for obs in f.observations:
            engine.resolve(obs)
        with (f.root / 'notices.json').open('a') as stream:
            stream.write(' ')
        with self.assertRaises(adj.AdjudicationError):
            engine.finish()
        f.seal()
        engine = f.engine()
        for obs in f.observations:
            engine.resolve(obs)
        with patch.object(adj, 'ordinary_environment', return_value={}):
            with self.assertRaises(adj.AdjudicationError):
                engine.finish()

    def notice(self, f, applicability='pending'):
        return identified(dict(member_keys=[f.decisions[0]['member_key']], discovered_at='2026-10-30T00:00:00Z', locator='new source',
            artifact=f.context['artifact'], applicability=applicability, applicability_evidence=[f.ref('/0/origin')],
            addressed_decision_ids=[], addressed_bounded_review_ids=[]), 'notice_id')

    def test_pending_notice_and_applicable_unread_rejected(self):
        f = self.fixture()
        f.notices['notices'] = [self.notice(f)]
        f.seal()
        self.rejects(f)
        f.notices['notices'] = [self.notice(f, 'applicable')]
        f.seal()
        self.rejects(f)

    def test_applicable_and_nonapplicable_reviewed_notice(self):
        f = self.fixture()
        notice = self.notice(f, 'applicable')
        notice['addressed_decision_ids'] = [f.decisions[0]['decision_id']]
        f.notices['notices'] = [identified(notice, 'notice_id')]
        f.seal()
        f.engine()
        f.notices['notices'] = [self.notice(f, 'not_applicable')]
        f.seal()
        f.engine()

    def test_strict_json_duplicate_nonfinite_and_boolean_version(self):
        f = self.fixture()
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                native.decode(text)
        f.decisions[0]['schema_version'] = True
        f.seal()
        self.rejects(f)

    def test_unsupported_hsdb_sql_remain_fail_closed(self):
        f = self.fixture()
        for adapter in ('hsdb', 'toxval'):
            f.contract['data_curation']['sources'][0]['adapter'] = adapter
            f.write(f.contract_path, f.contract)
            f.binding['full_contract'] = material.file_binding(f.contract_path)
            self.rejects(f)

    def test_identification_does_not_prove_target_review(self):
        for kind in ('native_record', 'source', 'support', 'context'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                target = {'native_record': f.observations[2]['body']['records'][0]['record_id'],
                          'source': f.entry['path'], 'support': f.support['path'],
                          'context': f.support_context['context_id']}[kind]
                def change(review):
                    path = next(p for p in review['paths'] if p['target'] == target)
                    self.assertTrue(path['identified_by'])
                    path['reviewed_evidence'] = [f.ref('/2/chemical')]
                f.change_review(0, change)
                self.rejects(f)

    @staticmethod
    def add_failed_retrieval(f, target, reviewed=None):
        def change(review):
            review['paths'].append(identified(dict(category='retrieval', target=target,
                identified_by=[f.ref('/2/question')], in_scope=True, scope_rationale='Synthetic bounded access assessment',
                state='attempted_access_failure', attempts=[dict(at='2026-10-30T00:00:00Z', target=target,
                outcome='access_failure', evidence=f.common['review_evidence'], context_ids=[])],
                reviewed_evidence=reviewed or [], remaining_feasible_actions=[], findings=[]), 'path_id'))
        f.change_review(0, change)

    def test_accessible_artifact_content_and_canonical_aliases_reject_failure(self):
        # Distinct artifact/content paths exercise both entries in the inventory.
        for field in ('artifact', 'content'):
            for alias in ('literal', 'dot_parent', 'case'):
                with self.subTest(field=field, alias=alias), tempfile.TemporaryDirectory() as directory:
                    f = Fixture(Path(directory))
                    original_id = f.context['context_id']
                    artifact = f.root / 'original-primary.txt'
                    artifact.write_text('Synthetic primary artifact, separately extracted to context.json.', encoding='utf-8')
                    f.context['artifact'] = material.file_binding(artifact)
                    f.context = identified(f.context, 'context_id')
                    f.contexts[0] = f.context
                    def replace(value, old, new):
                        if type(value) is dict:
                            return {k: replace(v, old, new) for k, v in value.items()}
                        if type(value) is list:
                            return [replace(v, old, new) for v in value]
                        return new if value == old else value
                    f.decisions = replace(f.decisions, original_id, f.context['context_id'])
                    f.reviews = replace(f.reviews, original_id, f.context['context_id'])
                    # Re-sort evidence sets after the context identity changes.
                    def sort_evidence(value):
                        if type(value) is dict:
                            return {k: sort_evidence(v) for k, v in value.items()}
                        if type(value) is list:
                            result = [sort_evidence(v) for v in value]
                            if result and type(result[0]) is dict and ('kind' in result[0] or 'interpretation' in result[0]):
                                result = ordered(result)
                            return result
                        return value
                    f.decisions = sort_evidence(f.decisions)
                    f.reviews = sort_evidence(f.reviews)
                    for i in range(len(f.reviews)):
                        f.change_review(i, lambda r: None)
                    f.engine()  # Complete reviewed extraction remains valid.
                    path = Path(f.context[field]['path'])
                    target = str(path)
                    if alias == 'dot_parent':
                        target = str(path.parent / 'alias' / '..' / path.name)
                    elif alias == 'case':
                        target = target.upper() if sys.platform == 'win32' else target
                    # Even supplying reviewed context cannot make its access fail.
                    self.add_failed_retrieval(f, target, [f.ref('/2/question')])
                    self.rejects(f)

    def test_unbound_failed_retrieval_and_inaccessible_notice_positive(self):
        f = self.fixture()
        target = 'https://example.invalid/unavailable-primary'
        self.add_failed_retrieval(f, target)
        notice = dict(member_keys=[f.decisions[2]['member_key']], discovered_at='2026-10-30T00:00:00Z', locator=target,
            artifact=None, applicability='applicable', applicability_evidence=[f.ref('/2/question')],
            addressed_decision_ids=[f.decisions[2]['decision_id']], addressed_bounded_review_ids=[f.reviews[0]['review_id']])
        f.notices['notices'] = [identified(notice, 'notice_id')]
        f.seal()
        engine = f.engine()
        for obs in f.observations:
            engine.resolve(obs)
        self.assertEqual(engine.finish()['summary']['observation_count'], 8)
        # New knowledge of a readable context cannot reuse the inaccessible case.
        notice['locator'] = f.context['content']['path']
        f.notices['notices'] = [identified(notice, 'notice_id')]
        f.seal()
        self.rejects(f)


if __name__ == '__main__':
    unittest.main()
