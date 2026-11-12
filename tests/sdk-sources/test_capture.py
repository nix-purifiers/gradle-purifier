# SPDX-License-Identifier: MIT
"""Binding boundaries after immutable pure source reconstruction."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('sdk_capture', Path(__file__).resolve().parents[2]/'pkgs/build-gradle-project/capture-project-jdks.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
HOME = '/nix/store/'+'0'*32+'-sdk/home'
OTHER = '/nix/store/'+'1'*32+'-sdk/home'
IDENTITY = 'a'*64


class SourceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = dict(version='jvm-sdk-context-v2', declarationHash='b'*64,
            manager=dict(version='jvm-manager-executable-runtime-home-v1', sdk=IDENTITY,
                         executable=HOME+'/bin/java', runtimeHome=HOME, identityHash='c'*64),
            sdks=[dict(system='x86_64-linux', home=HOME, identityHash=IDENTITY)])
        self.receipt = dict(managerHome=HOME,
            manager=dict(executable=HOME+'/bin/java', runtimeHome=HOME), tasks=[])

    def row(self, role='compiler'):
        binary = dict(compiler='javac', launcher='java', javadoc='javadoc')[role]
        return dict(task=':proof', role=role, home=HOME, canonicalHome=HOME,
                    executable=HOME+'/bin/'+binary, canonicalExecutable=HOME+'/bin/'+binary)

    def test_no_tasks_retains_manager_and_context(self):
        value = capture.capture_sources(self.receipt, self.manifest)['x86_64-linux']
        self.assertEqual(value['manager'], self.manifest['manager'])
        self.assertEqual(value['version'], 'jvm-sdk-context-v2')
        self.assertEqual(value['roles'], [])

    def test_manager_only_task_still_retains_typed_context_without_release_file(self):
        self.receipt['tasks'] = [self.row()]
        value = capture.capture_sources(self.receipt, self.manifest)['x86_64-linux']
        self.assertEqual(value['roles'][0]['sdk'], IDENTITY)
        self.assertEqual(value['manager'], self.manifest['manager'])

    def test_actual_manager_mismatch_rejects(self):
        for field in ('executable', 'runtimeHome'):
            with self.subTest(field=field):
                receipt = copy.deepcopy(self.receipt)
                receipt['manager'][field] = receipt['manager'][field].replace(HOME, OTHER)
                with self.assertRaisesRegex(ValueError, 'actual Gradle manager'):
                    capture.capture_sources(receipt, self.manifest)

    def test_unbound_home_alias_wrong_binary_and_provenance_reject(self):
        for mutation in ('unbound', 'alias', 'binary', 'provenance', 'native-compiler'):
            with self.subTest(mutation=mutation):
                row = self.row()
                if mutation == 'unbound': row.update(home=OTHER, canonicalHome=OTHER)
                if mutation == 'alias': row['canonicalHome'] = OTHER
                if mutation == 'binary': row.update(executable=HOME+'/bin/java', canonicalExecutable=HOME+'/bin/java')
                if mutation == 'provenance': row['selectionProvenance'] = 'invented'
                if mutation == 'native-compiler': row['selectionProvenance'] = 'native-effective-fork-options'
                self.receipt['tasks'] = [row]
                with self.assertRaises(ValueError): capture.capture_sources(self.receipt, self.manifest)

    def test_duplicate_task_roles_reject(self):
        self.receipt['tasks'] = [self.row(), self.row()]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            capture.capture_sources(self.receipt, self.manifest)

    def test_legacy_manager_only_contract_is_unchanged(self):
        self.assertEqual(capture.capture(dict(managerHome=HOME, tasks=[]), None, None), {})
        self.assertEqual(capture.capture(dict(managerHome=HOME, tasks=[self.row()]), None, None), {})


if __name__ == '__main__': unittest.main()
