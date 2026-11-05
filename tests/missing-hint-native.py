#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise immutable common-init hint selection on existing native controls."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cli', type=Path, required=True)
parser.add_argument('--cache', type=Path, required=True)
parser.add_argument('--sdks', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
lib = Path(re.search(r'^lib=(.+)$', args.cli.read_text(), re.M).group(1))
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=False)
# The executable must use these actual immutable companions, not the checkout.
names = ['purifier.init.gradle', 'missing-hint-flow.init.gradle', 'missing-hint-legacy.init.gradle']
for name in names:
    assert (lib / name).read_bytes() == (root / 'nix/lib' / name).read_bytes(), name
modern = (lib / names[1]).read_text()
legacy = (lib / names[2]).read_text()
def classifier(text):
    return text[text.index('    // Whether a failure'):text.index('\n}', text.index('    // Whether a failure'))]
assert classifier(modern) == classifier(legacy), 'classifier drift'
sdks = json.loads(args.sdks.read_text())
profiles = json.loads((root / 'tests/epoch-probes/profiles.json').read_text())['profiles']
spec = importlib.util.spec_from_file_location('epoch_probe', root / 'tests/epoch-probes/probe.py')
epoch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epoch)
args.cache = args.cache.resolve()
args.cache.mkdir(parents=True, exist_ok=True)
context = dict(sourceRevision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
               sdkInputs=sdks, cli=str(args.cli.resolve()), immutableLib=str(lib),
               profiles=[p for p in profiles if p['gradle'] in ['7.6.6', '8.14.3', '9.9.0-rc-1']],
               sourceHashes={p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in
                             subprocess.check_output(['git', 'ls-files'], cwd=root, text=True).splitlines()
                             if (root / p).is_file()})
(output / 'context.json').write_text(json.dumps(context, indent=2) + '\n')
rows = []
for version, sdk in [('7.6.6', 'java17'), ('8.14.3', 'java21'), ('9.9.0-rc-1', 'java21')]:
    work = output / version
    work.mkdir()
    project = work / 'project'
    project.mkdir()
    (project / 'settings.gradle').write_text("rootProject.name = 'hint-proof'\n")
    (project / 'build.gradle').write_text('''apply plugin: 'java'
repositories { maven { url = uri('https://example.invalid/maven') } }
dependencies { implementation 'example:missing:1' }
tasks.register('nestedFailure') {
    doLast { throw new GradleException('outer', new IllegalStateException('middle', new RuntimeException('Could not resolve nested dependency'))) }
}
tasks.register('unrelatedFailure') { doLast { throw new GradleException('unrelated sentinel') } }
''')
    source = project / 'src/main/java/Example.java'
    source.parent.mkdir(parents=True)
    source.write_text('public class Example {}\n')
    repository = work / 'repository'
    repository.mkdir()
    env = dict(os.environ, JAVA_HOME=sdks[sdk]['home'], GRADLE_USER_HOME=str(work / 'gradle-home'),
               GRADLE_PURIFIER_MODE='interactive', GRADLE_PURIFIER_REPOSITORY=repository.as_uri())
    profile = next(p for p in profiles if p['gradle'] == version)
    gradle = epoch.distribution(profile, args.cache)
    for task, succeeds, hint in [('help', True, False), ('compileJava', False, True),
                                 ('nestedFailure', False, True), ('unrelatedFailure', False, False)]:
        command = [shutil.which('bash'), str(gradle), '--offline', '--no-daemon', '--console=plain',
                   '--init-script', str(lib / names[0]), task]
        result = subprocess.run(command, cwd=project, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        log = result.stdout.decode(errors='replace')
        (work / (task + '.log')).write_text(log)
        count = log.count('gradle-purifier: the build needs a download that the lockfile does not hold')
        row = dict(version=version, task=task, exitCode=result.returncode, hintCount=count,
                   expectedSuccess=succeeds, expectedHint=hint)
        rows.append(row)
        (output / 'report.json').write_text(json.dumps(dict(lib=str(lib), results=rows), indent=2) + '\n')
        assert (result.returncode == 0) == succeeds, row
        assert count == int(hint), row
        if task == 'compileJava':
            assert 'example:missing:1' in log, 'native missing dependency detail lost'
        print(version, task, 'PASS', flush=True)

    # Exercise the actual installed interactive entry point, which applies
    # the common script from an immutable path. This hand-written properties
    # fixture tests path resolution; it is not a prefetch lifecycle receipt.
    metadata = project / '.gradle/purifier'
    metadata.mkdir(parents=True, exist_ok=True)
    lock = project / 'gradle-purifier.lock.json'
    lock.write_text('{}\n')
    properties = dict(lockFile=str(lock), lockSha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
                      portal=repository.as_uri(), initScript=str(lib / names[0]))
    properties['gradle.purifier.repository'] = repository.as_uri()
    (metadata / 'prefetch.properties').write_text(''.join(k + '=' + v + '\n' for k, v in properties.items()))
    interactive_env = {k: v for k, v in env.items() if k not in ['GRADLE_PURIFIER_MODE', 'GRADLE_PURIFIER_REPOSITORY']}
    for task, expected in [('help', 0), ('nestedFailure', 1)]:
        command = [shutil.which('bash'), str(gradle), '--offline', '--no-daemon', '--console=plain',
                   '--init-script', str(lib / 'user.init.gradle'), task]
        result = subprocess.run(command, cwd=project, env=interactive_env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        log = result.stdout.decode(errors='replace')
        (work / ('user-init-' + task + '.log')).write_text(log)
        count = log.count('gradle-purifier: the build needs a download that the lockfile does not hold')
        assert (result.returncode == 0) == (expected == 0) and count == expected, log
        rows.append(dict(version=version, task='user-init-' + task, exitCode=result.returncode, hintCount=count))
        (output / 'report.json').write_text(json.dumps(dict(lib=str(lib), results=rows), indent=2) + '\n')
        print(version, 'user-init-' + task, 'PASS', flush=True)

    if version != '7.6.6':
        # An error applying a present modern adapter is a real failure. It
        # must never cause an automatic fallback to the legacy adapter.
        broken = work / 'broken-companions'
        broken.mkdir()
        shutil.copyfile(lib / names[0], broken / names[0])
        (broken / names[1]).write_text("throw new GradleException('MODERN_ADAPTER_SENTINEL')\n")
        (broken / names[2]).write_text("throw new GradleException('LEGACY_FALLBACK_SENTINEL')\n")
        command = [shutil.which('bash'), str(gradle), '--offline', '--no-daemon', '--console=plain',
                   '--init-script', str(broken / names[0]), 'help']
        result = subprocess.run(command, cwd=project, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        log = result.stdout.decode(errors='replace')
        (work / 'modern-adapter-error.log').write_text(log)
        assert result.returncode != 0 and 'MODERN_ADAPTER_SENTINEL' in log, log
        assert 'LEGACY_FALLBACK_SENTINEL' not in log, log
        rows.append(dict(version=version, task='modern-adapter-error', exitCode=result.returncode,
                         fallback=False))
        (output / 'report.json').write_text(json.dumps(dict(lib=str(lib), results=rows), indent=2) + '\n')
        print(version, 'modern-adapter-error', 'PASS', flush=True)
