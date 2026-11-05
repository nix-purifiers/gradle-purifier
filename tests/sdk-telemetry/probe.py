#!/usr/bin/env python3
"""Probe one existing Gradle 8.14.3/manager21/project8 fixture, not a version matrix."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    expression = '''let
      f = builtins.getFlake ("path:" + builtins.getEnv "GRADLE_TELEMETRY_SOURCE");
      p = import f.inputs.nixpkgs { system = builtins.currentSystem; };
      describe = j: let context = builtins.getContext (toString j); in {
        path = toString j; home = j.home or "${j}/lib/openjdk";
        version = j.version; drvPath = j.drvPath; inherit context;
        identityHash = builtins.hashString "sha256" (builtins.toJSON context);
      };
    in { java8 = describe p.jdk8_headless; java21 = describe p.jdk21_headless; }'''
    sdk_env = dict(os.environ, GRADLE_TELEMETRY_SOURCE=str(ROOT))
    sdks = json.loads(subprocess.check_output(
        ['nix', 'eval', '--impure', '--json', '--expr', expression], env=sdk_env, text=True))
    for sdk in sdks.values():
        if not Path(sdk['home']).is_dir():
            raise ValueError('Realize the pinned SDK before running: ' + sdk['drvPath'])
    spec = importlib.util.spec_from_file_location('epoch', ROOT/'tests/epoch-probes/probe.py')
    epoch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(epoch)
    profiles = json.loads((ROOT/'tests/epoch-probes/profiles.json').read_text())
    # The existing profile inventory is an object containing profiles.
    if isinstance(profiles, dict):
        profiles = profiles['profiles']
    profile = next(p for p in profiles if p['id'] == 'gradle-8.14.3')
    project = out/'project'
    epoch.fixture(project, profile, sdks)
    original = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob('*') if p.is_file()}
    init = Path(__file__).with_name('providers.init.gradle')
    command = [str(args.cli.resolve()), 'lock', '--', '--init-script', str(init), '--info']
    env = dict(os.environ, JAVA_HOME=sdks['java21']['home'],
        GRADLE_SDK_TELEMETRY=str(out/'receipt.json'), HOME=str(out/'home'))
    Path(env['HOME']).mkdir()
    env['PATH'] = env['JAVA_HOME']+'/bin:'+env['PATH']
    lock = json.loads((ROOT/'flake.lock').read_text())
    source = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [
        'tests/sdk-telemetry/probe.py', 'tests/sdk-telemetry/providers.init.gradle',
        'tests/epoch-probes/probe.py', 'tests/epoch-probes/profiles.json', 'flake.lock']}
    context = dict(sourceRevision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        sourceHashes=source, nixpkgs=lock['nodes']['nixpkgs']['locked'], profile=profile,
        sdks=sdks, command=command, scope='One native diagnostic; no SDK compatibility certification')
    (out/'context.json').write_text(json.dumps(context, indent=2)+'\n')
    with (out/'capture.log').open('w') as log:
        result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError('Native capture failed; inspect '+str(out/'capture.log'))
    receipt = json.loads((out/'receipt.json').read_text())
    java8 = str(Path(sdks['java8']['home']).resolve())
    java21 = str(Path(sdks['java21']['home']).resolve())
    assert str(Path(receipt['manager']['javaHome']).resolve()) == java21
    observed = []
    for task in [':compileJava', ':compileTestJava', ':epochTest']:
        row = receipt['tasks'][task]
        assert row['provider']['home']['canonical'] == java8
        outcome = row['outcome']
        assert outcome['executed'] and not any(outcome[k] for k in ['skipped', 'upToDate', 'noSource', 'failed'])
        override = row.get('forkExecutable') or row.get('taskExecutable')
        assert override['canonical'] == row['provider']['executable']['canonical']
        observed.append(dict(task=task, role=row['role'], home=java8, identityHash=sdks['java8']['identityHash']))
    assert receipt['tasks'][':test']['outcome']['skipped']
    assert any(row['task']==':javadoc' for row in receipt['unobserved'])
    log = (out/'capture.log').read_text()
    assert 'Compiling with Java command line compiler '+repr(java8+'/bin/javac') in log
    assert 'Command: '+java8+'/bin/java ' in log and 'example.EpochCheck' in log
    compiled = project/'.gradle-purifier/lock-run/build/build/classes/java/main/example/Epoch.class'
    data = compiled.read_bytes()
    assert data[:4] == b'\xca\xfe\xba\xbe'
    major = int.from_bytes(data[6:8], 'big')
    assert major == 52
    assert receipt['buildSucceeded']
    assert all((project/name).read_bytes()==data for name,data in original.items())
    summary = dict(scope=context['scope'], managerHome=java21, classMajor=major,
        observed=observed, skippedTasks=[':test'], unobservedTasks=receipt['unobserved'],
        note='Raw task-state executed=true may also accompany skipped=true; skipped tasks are not runtime execution credit.',
        sourcePreserved=True, exitCode=result.returncode)
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
