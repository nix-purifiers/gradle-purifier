# SPDX-License-Identifier: MIT
"""Exercise retained POM metadata with the real shared JVM repository tool."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

tool = sys.argv.pop(1)
script = Path(__file__).resolve().parent.parent / 'nix/lib/export-runtime.py'


class RuntimeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.downloads = self.root / 'downloads'
        self.downloads.mkdir()
        self.coordinate = dict(group='example', name='bom', version='1')
        self.pom = self.root / 'bom.pom'
        self.pom.write_text('<project><!-- do_not_remove: published-with-gradle-metadata -->'
                            '<groupId>example</groupId><artifactId>bom</artifactId>'
                            '<version>1</version><packaging>pom</packaging></project>\n')
        self.data = dict(poms=[dict(self.coordinate, file=str(self.pom))], modules=[])
        self.catalog = dict(version=1, modules=[])

    def metadata(self, repository='repo', component=None, name='bom', version='1', extra=None):
        path = self.downloads / repository / 'example' / name / version / (name+'-'+version+'.module')
        path.parent.mkdir(parents=True, exist_ok=True)
        document = dict(formatVersion='1.1', component=component or
                        dict(group='example', module=name, version=version), variants=[])
        document.update(extra or {})
        path.write_text(json.dumps(document, indent=2)+'\n')
        return path

    def run_export(self):
        description = self.root / 'description.json'
        catalog = self.root / 'catalog.json'
        description.write_text(json.dumps(self.data))
        catalog.write_text(json.dumps(self.catalog))
        return subprocess.run([sys.executable, str(script), tool, str(description),
                               str(self.downloads), str(self.root/'runtime'),
                               str(self.root/'local'), str(catalog)], text=True, capture_output=True)

    def test_bom_sidecar_preserves_bytes_without_runtime_selection(self):
        source = self.metadata(extra=dict(variants=[dict(name='runtimeElements',
                               attributes={'org.gradle.category': 'platform'},
                               dependencyConstraints=[dict(group='elsewhere', module='unselected',
                                                           version=dict(requires='99'))])]))
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = self.root / 'runtime'
        self.assertEqual((runtime/'example/bom/1/bom-1.module').read_bytes(), source.read_bytes())
        self.assertEqual((runtime/'example/bom/1/bom-1.pom').read_bytes(), self.pom.read_bytes())
        self.assertEqual(json.loads((runtime/'purifier-runtime-modules.json').read_text()), dict(modules=[]))
        self.assertFalse(list(runtime.rglob('*.jar')))
        self.assertFalse((runtime/'elsewhere').exists())

    def test_unqueried_metadata_is_not_exported(self):
        self.metadata(name='unselected')
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list((self.root/'runtime').rglob('*.module')))

    def test_wrong_component_module_or_version_is_rejected(self):
        for field, wrong in [('module', 'other'), ('version', '2')]:
            with self.subTest(field=field):
                component = dict(group='example', module='bom', version='1')
                component[field] = wrong
                self.metadata(component=component)
                result = self.run_export()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('differs from retained POM coordinates', result.stderr)

    def test_wrong_group_is_not_exported(self):
        self.metadata(component=dict(group='other', module='bom', version='1'))
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list((self.root/'runtime').rglob('*.module')))

    def test_wrong_filename_is_not_exported(self):
        source = self.metadata()
        source.rename(source.with_name('other.module'))
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list((self.root/'runtime').rglob('*.module')))

    def test_selected_common_component_behavior_is_unchanged(self):
        source = self.metadata(component=dict(group='example', module='bom-common', version='1'))
        self.data['modules'] = [dict(self.coordinate, project=False, files=[], dependencies=[])]
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root/'runtime/example/bom/1/bom-1.module').read_bytes(), source.read_bytes())
        self.assertEqual(json.loads((self.root/'runtime/purifier-runtime-modules.json').read_text()),
                         dict(modules=[self.coordinate]))

    def test_wrong_repository_group_path_is_rejected(self):
        source = self.metadata()
        target = self.downloads/'repo/other/bom/1/bom-1.module'
        target.parent.mkdir(parents=True)
        source.rename(target)
        result = self.run_export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('differs from retained POM coordinates', result.stderr)

    def test_provided_coordinate_remains_authoritative(self):
        self.metadata()
        self.catalog['modules'] = [dict(self.coordinate, originalVersion='old', producer='provided')]
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root/'runtime/example').exists())

    def test_duplicate_conflicting_metadata_is_rejected(self):
        self.metadata(repository='a')
        self.metadata(repository='b', extra=dict(createdBy=dict(other=True)))
        result = self.run_export()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('conflicting artifact bytes', result.stderr)

    def test_identical_duplicate_metadata_is_accepted(self):
        self.metadata(repository='a')
        self.metadata(repository='b')
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
