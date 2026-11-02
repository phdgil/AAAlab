"""Synthetic integration evidence only; these fixtures are not scientific acceptance."""
import copy
import csv
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_qsar_adjudication_v2 as fixtures
import test_qsar_runtime as runtime_tests

data, adj = fixtures.data, fixtures.adj
evidence = data.curation_evidence_module()


class CandidateIntegrationTests(unittest.TestCase):
    def setUp(self):
        class SourceFixture(fixtures.V2Tests):
            def fixture(self, narratives):
                self.raw = self.root / 'raw'
                self.raw.mkdir()
                self.source = self.raw / 'hsdb.zip'
                super().fixture(narratives)
                self.entry['source_id'] = 'hsdb.zip'
                self.policy['sources'][0]['source_id'] = 'hsdb.zip'
                directory = self.root / 'renamed-inventory'
                adj.export_inventory(self.source, 'hsdb.zip', directory)
                (self.root / 'inventory/inventory.jsonl').write_bytes((directory / 'inventory.jsonl').read_bytes())
                self.inventory = list(adj.jsonl(directory / 'inventory.jsonl'))
                self.fields = [f for doc in adj.native_fields(self.source, 'hsdb.zip', self.entry['sha256']) for f in doc]
        self.f = SourceFixture()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.root = self.f.root
        support = self.f.raw / 'identity.csv'
        support.write_text('cas,smiles\n64-17-5,CCO\n', encoding='utf-8')
        self.f.policy['identity_sources'] = [{'source_id': support.name, 'columns': {'cas': 'cas', 'smiles': 'smiles'}}]
        self.f.contract['execution_policy'] = {'allow_training_with_unresolved_required_source_coverage': False}
        self.out = self.root / 'curation'
        self.collection = self.root / 'collection'

    def generate(self, hold=False):
        if hold:
            self.f.hold()
        config, self.contract, self.acceptance = self.f.bind_v2()
        self.binding = dict(config, full_contract=self.contract, accepted_contract_review=self.acceptance)
        self.mapping = self.f.desc('external.json', {'hsdb.zip': self.binding})
        data.collect(self.f.raw, self.collection)
        data.curate(self.contract['path'], self.collection / 'source_registry.json', self.out, self.mapping['path'])
        self.manifest = self.out / 'curation_manifest.json'
        return data.load(self.manifest)

    def check(self):
        return evidence.validate(self.contract['path'], self.manifest, self.out / 'curated.csv')

    def rewrite(self, name, change):
        path = self.out / name
        with path.open(encoding='utf-8', newline='') as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        change(rows)
        data.write_csv(path, rows, fields)
        self.rebind(name)

    def rebind(self, name):
        manifest = data.load(self.manifest)
        manifest['bound_evidence']['outputs'][name] = evidence.binding(self.out / name)
        data.dump(self.manifest, manifest)

    def test_mixed_sibling_full_checks_and_dependencies(self):
        manifest = self.generate(hold=True)
        self.assertEqual(manifest['output_rows'], 1)
        summary = manifest['narrative_accounting']['hsdb.zip']
        self.assertEqual(summary['candidates'], 2)
        self.assertEqual(summary['scientific_quarantine_candidates'], 1)
        self.assertNotIn('complete_narrative_accounting', summary)
        refs = self.check()
        self.assertIn(self.acceptance, refs)
        self.assertEqual(evidence.contract_acceptance_bindings(self.manifest), [self.acceptance])
        paths = {item['path'] for item in refs}
        accepted = data.load(self.acceptance['path'])
        self.assertIn(accepted['candidate']['path'], paths)
        self.assertIn(accepted['review_evidence']['path'], paths)
        with (self.out / 'curated.csv').open(encoding='utf-8') as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['candidate_id'], self.f.v2[0]['candidates'][0]['candidate_id'])
        self.assertAlmostEqual(float(row['target_mg_kg']), 20)

    def test_missing_duplicate_and_sibling_body(self):
        self.generate()
        self.check()
        for name in ['dispositions.csv', 'identity_ledger.csv', 'curated.csv']:
            original = (self.out / name).read_bytes()
            for change in [lambda rows: rows.pop(), lambda rows: rows.append(dict(rows[0])),
                           lambda rows: rows[0].update(candidate_id=rows[1]['candidate_id']),
                           lambda rows: rows[0].update(adjudication=rows[1]['adjudication'])]:
                with self.subTest(name=name, change=change):
                    self.rewrite(name, change)
                    with self.assertRaises(evidence.EvidenceError): self.check()
                    (self.out / name).write_bytes(original)
                    self.rebind(name)

    def test_coordinated_quarantined_candidate_emission(self):
        self.generate(hold=True)
        held = self.f.v2[0]['candidates'][1]
        for name in ['dispositions.csv', 'identity_ledger.csv', 'curated.csv']:
            self.rewrite(name, lambda rows: rows[0].update(source_record_id=held['candidate_id'],
                **{k: held[k] for k in ('candidate_id', 'measurement_id', 'population_id')}))
        with self.assertRaises(evidence.EvidenceError): self.check()

    def test_exact_native_fields_and_field_ledger(self):
        manifest = self.generate()
        name = manifest['narrative_accounting']['hsdb.zip']['field_ledger']
        ledger = list(evidence.json_rows(self.out / name))
        ledger[0]['document_context'][0]['native_record']['text'] = 'invented'
        (self.out / name).write_text(''.join(json.dumps(r) + '\n' for r in ledger), encoding='utf-8')
        self.rebind(name)
        with self.assertRaisesRegex(evidence.EvidenceError, 'field ledger'): self.check()

    def test_stale_contract_acceptance_and_review_dependencies(self):
        self.generate()
        acceptance = data.load(self.acceptance['path'])
        for descriptor in [self.contract, self.acceptance, acceptance['candidate'], acceptance['review_evidence'],
                           self.binding['review_receipt'], self.binding['decisions']]:
            path = Path(descriptor['path'])
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.subTest(path=path.name), self.assertRaises(evidence.EvidenceError): self.check()
            path.write_bytes(original)

    def test_unaccepted_draft_and_wrong_active_contract(self):
        self.generate()
        for change in [lambda b: b.pop('accepted_contract_review'),
                       lambda b: b.update(full_contract=self.f.desc('other-contract.json', self.f.contract)),
                       lambda b: b.update(accepted_contract_review=self.f.desc('proposal.json', {'status': 'PASS', 'stage': 'method_proposal'}))]:
            binding = copy.deepcopy(self.binding)
            change(binding)
            self.f.desc('external.json', {'hsdb.zip': binding})
            with self.assertRaises((data.DataError, ValueError)):
                data.curate(self.contract['path'], self.collection / 'source_registry.json', self.root / 'rejected', self.mapping['path'])

    def test_unresolved_candidate_remains_blocking(self):
        held = self.f.hold()
        held.pop('review'); held.pop('policy_clause_id')
        held.update(disposition='unresolved', reason='origin_unknown', remaining_issues=['origin_unknown'])
        manifest = self.generate()
        self.assertEqual(manifest['output_rows'], 1)
        self.assertEqual(manifest['status'], 'blocked')
        self.assertFalse(manifest['narrative_accounting']['hsdb.zip']['required_source_coverage_complete'])
        with self.assertRaises(evidence.EvidenceError): self.check()

    def test_normal_identity_and_qualifier_checks_still_apply(self):
        self.f.contract['data_curation']['relation_policy'] = 'exact_only'
        manifest = self.generate()
        self.assertEqual(manifest['output_rows'], 0)
        self.check()
        with (self.out / 'dispositions.csv').open(encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(all('nonexact_or_unknown_relation' in json.loads(r['reasons']) for r in rows))

    def test_unknown_origin_cannot_be_injected_into_emission(self):
        self.generate()
        candidate = self.f.v2[0]['candidates'][0]
        origin = self.f.origin(candidate, classification='origin_unknown', subtype=None)
        for name in ['dispositions.csv', 'identity_ledger.csv', 'curated.csv']:
            path = self.out / name
            with path.open(encoding='utf-8', newline='') as stream:
                rows = list(csv.DictReader(stream))
            rows[0]['target_origin'] = json.dumps(origin)
            data.write_csv(path, rows, list(rows[0]))
            self.rebind(name)
        with self.assertRaises(evidence.EvidenceError): self.check()

    def test_unknown_origin_decision_cannot_emit(self):
        candidate = self.f.v2[0]['candidates'][0]
        self.f.origin(candidate, classification='origin_unknown', subtype=None)
        with self.assertRaises(ValueError): self.generate()

    def test_missing_identity_never_trains(self):
        self.f.policy['identity_sources'] = []
        manifest = self.generate()
        self.assertEqual(manifest['output_rows'], 0)
        self.check()

    def test_missing_optional_metadata_is_not_a_hold(self):
        candidate = self.f.v2[0]['candidates'][0]
        candidate['observation']['fields']['study_id'] = self.f.evidence(self.f.inventory[0], '-1')
        candidate['population_scope']['study_id'] = candidate['observation']['fields']['study_id']
        manifest = self.generate()
        self.assertEqual(manifest['output_rows'], 2)
        self.check()

    def runtime_generate(self, mutate_acceptance=None):
        """Use public dispatch/submit/review APIs, never inject accepted state."""
        config, self.contract, acceptance = self.f.bind_v2()
        runtime = runtime_tests.RuntimeTests()
        runtime.setUp()
        self.addCleanup(runtime.tearDown)
        runtime.rt.replace_contract(self.contract['path'], 'Synthetic v2 full contract')
        dispatch = runtime.bind(runtime.prepare('contract'))
        candidate = Path(dispatch['owned_output_dir']) / 'contract-candidate.json'
        data.dump(candidate, {'contract': self.contract})
        runtime.rt.submit(dispatch['dispatch_id'], {'outputs': [str(candidate)],
            'summary': 'Synthetic contract candidate', 'checks': ['fixture only']})
        review = runtime.accept('contract')
        envelope = data.load(acceptance['path'])
        envelope.update(candidate=evidence.binding(candidate),
                        review_evidence=evidence.binding(review['evidence_bindings'][0]['path']),
                        producer_execution_id=dispatch['native_execution_id'],
                        reviewer_execution_id=review['reviewer_execution_id'])
        if mutate_acceptance:
            mutate_acceptance(envelope)
        self.acceptance = self.f.desc('acceptance-v2.json', envelope)
        receipt = data.load(config['review_receipt']['path'])
        receipt['accepted_contract_review_sha256'] = self.acceptance['sha256']
        config['review_receipt'] = self.f.desc('receipt-v2.json', receipt)
        self.binding = dict(config, full_contract=self.contract, accepted_contract_review=self.acceptance)
        self.mapping = self.f.desc('external.json', {'hsdb.zip': self.binding})
        dispatch = runtime.bind(runtime.prepare('collection'))
        self.collection = Path(dispatch['owned_output_dir']) / 'collected'
        data.collect(self.f.raw, self.collection)
        runtime.rt.submit(dispatch['dispatch_id'], {'outputs': [str(p) for p in self.collection.iterdir() if p.is_file()],
            'summary': 'Synthetic collection', 'checks': ['fixture inventory']})
        runtime.accept('collection')
        dispatch = runtime.bind(runtime.prepare('curation'))
        self.out = Path(dispatch['owned_output_dir']) / 'curated'
        data.curate(self.contract['path'], self.collection / 'source_registry.json', self.out, self.mapping['path'])
        self.manifest = self.out / 'curation_manifest.json'
        runtime.rt.submit(dispatch['dispatch_id'], {'outputs': [str(p) for p in self.out.iterdir() if p.is_file()],
            'summary': 'Synthetic v2 curation', 'checks': ['fixture candidate membership'],
            'coverage': data.load(self.manifest)})
        return runtime

    def test_public_runtime_acceptance_and_downstream_freshness(self):
        runtime = self.runtime_generate()
        runtime.accept('curation')
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'accepted')
        dispatch = runtime.bind(runtime.prepare('preprocessing'))
        output = Path(dispatch['owned_output_dir']) / 'untrusted-preprocessing.json'
        data.dump(output, {'fixture_only': True})
        path = Path(self.binding['decisions']['path'])
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaises(runtime_tests.GateError):
            runtime.rt.submit(dispatch['dispatch_id'], {'outputs': [str(output)],
                'summary': 'Should be blocked', 'checks': ['fixture']})
        self.assertNotEqual(runtime.rt.status()['stages']['preprocessing']['status'], 'produced')
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'stale')

    def test_public_runtime_rejects_unaccepted_review_evidence(self):
        runtime = self.runtime_generate(lambda envelope: envelope.update(
            review_evidence=self.f.desc('unaccepted-review.json', {'status': 'PASS', 'fixture_only': True})))
        # Structural producer integrity can pass; runtime must establish acceptance linkage.
        self.check()
        with self.assertRaises(runtime_tests.GateError): runtime.accept('curation')
        self.assertEqual(runtime.rt.status()['stages']['curation']['status'], 'produced')

    def test_public_eligible_sibling_cannot_be_silently_excluded(self):
        original = data.curate
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            self.manifest = self.out / 'curation_manifest.json'
            self.rewrite('dispositions.csv', lambda rows: rows[1].update(
                disposition='excluded', reasons='["invented_exclusion"]', output_row_ids='[]'))
            self.rewrite('curated.csv', lambda rows: rows.pop())
            self.rewrite('conflict_ledger.csv', lambda rows: rows.clear())
            manifest = data.load(self.manifest)
            manifest.update(output_rows=1, dispositions={'retained': 1, 'excluded': 1}, task_counts={'rat': 1})
            data.dump(self.manifest, manifest)
            return result
        with patch.object(data, 'curate', side_effect=mutate):
            runtime = self.runtime_generate()
        with self.assertRaises(runtime_tests.GateError): runtime.accept('curation')


if __name__ == '__main__':
    unittest.main()
