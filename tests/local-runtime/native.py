#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise local dependency replay with real installed, non-store JVMs."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cli', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--manager-jdk', type=Path, required=True)
parser.add_argument('--task-jdk', type=Path, required=True)
parser.add_argument('--via-path', action='store_true')
parser.add_argument('--via-path-shim', action='store_true', help='Select the real runtime through a version-manager-style PATH launcher')
parser.add_argument('--unused-sdk-declaration', action='store_true')
parser.add_argument('--included-build-task-sdk', action='store_true', help='Use the separate SDK for the included build too, allowing a JRE-only manager')
parser.add_argument('--pure-manager-attribute')
parser.add_argument('--pure-task-attribute')
args = parser.parse_args()
assert not args.via_path_shim or args.via_path
assert bool(args.pure_manager_attribute) == bool(args.pure_task_attribute)
work = args.output.resolve()
work.mkdir(parents=True, exist_ok=False)
manager = args.manager_jdk.resolve()
task_sdk = args.task_jdk.resolve()
assert all(not str(home).startswith('/nix/store/') for home in [manager, task_sdk])
spec = importlib.util.spec_from_file_location('local_jvms', ROOT / 'pkgs/build-gradle-project/local-jvms.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
manager_info = observer.observe(manager)
task_info = observer.observe(task_sdk)
assert manager_info['javaMajor'] != task_info['javaMajor'], 'this control needs distinct JVMs'
target = min(manager_info['javaMajor'], task_info['javaMajor'])
assert target >= 8, 'the JUnit 5 fixture itself requires Java 8 or newer'
source = work / 'project'
shutil.copytree(ROOT / 'tests/fixtures/java-library', source)
build = source / 'build.gradle'
build.write_text(build.read_text() + '\njava {\n'
                 '  toolchain { languageVersion = JavaLanguageVersion.of(' + str(task_info['javaMajor']) + ') }\n'
                 '  sourceCompatibility = JavaVersion.toVersion(' + str(target) + ')\n'
                 '  targetCompatibility = JavaVersion.toVersion(' + str(target) + ')\n}\n')
build = source / 'shout/build.gradle'
build.write_text(build.read_text() + '\njava {\n'
                 '  sourceCompatibility = JavaVersion.toVersion(' + str(target) + ')\n'
                 '  targetCompatibility = JavaVersion.toVersion(' + str(target) + ')\n}\n')
(source / 'gradle.properties').write_text(
    'org.gradle.java.installations.fromEnv=PROJECT_TEST_JDK\n'
    'org.gradle.java.installations.auto-detect=false\n'
    'org.gradle.java.installations.auto-download=false\n')
if args.included_build_task_sdk:
    build = source / 'shout/build.gradle'
    build.write_text(build.read_text() + '\njava { toolchain { languageVersion = JavaLanguageVersion.of(' +
                     str(task_info['javaMajor']) + ') } }\n')
    (source / 'shout/gradle.properties').write_bytes((source / 'gradle.properties').read_bytes())
if args.unused_sdk_declaration:
    # A valid declaration shape with a deliberately unavailable attribute:
    # resolving this optional pure-build configuration would fail the control.
    declaration = {'version': 1, 'system': 'x86_64-linux', 'manager': 'future', 'sdks': [{
        'id': 'future', 'selectionKind': 'jvm-sdk-adapter-v1',
        'source': {'type': 'github', 'owner': 'nix-purifiers', 'repo': 'purifier',
                   'rev': 'f444063c705056a605583bd273cc8147f57808ee',
                   'narHash': 'sha256-NaeaVq9B520WgzmRSMLGdN2zqnFlvM/wfZ8hECHhYqY='},
        'attribute': ['unresolved_for_local_adoption']} ]}
    (source / 'gradle-purifier.sdks.json').write_text(json.dumps(declaration, indent=2) + '\n')
lock = source / 'gradle-purifier.lock.json'
inputs = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in source.rglob('*') if p.is_file() and p != lock}
for name in ['tmp', 'cache', 'data', 'gradle-home']:
    (work / name).mkdir()
env = dict(os.environ, JAVA_HOME=str(manager), PROJECT_TEST_JDK=str(task_sdk),
           TMPDIR=str(work / 'tmp'), TMPPREFIX=str(work / 'tmp/prefix-'),
           XDG_CACHE_HOME=str(work / 'cache'), XDG_DATA_HOME=str(work / 'data'),
           GRADLE_USER_HOME=str(work / 'gradle-home'),
           JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2 -Djava.io.tmpdir=' + str(work / 'tmp'),
           GRADLE_OPTS='-XX:ActiveProcessorCount=2 -Dorg.gradle.daemon=false')
if args.via_path:
    env.pop('JAVA_HOME', None)
    launcher = manager / 'bin'
    if args.via_path_shim:
        launcher = work / 'path-shim'
        launcher.mkdir()
        shim = launcher / 'java'
        shim.write_text('#!/bin/sh\nexec ' + shlex.quote(str(manager / 'bin/java')) + ' "$@"\n')
        shim.chmod(0o755)
    env['PATH'] = str(launcher) + os.pathsep + env['PATH']
if hasattr(os, 'sched_getaffinity'):
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
cli = args.cli.resolve()
report = {'scope': 'synthetic local JVM regression; not cohort project completion',
          'status': 'running', 'cli': str(cli), 'cliSha256': observer.digest(cli),
          'manager': manager_info, 'taskSdk': task_info, 'viaPath': args.via_path,
          'viaPathShim': args.via_path_shim,
          'unusedSdkDeclaration': args.unused_sdk_declaration,
          'includedBuildTaskSdk': args.included_build_task_sdk,
          'sourceInputs': inputs, 'commands': [], 'deadlineSecondsPerCommand': 900}


def save():
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


def run(name, command, extra=None, rejection=None):
    command = list(map(str, command))
    started = time.monotonic()
    logfile = work / (name + '.log')
    with logfile.open('w') as log:
        process = subprocess.Popen(command, cwd=source, env=env | (extra or {}),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=900)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise AssertionError(name + ': owned process deadline exceeded')
    report['commands'].append({'name': name, 'argv': command, 'exitCode': code,
                               'elapsedSeconds': round(time.monotonic() - started, 3),
                               'logSha256': observer.digest(logfile)})
    save()
    text = logfile.read_text()
    if rejection:
        assert code != 0 and rejection in text, name
    else:
        assert code == 0, name
    print(name, 'passed', flush=True)
    return text


def attribute(value):
    return '(p.lib.getAttrFromPath [ ' + ' '.join(json.dumps(c) for c in value.split('.')) + ' ] p)'


try:
    save()
    run('capture', [cli, 'lock', '--relock', '--', '--no-daemon', '--max-workers=2'])
    captured = lock.read_bytes()
    assert 'localJvms' not in json.loads(captured), 'host observations must not enter the portable lock'
    diagnostics = source / '.gradle-purifier/native-runtime.properties'
    assert 'java.version=' + manager_info['javaVersion'] in diagnostics.read_text()
    run('prefetch', [cli, 'prefetch'])
    assert not (source / '.gradle-purifier/jdk').exists()
    assert not list((source / '.gradle-purifier').glob('jdk-*'))
    gradle = source / '.gradle-purifier/distribution/bin/gradle'
    run('native-offline', [gradle, '--offline', '--no-daemon', '--max-workers=2', 'clean', 'build'])
    counts = {key: 0 for key in ['tests', 'skipped', 'failures', 'errors']}
    for path in (source / 'build/test-results/test').glob('TEST-*.xml'):
        document = ET.parse(path).getroot()
        for key in counts:
            counts[key] += int(document.get(key, 0))
    assert counts == {'tests': 3, 'skipped': 0, 'failures': 0, 'errors': 0}
    report['nativeTests'] = counts
    run('replacement-manager', [gradle, '--offline', '--no-daemon', '--max-workers=2', 'clean', 'build'], {'JAVA_HOME': str(task_sdk)})
    assert lock.read_bytes() == captured, 'host runtime replacement must not require or mutate a lock'
    assert 'java.version=' + task_info['javaVersion'] in diagnostics.read_text()
    expression = ('let f=builtins.getFlake ' + json.dumps(str(ROOT)) + '; '
                  'p=import f.inputs.nixpkgs {system=builtins.currentSystem;overlays=[f.overlays.default];}; '
                  'in p.gradle-purifier.buildGradleProject {src=' + str(source) + ';')
    default_command = ['nix', 'eval', '--impure', '--raw', '--expr', '(' + expression + '}).jdk.drvPath']
    if args.unused_sdk_declaration:
        run('pure-unbound-declaration', default_command, rejection='SDK declaration is not bound')
    else:
        result = run('pure-packaged-default', default_command)
        assert '/nix/store/' in result and '.drv' in result
    run('pure-host-sdk', ['nix', 'eval', '--impure', '--raw', '--expr', '(' + expression +
                         'jdk=' + json.dumps(str(manager)) + ';}).jdk.drvPath'],
        rejection='sandboxed jdk must carry immutable Nix package context')
    if args.pure_manager_attribute:
        expression += ('jdk=' + attribute(args.pure_manager_attribute) + ';toolchainJdks=[' +
                       attribute(args.pure_task_attribute) + '];')
        run('pure-explicit-sdks', ['nix', 'build', '--impure', '--no-link', '--print-out-paths',
                                   '--max-jobs', '1', '--cores', '2', '--expr', '(' + expression + '}).tests'])
    run('relock', [cli, 'lock', '--relock', '--', '--no-daemon', '--max-workers=2'])
    assert lock.read_bytes() == captured
    assert all(observer.digest(source / name) == digest for name, digest in inputs.items())
    report.update(status='passed', lockSha256=hashlib.sha256(captured).hexdigest(),
                  nativeInputsUnchanged=True, localProjectSdkAcquisition=False)
except BaseException as error:
    report.update(status='failed', error=str(error))
    raise
finally:
    save()
