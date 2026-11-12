#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Capture/build without release keys, then sign the exact Nix publication."""
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
    parser.add_argument('--gpg', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    work = args.results.resolve()
    work.mkdir(parents=True, exist_ok=False)
    source = work / 'project'
    shutil.copytree(root / 'tests/signing', source)
    wrapper = source / 'gradle/wrapper'
    wrapper.mkdir(parents=True)
    (wrapper / 'gradle-wrapper.properties').write_text(
        'distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10-bin.zip\n')
    for name in ['cache', 'gradle-home', 'gnupg']:
        (work / name).mkdir(mode=0o700)
    env = dict(os.environ, XDG_CACHE_HOME=str(work / 'cache'),
               GRADLE_USER_HOME=str(work / 'gradle-home'), GNUPGHOME=str(work / 'gnupg'))
    env.pop('TEST_SIGNING_KEY_FILE', None)
    report = {'schema': 1, 'status': 'running', 'commands': [],
              'cli': str(args.cli), 'source': str(root)}

    def save():
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(name, command, expected=True, extra=None, contains=None):
        command = list(map(str, command))
        log = work / (name + '.log')
        with log.open('w') as output:
            process = subprocess.run(command, cwd=source, env=env | (extra or {}),
                                     stdout=output, stderr=subprocess.STDOUT, timeout=1200)
        raw = log.read_bytes()
        report['commands'].append({'name': name, 'argv': command,
                                   'exitCode': process.returncode,
                                   'logSha256': hashlib.sha256(raw).hexdigest()})
        save()
        if (process.returncode == 0) != expected or (contains and contains.encode() not in raw):
            raise AssertionError(f'{name}: unexpected result; see {log}')
        print(name, 'pass', flush=True)
        return raw.decode()

    try:
        run('lock-without-key', [args.cli, 'lock', '--java', '17'])
        lock = source / 'gradle-purifier.lock.json'
        report['lockSha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
        run('prefetch', [args.cli, 'prefetch'])
        gradle = source / '.gradle-purifier/distribution/bin/gradle'
        run('interactive-signing-still-required', [gradle, '--no-daemon', '--offline',
            'publishAllPublicationsToReleaseRepository'], expected=False, contains='no configured signatory')
        expression = ('let f=builtins.getFlake ' + json.dumps(str(root)) +
                      '; p=import f.inputs.nixpkgs {system=builtins.currentSystem;overlays=[f.overlays.default];}; '
                      'in p.gradle-purifier.buildGradleProject {src=' + str(source) + ';}')
        (work / 'project.nix').write_text(expression + '\n')
        release = Path(run('sandbox-release-without-key', ['nix', 'build', '--impure', '--no-link',
            '--print-out-paths', '--expr', '(' + expression + ').release']).strip().splitlines()[-1])
        release_files = [Path(parent) / name for parent, _, names in
                         os.walk(release, followlinks=True) for name in names]
        assert not any(path.suffix == '.asc' for path in release_files), 'release unexpectedly contains private-key signatures'
        jars = [path for path in release_files if path.name == 'signed-library-1.0.jar']
        assert jars, 'no compiled library in release'
        run('publish-signing-still-required', [args.cli, 'publish', '--no-daemon',
            'publishAllPublicationsToReleaseRepository'], expected=False, contains='no configured signatory')
        gpg = args.gpg
        run('generate-test-key', [gpg, '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
            '--quick-generate-key', 'Purifier Test <test@example.invalid>', 'rsa2048', 'sign', '0'])
        key = work / 'test-key.asc'
        run('export-test-key', [gpg, '--batch', '--armor', '--output', key, '--export-secret-keys'])
        # A poisoned compiler verifies that CLI publication reuses the Nix
        # artifacts rather than silently rebuilding in the native working tree.
        init = work / 'reject-compile.init.gradle'
        init.write_text("gradle.beforeProject { p -> p.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).configureEach { t -> t.doFirst { throw new GradleException('unexpected native compilation') } } }\n")
        run('signed-publication', [args.cli, 'publish', '--no-daemon', '--init-script', init,
            'publishAllPublicationsToReleaseRepository'], extra={'TEST_SIGNING_KEY_FILE': str(key)})
        publication = source / '.gradle-purifier/publication'
        module = json.loads((publication / 'catalog.json').read_text())['modules'][0]
        version = module['version']
        prefix = 'signed-library-' + version
        relative = Path('example/signed-library') / version
        deployed = source / 'build/release-repository' / relative
        signatures = sorted(deployed.glob('*.asc'))
        assert signatures, 'native publication produced no signatures'
        verified = []
        for index, signature in enumerate(signatures):
            artifact = signature.with_suffix('')
            immutable = publication / 'repository' / relative / artifact.name
            assert immutable.read_bytes() == artifact.read_bytes(), artifact
            if artifact.suffix == '.jar':
                original = 'signed-library-1.0' + artifact.name[len(prefix):]
                candidates = [path for path in release_files if path.name == original]
                assert candidates, f'{artifact.name} was not supplied by the Nix release'
                assert any(candidate.read_bytes() == artifact.read_bytes() for candidate in candidates), artifact
            run(f'verify-signature-{index}', [gpg, '--batch', '--verify', signature, artifact])
            verified.append(artifact.name)
        assert any(name.endswith('.pom') for name in verified)
        assert prefix + '.jar' in verified
        assert prefix + '-sources.jar' in verified
        assert prefix + '-javadoc.jar' in verified
        report.update(status='passed', release=str(release), signedArtifacts=verified)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
