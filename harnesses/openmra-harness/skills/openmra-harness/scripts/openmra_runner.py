"""Run OpenMRA v0.2.0 RM inputs through its Windows GUI and verify exports.

No model calculations or workbook edits occur here. Each input gets a fresh app
instance; a report is accepted only after independent content validation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

TESTED_APP_SHA256 = '2b30c03ce2ee87b11fa3b6effb2d3d0617b185aa2462252a5a164257d73843ec'


class Blocked(RuntimeError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)


def foreground_image():
    """Query only the foreground process to detect the Windows lock screen."""
    from ctypes import wintypes
    user, kernel = ctypes.windll.user32, ctypes.windll.kernel32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ''
    try:
        buf, count = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(count)):
            return ''
        return buf.value
    finally:
        kernel.CloseHandle(handle)


def desktop_check():
    if os.name != 'nt':
        raise Blocked('GUI execution requires Windows. Report validation is cross-platform.')
    from ctypes import wintypes
    user = ctypes.windll.user32
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    desktop = user.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        raise Blocked('No accessible interactive desktop. Unlock/sign in before resuming.')
    user.CloseDesktop(desktop)
    if Path(foreground_image()).name.lower() in {'lockapp.exe', 'logonui.exe'}:
        raise Blocked('Windows is locked. Unlock/sign in before resuming; no unlocking is attempted.')


def preflight(app):
    errors = []
    if sys.version_info < (3, 11):
        errors.append('Python 3.11 or later is required.')
    app = Path(app).resolve()
    if not app.is_file() or app.suffix.lower() != '.exe':
        errors.append('Supply the installed OpenMRA.exe path.')
    app_hash = digest(app) if app.is_file() else None
    if app_hash is not None and app_hash != TESTED_APP_SHA256:
        errors.append('Untested executable build. This GUI profile requires the documented RMFix SHA-256; calibrate and smoke-test a new profile before use.')
    for name in ('pywinauto', 'PIL'):
        if importlib.util.find_spec(name) is None:
            errors.append(f'Missing dependency: {name}. See README prerequisites.')
    try:
        desktop_check()
    except Blocked as exc:
        errors.append(str(exc))
    return {'status': 'BLOCKED' if errors else 'READY', 'errors': errors,
            'app': str(app), 'app_sha256': app_hash,
            'profile': 'OpenMRA v0.2.0 / RM / English / 96 DPI', 'python': sys.version.split()[0],
            'note': 'READY checks prerequisites; each GUI action also checks foreground ownership.'}


@contextmanager
def gui_lock():
    """One operator per Windows session, automatically released on process exit."""
    from ctypes import wintypes
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, True, 'Local\\AAAlabOpenMRAGuiOperator')
    if not handle:
        raise Blocked('Could not create GUI session lock.')
    if kernel.GetLastError() == 183:
        kernel.CloseHandle(handle)
        raise Blocked('Another OpenMRA harness owns this GUI session.')
    try:
        yield
    finally:
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


class Gui:
    """Bounded geometry profile for the verified v0.2.0 desktop layout."""
    def __init__(self, app_path, evidence, log):
        from pywinauto import Application, Desktop
        self.Desktop, self.evidence, self.log = Desktop, evidence, log
        desktop_check()
        self.app = Application(backend='win32').start(
            '"' + str(app_path) + '"', work_dir=str(app_path.parent))
        self.log('app started', pid=self.app.process)
        try:
            self.window = self.app.window(title_re='OpenMRA.*')
            self.window.wait('visible', timeout=30)
            self.window.move_window(x=60, y=60, width=1196, height=859)
            time.sleep(.5)
            if hasattr(ctypes.windll.user32, 'GetDpiForWindow'):
                dpi = ctypes.windll.user32.GetDpiForWindow(self.window.handle)
                if dpi != 96:
                    raise Blocked(f'Profile requires 100% display scale (96 DPI); found {dpi}.')
            self.focus()
            self.click(1127, 75, 'English')
            self.window.wait('visible', timeout=5)
            if 'Mixture Toxicity Prediction' not in self.window.window_text():
                raise Blocked('Unexpected application layout/language. Inspect the app before adapting profile.')
        except Exception:
            # This is the fresh, still-empty instance started above, never a user's session.
            self.app.kill(soft=False)
            raise

    def focus(self, window=None):
        import win32gui
        import win32process
        desktop_check()
        window = window or self.window
        try:
            window.set_focus()
        except Exception as exc:
            raise Blocked('Cannot focus OpenMRA; desktop may be locked or in use.') from exc
        time.sleep(.2)
        active = win32gui.GetForegroundWindow()
        if win32process.GetWindowThreadProcessId(active)[1] != self.window.process_id():
            raise Blocked('Foreground changed outside OpenMRA; no input was sent.')

    def click(self, x, y, action):
        from pywinauto import mouse
        self.focus()
        r = self.window.rectangle()
        if (r.width(), r.height()) != (1196, 859):
            raise Blocked('App geometry changed; refusing to click an unverified location.')
        mouse.click(coords=(r.left + x, r.top + y))
        self.log(action)
        time.sleep(.4)

    def capture(self, name):
        from PIL import ImageGrab
        self.focus()
        r = self.window.rectangle()
        ImageGrab.grab().crop((r.left, r.top, r.right, r.bottom)).save(self.evidence / name)

    def dialog(self, title=None, timeout=10):
        spec = {'process': self.window.process_id(), 'class_name': '#32770'}
        if title:
            spec['title'] = title
        dialog = self.Desktop(backend='win32').window(**spec)
        dialog.wait('visible', timeout=timeout)
        self.focus(dialog)
        return dialog

    def accept(self, dialog):
        import win32gui
        self.focus(dialog)
        old_handle = dialog.handle
        old_title = dialog.window_text()
        self.Desktop(backend='uia').window(handle=old_handle).child_window(
            auto_id='1', control_type='Button').invoke()
        # Windows can reuse the Save As HWND for the Report saved dialog.
        deadline = time.monotonic() + 15
        while (win32gui.IsWindow(old_handle) and win32gui.IsWindowVisible(old_handle)
               and win32gui.GetWindowText(old_handle) == old_title):
            if time.monotonic() > deadline:
                raise Blocked(f'Dialog did not finish: {old_title}')
            time.sleep(.1)

    def run(self, source, destination):
        self.click(755, 234, 'browse input')
        chooser = self.dialog()
        chooser.Edit1.set_edit_text(str(source))
        if chooser.Edit1.window_text() != str(source):
            raise Blocked('File dialog did not accept the exact input path.')
        self.accept(chooser)
        self.click(100, 304, 'load RM input')
        time.sleep(1)
        self.app.wait_cpu_usage_lower(threshold=3, timeout=30, usage_interval=1)
        self.capture('01_loaded.png')
        self.click(995, 449, 'run CA and IA prediction')
        confirmation = self.dialog('Confirm experimental conditions')
        # Reproduction of the user-supplied experiment; no scientific certification.
        self.accept(confirmation)
        time.sleep(2)
        self.app.wait_cpu_usage_lower(threshold=3, timeout=60, usage_interval=1)
        self.capture('02_result.png')
        self.click(1062, 191, 'export Excel report')
        chooser = self.dialog()
        chooser.Edit1.set_edit_text(str(destination))
        if chooser.Edit1.window_text() != str(destination):
            raise Blocked('Save dialog did not accept the exact output path.')
        self.accept(chooser)
        saved = self.dialog('Report saved', timeout=30)
        self.focus(saved)
        self.Desktop(backend='uia').window(handle=saved.handle).children(control_type='Button')[0].invoke()
        saved.wait_not('visible', timeout=10)
        self.log('report saved', path=str(destination))

    def close(self):
        # Close only the fresh app instance created by this operator.
        self.window.close()


def input_files(folder):
    paths = sorted(p.resolve() for p in Path(folder).rglob('*.xlsx') if not p.name.startswith('~$'))
    if not paths:
        raise ValueError('No .xlsx inputs found.')
    if len({p.name.casefold() for p in paths}) != len(paths):
        raise ValueError('Duplicate workbook filenames found; use one input version/directory.')
    return paths


@contextmanager
def record_failure(manifest_path, manifest):
    """Persist failures between jobs as well as failures inside a GUI attempt."""
    try:
        yield
    except Exception as exc:
        manifest.update(status='BLOCKED' if isinstance(exc, Blocked) else 'FAIL', blocker=str(exc))
        write_json(manifest_path, manifest)
        raise


def run(args):
    from report_validation import validate_report
    if sys.version_info < (3, 11):
        raise Blocked('Python 3.11 or later is required.')
    app, output = Path(args.app).resolve(), Path(args.output_dir).resolve()
    sources = input_files(args.input_dir)
    source_root = Path(args.input_dir).resolve()
    if output == source_root or output.is_relative_to(source_root):
        raise ValueError('Output must be outside the input directory.')
    manifest_path = output / 'manifest.json'
    if args.resume:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['app_sha256'] != digest(app):
            raise ValueError('Application changed since previous run; use a new output directory.')
        if manifest['inputs'] != {str(p): digest(p) for p in sources}:
            raise ValueError('Input set/content changed; use a new output directory.')
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError('Output directory is not empty. Use --resume or a new directory.')
        output.mkdir(parents=True, exist_ok=True)
        manifest = {'schema': 1, 'created': stamp(), 'status': 'RUNNING',
                    'app': str(app), 'app_sha256': digest(app),
                    'profile': 'OpenMRA v0.2.0 / RM / English / 96 DPI',
                    'inputs': {str(p): digest(p) for p in sources},
                    'jobs': {p.name: {'status': 'PENDING', 'attempt': 0} for p in sources}}
    for sub in ('inputs', 'exports', '_workspace'):
        (output / sub).mkdir(exist_ok=True)
    write_json(manifest_path, manifest)
    check = preflight(app)
    write_json(output / '_workspace' / 'preflight.json', check)
    if check['status'] != 'READY':
        manifest.update(status='BLOCKED', blocker='; '.join(check['errors']))
        write_json(manifest_path, manifest)
        raise Blocked(manifest['blocker'])
    with record_failure(manifest_path, manifest), gui_lock():
        for source in sources:
            prior = manifest['jobs'].get(source.name, {})
            dest = output / 'exports' / source.name
            # Recover a crash after atomic report publication but before PASS state.
            if prior.get('status') != 'PASS' and dest.exists():
                recovered = validate_report(dest, source)
                if recovered['status'] != 'PASS':
                    raise ValueError(f'Existing unaccepted export failed validation; preserved at {dest}. Use a new run directory after inspection.')
                prior.update(status='PASS', report_sha256=digest(dest), completed=stamp(),
                             warnings=recovered['warnings'], recovered_after_interruption=True)
                manifest['jobs'][source.name] = prior
                write_json(output / '_workspace' / (source.stem + '_recovery_validation.json'), recovered)
                write_json(manifest_path, manifest)
            if prior.get('status') == 'PASS':
                if not dest.exists() or digest(dest) != prior['report_sha256']:
                    raise ValueError(f'Previously accepted report changed: {dest}')
                if validate_report(dest, source)['status'] != 'PASS':
                    raise ValueError(f'Previously accepted report no longer validates: {dest}')
                continue
            snapshot = output / 'inputs' / source.name
            if snapshot.exists() and digest(snapshot) != digest(source):
                raise ValueError('Input snapshot differs from source; use a new run directory.')
            if not snapshot.exists():
                shutil.copy2(source, snapshot)
            prefix = f'{source.stem}_attempt'
            existing_attempts = [int(p.name[len(prefix):])
                                 for p in (output / '_workspace').iterdir()
                                 if p.is_dir() and p.name.startswith(prefix) and p.name[len(prefix):].isdigit()]
            attempt = max([int(prior.get('attempt', 0)), *existing_attempts]) + 1
            if attempt > 2:
                raise Blocked(f'{source.name} already has two attempts. Inspect evidence before a new run.')
            evidence = output / '_workspace' / f'{source.stem}_attempt{attempt}'
            evidence.mkdir()
            def log(action, **fields):
                with (evidence / 'actions.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'time': stamp(), 'action': action, **fields}, ensure_ascii=False) + '\n')
            pending = evidence / source.name
            job = {'status': 'RUNNING', 'attempt': attempt, 'started': stamp(), 'evidence': str(evidence.relative_to(output))}
            manifest['jobs'][source.name] = job
            write_json(manifest_path, manifest)
            gui = None
            try:
                gui = Gui(app, evidence, log)
                gui.run(snapshot, pending)
                checked = validate_report(pending, snapshot)
                write_json(evidence / 'validation.json', checked)
                if checked['status'] != 'PASS':
                    raise ValueError('Export validation failed: ' + '; '.join(checked['errors']))
                if digest(source) != manifest['inputs'][str(source)] or digest(snapshot) != digest(source):
                    raise ValueError('Input changed during execution.')
                if dest.exists():
                    raise ValueError('Unexpected existing destination; refusing to overwrite.')
                staged = evidence / 'accepted_export.tmp'
                shutil.copy2(pending, staged)
                staged.replace(dest)
                job.update(status='PASS', report_sha256=digest(dest), completed=stamp(), warnings=checked['warnings'])
                write_json(manifest_path, manifest)
                log('validation PASS', report_sha256=job['report_sha256'])
                print('PASS ' + source.name, flush=True)
            except Exception as exc:
                if gui is not None:
                    try:
                        gui.capture('03_failure.png')
                    except Exception:
                        pass
                job.update(status='BLOCKED' if isinstance(exc, Blocked) else 'FAIL', error=str(exc))
                manifest.update(status=job['status'], blocker=str(exc))
                log(job['status'], error=str(exc), traceback=traceback.format_exc())
                write_json(manifest_path, manifest)
                raise
            finally:
                if gui is not None:
                    try:
                        gui.close()
                    except Exception:
                        log('app left open', reason='Could not close the owned app; inspect before resume.')
            write_json(manifest_path, manifest)
    manifest.update(status='COMPLETE', completed=stamp())
    manifest.pop('blocker', None)
    write_json(manifest_path, manifest)
    print(f'COMPLETE: {len(sources)} verified reports in {output / "exports"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    pre = sub.add_parser('preflight')
    pre.add_argument('--app', required=True)
    batch = sub.add_parser('run')
    batch.add_argument('--app', required=True)
    batch.add_argument('--input-dir', required=True)
    batch.add_argument('--output-dir', required=True)
    batch.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'preflight':
            result = preflight(args.app)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result['status'] == 'READY' else 2
        run(args)
        return 0
    except Exception as exc:
        print(f'{"BLOCKED" if isinstance(exc, Blocked) else "FAIL"}: {exc}', file=sys.stderr)
        return 2 if isinstance(exc, Blocked) else 1


if __name__ == '__main__':
    sys.exit(main())
