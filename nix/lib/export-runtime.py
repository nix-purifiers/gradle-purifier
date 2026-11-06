# SPDX-License-Identifier: MIT
"""Lay out Gradle's evaluated runtime graph for the shared JVM projector."""
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import unquote
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('jvm_repository', sys.argv[1])
jvm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jvm)
description, downloads, runtime, local, catalog = map(Path, sys.argv[2:7])
data = json.loads(description.read_text())
provided = jvm.read_catalogs([{'catalog': str(catalog)}])
runtime.mkdir(parents=True, exist_ok=True)
local.mkdir(parents=True, exist_ok=True)
modules = []
retained_poms = set()


def directory(root, module):
    jvm.validate_coordinates(module['group'], module['name'], module['version'])
    return root / module['group'].replace('.', '/') / module['name'] / module['version']


for pom in data['poms']:
    coordinate = (pom['group'], pom['name'])
    if coordinate not in provided:
        target = directory(runtime, pom) / (pom['name'] + '-' + pom['version'] + '.pom')
        jvm.write(target, Path(pom['file']).read_bytes())
        retained_poms.add((pom['group'], pom['name'], pom['version']))
for module in data['modules']:
    coordinate = (module['group'], module['name'])
    if coordinate in provided:
        modules.append({key: module[key] for key in ('group', 'name', 'version')})
        continue
    target_root = local if module['project'] else runtime
    target = directory(target_root, module)
    for source in map(Path, module['files']):
        prefix = module['name'] + '-' + module['version']
        name = source.name if source.name.startswith(prefix + '.') or source.name.startswith(prefix + '-') else prefix + source.suffix
        jvm.write(target / name, source.read_bytes())
    if module['project']:
        pom = ET.Element('{' + jvm.POM_NS + '}project')
        for key, value in [('modelVersion', '4.0.0'), ('groupId', module['group']),
                           ('artifactId', module['name']), ('version', module['version'])]:
            jvm.put(pom, key, value)
        dependencies = ET.SubElement(pom, '{' + jvm.POM_NS + '}dependencies')
        for dependency in sorted(module['dependencies'], key=lambda item: (item['group'], item['name'], item['version'])):
            node = ET.SubElement(dependencies, '{' + jvm.POM_NS + '}dependency')
            for key, value in [('groupId', dependency['group']), ('artifactId', dependency['name']),
                               ('version', dependency['version'])]:
                jvm.put(node, key, value)
        jvm.write(target / (module['name'] + '-' + module['version'] + '.pom'),
                  ET.tostring(pom, encoding='utf-8', xml_declaration=True))
    else:
        pom = target / (module['name'] + '-' + module['version'] + '.pom')
        if not pom.is_file():
            raise ValueError('Gradle runtime component needs a Maven metadata projection: '
                             + ':'.join(module[key] for key in ('group', 'name', 'version')))
        modules.append({key: module[key] for key in ('group', 'name', 'version')})

# Preserve richer Gradle metadata for runtime components and queried POMs.
# Parents and imported BOMs belong to the metadata closure without becoming
# runtime components or adding their constraints' artifacts to that closure.
selected = {(module['group'], module['name'], module['version']) for module in modules}
for path in downloads.rglob('*.module'):
    document = json.loads(path.read_text())
    component = document.get('component', {})
    # Kotlin platform metadata may identify its common publishing component.
    # The downloaded Maven path identifies which selected module owns the file.
    coordinate = (component.get('group'), unquote(path.parent.parent.name), unquote(path.parent.name))
    if (coordinate in selected | retained_poms and coordinate[:2] not in provided
            and unquote(path.name) == str(coordinate[1])+'-'+str(coordinate[2])+'.module'):
        if coordinate not in selected:
            # The existing selected-component path also supports Kotlin's
            # common publishing component. Newly retained metadata-only POMs
            # require their own exact Maven component and path instead.
            suffix = '/'+coordinate[0].replace('.', '/')+'/'+coordinate[1]+'/'+coordinate[2]+'/'+coordinate[1]+'-'+coordinate[2]+'.module'
            if (component.get('module') != coordinate[1] or component.get('version') != coordinate[2]
                    or not unquote(path.as_posix()).endswith(suffix)):
                raise ValueError('Gradle module metadata differs from retained POM coordinates: '+str(coordinate))
        module = dict(zip(('group', 'name', 'version'), coordinate))
        jvm.write(directory(runtime, module) / (module['name'] + '-' + module['version'] + '.module'), path.read_bytes())
jvm.write(runtime / 'purifier-runtime-modules.json',
          (json.dumps({'modules': sorted(modules, key=lambda item: (item['group'], item['name'], item['version']))}, sort_keys=True) + '\n').encode())
