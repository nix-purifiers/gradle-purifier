#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native source-mode boundaries, including same-version foreign SDKs."""
import argparse
import copy
import hashlib
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
parser.add_argument('--positive', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
cli = args.cli.resolve()
positive = args.positive.resolve()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
variables = dict(re.findall(r'^(lib|nixpkgs|purifier_source)=(.+)$', cli.read_text(), re.M))
helper = Path(variables['lib'])/'project-sdk-sources.py'
common = ['--helper', variables['purifier_source'], '--nixpkgs', variables['nixpkgs']]
declaration = json.loads((positive/'gradle-purifier.sdks.json').read_text())
original_lock = json.loads((positive/'gradle-purifier.lock.json').read_text())
context = original_lock['projectJdks']['x86_64-linux']
sdk8 = next(s['home'] for s in context['sdks'] if s['id'] == 'compiler')
sdk17 = next(s['home'] for s in context['sdks'] if s['id'] == 'manager')
env = dict(os.environ, JAVA_HOME=sdk17, XDG_CACHE_HOME=str(out/'cache'), GRADLE_USER_HOME=str(out/'gradle-home'))
stages = []


def run(name, command, cwd, accepted=True, needle=None, extra=None):
    command = [str(x) for x in command]
    with (out/(name+'.log')).open('w') as log:
        r = subprocess.run(command, cwd=cwd, env=dict(env, **(extra or {})),
                           stdout=log, stderr=subprocess.STDOUT, timeout=600)
    text = (out/(name+'.log')).read_text()
    stages.append(dict(name=name, argv=command, exitCode=r.returncode, accepted=accepted))
    (out/'stages.json').write_text(json.dumps(stages, indent=2)+'\n')
    print(name, r.returncode, flush=True)
    assert (r.returncode == 0) == accepted, (name, text[-5000:])
    if needle: assert needle in text, (name, text[-5000:])
    return text


def clone(name):
    project = out/name
    shutil.copytree(positive, project, ignore=shutil.ignore_patterns('.gradle', '.gradle-purifier',
        '.git', 'build', 'repository', 'test-started'))
    return project


def before_actions(project):
    work = project/'.gradle-purifier/lock-run/build'
    assert not (project/'test-started').exists() and not (work/'test-started').exists()
    assert not (work/'build/classes').exists()


alternate = dict(id='alternate', selectionKind='derivation-v1',
    source=dict(type='github', owner='nixos', repo='nixpkgs',
        rev='8825bebf6324e0579d012936eff73379af284b6d',
        narHash='sha256-FfjXg6mAzKQBCY+7V9keGWOXG4DV2Eym/qA987lFl8A='),
    attribute=['legacyPackages','x86_64-linux','javaPackages','compiler','openjdk8','headless'])
with_alternate = copy.deepcopy(declaration)
with_alternate['sdks'].append(alternate)
(out/'alternate-declaration.json').write_text(json.dumps(with_alternate, indent=2)+'\n')
manifest_path = run('alternate-acquisition', [sys.executable,helper,'preflight',*common,
    '--declaration',out/'alternate-declaration.json','--root',out/'alternate-root'],out).strip().splitlines()[-1]
manifest = json.loads(Path(manifest_path).read_text())
other = next(s for s in manifest['sdks'] if s['id'] == 'alternate')
original = next(s for s in manifest['sdks'] if s['id'] == 'compiler')
assert other['identityHash'] != original['identityHash'] and other['output'] != original['output']
versions = []
for label, home in [('original',sdk8),('alternate',other['home'])]:
    text = run(label+'-version',[Path(home)/'bin/java','-version'],out)
    versions.append(re.search(r'version "([^"]+)"', text).group(1))
assert versions[0] == versions[1]
project = clone('foreign-task-sdk')
for file in project.rglob('*'):
    if file.is_file() and file.name != 'gradle-purifier.lock.json':
        try: text = file.read_text()
        except UnicodeError: continue
        file.write_text(text.replace(sdk8,other['home']))
run('foreign-task-sdk',[cli,'lock','--bind-sdks','--relock'],project,False,'absent from the exact project declaration')
before_actions(project)
project = clone('foreign-manager')
manager8 = copy.deepcopy(declaration)
manager8['manager'] = 'compiler'
(project/'gradle-purifier.sdks.json').write_text(json.dumps(manager8,indent=2)+'\n')
with (project/'gradle.properties').open('a') as f: f.write('org.gradle.java.home='+other['home']+'\n')
run('foreign-manager',[cli,'lock','--bind-sdks','--relock'],project,False,'actual Gradle manager differs')
before_actions(project)
project = clone('explicit-alternate')
for file in project.rglob('*'):
    if file.is_file() and file.name != 'gradle-purifier.lock.json':
        try: text = file.read_text()
        except UnicodeError: continue
        file.write_text(text.replace(sdk8,other['home']))
(project/'gradle-purifier.sdks.json').write_bytes((out/'alternate-declaration.json').read_bytes())
run('explicit-alternate',[cli,'lock','--bind-sdks','--relock'],project)
accepted = json.loads((project/'gradle-purifier.lock.json').read_text())['projectJdks']['x86_64-linux']
assert all(role['sdk'] == other['identityHash'] for role in accepted['roles'])
assert len(accepted['sdks']) == 3
# Changing bytes of an otherwise equivalent declaration still requires relocking.
project = clone('stale-declaration')
with (project/'gradle-purifier.sdks.json').open('a') as f: f.write('\n')
run('stale-declaration',[cli,'prefetch'],project,False,'changed since')
# A disabled Test has no role; enabling it later cannot borrow another SDK role.
project = clone('disabled-test')
with (project/'build.gradle').open('a') as f: f.write("\ntest.enabled=project.hasProperty('enableLockedTest')\n")
run('disabled-test-capture',[cli,'lock','--bind-sdks','--relock'],project)
disabled = json.loads((project/'gradle-purifier.lock.json').read_text())['projectJdks']['x86_64-linux']
assert not any(role['task'] == ':test' for role in disabled['roles'])
disabled_env = dict(GRADLE_USER_HOME=str(out/'disabled-gradle-home'))
run('disabled-test-prefetch',[cli,'prefetch'],project,extra=disabled_env)
namespace = ['unshare','-rn','sh','-c','ip link set lo up && exec "$@"','source-sdk-negative']
run('disabled-test-enabled',[*namespace,'bash',project/'.gradle-purifier/distribution/bin/gradle',
    '--offline','--no-daemon','-PenableLockedTest','check'],project,False,'was not selected by the lock',disabled_env)
assert not (project/'test-started').exists()
# Capture may supply startup defaults. Ordinary commands require original native discovery.
project = clone('missing-discovery')
(project/'gradle.properties').unlink()
run('missing-discovery-capture',[cli,'lock','--bind-sdks','--relock'],project)
missing_env = dict(GRADLE_USER_HOME=str(out/'missing-gradle-home'))
run('missing-discovery-prefetch',[cli,'prefetch'],project,extra=missing_env)
# Keep the unavailable-provider branch independent of SDKs installed on the host.
run('missing-discovery-ordinary',[*namespace,'bash',project/'.gradle-purifier/distribution/bin/gradle',
    '--offline','--no-daemon','-Porg.gradle.java.installations.auto-detect=false',
    '-Porg.gradle.java.installations.auto-download=false','check'],project,False,
    'project SDK discovery is unavailable',missing_env)
assert not (project/'test-started').exists()
(out/'result.json').write_text(json.dumps(dict(status='passed', exactJavaVersion=versions[0],
    differentSdkIdentities=True, foreignSdkRejectedBeforeActions=True,
    foreignSameVersionManagerRejectedBeforeActions=True, originalNativeSelectionPreserved=True,
    disabledTestCannotBecomeEnabled=True, missingOrdinaryDiscoveryRejected=True,
    staleDeclarationRejected=True, stages=len(stages)),indent=2)+'\n')
