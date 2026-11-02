"""Synthetic runtime-stage provenance linkage, not scientific acceptance."""
import copy
import json
import unittest
from unittest.mock import patch

import test_qsar_runtime as fixture


class ContractAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.RuntimeTests()
        self.h.setUp()
        self.h.produce('contract')
        self.h.accept('contract')
        self.state = self.h.rt.status()
        entry = self.state['stages']['contract']
        self.candidate = {'upstream_tokens': {'contract': {
            'submission': entry['submission']['candidate_hash'],
            'review': entry['review']['review_hash'],
        }}}
        self.envelope = {
            'stage': 'contract', 'status': 'PASS',
            'contract_sha256': self.state['contract']['sha256'],
            'producer_execution_id': entry['submission']['producer_execution_id'],
            'reviewer_execution_id': entry['review']['reviewer_execution_id'],
            'candidate': self.small(entry['submission']['outputs'][0]),
            'review_evidence': self.small(entry['review']['evidence_bindings'][0]),
        }
        self.path = self.h.base / 'acceptance-adapter.json'

    def tearDown(self):
        self.h.tearDown()

    @staticmethod
    def small(value):
        return {key: value[key] for key in ('path', 'sha256')}

    def write(self):
        self.path.write_text(json.dumps(self.envelope), encoding='utf-8')
        return self.small(fixture.mod.snapshot(self.path))

    def check(self, descriptor=None):
        self.h.rt._contract_acceptance(self.state, self.candidate,
                                       [descriptor or self.write()])

    def test_current_stage_membership_passes(self):
        self.check()

    def test_unaccepted_or_stale_contract_rejects(self):
        for status in ('pending', 'produced', 'stale', 'revise'):
            with self.subTest(status=status):
                self.state['stages']['contract']['status'] = status
                with self.assertRaises(fixture.GateError):
                    self.check()

    def test_wrong_stage_or_identity_rejects_after_rehash(self):
        original = copy.deepcopy(self.envelope)
        for field in ('stage', 'status', 'contract_sha256', 'producer_execution_id', 'reviewer_execution_id'):
            with self.subTest(field=field):
                self.envelope = dict(original, **{field: 'wrong'})
                with self.assertRaises(fixture.GateError):
                    self.check()

    def test_same_content_unaccepted_evidence_cannot_replace_current_output(self):
        for field in ('candidate', 'review_evidence'):
            original = self.envelope[field]
            copied = self.h.base / (field + '-unaccepted.json')
            copied.write_bytes(fixture.Path(original['path']).read_bytes())
            self.envelope[field] = self.small(fixture.mod.snapshot(copied))
            with self.subTest(field=field), self.assertRaises(fixture.GateError):
                self.check()
            self.envelope[field] = original

    def test_changed_adapter_rejects(self):
        descriptor = self.write()
        self.path.write_text('{}', encoding='utf-8')
        with self.assertRaises(fixture.GateError):
            self.check(descriptor)

    def test_changed_accepted_evidence_rejects(self):
        fixture.Path(self.envelope['review_evidence']['path']).write_text('changed')
        with self.assertRaises(fixture.GateError):
            self.check()

    def test_stale_upstream_token_rejects(self):
        self.candidate['upstream_tokens']['contract']['review'] = 'old-review'
        with self.assertRaises(fixture.GateError):
            self.check()

    def test_legacy_without_adapter_is_unchanged(self):
        self.h.rt._contract_acceptance({}, {}, [])

    def accepted_curation_fixture(self):
        self.write()
        self.state['stages']['curation'] = {
            'status': 'accepted', 'submission': self.candidate,
            'review': {'curation_evidence_bindings': [fixture.mod.snapshot(self.path)]},
        }

    def test_exact_accepted_evidence_reuses_hashes_without_native_replay(self):
        self.accepted_curation_fixture()
        with patch.object(fixture.mod.importlib.util, 'spec_from_file_location',
                          side_effect=AssertionError('unexpected native replay')):
            self.assertEqual(self.h.rt._curation_evidence(self.state, self.candidate),
                             [self.small(fixture.mod.snapshot(self.path))])
            self.path.write_text('changed', encoding='utf-8')
            with self.assertRaises(fixture.GateError):
                self.h.rt._curation_evidence(self.state, self.candidate)

    def test_new_candidate_and_legacy_receipt_require_full_validation(self):
        self.accepted_curation_fixture()
        with patch.object(fixture.mod.importlib.util, 'spec_from_file_location',
                          side_effect=AssertionError('full validation required')):
            with self.assertRaisesRegex(AssertionError, 'full validation required'):
                self.h.rt._curation_evidence(self.state, {'different': True})
            self.state['stages']['curation']['review'].pop('curation_evidence_bindings')
            with self.assertRaisesRegex(AssertionError, 'full validation required'):
                self.h.rt._curation_evidence(self.state, self.candidate)


if __name__ == '__main__':
    unittest.main()
