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
        init.write_text("""gradle.beforeProject { p ->
    p.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).configureEach { t ->
        t.doFirst { throw new GradleException('unexpected native compilation') }
    }
}
gradle.projectsEvaluated { g ->
    def destination = System.getenv('TEST_PUBLICATION_REPOSITORY')
    if (destination) g.rootProject.publishing.repositories.getByName('release').url = new File(destination)
}
""")
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

        # Exercise native publish mode against a deliberately incomplete
        # projection. CLI publish would rebuild/restore the projection first,
        # hiding the stale-stage counterexample this regression must cover.
        shadow = work / 'projection'
        shutil.copytree(publication / 'repository', shadow, symlinks=False)
        shadow.chmod(0o755)
        for path in shadow.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        catalog = work / 'catalog.json'
        catalog.write_bytes((publication / 'catalog.json').read_bytes())
        built = {path.name: str(path.resolve()) for path in release.iterdir() if path.exists()}
        tree = source / '.gradle-purifier/repository'
        java = source / '.gradle-purifier/jdk'
        if (java / 'lib/openjdk/bin/java').is_file():
            java = java / 'lib/openjdk'
        native = [gradle, '--no-daemon', '--offline', '--no-build-cache', '--no-configuration-cache',
                  '--max-workers=2', '--init-script', root / 'nix/lib/purifier.init.gradle',
                  '--init-script', root / 'nix/lib/task-jdks.init.gradle', '--init-script', init,
                  '-Dorg.gradle.internal.plugins.portal.url.override=' + tree.as_uri() + '/https/plugins.gradle.org/m2',
                  '-x', 'generateMetadataFileForLibraryPublication', 'publishAllPublicationsToReleaseRepository']
        native_env = {'JAVA_HOME': str(java), 'TEST_SIGNING_KEY_FILE': str(key),
                      'GRADLE_PURIFIER_MODE': 'publish', 'GRADLE_PURIFIER_BUILD_ROOT': str(source),
                      'GRADLE_PURIFIER_REPOSITORY': tree.as_uri(),
                      'GRADLE_PURIFIER_ARTIFACTS': json.dumps(built),
                      'GRADLE_PURIFIER_PUBLICATION_REPOSITORY': str(shadow),
                      'GRADLE_PURIFIER_PUBLICATION_CATALOG': str(catalog)}
        staging = source / '.gradle-purifier/publication-staging'

        def stage_key(publication_name, artifact_name):
            return hashlib.sha256(':'.join([':', publication_name, module['group'], artifact_name,
                                           version]).encode()).hexdigest()

        # Seed the exact old deterministic staging location and every genuine
        # prior stage. Neither may stand in for an absent immutable main JAR.
        old_stage = staging / stage_key('library', module['name'])
        old_stage.mkdir(exist_ok=True)
        for directory in staging.iterdir():
            if directory.is_dir():
                stale = directory / (prefix + '.jar')
                stale.unlink(missing_ok=True)
                stale.write_bytes(b'stale main jar must never be published')
        (shadow / relative / (prefix + '.jar')).unlink()
        negative_destination = work / 'missing-main-deployment'
        run('missing-projection-rejects-stale-main', native, expected=False,
            extra=native_env | {'TEST_PUBLICATION_REPOSITORY': str(negative_destination)},
            contains='the Nix projection has no ' + prefix + '.jar')
        assert not any(negative_destination.rglob('*.jar')), 'missing immutable JAR was deployed'

        # Restore required bytes, remove the optional module, and inject it
        # into old staging. Native signing/publication must use a fresh stage
        # and must not invent optional metadata from old writable files.
        shutil.copyfile(publication / 'repository' / relative / (prefix + '.jar'),
                        shadow / relative / (prefix + '.jar'))
        (shadow / relative / (prefix + '.module')).unlink()
        for directory in staging.iterdir():
            if directory.is_dir():
                stale = directory / (prefix + '.module')
                stale.unlink(missing_ok=True)
                stale.write_text('stale optional module')
        before_stages = {path.name for path in staging.iterdir()}
        without_module = work / 'without-module-deployment'
        run('stale-optional-module-not-published', native,
            extra=native_env | {'TEST_PUBLICATION_REPOSITORY': str(without_module)})
        deployed_without_module = without_module / relative
        assert not list(deployed_without_module.glob('*.module*')), 'stale optional module was deployed'
        assert {path.name for path in staging.iterdir()} > before_stages, 'publication reused a prior stage'
        for name in [prefix + '.jar', prefix + '-sources.jar', prefix + '-javadoc.jar', prefix + '.pom']:
            assert (deployed_without_module / name).read_bytes() == (shadow / relative / name).read_bytes(), name
            run('verify-fresh-stage-' + name.rsplit('.', 1)[-1] + '-' + str(len(report['commands'])),
                [gpg, '--batch', '--verify', deployed_without_module / (name + '.asc'), deployed_without_module / name])

        # Provided input publications enumerate artifacts rather than using
        # the native publication's required artifact list. That enumeration
        # must also come exclusively from the immutable projection.
        extra = dict(module, name='provided-input')
        extra_relative = Path(module['group'].replace('.', '/')) / extra['name'] / version
        extra_directory = shadow / extra_relative
        extra_directory.mkdir(parents=True)
        extra_prefix = extra['name'] + '-' + version
        shutil.copyfile(shadow / relative / (prefix + '.jar'), extra_directory / (extra_prefix + '.jar'))
        (extra_directory / (extra_prefix + '.pom')).write_text(
            (shadow / relative / (prefix + '.pom')).read_text().replace(module['name'], extra['name']))
        extended = json.loads(catalog.read_text())
        extended['publications'] = extended.get('publications', extended['modules']) + [extra]
        extended['modules'] = extended['modules'] + [extra]
        catalog.write_text(json.dumps(extended))
        extra_stage = staging / stage_key('purifierNixInput0', extra['name'])
        extra_stage.mkdir(exist_ok=True)
        (extra_stage / (extra_prefix + '-injected.jar')).write_bytes(b'not in immutable input projection')
        (extra_stage / (extra_prefix + '.module')).write_text('not in immutable input projection')
        closure_destination = work / 'closure-deployment'
        run('provided-input-ignores-injected-stage', native,
            extra=native_env | {'TEST_PUBLICATION_REPOSITORY': str(closure_destination)})
        deployed_extra = closure_destination / extra_relative
        assert (deployed_extra / (extra_prefix + '.jar')).read_bytes() == (extra_directory / (extra_prefix + '.jar')).read_bytes()
        assert (deployed_extra / (extra_prefix + '.pom')).read_bytes() == (extra_directory / (extra_prefix + '.pom')).read_bytes()
        assert not list(deployed_extra.glob('*-injected.jar*')), 'injected input classifier was deployed'
        assert not list(deployed_extra.glob('*.module*')), 'injected input module was deployed'
        report.update(status='passed', release=str(release), signedArtifacts=verified,
                      missingProjectionRejected=True, freshStageVerified=True,
                      immutableOptionalMetadata=True, immutableInputEnumeration=True)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
