#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Capture external Javadoc metadata, then replay after its server stops."""
import argparse
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cli', nargs='?')
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--gradle-version', default='8.14')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    work = args.results.resolve()
    work.mkdir(parents=True, exist_ok=False)
    source = work / 'project'
    shutil.copytree(root / 'tests/javadoc-links', source,
                    ignore=shutil.ignore_patterns('build', '.gradle', '.gradle-purifier', '*lock.json'))
    home = work / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), XDG_CACHE_HOME=str(home / 'cache'),
               GRADLE_USER_HOME=str(work / 'gradle'))
    state = {'requests': [], 'contents': b'external\n'}
    outcomes = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state['requests'].append(self.path)
            self.send_response(200 if self.path == '/api/package-list' else 404)
            self.end_headers()
            if self.path == '/api/package-list':
                self.wfile.write(state['contents'])

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    link = f'http://127.0.0.1:{server.server_address[1]}/api/'
    wrapper = source / 'gradle/wrapper/gradle-wrapper.properties'
    wrapper.write_text(f'distributionUrl=https\\://services.gradle.org/distributions/gradle-{args.gradle_version}-bin.zip\n')
    gradle_args = [f'-PdocsUrl={link}']

    def run(stage, command, environment=env, success=True, contains=None, directory=None):
        log = work / (stage + '.log')
        result = subprocess.run(list(map(str, command)), cwd=directory or source, env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        log.write_text(result.stdout)
        outcomes.append({'stage': stage, 'command': list(map(str, command)),
                         'exitCode': result.returncode, 'log': str(log), 'cwd': str(directory or source)})
        (work / 'commands.json').write_text(json.dumps(outcomes, indent=2) + '\n')
        if (result.returncode == 0) != success or (contains and contains not in result.stdout):
            raise AssertionError(f'{stage}: unexpected result; see {log}')
        print(stage, 'pass', flush=True)
        return result.stdout

    cli = Path(args.cli).resolve() if args.cli else Path(run('cli', [
        'nix', 'build', str(root) + '#default', '--no-link', '--print-out-paths'
    ]).strip().splitlines()[-1]) / 'bin/gradle-purifier'
    # The command keeps the caller's installed runtime; select one explicitly.
    sys.path.insert(0, str(root / 'tests'))
    from fixture_jvm import fixture_jvm
    env['JAVA_HOME'] = fixture_jvm(cli, 21, work / 'fixture-jvm')['home']
    try:
        run('capture', [cli, 'lock', '--', *gradle_args])
        lock_path = source / 'gradle-purifier.lock.json'
        lock = json.loads(lock_path.read_text())
        record = lock['resources'][0]
        assert len(lock['resources']) == 1 and record['linkUrl'] == link
        assert record['artifactUrl'] == link + 'package-list'
        assert record['kind'] == 'javadoc-link-metadata'
        before = lock_path.read_bytes()
        requests = len(state['requests'])
        run('relock', [cli, 'lock', '--relock', '--', *gradle_args])
        assert lock_path.read_bytes() == before and len(state['requests']) == requests
        run('prefetch', [cli, 'prefetch'])
        gradle = source / '.gradle-purifier/distribution/bin/gradle'
        ordinary = dict(env, GRADLE_USER_HOME=str(work / 'ordinary-gradle'))
        run('ordinary-javadoc', [gradle, '--no-daemon', *gradle_args, 'javadoc'], ordinary)
        original = (source / 'build/docs/javadoc/sample/Example.html').read_bytes()
        assert (link + 'external/Library.html').encode() in original
        server.shutdown()
        server.server_close()
        run('offline-javadoc', [gradle, '--no-daemon', '--offline', '--rerun-tasks', *gradle_args, 'javadoc'])
        replay = (source / 'build/docs/javadoc/sample/Example.html').read_bytes()
        assert original == replay, 'native Javadoc HTML changed in replay'
        assert b'file:/nix/store/' not in replay
        requests = len(state['requests'])
        run('relock-server-stopped', [cli, 'lock', '--relock', '--', *gradle_args])
        assert lock_path.read_bytes() == before and len(state['requests']) == requests
        cold_source = work / 'cold-project'
        shutil.copytree(root / 'tests/javadoc-links', cold_source)
        shutil.copyfile(wrapper, cold_source / 'gradle/wrapper/gradle-wrapper.properties')
        cold_lock = cold_source / 'gradle-purifier.lock.json'
        cold_lock.write_bytes(before)
        cold_home = work / 'cold-home'
        cold_home.mkdir()
        cold_gradle_home = work / 'cold-gradle-home'
        cold_env = dict(env, HOME=str(cold_home), XDG_CACHE_HOME=str(cold_home / 'cache'),
                        GRADLE_USER_HOME=str(cold_gradle_home))
        assert not cold_gradle_home.exists()
        assert not any((cold_source / name).exists() for name in ['.gradle', '.gradle-purifier', 'build'])
        run('cold-relocated-relock-server-stopped', [cli, 'lock', '--relock', '--', *gradle_args],
            cold_env, directory=cold_source)
        assert cold_lock.read_bytes() == before and len(state['requests']) == requests
        project_expr = ('let f=builtins.getFlake ' + json.dumps(str(root)) +
                        '; p=import f.inputs.nixpkgs {system=builtins.currentSystem;overlays=[f.overlays.default];}; '
                        'in p.gradle-purifier.buildGradleProject {src=' + str(source) + ';}')
        package = Path(run('sandbox-publication', ['nix', 'build', '--impure', '--no-link',
            '--print-out-paths', '--expr', '(' + project_expr +
            ').packages.javadoc-links.maven']).strip().splitlines()[-1])
        jar = package / 'nix-support/artifacts/maven/javadoc-links-javadoc.jar'
        with zipfile.ZipFile(jar) as archive:
            assert (link + 'external/Library.html').encode() in archive.read('sample/Example.html')
        properties = dict(line.split('=', 1) for line in
                          (source / '.gradle/purifier/prefetch.properties').read_text().splitlines()
                          if '=' in line and not line.startswith('#'))
        init = properties['initScript']
        repository = properties['gradle.purifier.repository']
        role_file = work / 'roles.json'
        negative_env = dict(env, GRADLE_PURIFIER_MODE='interactive',
                            GRADLE_PURIFIER_REPOSITORY=repository,
                            GRADLE_PURIFIER_RESOURCES=str(role_file))
        command = [gradle, '--no-daemon', '--offline', '--rerun-tasks', '--init-script', init,
                   *gradle_args, 'purifierJavadocLinkMetadata']
        role_file.write_text('[]')
        run('missing-role', command, negative_env, success=False, contains='not locked')
        for name, url in [('non-native-path', 'file:///tmp/package-list'),
                          ('credential', 'https://token@example.invalid/package-list')]:
            role_file.write_text(json.dumps([dict(record, artifactUrl=url)]))
            run(name, command, negative_env, success=False, contains='unsupported Javadoc link URL')
        malformed_roles = [
            ('duplicate-identical-role', [record, dict(record)], 'ambiguous Javadoc metadata roles'),
            ('duplicate-conflicting-role', [record, dict(record, hash='sha256-' + 'A' * 43 + '=')], 'ambiguous Javadoc metadata roles'),
            ('unknown-role-attribute', [dict(record, unexpected=True)], 'invalid external resource role'),
            ('malformed-role-hash', [dict(record, hash='not-a-hash')], 'invalid external resource role'),
        ]
        for name, link_value in [('query-link', 'https://example.invalid/api/?v=1'),
                                 ('traversal-link', 'https://example.invalid/api/../'),
                                 ('encoded-traversal-link', 'https://example.invalid/api/%2e%2e/'),
                                 ('invalid-port-link', 'http://127.0.0.1:65536/api/'),
                                 ('unsupported-ipv6-link', 'http://[::1]:8080/api/'),
                                 ('malformed-ipv6-link', 'http://[:::]/api/')]:
            malformed_roles.append((name, [dict(record, linkUrl=link_value,
                artifactUrl=link_value + 'package-list')], 'unsupported Javadoc link URL'))
        for name, records, diagnostic in malformed_roles:
            role_file.write_text(json.dumps(records))
            run(name, command, negative_env, success=False, contains=diagnostic)
        role_file.write_text(json.dumps([record]))
        bad_tree = work / 'bad-repository'
        bad_tree.mkdir()
        negative_env['GRADLE_PURIFIER_REPOSITORY'] = bad_tree.as_uri()
        run('missing-bytes', command, negative_env, success=False, contains='metadata is missing')
        uri = urlsplit(record['artifactUrl'])
        bad_file = bad_tree / uri.scheme / uri.netloc / uri.path.lstrip('/')
        bad_file.parent.mkdir(parents=True)
        bad_file.write_bytes(b'tampered\n')
        run('tampered-bytes', command, negative_env, success=False, contains='hash mismatch')
        role_file.write_text(json.dumps([dict(record, artifactUrl=link + 'extra/package-list')]))
        run('non-native-metadata-location', command, negative_env, success=False, contains='does not name native link metadata')
        url_tree = work / 'url-control-repository'
        url_tree.mkdir()
        url_env = dict(negative_env, GRADLE_PURIFIER_REPOSITORY=url_tree.as_uri())
        for name, suffix in [('escaped-space-directory', 'escaped%20space/'),
                             ('literal-percent-directory', 'literal%2520percent/')]:
            url_link = link + suffix
            role = dict(record, linkUrl=url_link, artifactUrl=url_link + 'package-list')
            role_file.write_text(json.dumps([role]))
            uri = urlsplit(role['artifactUrl'])
            metadata_file = url_tree / uri.scheme / uri.netloc / unquote(uri.path).lstrip('/')
            metadata_file.parent.mkdir(parents=True)
            metadata_file.write_bytes(b'external\n')
            url_command = [f'-PdocsUrl={url_link}' if str(arg).startswith('-PdocsUrl=') else arg
                           for arg in command]
            run(name, url_command, url_env)
            assert (source / 'build/purifier-javadoc/javadoc/0/package-list').read_bytes() == b'external\n'
        server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
        state['contents'] = b'external\nfreshpackage\n'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        requests = len(state['requests'])
        run('relock-pins-documentation', [cli, 'lock', '--relock', '--', *gradle_args])
        assert lock_path.read_bytes() == before and len(state['requests']) == requests
        run('update-refreshes-documentation', [cli, 'update'])
        updated = json.loads(lock_path.read_text())
        assert updated['resources'][0]['hash'] != record['hash']
        assert len(state['requests']) > requests
        run('prefetch-updated', [cli, 'prefetch'])
        server.shutdown()
        server.server_close()
        run('offline-after-update', [gradle, '--no-daemon', '--offline', '--rerun-tasks', *gradle_args, 'javadoc'])
        assert (source / 'build/purifier-javadoc/javadoc/0/package-list').read_bytes() == state['contents']
        (work / 'report.json').write_text(json.dumps({'passed': True, 'requests': state['requests'],
            'htmlByteIdentical': True, 'coldRelocatedRelockByteIdentical': True,
            'coldProjectStateFresh': True, 'coldMetadataServerRequestsUnchanged': True, 'resources': lock['resources'], 'updatedResources': updated['resources'],
            'commands': outcomes}, indent=2) + '\n')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
