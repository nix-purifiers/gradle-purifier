#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build fixture attributes through NAR-bound pure evaluation, including SDK sources."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def command(cli, project, attribute, lock=None, extra=()):
    return ['python3', str(Path(__file__).resolve()), '--cli', str(cli), '--project', str(project),
            '--lock', str(lock or Path(project) / 'gradle-purifier.lock.json'),
            '--attribute', attribute, *extra]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--attribute', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    variables = dict(re.findall(r'^(lib|nixpkgs|purifier_source)=(.+)$', args.cli.read_text(), re.M))

    def tree(path):
        stored = subprocess.check_output(['nix', 'store', 'add-path', str(path)], text=True).strip()
        digest = subprocess.check_output(['nix', 'hash', 'path', stored], text=True).strip()
        return dict(type='path', path=stored, narHash=digest)

    with tempfile.TemporaryDirectory(prefix='gradle-fixture-build-') as temporary:
        temporary = Path(temporary)
        project = temporary / 'project'
        shutil.copytree(args.project, project, ignore=shutil.ignore_patterns(
            '.git', '.gradle', '.gradle-purifier', 'build', 'result', '__pycache__'))
        locked = temporary / 'locked'
        locked.mkdir()
        shutil.copyfile(args.lock, locked / 'lock.json')
        data = dict(library=tree(Path(variables['lib']).parents[1]),
                    helper=tree(variables['purifier_source']), nixpkgs=tree(variables['nixpkgs']),
                    project=tree(project), lock=tree(locked), attribute=args.attribute.split('.'))
        transport = temporary / 'transport'
        transport.mkdir()
        (transport / 'arguments.json').write_text(json.dumps(data))
        bound = tree(transport)
        expression = '''let transport = builtins.fetchTree (builtins.fromJSON %s);
          args = builtins.fromJSON (builtins.unsafeDiscardStringContext (builtins.readFile (transport.outPath + "/arguments.json")));
          library = builtins.fetchTree args.library; helper = builtins.fetchTree args.helper;
          nixpkgs = builtins.fetchTree args.nixpkgs; project = builtins.fetchTree args.project;
          locked = builtins.fetchTree args.lock;
          pkgs = import nixpkgs.outPath {system="x86_64-linux";config={};overlays=[];};
          inherit (pkgs) lib; purifier = import helper.outPath {inherit lib;};
          build = pkgs.callPackage (library.outPath + "/pkgs/build-gradle-project/default.nix") {inherit purifier;}
            {src=project.outPath;lockFile=locked.outPath + "/lock.json";};
          in lib.getAttrFromPath args.attribute build''' % json.dumps(json.dumps(bound))
        argv = ['nix', 'build', '--expr', expression, '--option', 'pure-eval', 'true',
                '--option', 'allow-import-from-derivation', 'false', '--no-use-registries',
                '--no-link', '--print-out-paths', '--print-build-logs']
        if args.check:
            argv.append('--rebuild')
        subprocess.run(argv, check=True)


if __name__ == '__main__':
    main()
