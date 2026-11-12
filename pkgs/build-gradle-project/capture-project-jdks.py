#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bind selected native Gradle SDK roles to the command's immutable Nix inputs."""
import argparse
import json
import re
from pathlib import Path
import subprocess


def capture(receipt, descriptor, expression):
    for row in receipt['tasks']:
        provenance = row.get('selectionProvenance', 'resolved-selected-provider')
        if provenance not in {'resolved-selected-provider', 'configured-native-executable', 'native-effective-fork-options', 'gradle-5.6.4-configured-javadoc-executable-v1'}:
            raise ValueError('unsupported SDK selection provenance for ' + row['task'])
        if provenance == 'gradle-5.6.4-configured-javadoc-executable-v1' and (receipt.get('gradleVersion') != '5.6.4' or row['role'] != 'javadoc'):
            raise ValueError('unsupported native Gradle5.6.4 Javadoc provenance')
        if provenance == 'configured-native-executable' and row['role'] not in {'compiler', 'launcher'}:
            raise ValueError('explicit executable SDK provenance is unsupported for ' + row['role'])
        if provenance == 'native-effective-fork-options' and row['role'] != 'launcher':
            raise ValueError('native fork-options SDK provenance is unsupported for ' + row['role'])
        if provenance in {'configured-native-executable', 'native-effective-fork-options', 'gradle-5.6.4-configured-javadoc-executable-v1'}:
            binary = 'javac' if row['role'] == 'compiler' else ('javadoc' if row['role'] == 'javadoc' else 'java')
            if row['executable'] != str(Path(row['home']) / 'bin' / binary):
                raise ValueError('explicit SDK executable does not match ' + row['role'] + ' role')
    if not receipt['tasks'] or all(row['home']==receipt['managerHome'] and row['home']==row['canonicalHome'] for row in receipt['tasks']):
        return {}  # Preserve the existing manager-only lock contract and byte identity.
    if not descriptor:
        raise ValueError('this CLI package cannot identify its nixpkgs source; use the pinned gradle-purifier CLI and relock')
    sdks = {}
    roles = []
    candidates = {}
    for row in receipt['tasks']:
        home = Path(row['home'])
        if not str(home).startswith('/nix/store/') or str(home) != str(home.resolve()):
            raise ValueError('unsupported ambient or mutable SDK home for '+row['task']+': '+str(home)+'; select a canonical Nix SDK in the native project and run gradle-purifier lock --relock')
        if row['home'] != row['canonicalHome'] or row['executable'] != row['canonicalExecutable']:
            raise ValueError('mutable SDK alias for '+row['task']+'; select the canonical Nix SDK and relock')
        release=(home/'release').read_text()
        version=re.search(r'^JAVA_VERSION="([^"]+)"',release,re.MULTILINE)
        if not version: raise ValueError('SDK release metadata is unavailable for '+row['task'])
        parts=version.group(1).split('.')
        major=int(parts[1] if parts[0]=='1' else re.match(r'[0-9]+',parts[0]).group())
        if major not in candidates:
            candidates[major] = json.loads(subprocess.check_output([
                'nix-instantiate', '--eval', '--strict', '--json', str(expression),
                '--argstr', 'nixpkgs', descriptor['nixpkgs'], '--arg', 'major', str(major),
                '--argstr', 'system', descriptor['system'], '--argstr', 'sourceJson', json.dumps(descriptor['source'])], text=True))
        candidate = candidates[major]
        if candidate['home'] != str(home):
            raise ValueError('selected SDK for '+row['task']+' does not match the pinned Nix SDK output, even if its Java version matches; select that exact Nix SDK and run gradle-purifier lock --relock')
        key = candidate['identityHash']
        sdks[key] = dict(candidate, source=descriptor['source'])
        roles.append(dict(task=row['task'], role=row['role'], sdk=key, provenance=row.get('selectionProvenance', 'resolved-selected-provider')))
    return {descriptor['system']: dict(sdks=[sdks[key] for key in sorted(sdks)],
        roles=sorted(roles, key=lambda row: (row['task'], row['role'])))}


def capture_sources(receipt, manifest):
    """Bind native observations to fully reconstructed project declarations."""
    if manifest.get('version') != 'jvm-sdk-context-v2':
        raise ValueError('unsupported explicit SDK context')
    expected = manifest['manager']
    actual = receipt['manager']
    for key in ('executable', 'runtimeHome'):
        if actual[key] != str(Path(actual[key]).resolve()) or actual[key] != expected[key]:
            raise ValueError('actual Gradle manager ' + key + ' differs from the declared SDK adapter')
    sdks = manifest['sdks']
    roles = []
    for row in receipt['tasks']:
        provenance = row.get('selectionProvenance', 'resolved-selected-provider')
        if provenance not in {'resolved-selected-provider', 'configured-native-executable',
                              'native-effective-fork-options', 'gradle-5.6.4-configured-javadoc-executable-v1'}:
            raise ValueError('unsupported SDK selection provenance for ' + row['task'])
        if provenance == 'gradle-5.6.4-configured-javadoc-executable-v1' and (receipt.get('gradleVersion') != '5.6.4' or row['role'] != 'javadoc'):
            raise ValueError('unsupported native Gradle5.6.4 Javadoc provenance')
        if provenance == 'configured-native-executable' and row['role'] not in {'compiler', 'launcher'}:
            raise ValueError('explicit executable SDK provenance is unsupported for ' + row['role'])
        if provenance == 'native-effective-fork-options' and row['role'] != 'launcher':
            raise ValueError('native fork-options SDK provenance is unsupported for ' + row['role'])
        matches = [sdk for sdk in sdks if sdk['home'] == row['home']]
        if len(matches) != 1:
            raise ValueError('native SDK home is absent or ambiguous in the project declaration: ' + row['task'])
        sdk = matches[0]
        binary = {'compiler': 'javac', 'launcher': 'java', 'javadoc': 'javadoc'}[row['role']]
        if (row['home'] != row['canonicalHome'] or row['home'] != str(Path(row['home']).resolve())
                or row['executable'] != row['canonicalExecutable']
                or row['executable'] != str(Path(sdk['home']) / 'bin' / binary)):
            raise ValueError('native task SDK layout or executable differs from declared canonical SDK: ' + row['task'])
        roles.append(dict(task=row['task'], role=row['role'], sdk=sdk['identityHash'], provenance=provenance))
    keys = [(row['task'], row['role']) for row in roles]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate native SDK task roles')
    context = {key: manifest[key] for key in ('version', 'declarationHash', 'sdks', 'manager')}
    context['roles'] = sorted(roles, key=lambda row: (row['task'], row['role']))
    return {sdks[0]['system']: context}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt',type=Path)
    parser.add_argument('descriptor',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--source-context',type=Path)
    args=parser.parse_args()
    try:
        receipt = json.loads(args.receipt.read_text())
        if args.source_context:
            result = capture_sources(receipt, json.loads(args.source_context.read_text()))
        else:
            result=capture(receipt,json.loads(args.descriptor.read_text()),
                Path(__file__).with_name('capture-project-jdk.nix'))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit('gradle-purifier: project SDK capture failed: '+str(error))
    args.output.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n')


if __name__=='__main__': main()
