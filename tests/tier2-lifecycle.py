#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the pinned independent sample through capture, native replay and packaging.

Requires Nix, git, and (on Linux) unshare/ip. Output retains bounded logs and
receipts, never Gradle caches or the downloaded source tree.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


# Exact distribution checksums from services.gradle.org, checked 2026-11-06.
# These are test inputs, not a claim about untested intermediate releases.
SUPPORTED_POINTS = {
    '8.14.6': '7988ed071b2a07900e2ec715fca15c6ab72bce6db433af301fa7fa7e9407bd24',
    '9.9.0': '0b00b0ffe07c4d4789c12d4935a44a7057d55774b7540bd4a3f76f336deb177a',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gradle-version', choices=SUPPORTED_POINTS,
                        help='Change only the pinned sample wrapper to this exact supported point')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    receipt = {'status': 'running', 'commands': [], 'platform': platform.platform()}
    env = os.environ.copy()

    def save():
        (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

    def run(name, command, cwd=root, extra=None):
        command = [str(arg) for arg in command]
        print(f'== {name}: {shlex.join(command)}', flush=True)
        step_env = env | (extra or {})
        stdout_path = output / f'{name}.stdout'
        with (output / f'{name}.log').open('w') as log, stdout_path.open('w') as stdout:
            result = subprocess.run(command, cwd=cwd, env=step_env,
                                    stdout=stdout, stderr=log, text=True)
        receipt['commands'].append({'name': name, 'argv': command,
                                    'cwd': str(cwd), 'environment': extra or {},
                                    'exitCode': result.returncode})
        save()
        if result.returncode:
            if name == 'ordinary-test':
                with (output / 'ordinary-test-diagnostics.log').open('w') as diagnostics:
                    subprocess.run([sys.executable, str(root / 'tests/tier2-diagnostics.py'),
                        '--gradle-home', step_env['GRADLE_USER_HOME'], '--project', str(cwd),
                        '--tmpdir', str(Path(cwd).parent)], stdout=diagnostics,
                        stderr=subprocess.STDOUT, env=step_env, check=False)
            print((output / f'{name}.log').read_text()[-16000:], file=sys.stderr)
            raise RuntimeError(f'{name} failed: {result.returncode}')
        return stdout_path.read_text().strip()

    def build_check(name, expression):
        (output / f'{name}.nix').write_text(expression + '\n')
        drv = run(f'{name}-derivation', ['nix', 'eval', '--impure', '--raw',
                  '--expr', f'({expression}).drvPath'])
        run(f'{name}-derivation-json', ['nix', 'derivation', 'show', drv])
        result = json.loads(run(name, ['nix', 'build', '--impure', '--expr', expression,
            '--option', 'sandbox', 'true', '--option', 'max-jobs', '2', '--option', 'cores', '2',
            '--no-link', '--json', '--print-build-logs', '--keep-failed']))
        assert result[0]['drvPath'] == drv
        built = Path(result[0]['outputs']['out'])
        info = json.loads(run(f'{name}-path-info', ['nix', 'path-info', '--json', built]))
        receipt[name] = {'derivation': drv, 'output': str(built), 'pathInfo': info}
        save()
        return built

    try:
        receipt['sourceRevision'] = run('source', ['git', 'rev-parse', 'HEAD'])
        receipt['sourceDiff'] = run('source-diff', ['git', 'diff', '--binary', 'HEAD'])
        assert not receipt['sourceDiff'], 'freeze tracked source before collecting evidence'
        receipt['harnessSha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        receipt['evidenceFiles'] = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), root / 'tests/tier2-offline.nix',
                         root / 'tests/tier2-diagnostics.py',
                         root / 'tests/GradleSocketProbe.java',
                         root / '.github/workflows/tier2-runtime.yml')}
        receipt['sample'] = json.loads((root / 'tests/real-world/petclinic.json').read_text())
        source_ref = f'git+file://{root}?rev={receipt["sourceRevision"]}'
        archive = json.loads(run('source-archive', ['nix', 'flake', 'archive', '--json',
            '--no-update-lock-file', source_ref]))
        archived_root = Path(archive['path']).resolve(strict=True)
        assert archived_root.is_dir() and (archived_root / 'flake.nix').is_file()
        receipt['sourceArchive'] = archive
        receipt['sourcePathInfo'] = json.loads(run('source-path-info', ['nix', 'path-info', '--json', archived_root]))
        system = run('system', ['nix', 'eval', '--impure', '--raw', '--expr', 'builtins.currentSystem'])
        receipt['nixSystem'] = system
        receipt['sandboxSetting'] = run('sandbox-config', ['nix', 'config', 'show', 'sandbox',
                                                        '--option', 'sandbox', 'true'])
        assert receipt['sandboxSetting'] == 'true', receipt['sandboxSetting']
        expected = {'Linux': {'x86_64-linux', 'aarch64-linux'}, 'Darwin': {'aarch64-darwin'}}
        assert system in expected[platform.system()], (platform.system(), system)
        cli = Path(run('cli', ['nix', 'build', f'{source_ref}#gradle-purifier',
            '--option', 'max-jobs', '2', '--option', 'cores', '2', '--no-link', '--print-out-paths'])) / 'bin/gradle-purifier'
        receipt['cli'] = str(cli)
        with tempfile.TemporaryDirectory(prefix='gradle-tier2-') as temporary:
            work = Path(temporary)
            project = work / 'petclinic'
            env.update(GRADLE_USER_HOME=str(work / 'gradle-home'),
                       XDG_CACHE_HOME=str(work / 'cache'), GRADLE_PURIFIER_CENTRAL_MIRROR='1')
            pin = receipt['sample']
            run('sample-init', ['git', 'init', project])
            run('sample-fetch', ['git', 'fetch', '--depth', '1',
                f'https://github.com/{pin["owner"]}/{pin["repo"]}.git', pin['rev']], project)
            run('sample-checkout', ['git', 'checkout', '--detach', 'FETCH_HEAD'], project)
            assert run('sample-revision', ['git', 'rev-parse', 'HEAD'], project) == pin['rev']
            if args.gradle_version:
                wrapper = project / 'gradle/wrapper/gradle-wrapper.properties'
                wrapper.write_text(re.sub(r'gradle-[\d.]+-bin.zip',
                    f'gradle-{args.gradle_version}-bin.zip', wrapper.read_text())
                    + f'\ndistributionSha256Sum={SUPPORTED_POINTS[args.gradle_version]}\n')
            receipt['samplePatch'] = run('sample-patch', ['git', 'diff', '--binary'], project)
            run('lock', [cli, 'lock', '--java', '17'], project)
            lock_path = project / 'gradle-purifier.lock.json'
            shutil.copy2(lock_path, output / lock_path.name)
            lock = json.loads(lock_path.read_text())
            if args.gradle_version:
                assert lock['distribution'].endswith(f'gradle-{args.gradle_version}-bin.zip')
                distribution_hash = 'sha256-' + base64.b64encode(
                    bytes.fromhex(SUPPORTED_POINTS[args.gradle_version])).decode()
                assert any(a.get('url', '').endswith(f'gradle-{args.gradle_version}-bin.zip')
                           and a['hash'] == distribution_hash for a in lock['artifacts'])
            receipt.update(javaMajor=lock['javaMajor'], distribution=lock['distribution'],
                           lockSha256=hashlib.sha256(lock_path.read_bytes()).hexdigest())
            # Replay starts without native caches or outputs from capture.
            shutil.rmtree(work / 'gradle-home', ignore_errors=True)
            for directory in ('build', '.gradle', '.gradle-purifier'):
                shutil.rmtree(project / directory, ignore_errors=True)
            run('prefetch', [cli, 'prefetch'], project)
            jdk = (project / '.gradle-purifier/jdk').resolve()
            java_home = jdk / 'lib/openjdk' if (jdk / 'lib/openjdk').is_dir() else jdk
            receipt['javaHome'] = str(java_home)
            run('java', [java_home / 'bin/java', '-version'])
            # The executable ordinary-workflow check uses precisely the files
            # prefetch wrote, relocated to the sandbox with a cold native cache.
            source = work / 'pure-source'
            shutil.copytree(project, source, ignore=shutil.ignore_patterns(
                '.git', '.gradle', '.gradle-purifier', 'build'))
            properties = work / 'prefetch.properties'
            # macOS /var aliases /private/var; prefetch writes canonical paths.
            # Replace the canonical prefix first so no /private prefix survives.
            captured_properties = (project / '.gradle/purifier/prefetch.properties').read_bytes()
            (output / 'prefetch-captured.properties').write_bytes(captured_properties)
            receipt['capturedPrefetchPropertiesSha256'] = hashlib.sha256(captured_properties).hexdigest()
            relocated = captured_properties.decode()
            for project_path in sorted({str(project.resolve()), str(project)}, key=len, reverse=True):
                relocated = relocated.replace(project_path, '@PROJECT@')
            for key in ('lockFile', 'gradle.purifier.resources'):
                assert f'{key}=@PROJECT@/gradle-purifier.lock.json' in relocated.splitlines(), key
            assert str(project) not in relocated and str(project.resolve()) not in relocated
            properties.write_text(relocated)
            bindings = {
                'jdk': str(jdk), 'distribution': str((project / '.gradle-purifier/distribution').resolve()),
                'library': str((project / '.gradle-purifier/lib').resolve()),
                'repository': str((project / '.gradle-purifier/repository').resolve()),
            }
            receipt['prefetchBindings'] = bindings
            receipt['prefetchPropertiesSha256'] = hashlib.sha256(properties.read_bytes()).hexdigest()
            shutil.copy2(properties, output / 'prefetch.properties')
            prefix = ('let f = builtins.getFlake ' + json.dumps(str(archived_root)) + '; '
                'p = import f.inputs.nixpkgs { system = builtins.currentSystem; '
                'overlays = [ f.overlays.default ]; }; in ')
            offline_expression = prefix + '(import (builtins.toPath ' + json.dumps(str(archived_root / 'tests/tier2-offline.nix')) + ') { pkgs = p; '
            offline_expression += ''.join(f'{key} = builtins.storePath {json.dumps(value)}; ' for key, value in bindings.items())
            offline_expression += ('javaHome = ' + json.dumps(str(java_home)) + '; '
                'src = builtins.path { path = ' + json.dumps(str(source)) + '; name = "petclinic-tier2-source"; }; '
                'properties = builtins.path { path = ' + json.dumps(str(properties)) + '; name = "prefetch.properties"; }; '
                'wrapperHome = builtins.path { path = ' + json.dumps(str(work / 'gradle-home/wrapper')) + '; name = "wrapper-home"; }; })')
            offline_built = build_check('ordinary-offline-check', offline_expression)
            shutil.copy2(offline_built / 'receipt.json', output / 'ordinary-offline-receipt.json')
            shutil.copytree(offline_built / 'test-results', output / 'ordinary-offline-test-results')
            receipt['darwinNixIPv4Stack'] = platform.system() == 'Darwin'
            if receipt['darwinNixIPv4Stack']:
                for name in ('transport-baseline.log', 'transport-ipv4.log'):
                    shutil.copy2(offline_built / name, output / name)
            save()
            if platform.system() == 'Darwin':
                # File access remains normal. Only loopback network traffic is
                # allowed, because Gradle workers/daemons require local sockets.
                profile = work / 'offline.sb'
                profile.write_text('(version 1)\n(allow default)\n(deny network*)\n'
                    '(allow network-outbound (remote ip "localhost:*"))\n'
                    '(allow network-bind (local ip "localhost:*"))\n'
                    '(allow network-inbound (local ip "localhost:*"))\n'
                    '(allow network* (local unix-socket))\n'
                    '(allow network* (remote unix-socket))\n')
                cut = ['/usr/bin/sandbox-exec', '-f', str(profile)]
                shutil.copy2(profile, output / 'offline.sb')
            else:
                cut = ['unshare', '-rn', 'sh', '-c', 'ip link set lo up && exec "$@"', 'sh']
            run('network-positive', [sys.executable, '-c',
                'import socket\nwith socket.create_connection(("1.1.1.1",443),timeout=10):\n print("external socket reachable")'])
            run('loopback-control', cut + [sys.executable, '-c',
                'import http.server,threading,urllib.request\n'
                'class Handler(http.server.BaseHTTPRequestHandler):\n'
                ' def do_GET(self):\n'
                '  self.send_response(200);self.end_headers();self.wfile.write(b"loopback allowed")\n'
                'server=http.server.HTTPServer(("127.0.0.1",0),Handler)\n'
                'threading.Thread(target=server.serve_forever,daemon=True).start()\n'
                'with urllib.request.urlopen("http://127.0.0.1:%d/"%server.server_port,timeout=5) as r:\n'
                ' assert r.read()==b"loopback allowed"\n'
                'server.shutdown();print("loopback HTTP allowed")'])
            # Prove the isolation primitive actually denies the same socket.
            run('network-control', cut + [sys.executable, '-c',
                'import socket\ntry:\n socket.create_connection(("1.1.1.1",443),timeout=5)\n'
                'except OSError:\n print("external socket denied")\n'
                'else:\n raise SystemExit("external socket unexpectedly allowed")'])
            replay_env = {'JAVA_HOME': str(java_home),
                'JAVA_TOOL_OPTIONS': '-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=9 -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=9'}
            # This invocation is explicitly inside the test's Seatbelt cut;
            # ordinary host commands and capture keep their normal JVM stack.
            if platform.system() == 'Darwin':
                replay_env['JAVA_TOOL_OPTIONS'] += ' -Djava.net.preferIPv4Stack=true'
            receipt['hostCutIPv4Stack'] = platform.system() == 'Darwin'
            run('ordinary-test', cut + ['./gradlew', '--no-daemon', '--info', '--stacktrace', 'clean', 'test', 'bootJar'], project, replay_env)
            reports = list(project.glob('build/test-results/test/TEST-*.xml'))
            assert reports, 'the independent sample produced no test reports'
            tests = failures = skipped = 0
            for report in reports:
                suite = ET.parse(report).getroot()
                tests += int(suite.get('tests', 0))
                failures += int(suite.get('failures', 0)) + int(suite.get('errors', 0))
                skipped += int(suite.get('skipped', 0))
            assert tests > skipped and failures == 0, (tests, failures, skipped)
            receipt['ordinaryTests'] = {'tests': tests, 'failures': failures, 'skipped': skipped}
            reports_dir = output / 'test-results'
            reports_dir.mkdir()
            for report in reports:
                shutil.copy2(report, reports_dir / report.name)
            cache = work / 'gradle-home/caches/modules-2/files-2.1'
            assert not cache.exists() or not any(cache.iterdir()), 'Gradle populated its download cache'
            receipt['downloadCacheEmpty'] = True
            expression = (prefix +
                '(p.gradle-purifier.buildGradleProject { src = builtins.path { path = '
                + json.dumps(str(source)) + '; name = "petclinic-tier2-source"; }; doCheck = true; })'
                '.packages.spring-petclinic.overrideAttrs (old: { postInstall = (old.postInstall or "") + \'\'\n'
                'mkdir -p "$out/nix-support/test-results"\n'
                'find build/test-results/test -name "TEST-*.xml" -exec cp {} "$out/nix-support/test-results/" \\;\n'
                '\'\'; })')
            built = build_check('pure-build', expression)
            pure_reports = list((built / 'nix-support/test-results').glob('TEST-*.xml'))
            assert pure_reports, 'sandboxed package retained no test reports'
            pure_counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
            for report in pure_reports:
                suite = ET.parse(report).getroot()
                for key in pure_counts:
                    pure_counts[key] += int(suite.get(key, 0))
            assert pure_counts['tests'] > pure_counts['skipped'] and pure_counts['failures'] == pure_counts['errors'] == 0, pure_counts
            receipt['pureTests'] = pure_counts
            shutil.copytree(built / 'nix-support/test-results', output / 'pure-test-results')
            jar = built / 'share/archives/spring-petclinic.jar'
            assert jar.is_file() and jar.stat().st_size > 0, 'sandboxed Petclinic JAR missing'
            receipt['pureOutput'] = str(built)
            receipt['pureJarSha256'] = hashlib.sha256(jar.read_bytes()).hexdigest()
            receipt['status'] = 'passed'
    except Exception as error:
        receipt['status'] = 'failed'
        receipt['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
