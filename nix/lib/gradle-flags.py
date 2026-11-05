#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Select native Gradle controls from that distribution's own --help output."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import selectors
import subprocess
import sys
import time


def select(mode, text):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    if len(text.encode('utf-8')) > 262144 or not text.endswith('\n'):
        raise ValueError('native Gradle help is oversized or truncated')
    if len(re.findall(r'(?im)^\s*usage:\s*gradle\b', text)) != 1:
        raise ValueError('native Gradle did not return recognizable --help output')
    options = set()
    rows = []
    for line in text.splitlines():
        line = line.lstrip()
        if line.startswith('-'):
            column = re.split(r'\s{2,}', line, maxsplit=1)[0]
            flags = re.findall(r'--(?:\[no-\])?[a-z][a-z0-9-]*', column)
            if flags:
                rows.append(flags)
                options.update(flags)
    usage = re.search(r'(?im)^\s*usage:\s*gradle\b', text)
    headings = re.findall(r'(?m)^([A-Z][A-Za-z -]+):\s*$', text[usage.end():])
    categorized = ['Help', 'Logging', 'Console', 'Configuration', 'Execution', 'Performance',
                   'Security', 'Diagnostics', 'Daemon', 'Develocity']
    complete = rows and ((not headings and '--exclude-task' in rows[-1]) or
                         (headings == categorized and '--no-scan' in rows[-1]))
    if not {'--help', '--version', '--init-script', '--exclude-task'} <= options or not complete:
        raise ValueError('native Gradle help is incomplete or has an unsupported format')
    features = ['daemon', 'configuration-cache', 'build-cache']
    if mode == 'sandbox':
        features.append('watch-fs')
    result = []
    for feature in features:
        if '--no-' + feature in options or '--[no-]' + feature in options:
            result.append('--no-' + feature)
        elif '--' + feature in options:
            raise ValueError('native Gradle advertises ' + feature + ' without a supported disable option')
    if mode != 'publish':
        if '--console' in options:
            result.append('--console=plain')
        elif '--no-color' in options:
            result.append('--no-color')
    return result


def validate_arguments(arguments):
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise ValueError('Gradle arguments must be a JSON array of strings')
    enabling = {'--daemon', '--foreground', '--configuration-cache', '--build-cache', '--watch-fs', '--isolated-projects'}
    properties = {'org.gradle.daemon', 'org.gradle.configuration-cache', 'org.gradle.unsafe.configuration-cache',
                  'org.gradle.caching', 'org.gradle.vfs.watch', 'org.gradle.unsafe.isolated-projects'}
    property_options = {'-D', '-P', '--system-prop', '--project-prop'}
    for index, argument in enumerate(arguments):
        option = argument.split('=', 1)[0]
        if argument == '--' or option in enabling:
            raise ValueError('Gradle argument conflicts with enforced daemon/cache/offline controls: ' + option)
        property_argument = None
        if argument in property_options:
            if index + 1 >= len(arguments):
                raise ValueError('Gradle property option is missing its argument')
            property_argument = arguments[index + 1]
        elif argument.startswith(('-D', '-P')):
            property_argument = argument[2:]
        elif option in {'--system-prop', '--project-prop'}:
            property_argument = argument.partition('=')[2]
        if property_argument is not None:
            key, separator, value = property_argument.partition('=')
            if key in properties and (not separator or value.lower() != 'false'):
                raise ValueError('Gradle property conflicts with enforced daemon/cache controls: ' + key)


def probe(executable, help_file, timeout):
    limit = 262144
    process = subprocess.Popen([str(executable), '--help'], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, bufsize=0)
    selector = selectors.DefaultSelector()
    streams = [process.stdout, process.stderr]
    retained = [bytearray(), bytearray()]
    eof = [False, False]
    observed = 0
    reason = None
    cancellation = None
    killed_at = None
    deadline = time.monotonic() + timeout

    def signal_group(signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    def cancel(message):
        nonlocal reason, cancellation
        if reason is None:
            reason = message
            cancellation = time.monotonic()
            signal_group(signal.SIGTERM)

    try:
        for index, stream in enumerate(streams):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while True:
            now = time.monotonic()
            if reason is None:
                # Keep the parent unreaped while pipes remain open: its PID
                # then cannot be reused before cancellation signals its group.
                if not selector.get_map() and process.poll() is not None:
                    break
                if now >= deadline:
                    cancel('native Gradle --help exceeded its bounded probe deadline')
            if reason is not None:
                if killed_at is None and now >= cancellation + 0.5:
                    signal_group(signal.SIGKILL)
                    killed_at = now
                if killed_at is not None and (not selector.get_map() or now >= killed_at + 0.5):
                    break
            if not selector.get_map():
                time.sleep(0.02)
                continue
            for key, _ in selector.select(0.05):
                try:
                    data = os.read(key.fileobj.fileno(), 65536)
                except (BlockingIOError, InterruptedError):
                    continue
                if not data:
                    eof[key.data] = True
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                observed += len(data)
                remaining = max(0, limit - sum(map(len, retained)))
                retained[key.data].extend(data[:remaining])
                if observed > limit:
                    cancel('native Gradle --help exceeded the output limit; retained diagnostics are capped')
    except OSError as error:
        cancel('could not read native Gradle help: ' + str(error))
    finally:
        # No communicate(): EOF may be held by a process outside this group.
        # Closing our readers and waiting for only the owned parent is bounded.
        for stream in streams:
            if not stream.closed:
                stream.close()
        selector.close()
        if process.returncode is None:
            signal_group(signal.SIGKILL)
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            reason = 'native Gradle help did not exit after bounded cleanup'
    stdout, stderr = map(bytes, retained)
    help_file.write_bytes(stdout)
    Path(str(help_file) + '.stderr').write_bytes(stderr)
    Path(str(help_file) + '.probe.json').write_text(json.dumps({
        'executable': str(executable), 'arguments': ['--help'],
        'javaHome': os.environ.get('JAVA_HOME'),
        'gradleUserHome': os.environ.get('GRADLE_USER_HOME'),
        'pid': process.pid, 'exitCode': process.returncode, 'failure': reason,
        'observedBytes': observed, 'retainedBytes': len(stdout) + len(stderr),
        'stdoutEOF': eof[0], 'stderrEOF': eof[1],
        'ownedParentReaped': process.returncode is not None,
    }, indent=2) + '\n')
    sys.stderr.buffer.write(stderr)
    if reason is not None:
        raise ValueError(reason)
    if process.returncode != 0:
        raise ValueError('native Gradle --help failed with exit status ' + str(process.returncode))
    return stdout.decode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['capture', 'sandbox', 'publish'])
    parser.add_argument('help_file', type=Path)
    parser.add_argument('--arguments-json', default='[]')
    parser.add_argument('--gradle', type=Path, help='Probe this exact native command in the caller-selected SDK/environment')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    if not 0 < args.timeout <= 30:
        parser.error('the probe timeout must be positive and at most 30 seconds')
    try:
        validate_arguments(json.loads(args.arguments_json))
        text = probe(args.gradle, args.help_file, args.timeout) if args.gradle else args.help_file.read_text()
        flags = select(args.mode, text)
    except (OSError, UnicodeError, ValueError) as error:
        print('gradle-purifier: ' + str(error), file=sys.stderr)
        return 1
    for flag in flags:
        print(flag)
    return 0


if __name__ == '__main__':
    sys.exit(main())
