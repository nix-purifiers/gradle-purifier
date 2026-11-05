#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native help snapshots and bounded probe failures must preserve Gradle controls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'tests/gradle-flags-help'
spec = importlib.util.spec_from_file_location('gradle_flags', ROOT / 'nix/lib/gradle-flags.py')
flags = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flags)


class GradleFlagsTest(unittest.TestCase):
    def test_actual_native_help(self):
        provenance = json.loads((FIXTURES / 'provenance.json').read_text())
        for row in provenance['observations']:
            path = FIXTURES / (row['profile'] + '.txt')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['helpSha256'])
            for mode in ['capture', 'sandbox', 'publish']:
                self.assertEqual(flags.select(mode, path.read_text()), row['flags'][mode])
        legacy = (FIXTURES / 'gradle-2.14.1.txt').read_text()
        self.assertEqual(flags.select('capture', legacy), ['--no-daemon', '--console=plain'])
        modern = (FIXTURES / 'gradle-8.14.3.txt').read_text()
        self.assertEqual(flags.select('sandbox', modern), ['--no-daemon', '--no-configuration-cache',
            '--no-build-cache', '--no-watch-fs', '--console=plain'])
        self.assertEqual(flags.select('publish', modern), ['--no-daemon', '--no-configuration-cache', '--no-build-cache'])

    def test_categorized_help_keeps_strict_completeness(self):
        text = (FIXTURES / 'gradle-9.9.0-rc-1-rejected-v3.txt').read_text()
        self.assertEqual(text, (FIXTURES / 'gradle-9.9.0-rc-1.txt').read_text())
        self.assertIn('--no-configuration-cache', flags.select('capture', text))
        for broken in [text[:text.index('Develocity:')], text.replace('Develocity:', 'Unknown section:'),
                       text[:text.index('  --no-scan')], text + text]:
            with self.assertRaises(ValueError):
                flags.select('capture', broken)

    def test_failed_or_incomplete_help_never_selects_partial_controls(self):
        text = (FIXTURES / 'gradle-8.14.3.txt').read_text()
        for broken in ['', text.rstrip('\n'), text[:text.index('--exclude-task')], text + text, 'x' * 262145]:
            with self.assertRaises(ValueError):
                flags.select('sandbox', broken)
        # An advertised cache with no supported disable operation must fail closed.
        positive_only = '\n'.join(line for line in text.splitlines() if '--no-configuration-cache' not in line) + '\n'
        positive_only = positive_only.replace('--[no-]configuration-cache', '--configuration-cache')
        with self.assertRaisesRegex(ValueError, 'without a supported disable'):
            flags.select('capture', positive_only)

    def test_user_arguments_cannot_reenable_controls(self):
        for argument in ['--daemon', '--foreground', '--configuration-cache', '--build-cache', '--watch-fs',
                         '--isolated-projects', '--', '-Dorg.gradle.configuration-cache=true',
                         '-Dorg.gradle.unsafe.configuration-cache=true', '-Porg.gradle.caching=true',
                         '-Dorg.gradle.vfs.watch=true', '-Dorg.gradle.daemon=true',
                         '-Dorg.gradle.unsafe.isolated-projects=true']:
            with self.subTest(argument=argument), self.assertRaises(ValueError):
                flags.validate_arguments(['check', argument])
        for arguments in [['-D', 'org.gradle.caching=true'], ['-P', 'org.gradle.daemon=true'],
                          ['--system-prop', 'org.gradle.configuration-cache=true'],
                          ['--project-prop=org.gradle.unsafe.isolated-projects=true']]:
            with self.assertRaises(ValueError):
                flags.validate_arguments(arguments)
        flags.validate_arguments(['--system-prop', 'org.gradle.configuration-cache=false'])
        flags.validate_arguments(['check', '-Dorg.gradle.configuration-cache=false', '--no-build-cache'])
        for arguments in [None, {}, [3]]:
            with self.assertRaises(ValueError):
                flags.validate_arguments(arguments)

    def executable(self, directory, body):
        path = directory / 'native-gradle'
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)
        return path

    def test_nonzero_probe_rejects_even_complete_help(self):
        text = (FIXTURES / 'gradle-8.14.3.txt').read_text()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            native = self.executable(directory, 'import sys\nsys.stdout.write(' + repr(text) + ')\nsys.exit(7)\n')
            with self.assertRaisesRegex(ValueError, 'exit status 7'):
                flags.probe(native, directory / 'help.txt', 2)

    def test_deadline_rejects_complete_help_and_reaps_owned_group(self):
        text = (FIXTURES / 'gradle-8.14.3.txt').read_text()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            pid_file = directory / 'pids.json'
            body = '''import json,os,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
def stop(signum,frame):
    child.wait(timeout=2)
    sys.exit(0)
signal.signal(signal.SIGTERM,stop)
with open(PID_FILE,'w') as target:json.dump([os.getpid(),child.pid],target)
sys.stdout.write(HELP_TEXT);sys.stdout.flush()
time.sleep(60)
'''.replace('PID_FILE', repr(str(pid_file))).replace('HELP_TEXT', repr(text))
            native = self.executable(directory, body)
            with self.assertRaisesRegex(ValueError, 'bounded probe deadline'):
                flags.probe(native, directory / 'help.txt', 1)
            for pid in json.loads(pid_file.read_text()):
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)


    def test_output_limit_is_enforced_while_draining(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            native = self.executable(directory, "import os\nwhile True:os.write(1,b'x'*65536)\n")
            start = time.monotonic()
            with self.assertRaisesRegex(ValueError, 'output limit'):
                flags.probe(native, directory / 'help.txt', 10)
            self.assertLess(time.monotonic() - start, 3)
            retained = (directory / 'help.txt').stat().st_size + (directory / 'help.txt.stderr').stat().st_size
            self.assertLessEqual(retained, 262144)
            report = json.loads((directory / 'help.txt.probe.json').read_text())
            self.assertTrue(report['ownedParentReaped'])
            with self.assertRaises(ProcessLookupError):
                os.kill(report['pid'], 0)

    def test_external_pipe_writer_cannot_hold_probe_open(self):
        # Model an escaped descriptor holder with a separate test-owned group.
        # The controller must return on deadline without killing that group.
        text = (FIXTURES / 'gradle-8.14.3.txt').read_text()
        real_popen = subprocess.Popen
        processes = {}
        watchdog = None
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def cleanup_keeper():
                keeper = processes['keeper']
                if keeper.poll() is None:
                    keeper.terminate()
                try:
                    keeper.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    keeper.kill()
                    keeper.wait(timeout=1)

            def launch(*args, **kwargs):
                nonlocal watchdog
                out_read, out_write = os.pipe()
                err_read, err_write = os.pipe()
                processes['keeper'] = real_popen([sys.executable, '-c', 'import time;time.sleep(60)'],
                    stdin=subprocess.DEVNULL, stdout=out_write, stderr=err_write, start_new_session=True)
                parent = real_popen([sys.executable, '-c', 'import sys;sys.stdout.write(' + repr(text) + ')'],
                    stdin=subprocess.DEVNULL, stdout=out_write, stderr=err_write, start_new_session=True)
                os.close(out_write)
                os.close(err_write)
                parent.stdout = os.fdopen(out_read, 'rb', buffering=0)
                parent.stderr = os.fdopen(err_read, 'rb', buffering=0)
                processes['parent'] = parent
                # Bound the test itself even if a future controller regresses.
                watchdog = threading.Timer(4, cleanup_keeper)
                watchdog.daemon = True
                watchdog.start()
                return parent

            try:
                start = time.monotonic()
                with mock.patch.object(flags.subprocess, 'Popen', side_effect=launch):
                    with self.assertRaisesRegex(ValueError, 'bounded probe deadline'):
                        flags.probe(directory / 'native-gradle', directory / 'help.txt', 1)
                self.assertLess(time.monotonic() - start, 3.5)
                self.assertIsNone(processes['keeper'].poll())
                self.assertIsNotNone(processes['parent'].returncode)
                report = json.loads((directory / 'help.txt.probe.json').read_text())
                self.assertFalse(report['stdoutEOF'])
                self.assertTrue(report['ownedParentReaped'])
            finally:
                if watchdog:
                    watchdog.cancel()
                if 'keeper' in processes:
                    cleanup_keeper()


if __name__ == '__main__':

    unittest.main()
