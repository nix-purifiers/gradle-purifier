#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile an exact upstream worker source with one transport-property change."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

CLASS_NAME = 'org.gradle.workers.internal.DefaultProcessWorkerSpec'
CLASS_ENTRY = CLASS_NAME.replace('.', '/') + '.class'
SOURCE_ANCHOR = '        this.forkOptions.setEnvironment(sanitizeEnvironment(forkOptions));'
SOURCE_ADDITION = '''
        // Explicit sandbox transport choice; retain environment sanitization.
        if (Boolean.getBoolean("java.net.preferIPv4Stack")) {
            this.forkOptions.systemProperty("java.net.preferIPv4Stack", "true");
        }'''
VARIANT = 'purifier-darwin-worker-ipv4-source-component-v1'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def prepare_source(upstream_source, point, workspace):
    raw = upstream_source.read_bytes()
    require(sha256(raw) == point['sourceSha256'], 'upstream worker source hash mismatch')
    original = raw.decode('utf-8')
    require(original.count(SOURCE_ANCHOR) == 1, 'worker constructor source anchor mismatch')
    patched = original.replace(SOURCE_ANCHOR, SOURCE_ANCHOR + SOURCE_ADDITION)
    patch = ''.join(difflib.unified_diff(
        original.splitlines(True), patched.splitlines(True),
        fromfile='a/DefaultProcessWorkerSpec.java', tofile='b/DefaultProcessWorkerSpec.java'))
    source = workspace / 'source' / (CLASS_NAME.replace('.', '/') + '.java')
    source.parent.mkdir(parents=True)
    source.write_text(patched, encoding='utf-8')
    return source, raw, patch


def validate_component(upstream, point):
    component = upstream / 'lib/plugins' / f"gradle-workers-{point['version']}.jar"
    require(sha256(component.read_bytes()) == point['componentJarSha256'],
            'upstream worker component hash mismatch')
    with zipfile.ZipFile(component) as jar:
        require(sha256(jar.read(CLASS_ENTRY)) == point['originalClassSha256'],
                'upstream worker class hash mismatch')
    return component


def compile_source(source, upstream, point, workspace):
    classes = workspace / 'classes'
    classes.mkdir()
    javac = os.environ['GRADLE_TRANSPORT_JAVAC']
    classpath = str(upstream / 'lib/*') + os.pathsep + str(upstream / 'lib/plugins/*')
    command = [javac, '--release', str(point['javacRelease']), '-proc:none', '-implicit:none',
               '-encoding', 'UTF-8', '-g', '-classpath', classpath,
               '-d', str(classes.relative_to(workspace)), str(source.relative_to(workspace))]
    subprocess.run(command, check=True)
    entries = sorted(str(path.relative_to(classes)) for path in classes.rglob('*.class'))
    require(entries == [CLASS_ENTRY], 'compiler produced unexpected classes')
    compiled = (classes / CLASS_ENTRY).read_bytes()
    require(struct.unpack('>H', compiled[6:8])[0] == point['originalClassMajor'],
            'compiled worker class target mismatch')
    return compiled, command


def assemble_component(original, compiled, output):
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(output, 'w') as target:
        target.comment = source.comment
        for entry in source.infolist():
            data = compiled if entry.filename == CLASS_ENTRY else source.read(entry.filename)
            target.writestr(entry, data)


def verify_component(original, rebuilt, repeated):
    require(rebuilt.read_bytes() == repeated.read_bytes(), 'component assembly is not deterministic')
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(rebuilt) as target:
        require(source.namelist() == target.namelist(), 'component entry inventory changed')
        changed = [name for name in source.namelist() if source.read(name) != target.read(name)]
    require(changed == [CLASS_ENTRY], 'unexpected component entry data changed')
    javap = os.environ['GRADLE_TRANSPORT_JAVAP']

    def class_api(component):
        return subprocess.check_output(
            [javap, '-p', '-s', '-classpath', str(component), CLASS_NAME], text=True)

    api = class_api(original)
    require(api == class_api(rebuilt), 'compiled worker class API changed')
    return api, changed


def install_distribution(upstream, component, rebuilt, output):
    # Copy every jar: symlinks make Gradle rediscover the official installation.
    shutil.copytree(upstream, output, copy_function=shutil.copyfile)
    output.chmod(0o755)
    target = output / 'lib/plugins' / component.name
    target.parent.chmod(0o755)
    target.unlink()
    shutil.copyfile(rebuilt, target)
    for original in (upstream / 'bin').iterdir():
        if original.is_file():
            (output / 'bin' / original.name).chmod(original.stat().st_mode)


def main():
    upstream, upstream_source, descriptor, output = map(Path, sys.argv[1:])
    point = json.loads(descriptor.read_text())
    workspace = Path.cwd()
    component = validate_component(upstream, point)
    source, raw_source, patch = prepare_source(upstream_source, point, workspace)
    compiled, command = compile_source(source, upstream, point, workspace)
    rebuilt, repeated = workspace / 'component.jar', workspace / 'component-repeat.jar'
    assemble_component(component, compiled, rebuilt)
    assemble_component(component, compiled, repeated)
    api, changed = verify_component(component, rebuilt, repeated)
    install_distribution(upstream, component, rebuilt, output)
    provenance = output / 'nix-support/purifier-worker-transport'
    provenance.mkdir(parents=True)
    (provenance / 'upstream.java').write_bytes(raw_source)
    (provenance / 'transport.patch').write_text(patch)
    (provenance / 'class-api.txt').write_text(api)
    manifest = {
        'variant': VARIANT, 'version': point['version'],
        'upstreamDistribution': str(upstream),
        'upstreamDistributionUrl': point['distributionUrl'],
        'upstreamDistributionHash': point['distributionHash'],
        'upstreamComponentSha256': point['componentJarSha256'],
        'upstreamClassSha256': point['originalClassSha256'],
        'sourceCommit': point['sourceCommit'], 'sourceUrl': point['immutableSourceUrl'],
        'sourceSha256': sha256(raw_source), 'sourceSRI': point['sourceSRI'],
        'sourcePath': str(upstream_source),
        'patchSha256': sha256(patch.encode()), 'javac': command[0],
        'javacVersion': subprocess.check_output([command[0], '-version'], text=True).strip(),
        'compilerRoot': os.environ['GRADLE_TRANSPORT_COMPILER_ROOT'],
        'javacRelease': point['javacRelease'], 'compileArgv': command,
        'compiledClassMajor': point['originalClassMajor'],
        'compiledClassSha256': sha256(compiled), 'componentSha256': sha256(rebuilt.read_bytes()),
        'entryDataDelta': changed, 'inventoryEqual': True, 'apiEqual': True,
        'twoAssembliesIdentical': True,
        'scope': 'Derived Darwin Nix build SDK; not the unchanged official Gradle distribution',
    }
    (provenance / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
