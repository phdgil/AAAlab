"""State/provenance tests; these tests never launch or click the desktop."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import openmra_runner as runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.source = self.inputs / 'mixture.xlsx'
        self.source.write_bytes(b'test-only source')
        self.app = self.root / 'OpenMRA.exe'
        self.app.write_bytes(b'test-only executable identity; never executed')
        self.output = self.root / 'output'
        self.args = argparse.Namespace(app=str(self.app), input_dir=str(self.inputs),
                                       output_dir=str(self.output), resume=False)

    def test_duplicate_basenames_rejected(self):
        nested = self.inputs / 'old'
        nested.mkdir()
        (nested / self.source.name).write_bytes(b'old version')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            runner.input_files(self.inputs)

    def test_untested_executable_is_blocked(self):
        with patch.object(runner, 'desktop_check'), \
             patch.object(runner.importlib.util, 'find_spec', return_value=object()):
            checked = runner.preflight(self.app)
        self.assertEqual(checked['status'], 'BLOCKED')
        self.assertTrue(any('Untested executable' in e for e in checked['errors']))

    def test_old_python_is_blocked_by_preflight(self):
        with patch.object(runner.sys, 'version_info', (3, 10)), \
             patch.object(runner, 'desktop_check'), \
             patch.object(runner.importlib.util, 'find_spec', return_value=object()):
            checked = runner.preflight(self.app)
        self.assertEqual(checked['status'], 'BLOCKED')
        self.assertTrue(any('Python 3.11' in e for e in checked['errors']))

    def test_output_must_not_be_inside_inputs(self):
        self.args.output_dir = str(self.inputs / 'outputs')
        with self.assertRaisesRegex(ValueError, 'outside'):
            runner.run(self.args)
        self.assertFalse((self.inputs / 'outputs').exists())

    def test_nonempty_output_not_overwritten(self):
        self.output.mkdir()
        retained = self.output / 'user_file.txt'
        retained.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            runner.run(self.args)
        self.assertEqual(retained.read_text(), 'keep')

    def prepare_completed(self):
        (self.output / 'exports').mkdir(parents=True)
        report = self.output / 'exports' / self.source.name
        report.write_bytes(b'test report')
        manifest = {'schema': 1, 'status': 'COMPLETE', 'app': str(self.app),
                    'app_sha256': runner.digest(self.app),
                    'inputs': {str(self.source): runner.digest(self.source)},
                    'jobs': {self.source.name: {'status': 'PASS', 'attempt': 1,
                                               'report_sha256': runner.digest(report)}}}
        runner.write_json(self.output / 'manifest.json', manifest)
        self.args.resume = True
        return report

    def test_resume_rejects_changed_source(self):
        self.prepare_completed()
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Input set/content changed'):
            runner.run(self.args)

    def test_resume_rechecks_report_hash(self):
        report = self.prepare_completed()
        report.write_bytes(b'replaced')
        with patch.object(runner, 'preflight', return_value={'status': 'READY'}), \
             patch.object(runner, 'gui_lock', return_value=nullcontext()), \
             patch.object(runner, 'Gui') as gui:
            with self.assertRaisesRegex(ValueError, 'report changed'):
                runner.run(self.args)
            gui.assert_not_called()

    def test_resume_does_not_rerun_verified_report(self):
        self.prepare_completed()
        with patch.object(runner, 'preflight', return_value={'status': 'READY'}), \
             patch.object(runner, 'gui_lock', return_value=nullcontext()), \
             patch('report_validation.validate_report', return_value={'status': 'PASS'}), \
             patch.object(runner, 'Gui') as gui:
            runner.run(self.args)
            gui.assert_not_called()
        self.assertEqual(json.loads((self.output / 'manifest.json').read_text())['status'], 'COMPLETE')

    def test_resume_recovers_published_report_without_pass_state(self):
        report = self.prepare_completed()
        state = json.loads((self.output / 'manifest.json').read_text())
        state['status'] = 'RUNNING'
        state['jobs'][self.source.name] = {'status': 'RUNNING', 'attempt': 1}
        runner.write_json(self.output / 'manifest.json', state)
        before = runner.digest(report)
        with patch.object(runner, 'preflight', return_value={'status': 'READY'}), \
             patch.object(runner, 'gui_lock', return_value=nullcontext()), \
             patch('report_validation.validate_report', return_value={'status': 'PASS', 'warnings': []}), \
             patch.object(runner, 'Gui') as gui:
            runner.run(self.args)
            gui.assert_not_called()
        self.assertEqual(runner.digest(report), before)
        state = json.loads((self.output / 'manifest.json').read_text())
        self.assertTrue(state['jobs'][self.source.name]['recovered_after_interruption'])

    def test_bad_export_never_enters_exports(self):
        class FakeGui:
            def __init__(self, *args):
                pass
            def run(self, source, destination):
                destination.write_bytes(b'report for another input')
            def close(self):
                pass
        with patch.object(runner, 'preflight', return_value={'status': 'READY'}), \
             patch.object(runner, 'gui_lock', return_value=nullcontext()), \
             patch('report_validation.validate_report', return_value={'status': 'FAIL', 'errors': ['wrong input']}), \
             patch.object(runner, 'Gui', FakeGui):
            with self.assertRaisesRegex(ValueError, 'wrong input'):
                runner.run(self.args)
        self.assertEqual(list((self.output / 'exports').iterdir()), [])
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'FAIL')
        self.assertTrue(list((self.output / '_workspace').rglob('validation.json')))

    def test_locked_desktop_records_blocker_before_app_launch(self):
        with patch.object(runner, 'preflight', return_value={'status': 'BLOCKED', 'errors': ['Windows is locked']}), \
             patch.object(runner, 'Gui') as gui:
            with self.assertRaisesRegex(runner.Blocked, 'locked'):
                runner.run(self.args)
            gui.assert_not_called()
        self.assertEqual(json.loads((self.output / 'manifest.json').read_text())['status'], 'BLOCKED')

    def test_operator_lock_failure_is_recorded(self):
        with patch.object(runner, 'preflight', return_value={'status': 'READY'}), \
             patch.object(runner, 'gui_lock', side_effect=runner.Blocked('Another operator owns GUI')):
            with self.assertRaisesRegex(runner.Blocked, 'Another operator'):
                runner.run(self.args)
        state = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(state['status'], 'BLOCKED')
        self.assertEqual(state['jobs'][self.source.name]['status'], 'PENDING')

    def test_orphan_attempt_directory_is_preserved_on_resume(self):
        report = self.prepare_completed()
        report.unlink()
        state = json.loads((self.output / 'manifest.json').read_text())
        state['jobs'][self.source.name] = {'status': 'PENDING', 'attempt': 0}
        runner.write_json(self.output / 'manifest.json', state)
        orphan = self.output / '_workspace' / 'mixture_attempt1'
        orphan.mkdir(parents=True)
        (orphan / 'keep.txt').write_text('prior interrupted evidence')
        class FakeGui:
            def __init__(self, *args):
                pass
            def run(self, source, destination):
                destination.write_bytes(b'valid report stub')
            def close(self):
                pass
        with patch.object(runner, 'preflight', return_value={'status': 'READY'}), \
             patch.object(runner, 'gui_lock', return_value=nullcontext()), \
             patch('report_validation.validate_report', return_value={'status': 'PASS', 'warnings': []}), \
             patch.object(runner, 'Gui', FakeGui):
            runner.run(self.args)
        self.assertEqual((orphan / 'keep.txt').read_text(), 'prior interrupted evidence')
        state = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(state['jobs'][self.source.name]['attempt'], 2)


if __name__ == '__main__':
    unittest.main()
