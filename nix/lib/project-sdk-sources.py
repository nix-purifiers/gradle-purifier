#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pure CLI entry points for project-declared SDK sources."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True


def run(argv):
    return subprocess.check_output(argv, text=True).strip()


def tree(path):
    path = Path(path).resolve()
    stored = run(['nix', 'store', 'add-path', str(path)])
    return {'type': 'path', 'path': stored,
            'narHash': run(['nix', 'hash', 'path', stored])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preflight', 'hash', 'build'])
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--nixpkgs', type=Path, required=True)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--declaration', type=Path)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--arguments', default='[]')
    parser.add_argument('--attribute')
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    system = {'x86_64': 'x86_64-linux'}.get(platform.machine()) if sys.platform == 'linux' else None
    if system != 'x86_64-linux':
        raise ValueError('project SDK source commands currently require x86_64-linux')
    data = dict(mode=args.mode, system=system, helper=tree(args.helper),
                library=tree(Path(__file__).resolve().parent), nixpkgs=tree(args.nixpkgs))
    if args.mode == 'preflight':
        sys.path.insert(0, str(args.helper / 'tools'))
        import jvm_sdk_sources
        raw = args.declaration.read_bytes()
        declaration = jvm_sdk_sources.read_json(args.declaration)
        jvm_sdk_sources.validate_declaration(declaration, system)
        seen = set()
        for selection in declaration['sdks']:
            key = json.dumps(selection['source'], sort_keys=True)
            if key not in seen:
                jvm_sdk_sources.validate_source_graph(selection['source'])
                seen.add(key)
        data.update(declaration=declaration, declarationHash=hashlib.sha256(raw).hexdigest())
    else:
        data['project'] = tree(args.project)
        if args.mode == 'hash':
            arguments = json.loads(args.arguments)
            if not isinstance(arguments, list) or not all(isinstance(x, str) for x in arguments):
                raise ValueError('Gradle arguments must be strings')
            data['gradleArguments'] = arguments
        else:
            if args.attribute not in {'repository', 'jdk', 'distribution', 'upstreamDistribution', 'sdkContext', 'konan',
                                      'release', 'publication', 'tests'}:
                raise ValueError('unsupported project build attribute')
            data['attribute'] = args.attribute
    with tempfile.TemporaryDirectory(prefix='gradle-sdk-command-') as temporary:
        temporary = Path(temporary)
        if args.mode == 'build':
            lock = temporary / 'locked'
            lock.mkdir()
            (lock / 'lock.json').write_bytes(args.lock.read_bytes())
            data['lock'] = tree(lock)
        (temporary / 'arguments.json').write_text(json.dumps(data, sort_keys=True))
        command = tree(temporary)
        # Only the JSON transport metadata loses its incidental file context.
        # SDK output/home/manager contexts are reconstructed inside Nix.
        expression = ('let command = builtins.fetchTree ' +
                      '{ type="path"; path=' + json.dumps(command['path']) +
                      '; narHash=' + json.dumps(command['narHash']) + '; }; ' +
                      'args = builtins.fromJSON (builtins.unsafeDiscardStringContext ' +
                      '(builtins.readFile (command.outPath + "/arguments.json"))); ' +
                      'library = builtins.fetchTree args.library; ' +
                      'in (import (library.outPath + "/project-sdk-command.nix")) args')
        argv = ['nix', 'build', '--expr', expression, '--json', '--option', 'pure-eval', 'true',
                '--option', 'allow-import-from-derivation', 'false', '--no-use-registries']
        argv += ['--out-link', str(args.root)] if args.root else ['--no-link']
        output = json.loads(run(argv))[0]['outputs']['out']
    print(Path(output).read_text() if args.mode == 'hash' else output)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit('gradle-purifier: project SDK source command failed: ' + str(error))
