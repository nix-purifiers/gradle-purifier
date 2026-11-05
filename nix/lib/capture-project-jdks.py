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
        if provenance not in {'resolved-selected-provider', 'configured-native-executable', 'native-effective-fork-options'}:
            raise ValueError('unsupported SDK selection provenance for ' + row['task'])
        if provenance == 'configured-native-executable' and row['role'] not in {'compiler', 'launcher'}:
            raise ValueError('explicit executable SDK provenance is unsupported for ' + row['role'])
        if provenance == 'native-effective-fork-options' and row['role'] != 'launcher':
            raise ValueError('native fork-options SDK provenance is unsupported for ' + row['role'])
        if provenance in {'configured-native-executable', 'native-effective-fork-options'}:
            binary = 'javac' if row['role'] == 'compiler' else 'java'
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt',type=Path)
    parser.add_argument('descriptor',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    try:
        result=capture(json.loads(args.receipt.read_text()),json.loads(args.descriptor.read_text()),
            Path(__file__).with_name('capture-project-jdk.nix'))
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit('gradle-purifier: project SDK capture failed: '+str(error))
    args.output.write_text(json.dumps(result,sort_keys=True,separators=(',',':'))+'\n')


if __name__=='__main__': main()
