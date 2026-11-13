#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit packaged JVM selection for repository fixtures, outside prefetch."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def fixture_jvm(cli, major, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    nixpkgs = re.search(r'^nixpkgs=(.+)$', Path(cli).read_text(), re.M).group(1)
    command = ['nix-build', '--quiet', nixpkgs, '-A',
               f'javaPackages.compiler.openjdk{int(major)}.headless',
               '--out-link', str(root / f'jdk-{int(major)}')]
    package = Path(subprocess.check_output(command, text=True).strip())
    home = package / 'lib/openjdk' if (package / 'lib/openjdk').is_dir() else package
    assert (home / 'bin/java').is_file(), home
    observed = subprocess.run([str(home / 'bin/java'), '-XshowSettings:properties', '-version'],
                              capture_output=True, text=True, check=True)
    specification = re.search(r'java.specification.version = ([^\s]+)', observed.stderr).group(1)
    actual_major = int(specification.split('.')[1] if specification.startswith('1.') else specification)
    assert actual_major == int(major), observed.stderr
    import hashlib
    receipt = {'major': actual_major, 'package': str(package), 'home': str(home), 'argv': command,
               'cli': str(Path(cli).resolve()), 'cliSHA256': hashlib.sha256(Path(cli).read_bytes()).hexdigest(),
               'nixpkgs': nixpkgs, 'nativeVersionOutput': observed.stderr}
    (root / f'jdk-{int(major)}.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt




def bind_fixture_sdks(project, manager_major, majors, cli, expected_sdks):
    """Opt a strict Linux SDK fixture into the existing source declaration API."""
    source = dict(type='github', owner='nix-purifiers', repo='purifier',
                  rev='f444063c705056a605583bd273cc8147f57808ee',
                  narHash='sha256-NaeaVq9B520WgzmRSMLGdN2zqnFlvM/wfZ8hECHhYqY=')
    assert manager_major in (8, 11, 17), 'fixture needs an existing immutable manager adapter'
    declaration = dict(version=1, system='x86_64-linux', manager=f'java{manager_major}', sdks=[
        dict(id=f'java{major}', source=source, selectionKind='jvm-sdk-adapter-v1',
             attribute=['lib', 'jvmSdkAdapters', 'x86_64-linux', f'openjdk{major}'])
        for major in sorted(set([manager_major, *majors]))])
    project = Path(project)
    declaration_path = project / 'gradle-purifier.sdks.json'
    declaration_path.write_text(json.dumps(declaration, indent=2) + '\n')
    variables = dict(re.findall(r'^(lib|nixpkgs|purifier_source)=(.+)$', Path(cli).read_text(), re.M))
    manifest_path = subprocess.check_output([
        'python3', str(Path(variables['lib']) / 'project-sdk-sources.py'), 'preflight',
        '--helper', variables['purifier_source'], '--nixpkgs', variables['nixpkgs'],
        '--declaration', str(declaration_path), '--root', str(project.parent / (project.name + '-sdk-preflight'))
    ], text=True).strip()
    manifest = json.loads(Path(manifest_path).read_text())
    for selected in manifest['sdks']:
        assert selected['home'] == expected_sdks[selected['id']]['home'], (selected['id'], selected['home'])
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--major', type=int, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fixture_jvm(args.cli, args.major, args.root)))
