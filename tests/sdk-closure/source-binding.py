#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prove real declared SDK binding for the existing Gradle 8.14.3 / Java21+8 profile.

The caller supplies the immutable SDK source and its publication status. This
consumer proof does not update a product pin or assert that its source merged.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from fixture_jvm import bind_fixture_sdks
from fixture_build import command as fixture_build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--sdk-receipt', type=Path, required=True)
    parser.add_argument('--sdk-source', type=Path, required=True)
    parser.add_argument('--source-status', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    context = json.loads(args.sdk_receipt.read_text())
    profile, sdks = context['profile'], context['sdks']
    assert (profile['gradle'], profile['managerRuntime'], profile['projectCompiler'], profile['projectRuntime']) == ('8.14.3', 'java21', 'java8', 'java8')
    source = json.loads(args.sdk_source.read_text())
    spec = importlib.util.spec_from_file_location('epoch', ROOT / 'tests/epoch-probes/probe.py')
    epoch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(epoch)
    project = out / 'project'
    epoch.fixture(project, profile, sdks)
    manifest = bind_fixture_sdks(project, 21, [8], args.cli, sdks, source=source)
    originals = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob('*') if p.is_file()}
    home = out / 'home'
    home.mkdir()
    env = dict(os.environ, JAVA_HOME=sdks['java21']['home'], HOME=str(home),
               GRADLE_USER_HOME=str(out / 'gradle-home'), XDG_CACHE_HOME=str(out / 'cache'),
               JAVA_TOOL_OPTIONS='-Duser.home=' + str(home),
               GRADLE_OPTS='-XX:ActiveProcessorCount=2 -Dorg.gradle.workers.max=2')
    result = dict(profile=profile, source=source, sourceStatus=args.source_status,
                  manifest=manifest, cli=str(args.cli.resolve()), stages=[])

    def run(name, command, cwd=project):
        command = list(map(str, command))
        with (out / (name + '.log')).open('w') as log:
            process = subprocess.run(command, cwd=cwd, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=900)
        result['stages'].append(dict(name=name, argv=command, cwd=str(cwd), exit=process.returncode))
        (out / 'proof.json').write_text(json.dumps(result, indent=2) + '\n')
        assert process.returncode == 0, (name, (out / (name + '.log')).read_text()[-5000:])
        print(name, 'PASS', flush=True)

    run('bound-capture', [args.cli, 'lock', '--bind-sdks'])
    lock = project / 'gradle-purifier.lock.json'
    first = lock.read_bytes()
    captured = json.loads(first)['projectJdks']['x86_64-linux']
    assert captured['version'] == 'jvm-sdk-context-v2'
    assert captured['manager']['runtimeHome'] == sdks['java21']['home']
    roles = captured['roles']
    assert {r['task'] for r in roles} == {':compileJava', ':compileTestJava', ':epochTest'}
    assert all(next(s['home'] for s in captured['sdks'] if s['identityHash'] == r['sdk']) == sdks['java8']['home'] for r in roles)
    observations = json.loads((project / '.gradle-purifier/lock-run/output/task-sdk-observations.json').read_text())
    assert observations[':compileJava']['executed'] and observations[':epochTest']['executed']
    assert 'EPOCH TEST PASSED' in (out / 'bound-capture.log').read_text()
    (out / 'task-sdks.json').write_bytes((project / '.gradle-purifier/lock-run/output/task-sdks.json').read_bytes())
    run('bound-relock', [args.cli, 'lock', '--bind-sdks', '--relock'])
    assert lock.read_bytes() == first
    run('prefetch', [args.cli, 'prefetch'])
    run('native-offline', [project / '.gradle-purifier/distribution/bin/gradle', '--offline', '--no-daemon', 'clean', 'check'])
    run('pure-tests', fixture_build(args.cli, project, 'tests'))
    run('publish', [args.cli, 'publish', 'publishProofPublicationToMavenRepository'])
    jars = list((project / 'repository/example/epoch').glob('*/*-nix-*.jar'))
    assert len(jars) == 1
    consumer = out / 'consumer'
    consumer.mkdir()
    (consumer / 'Consumer.java').write_text('public class Consumer {public static void main(String[] a){if(example.Epoch.answer()!=42)throw new AssertionError();}}\n')
    run('consumer-compile', [Path(sdks['java8']['home']) / 'bin/javac', '-cp', jars[0], 'Consumer.java'], consumer)
    run('consumer-java8', [Path(sdks['java8']['home']) / 'bin/java', '-cp', str(consumer) + os.pathsep + str(jars[0]), 'Consumer'], consumer)
    assert all((project / name).read_bytes() == data for name, data in originals.items())
    assert lock.read_bytes() == first
    result.update(passed=True, sourcePreserved=True, boundRelockByteEqual=True,
                  lockSHA256=hashlib.sha256(first).hexdigest(), qualification='Real immutable GitHub source binding; publication status supplied above. No product pin or merge claim.')
    (out / 'proof.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
