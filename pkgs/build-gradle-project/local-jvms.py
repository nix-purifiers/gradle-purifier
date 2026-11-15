#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Observe installed JVMs without resolving or acquiring a project SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


VERSION = 'gradle-local-jvms-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def locate(executable):
    executable = Path(executable).resolve()
    candidate = executable.parent.parent
    markers = ['release', 'lib/modules', 'lib/rt.jar', 'jre/lib/rt.jar']
    if (candidate / 'bin/java').resolve() == executable and any((candidate / name).exists() for name in markers):
        return candidate
    # PATH may contain a system launcher or a version-manager shim, rather
    # than a file below the selected runtime's home. Ask that real launcher.
    process = subprocess.run([str(executable), '-XshowSettings:properties', '-version'],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    match = re.search(r'^\s*java\.home\s*=\s*(.+?)\s*$', process.stdout + process.stderr, re.MULTILINE)
    if process.returncode == 0 and match:
        home = Path(match.group(1))
        if (home / 'bin/java').is_file() and os.access(home / 'bin/java', os.X_OK):
            return home
    # Old native JVMs can lack -XshowSettings; a normal bin/java layout
    # still needs no helper bytecode or installed-version lookup table.
    if (candidate / 'bin/java').is_file() and (candidate / 'bin/java').resolve() == executable:
        return candidate
    raise ValueError('native java launcher did not expose its runtime home; set JAVA_HOME to that installed runtime')


def observe(home):
    home = Path(home)
    java = home / 'bin/java'
    process = subprocess.run([str(java), '-version'], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=30)
    output = process.stdout + process.stderr
    if process.returncode:
        raise ValueError('selected native Java cannot run: ' + output.strip())
    version = re.search(r'\bversion\s+"([^"]+)"', output)
    if not version:
        raise ValueError('selected native Java did not report its actual version')
    value = version.group(1)
    major = int(re.match(r'\d+', value.split('.')[1] if value.startswith('1.') else value).group())
    tools = {name: digest(home / 'bin' / name) for name in ['java', 'javac', 'javadoc']
             if (home / 'bin' / name).is_file() and os.access(home / 'bin' / name, os.X_OK)}
    return {'javaVersion': value, 'javaMajor': major, 'tools': tools,
            'releaseSha256': digest(home / 'release') if (home / 'release').is_file() else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, help='Inspect an installed runtime without acquiring an SDK')
    parser.add_argument('--locate', type=Path, help='Locate the runtime selected by a real PATH launcher')
    args = parser.parse_args()
    if bool(args.home) == bool(args.locate):
        parser.error('provide --home or --locate')
    try:
        if args.locate:
            print(locate(args.locate))
            return
        if args.home:
            print(json.dumps(observe(args.home), sort_keys=True, separators=(',', ':')))
            return

    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit('gradle-purifier: native JVM observation failed: ' + str(error))


if __name__ == '__main__':
    main()
