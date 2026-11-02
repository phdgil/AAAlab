"""Synthetic v2 contracts/receipts only; no real contract acceptance."""
import copy
import json
import unittest
from test_qsar_adjudication import AdjudicationTests, adj, data


class V2Tests(AdjudicationTests):
    # Reuse fixture helpers, not the inherited v1 test suite.
    def setUp(self):
        super().setUp()
        narrative = "ETHANOL LD50 Rat oral male 20 and female 30 mg/kg Study source_study_id=-1"
        if 'target_origin' in self._testMethodName:
            narrative += "; experimental comparator from same-substance mortality; QSAR prediction/read-across; TEST calculated model estimated"
        self.fixture([narrative])
        self.contract = {"data_curation": self.policy, "scientific_policy": {
            "narrative_adjudication_v2": {"schema_version": 2, "clauses": {
                "identity": {"disposition": "scientific_quarantine", "reason": "critical_original_tested_identity_ambiguity"},
                "metadata": {"disposition": "scientific_quarantine", "reason": "unavailable_critical_metadata"},
                "nonoral": {"disposition": "excluded", "reason": "incompatible_task"}}}}}
        self.v2 = []
        for field in self.inventory:
            doc = [f for f in self.fields if f['locator']['document_ordinal'] == field['locator']['document_ordinal']]
            self.v2.append({"schema_version": 2, "field_id": field['field_id'], "action": "candidate_dispositions",
                "reason": "Synthetic exhaustive review", "evidence": [self.evidence(field, field['native_record']['text'])],
                "coverage": {"candidate_enumeration": "complete", "examined_field_ids": sorted(f['field_id'] for f in doc),
                    "remaining_coverage_issues": [], "remaining_policy_issues": [],
                    "no_candidate_review": {"conclusion": "no_measurement_candidates", "evidence": [self.evidence(field, field['native_record']['text'])],
                                            "rationale": "Synthetic document has no measurement"}}, "candidates": []})
        field = self.inventory[0]
        self.v2[0]['coverage']['no_candidate_review'] = None
        for point, sex in [('20', 'male'), ('30', 'female')]:
            c = {"measurement_evidence": self.evidence(field, point), "population_evidence": self.evidence(field, sex),
                 "disposition": "curation_candidate", "reason": "eligible", "observation": self.observation(field, point, sex)}
            c['population_scope'] = {k: c['observation']['fields'][k] for k in ('species', 'sex', 'strain', 'study_id')}
            c.update(adj.candidate_ids_v2(field, c))
            self.v2[0]['candidates'].append(c)

    def desc(self, name, value):
        p = self.root / name
        p.write_text(json.dumps(value), encoding='utf-8')
        return {'path': str(p.resolve()), 'sha256': adj.digest(p)}

    def bind_v2(self):
        contract = self.desc('contract-v2.json', self.contract)
        evidence = self.desc('evidence-v2.json', {'notice': 'Synthetic independent review only'})
        acceptance = self.desc('acceptance-v2.json', dict(schema_version=2, stage='contract', status='PASS',
            contract_sha256=contract['sha256'], candidate=self.desc('contract-candidate.json', {'contract': contract}),
            review_evidence=evidence, producer_execution_id='cp', reviewer_execution_id='cr', receipt_writer='leader',
            attestation='local_leader_attested_not_authenticated', findings=[]))
        dp = self.root / 'decisions-v2.jsonl'
        dp.write_text(''.join(json.dumps(d)+'\n' for d in self.v2), encoding='utf-8')
        config = {'schema_version': 2, 'inventory': {'path': str(self.root/'inventory/inventory.jsonl'), 'sha256': adj.digest(self.root/'inventory/inventory.jsonl')},
                  'decisions': {'path': str(dp), 'sha256': adj.digest(dp)}}
        scope = [{'field_id': d['field_id'], 'candidate_ids': sorted(c['candidate_id'] for c in d['candidates']),
                  'examined_field_ids': d['coverage']['examined_field_ids']} for d in self.v2]
        receipt = dict(schema_version=2, status='PASS', attestation='local_leader_attested_not_authenticated',
            producer_execution_id='p', reviewer_execution_id='r', receipt_writer='leader', findings=[], review_evidence=evidence,
            contract_sha256=contract['sha256'], accepted_contract_review_sha256=acceptance['sha256'],
            source_sha256=self.entry['sha256'], source_id=self.entry['source_id'], inventory_sha256=config['inventory']['sha256'],
            decisions_sha256=config['decisions']['sha256'], config_sha256=adj.stable(config), code_hashes=adj.code_hashes(),
            review_scope=scope, accepted_policy_clauses=self.contract['scientific_policy'].get('narrative_adjudication_v2', {}).get('clauses', {}))
        config['review_receipt'] = self.desc('receipt-v2.json', receipt)
        return config, contract, acceptance

    def run_v2(self, bindings=None):
        config, contract, acceptance = bindings or self.bind_v2()
        engine = adj.AdjudicationV2(config, self.entry, contract, acceptance, data.UNITS, self.root/'ledger-v2.jsonl')
        return list(engine.rows()), engine.summary

    def hold(self):
        c = self.v2[0]['candidates'][1]
        c.pop('observation')
        c.update(disposition='scientific_quarantine', reason='critical_original_tested_identity_ambiguity',
            policy_clause_id='identity', review={'evidence': [c['measurement_evidence']],
                'alternatives': [{'interpretation': name, 'evidence': [c['measurement_evidence']]}
                                 for name in ['material A', 'material B']], 'resolution': 'irreconcilable',
                'same_measurement_population': True, 'reopening_trigger': 'new source evidence'})
        return c

    def test_v2_mixed_siblings_and_stable_ids(self):
        original = self.v2[0]['candidates'][1]['candidate_id']
        c = self.hold()
        self.assertEqual(adj.candidate_ids_v2(self.inventory[0], c)['candidate_id'], original)
        rows, summary = self.run_v2()
        self.assertEqual([r['value'] for r in rows], ['20'])
        self.assertEqual(summary['scientific_quarantine_candidates'], 1)
        self.assertTrue(summary['required_source_coverage_complete'])
        c.clear(); c.update(self.v2[0]['candidates'][0]); c['candidate_id'] = original
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_unresolved_sibling_and_context_backlog(self):
        c = self.hold(); c.pop('review'); c.pop('policy_clause_id')
        c.update(disposition='unresolved', reason='parser_backlog', remaining_issues=['unsupported grammar'])
        rows, summary = self.run_v2()
        self.assertEqual(len(rows), 1)
        self.assertFalse(summary['required_source_coverage_complete'])
        self.v2[0]['coverage']['examined_field_ids'] = []
        _, summary = self.run_v2()
        self.assertFalse(summary['source_review_complete'])

    def test_v2_unsupported_reason_and_v3(self):
        self.hold()['reason'] = 'generic_conflict'
        with self.assertRaises(adj.AdjudicationError): self.run_v2()
        del self.contract['scientific_policy']['narrative_adjudication_v2']
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_binding_and_scope_mutations(self):
        for key in ['contract_sha256', 'source_sha256', 'config_sha256', 'code_hashes', 'review_scope', 'accepted_contract_review_sha256']:
            with self.subTest(key=key):
                config, contract, acceptance = self.bind_v2()
                receipt = adj.read_json(config['review_receipt']['path']); receipt[key] = []
                config['review_receipt'] = self.desc('receipt-v2.json', receipt)
                with self.assertRaises(adj.AdjudicationError): self.run_v2((config, contract, acceptance))

    def test_v2_no_omissions_and_missing_context_hold(self):
        bindings = self.bind_v2()
        removed = self.v2[0]['candidates'].pop()
        dp = bindings[0]['decisions']['path']
        from pathlib import Path
        Path(dp).write_text(''.join(json.dumps(d)+'\n' for d in self.v2), encoding='utf-8')
        with self.assertRaises(adj.AdjudicationError): self.run_v2(bindings)
        self.v2[0]['candidates'].append(removed)
        self.hold(); self.v2[0]['coverage']['remaining_coverage_issues'] = ['parser failure']
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_metadata_route_complete_review_and_new_asset_mutation(self):
        c = self.hold(); c.update(reason='unavailable_critical_metadata', policy_clause_id='metadata')
        c['review'] = {'missing_property': 'route', 'indispensable_property': 'route', 'evidence': [c['measurement_evidence']],
            'reviewed_assets': [self.entry], 'attempts': ['All known native/support joins reviewed'],
            'competing_interpretations': ['route absent'], 'remaining_uncertainty': 'route unknown',
            'reopening_trigger': 'New applicable crosswalk', 'extraction_complete': True,
            'context_complete': True, 'support_complete': True, 'linkage_complete': True}
        rows, summary = self.run_v2()
        self.assertEqual(len(rows), 1)
        self.assertEqual(summary['scientific_issue_counts'], {'unavailable_critical_metadata': 1})
        for key in ['extraction_complete', 'context_complete', 'support_complete', 'linkage_complete']:
            c['review'][key] = False
            with self.assertRaises(adj.AdjudicationError): self.run_v2()
            c['review'][key] = True

    def test_v2_self_review_and_contract_candidate_rejected(self):
        for key, value in [('reviewer_execution_id', 'cp'), ('stage', 'method_proposal')]:
            bindings = self.bind_v2()
            acceptance = adj.read_json(bindings[2]['path']); acceptance[key] = value
            altered = self.desc('acceptance-v2.json', acceptance)
            with self.assertRaises(adj.AdjudicationError): self.run_v2((bindings[0], bindings[1], altered))

    def test_v2_unknown_fields_cross_document_and_empty_lists(self):
        self.v2[0]['candidates'][0]['unexpected'] = True
        with self.assertRaises(adj.AdjudicationError): self.run_v2()
        del self.v2[0]['candidates'][0]['unexpected']
        self.v2[0]['coverage']['examined_field_ids'].append(self.v2[1]['field_id'])
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_independent_measurements_retained_and_reordering(self):
        rows, _ = self.run_v2()
        self.v2.reverse()
        for decision in self.v2:
            decision['candidates'].reverse()
            decision['reason'] = 'New review wording'
        reordered, _ = self.run_v2()
        self.assertEqual({r['record_id'] for r in rows}, {r['record_id'] for r in reordered})
        self.assertEqual(len(rows), 2)

    def test_v2_each_critical_reason_needs_its_own_clause(self):
        c = self.hold()
        for reason in adj.QUARANTINE_REASONS_V2 - {'unavailable_critical_metadata'}:
            c['reason'] = reason
            self.contract['scientific_policy']['narrative_adjudication_v2']['clauses']['identity']['reason'] = reason
            rows, summary = self.run_v2()
            self.assertEqual(len(rows), 1)
            self.assertEqual(summary['scientific_issue_counts'], {reason: 1})
        c['review']['alternatives'][0]['evidence'][0] = dict(c['measurement_evidence'], quote='wrong')
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_cosmetic_anchor_cannot_duplicate_observation(self):
        c = copy.deepcopy(self.v2[0]['candidates'][0])
        c['population_evidence'] = c['observation']['fields']['species']
        c.update(adj.candidate_ids_v2(self.inventory[0], c))
        self.v2[0]['candidates'].append(c)
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_source_changes_after_receipt_fail_closed(self):
        bindings = self.bind_v2()
        with self.source.open('ab') as stream:
            stream.write(b'mutation')
        with self.assertRaises(adj.AdjudicationError): self.run_v2(bindings)

    def test_v2_empty_enumeration_needs_explicit_review(self):
        self.v2[1]['coverage']['no_candidate_review'] = None
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_review_r1_held_population_reanchor_cannot_emit(self):
        duplicate = copy.deepcopy(self.v2[0]['candidates'][1])
        self.hold()
        duplicate['population_evidence'] = duplicate['observation']['fields']['species']
        duplicate.update(adj.candidate_ids_v2(self.inventory[0], duplicate))
        self.v2[0]['candidates'].append(duplicate)
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_review_r2_cosmetic_population_anchor_preserves_identity(self):
        before, _ = self.run_v2()
        candidate = self.v2[0]['candidates'][0]
        candidate['population_evidence'] = candidate['observation']['fields']['species']
        candidate.update(adj.candidate_ids_v2(self.inventory[0], candidate))
        after, _ = self.run_v2()
        self.assertEqual(before[0]['record_id'], after[0]['record_id'])

    def test_v2_review_same_value_disjoint_male_female_survive_hold(self):
        female = self.v2[0]['candidates'][1]
        shared = self.v2[0]['candidates'][0]['measurement_evidence']
        female['measurement_evidence'] = shared
        female['observation']['fields']['value'] = shared
        female.update(adj.candidate_ids_v2(self.inventory[0], female))
        rows, _ = self.run_v2()
        self.assertEqual([(r['value'], r['sex']) for r in rows], [('20', 'male'), ('20', 'female')])
        self.hold()
        rows, _ = self.run_v2()
        self.assertEqual([(r['value'], r['sex']) for r in rows], [('20', 'male')])

    def test_v2_review_unknown_population_overlaps_every_disposition(self):
        original = copy.deepcopy(self.v2[0]['candidates'][1])
        held = self.hold()
        for disposition in ('scientific_quarantine', 'unresolved', 'excluded'):
            with self.subTest(disposition=disposition):
                c = copy.deepcopy(held)
                c['population_scope']['sex'] = None
                c.update(adj.candidate_ids_v2(self.inventory[0], c))
                if disposition == 'unresolved':
                    c.pop('review'); c.pop('policy_clause_id')
                    c.update(disposition=disposition, remaining_issues=['unknown population'])
                elif disposition == 'excluded':
                    c.update(disposition=disposition, reason='incompatible_task', policy_clause_id='nonoral',
                        review={'evidence': [c['measurement_evidence']], 'rationale': 'synthetic', 'reopening_trigger': 'new evidence'})
                self.v2[0]['candidates'] = [copy.deepcopy(original), c]
                with self.assertRaisesRegex(adj.AdjudicationError, 'Overlapping'): self.run_v2()
                self.v2[0]['candidates'].reverse()
                with self.assertRaisesRegex(adj.AdjudicationError, 'Overlapping'): self.run_v2()

    def test_v2_review_aliases_and_missing_study_do_not_prove_disjointness(self):
        for key, a, b in [('species', 'rat', 'rattus norvegicus'), ('strain', 'sd', 'sprague dawley'),
                          ('study_id', '-1', 'Study A'), ('study_id', 'n.a.', 'Study B')]:
            left = dict.fromkeys(adj.POPULATION_FIELDS_V2)
            right = dict(left)
            left[key], right[key] = a, b
            self.assertFalse(adj.populations_disjoint_v2(left, right))
        c = self.v2[0]['candidates'][0]
        old = c['candidate_id']
        c['population_scope']['study_id'] = self.evidence(self.inventory[0], '-1')
        self.assertEqual(adj.candidate_ids_v2(self.inventory[0], c)['candidate_id'], old)
        c['observation']['fields']['study_id'] = c['population_scope']['study_id']
        self.assertEqual(len(self.run_v2()[0]), 2)

    def test_v2_review_scope_cannot_misrepresent_emitted_population(self):
        c = self.v2[0]['candidates'][1]
        c['population_scope']['sex'] = self.v2[0]['candidates'][0]['population_scope']['sex']
        c.update(adj.candidate_ids_v2(self.inventory[0], c))
        with self.assertRaisesRegex(adj.AdjudicationError, 'differs from observation'): self.run_v2()

    def origin(self, candidate, classification='nonexperimental', subtype='qsar_prediction'):
        obs = candidate['observation']
        field = self.inventory[0]
        origin_label = ('QSAR prediction/read-across' if classification == 'nonexperimental' else
                        'experimental comparator from same-substance mortality' if classification == 'experimental' else 'Study')
        origin = dict(classification=classification, subtype=subtype,
            evidence=[self.evidence(field, origin_label)], binding={
                'chemical': obs['chemical_attribution']['subject'],
                'row': self.evidence(field, field['native_record']['text']),
                'column': self.evidence(field, origin_label),
                **{k: obs['fields'][k] for k in ('endpoint', 'species', 'route', 'value', 'unit')},
                'modifier': obs['fields']['relation']})
        candidate['target_origin'] = origin
        return origin

    def exclude_prediction(self, candidate, subtype='qsar_prediction'):
        self.origin(candidate, subtype=subtype)
        candidate.pop('observation')
        candidate.update(disposition='excluded', reason='nonexperimental_target', policy_clause_id='prediction',
            review={'evidence': [candidate['measurement_evidence']], 'rationale': 'Synthetic exact-column origin review',
                    'reopening_trigger': 'Corrected source classification'})
        self.contract['scientific_policy']['narrative_adjudication_v2']['clauses']['prediction'] = {
            'disposition': 'excluded', 'reason': 'nonexperimental_target'}

    def test_v2_target_origin_explicit_prediction_and_read_across(self):
        candidate = self.v2[0]['candidates'][1]
        self.exclude_prediction(candidate)
        for subtype in ('qsar_prediction', 'read_across'):
            candidate['target_origin']['subtype'] = subtype
            rows, summary = self.run_v2()
            self.assertEqual([(r['value'], r['sex']) for r in rows], [('20', 'male')])
            self.assertEqual(summary['excluded'], 1)

    def test_v2_target_origin_statistical_estimate_and_transform_survive(self):
        candidate = self.v2[0]['candidates'][0]
        origin = self.origin(candidate, 'experimental', 'same_substance_mortality_estimate')
        origin.update(experimental_basis_evidence=[candidate['measurement_evidence']], estimation_method='probit', transformation='none')
        for method in ('probit', 'maximum_likelihood', 'up_and_down'):
            origin['estimation_method'] = method
            self.assertEqual(len(self.run_v2()[0]), 2)
        origin.update(subtype='validated_experimental_transform', estimation_method='none')
        for transformation in ('unit', 'molar', 'logarithmic'):
            origin['transformation'] = transformation
            rows, _ = self.run_v2()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['native_value'], '20')

    def test_v2_target_origin_unknown_conflict_cannot_exclude_or_quarantine(self):
        candidate = self.v2[0]['candidates'][1]
        self.exclude_prediction(candidate)
        for classification in ('origin_unknown', 'origin_conflict'):
            candidate['target_origin'].update(classification=classification, subtype=None)
            with self.assertRaises(adj.AdjudicationError): self.run_v2()
        candidate.pop('policy_clause_id'); candidate.pop('review')
        candidate.update(disposition='unresolved', reason='origin_conflict', remaining_issues=['origin_conflict'])
        rows, summary = self.run_v2()
        self.assertEqual(len(rows), 1)
        self.assertFalse(summary['required_source_coverage_complete'])

    def test_v2_target_origin_missing_classification_and_wrong_value_reject(self):
        candidate = self.v2[0]['candidates'][1]
        self.exclude_prediction(candidate)
        origin = candidate.pop('target_origin')
        with self.assertRaises(adj.AdjudicationError): self.run_v2()
        candidate['target_origin'] = origin
        origin['binding']['value'] = self.v2[0]['candidates'][0]['measurement_evidence']
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_target_origin_prediction_excludes_even_without_route(self):
        candidate = self.v2[0]['candidates'][1]
        self.exclude_prediction(candidate)
        for name in ('route', 'species', 'endpoint', 'unit', 'modifier'):
            candidate['target_origin']['binding'][name] = None
        rows, summary = self.run_v2()
        self.assertEqual([r['value'] for r in rows], ['20'])
        self.assertEqual(summary['exclusion_reason_counts'], {'nonexperimental_target': 1})
        ledger = list(adj.jsonl(self.root/'ledger-v2.jsonl'))
        self.assertIsNone(ledger[0]['candidates'][1]['target_origin']['binding']['route'])

    def test_v2_target_origin_unknown_cannot_be_route_quarantine_proxy(self):
        candidate = self.v2[0]['candidates'][1]
        self.origin(candidate, 'origin_unknown', None)
        self.hold()
        with self.assertRaisesRegex(adj.AdjudicationError, 'remains unresolved'): self.run_v2()

    def test_v2_target_origin_experimental_project_and_missing_study_retained(self):
        candidate = self.v2[0]['candidates'][0]
        candidate['observation']['fields']['study_id'] = self.evidence(self.inventory[0], '-1')
        candidate['population_scope']['study_id'] = candidate['observation']['fields']['study_id']
        origin = self.origin(candidate, 'experimental', 'reported_measurement')
        origin.update(experimental_basis_evidence=[candidate['measurement_evidence']], estimation_method='none', transformation='none')
        rows, _ = self.run_v2()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['target_origin']['classification'], 'experimental')
        candidate.pop('observation')
        candidate.update(disposition='excluded', reason='nonexperimental_target', policy_clause_id='prediction',
            review={'evidence': [candidate['measurement_evidence']], 'rationale': 'Missing study ID', 'reopening_trigger': 'new record'})
        self.contract['scientific_policy']['narrative_adjudication_v2']['clauses']['prediction'] = {'disposition': 'excluded', 'reason': 'nonexperimental_target'}
        with self.assertRaisesRegex(adj.AdjudicationError, 'Experimental origin'): self.run_v2()

    def test_v2_target_origin_requires_new_authorized_clause_and_exact_column(self):
        candidate = self.v2[0]['candidates'][1]
        self.exclude_prediction(candidate)
        clauses = self.contract['scientific_policy']['narrative_adjudication_v2']['clauses']
        clause = clauses.pop('prediction')
        with self.assertRaises(adj.AdjudicationError): self.run_v2()
        clauses['prediction'] = clause
        candidate['target_origin']['binding']['column']['quote'] = 'invented QSAR column'
        with self.assertRaises(adj.AdjudicationError): self.run_v2()

    def test_v2_missing_study_id_does_not_justify_quarantine(self):
        rows, _ = self.run_v2()
        self.assertEqual(len(rows), 2)
        c = self.hold(); c.update(reason='unavailable_critical_metadata', policy_clause_id='metadata')
        c['review'] = {'missing_property': 'study_id', 'indispensable_property': 'route', 'evidence': [c['measurement_evidence']],
            'reviewed_assets': [self.entry], 'attempts': ['native linkage checked'], 'competing_interpretations': ['unknown'],
            'remaining_uncertainty': 'study id missing', 'reopening_trigger': 'crosswalk',
            'extraction_complete': True, 'context_complete': True, 'support_complete': True, 'linkage_complete': True}
        with self.assertRaises(adj.AdjudicationError): self.run_v2()


# Keep this suite focused while sharing the historical fixture builder.
for _name in list(AdjudicationTests.__dict__):
    if _name.startswith('test_'):
        setattr(V2Tests, _name, None)
del AdjudicationTests

if __name__ == '__main__':
    unittest.main()
