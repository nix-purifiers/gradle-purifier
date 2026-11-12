#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Resolve settings plugins two included builds deep before the outer root exists."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    work = args.results.resolve()
    work.mkdir(parents=True, exist_ok=False)
    source = work / 'project'
    fixture = root / 'tests/nested-settings'
    shutil.copytree(fixture, source)
    wrapper = source / 'gradle/wrapper'
    wrapper.mkdir(parents=True)
    (wrapper / 'gradle-wrapper.properties').write_text(
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-9.5.1-bin.zip\n')
    for name in ['cache', 'gradle-home', 'ordinary-gradle-home']:
        (work / name).mkdir()
    env = dict(os.environ, XDG_CACHE_HOME=str(work / 'cache'),
               GRADLE_USER_HOME=str(work / 'gradle-home'))
    report = {'schema': 1, 'status': 'running', 'commands': [],
              'cli': str(args.cli), 'cliSha256': hashlib.sha256(args.cli.read_bytes()).hexdigest(),
              'fixtureHashes': {str(path.relative_to(fixture)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in fixture.rglob('*') if path.is_file()}}

    def save():
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(name, command, extra=None, cwd=source):
        command = list(map(str, command))
        with (work / (name + '.log')).open('w') as log:
            process = subprocess.run(command, cwd=cwd, env=env | (extra or {}),
                                     stdout=log, stderr=subprocess.STDOUT, timeout=900)
        raw = (work / (name + '.log')).read_bytes()
        report['commands'].append({'name': name, 'argv': command, 'cwd': str(cwd),
                                   'exitCode': process.returncode,
                                   'logSha256': hashlib.sha256(raw).hexdigest()})
        save()
        if process.returncode:
            raise AssertionError(f'{name}: see {work / (name + ".log")}')
        print(name, 'pass', flush=True)
        return raw.decode()

    try:
        save()
        run('capture', [args.cli, 'lock', '--java', '25'])
        lock = source / 'gradle-purifier.lock.json'
        captured = lock.read_bytes()
        run('prefetch', [args.cli, 'prefetch'])
        gradle = source / '.gradle-purifier/distribution/bin/gradle'
        jdk = source / '.gradle-purifier/jdk'
        native = {'JAVA_HOME': str(jdk)}
        run('ordinary-settings-plugins', [gradle, '--offline', '--no-daemon', 'help'],
            native | {'GRADLE_USER_HOME': str(work / 'ordinary-gradle-home')})
        run('interactive-settings-plugins', [gradle, '--offline', '--no-daemon',
            '--project-dir', source, 'clean', 'build'], native, cwd=work)
        native_jar = source / 'build/libs/nested-settings-1.0.jar'
        assert run('native-program', [jdk / 'bin/java', '-cp', native_jar, 'fixture.Library']).strip() == 'nested-settings'
        expression = ('let f=builtins.getFlake ' + json.dumps(str(root)) +
                      '; p=import f.inputs.nixpkgs {system=builtins.currentSystem;overlays=[f.overlays.default];}; '
                      'in p.gradle-purifier.buildGradleProject {src=' + str(source) + ';}')
        release = Path(run('sandbox-release', ['nix', 'build', '--impure', '--no-link',
            '--print-out-paths', '--expr', '(' + expression + ').release']).strip().splitlines()[-1])
        jar = release / 'nested-settings/fixture/nested-settings/1.0/nested-settings-1.0.jar'
        assert run('sandbox-program', [jdk / 'bin/java', '-cp', jar, 'fixture.Library']).strip() == 'nested-settings'
        run('relock', [args.cli, 'lock', '--relock', '--java', '25'])
        assert captured == lock.read_bytes(), 'nested plugin replay changed the lock'
        report.update(status='passed', release=str(release), lockSha256=hashlib.sha256(captured).hexdigest())
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
