#!/usr/bin/env python3
"""Measure native Gradle controls separately from unchanged purifier capture.

Failures describe only this fixture/profile; they never establish a version floor.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def distribution(profile, cache):
    archive = cache / ('gradle-' + profile['gradle'] + '.zip')
    if not archive.exists():
        temporary = archive.with_suffix('.partial')
        with urllib.request.urlopen(profile['gradleUrl'], timeout=60) as response, temporary.open('wb') as target:
            shutil.copyfileobj(response, target)
        temporary.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != profile['gradleSha256']:
        raise ValueError('Gradle distribution SHA256 mismatch: ' + str(archive))
    directory = cache / ('gradle-' + profile['gradle'])
    if not directory.exists():
        with zipfile.ZipFile(archive) as z:
            for member in z.infolist():
                if member.filename.startswith('/') or '..' in Path(member.filename).parts:
                    raise ValueError('unsafe Gradle distribution member')
            z.extractall(cache)
    executable = directory / 'bin/gradle'
    executable.chmod(executable.stat().st_mode | 0o111)
    return executable


def fixture(project, profile, sdks):
    compiler = sdks[profile['projectCompiler']]['home']
    runtime = sdks[profile['projectRuntime']]['home']
    major = int(profile['gradle'].split('.')[0])
    target = profile['projectCompiler'].replace('java', '')
    project.mkdir()
    (project / 'settings.gradle').write_text("rootProject.name = 'epoch'\n")
    (project / 'gradle/wrapper').mkdir(parents=True)
    (project / 'gradle/wrapper/gradle-wrapper.properties').write_text(
        'distributionUrl=' + profile['gradleUrl'].replace(':', '\\:') + '\n')
    build = """apply plugin: 'java'
group = 'example'
version = '1.0'
sourceCompatibility = '1.TARGET'
targetCompatibility = '1.TARGET'
compileJava.options.fork = true
compileJava.options.forkOptions.executable = 'COMPILER/bin/javac'
compileTestJava.options.fork = true
compileTestJava.options.forkOptions.executable = 'COMPILER/bin/javac'
task epochTest(type: JavaExec, dependsOn: testClasses) {
    MAIN
    classpath = sourceSets.test.runtimeClasspath
    executable = 'RUNTIME/bin/java'
}
check.dependsOn epochTest
// This fixture uses an asserting JavaExec check, not a JUnit test framework.
test.enabled = false
""".replace('TARGET', target).replace('COMPILER', compiler).replace('RUNTIME', runtime)
    build = build.replace('MAIN', "mainClass = 'example.EpochCheck'" if major >= 9 else "main = 'example.EpochCheck'")
    if major >= 9:
        build = build.replace("sourceCompatibility = '1."+target+"'\ntargetCompatibility = '1."+target+"'",
                              "java { sourceCompatibility = '1."+target+"'; targetCompatibility = '1."+target+"' }")
    if int(target) >= 9:
        build = build.replace("'1."+target+"'", "'"+target+"'")
    if profile['publication'] == 'legacy-upload':
        build += """apply plugin: 'maven'
uploadArchives { repositories { mavenDeployer { repository(url: uri('repository')) } } }
"""
    else:
        build += """apply plugin: 'maven-publish'
publishing {
    publications { proof(MavenPublication) { from components.java } }
    repositories { maven { url = uri('repository') } }
}
"""
    if profile['gradle'] == '0.7':
        # API and task names from upstream v0.7's JavaPlugin and samples.
        build = """usePlugin 'java'
usePlugin 'maven'
group = 'example'
version = '1.0'
sourceCompatibility = 1.6
targetCompatibility = 1.6
compile.options.fork = true
compile.options.forkOptions.executable = 'COMPILER/bin/javac'
compileTests.options.fork = true
compileTests.options.forkOptions.executable = 'COMPILER/bin/javac'
test.enabled = false
task epochTest(dependsOn: 'compileTests') << {
    ant.java(classname: 'example.EpochCheck', fork: true, failonerror: true, jvm: 'RUNTIME/bin/java') {
        classpath {
            pathelement(location: compile.destinationDir)
            pathelement(location: compileTests.destinationDir)
        }
    }
}
task check(dependsOn: 'epochTest')
uploadArchives { repositories { mavenDeployer { repository(url: 'file://' + projectDir + '/repository') } } }
""".replace('COMPILER', compiler).replace('RUNTIME', runtime)
    (project / 'build.gradle').write_text(build)
    for path, text in {
        'src/main/java/example/Epoch.java': 'package example; public final class Epoch { public static int answer() { return 42; } }\n',
        'src/test/java/example/EpochCheck.java': 'package example; public final class EpochCheck { public static void main(String[] a) { if (Epoch.answer() != 42) throw new AssertionError(); System.out.println("EPOCH TEST PASSED"); } }\n',
    }.items():
        p = project/path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdks', required=True, type=Path, help='Evaluated paths/home/version records from sdks.nix')
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', action='append')
    parser.add_argument('--cli', type=Path, help='Unchanged production purifier; omit for native controls only')
    parser.add_argument('--lifecycle', action='store_true', help='After successful capture, probe replay, sandbox tests and publication')
    parser.add_argument('--timeout', type=int, default=240)
    args = parser.parse_args()
    args.cache = args.cache.resolve(); args.cache.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True)
    sdks = json.loads(args.sdks.read_text())
    profiles = json.loads((Path(__file__).with_name('profiles.json')).read_text())['profiles']
    if args.profile:
        known = {p['id'] for p in profiles}
        if not set(args.profile) <= known: parser.error('unknown profile')
        profiles = [p for p in profiles if p['id'] in args.profile]
    report = dict(sourceRevision=subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                  system=os.uname().machine+'-'+os.uname().sysname.lower(),
                  cli=str(args.cli.resolve()) if args.cli else None,
                  probeInputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                               [Path(__file__), Path(__file__).with_name('profiles.json'), Path(__file__).with_name('sdks.nix')]},
                  interpretation='Native controls and purifier observations only; no supported range or CI certification.', profiles=[])
    for profile in profiles:
        row = dict(profile=profile, sdks={role:sdks[profile[role]] for role in ['managerRuntime','projectCompiler','projectRuntime']}, stages=[])
        report['profiles'].append(row)
        logs = args.output/profile['id']; logs.mkdir(exist_ok=True)
        def run(stage, command, cwd, env):
            start = time.monotonic(); log = logs/(stage+'.log')
            with log.open('w') as stream:
                stream.write(json.dumps([str(x) for x in command])+'\n'); stream.flush()
                try:
                    process = subprocess.Popen([str(x) for x in command], cwd=cwd, env=env,
                                               stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
                    try: code = process.wait(timeout=args.timeout)
                    except subprocess.TimeoutExpired:
                        import signal
                        os.killpg(process.pid, signal.SIGKILL); process.wait(); code = 'timeout'
                except OSError as error: stream.write(str(error)); code = 'launch-error'
            result=dict(stage=stage, outcome='success' if code == 0 else 'failed', exitCode=code,
                        seconds=round(time.monotonic()-start,2), log=str(log))
            row['stages'].append(result); print(profile['id'],stage,result['outcome'],code,flush=True)
            return code == 0
        try:
            gradle = distribution(profile, args.cache)
            with tempfile.TemporaryDirectory(prefix='gradle-epoch-') as temporary:
                work = Path(temporary); project = work/'native'; fixture(project,profile,sdks)
                (logs/'fixture.gradle').write_text((project/'build.gradle').read_text())
                env = dict(os.environ, JAVA_HOME=sdks[profile['managerRuntime']]['home'],
                           GRADLE_USER_HOME=str(work/'gradle-home'), HOME=str(work/'home'))
                Path(env['HOME']).mkdir()
                env['PATH']=env['JAVA_HOME']+'/bin:'+env['PATH']
                # Historical distributions hardcode /bin/bash, absent on NixOS.
                launcher = [shutil.which('bash'), gradle]
                if not run('native-version',[*launcher,'--version'],project,env): continue
                publication = 'uploadArchives' if profile['publication']=='legacy-upload' else 'publish'
                flags = [] if profile['gradle'].startswith('0.') else ['--no-daemon']
                if run('native-build-test-publish',[*launcher,*flags,'clean','check',publication],project,env):
                    jar = project/'repository/example/epoch/1.0/epoch-1.0.jar'
                    if not jar.exists(): raise ValueError('native publication did not create expected JAR')
                    row['nativeJarSha256']=hashlib.sha256(jar.read_bytes()).hexdigest()
                    with zipfile.ZipFile(jar) as archive:
                        header = archive.read('example/Epoch.class')[:8]
                    row['nativeClassMajor'] = int.from_bytes(header[6:8], 'big')
                    if row['nativeClassMajor'] != int(profile['projectCompiler'].replace('java',''))+44:
                        raise ValueError('native JAR did not use the selected bytecode target')
                    consumer=work/'ordinary';consumer.mkdir()
                    (consumer/'Consumer.java').write_text('public final class Consumer { public static void main(String[] a) { if(example.Epoch.answer()!=42) throw new AssertionError(); } }\n')
                    compiler=Path(sdks[profile['projectCompiler']]['home']); runtime=Path(sdks[profile['projectRuntime']]['home'])
                    if run('ordinary-compile',[compiler/'bin/javac','-cp',jar,'Consumer.java'],consumer,env):
                        run('ordinary-execute',[runtime/'bin/java','-cp',str(consumer)+os.pathsep+str(jar),'Consumer'],consumer,env)
                if args.cli:
                    capture = work/'capture'; fixture(capture,profile,sdks)
                    originals = {str(p.relative_to(capture)):p.read_bytes() for p in capture.rglob('*') if p.is_file()}
                    captured = run('purifier-lock',[args.cli.resolve(),'lock'],capture,env)
                    if captured:
                        shutil.copyfile(capture/'gradle-purifier.lock.json',logs/'captured-lock.json')
                    if captured and args.lifecycle:
                        prefetched = run('purifier-prefetch',[args.cli.resolve(),'prefetch'],capture,env)
                        if prefetched:
                            run('purifier-native-offline', [shutil.which('bash'),capture/'.gradle-purifier/distribution/bin/gradle',
                                '--offline','--no-daemon','clean','check'], capture,env)
                        expression = '''let
                          f = builtins.getFlake ("path:" + builtins.getEnv "EPOCH_SOURCE");
                          p = import f.inputs.nixpkgs { system = builtins.currentSystem; overlays = [ f.overlays.default ]; };
                          sdks = import (/. + builtins.getEnv "EPOCH_SDK_FILE") { pkgs = p; };
                        in (p.gradle-purifier.buildGradleProject {
                          src = /. + builtins.getEnv "EPOCH_PROJECT";
                          toolchainJdks = [ sdks.${builtins.getEnv "EPOCH_COMPILER"} ];
                        }).tests'''
                        pure_env = dict(env,EPOCH_SOURCE=str(ROOT),EPOCH_PROJECT=str(capture),
                            EPOCH_SDK_FILE=str(Path(__file__).with_name('sdks.nix')),EPOCH_COMPILER=profile['projectCompiler'])
                        run('purifier-sandbox-tests',['nix','build','--impure','--no-link','--print-build-logs','--expr',expression],capture,pure_env)
                        published = run('purifier-publish',[args.cli.resolve(),'publish','publishProofPublicationToMavenRepository'],capture,env)
                        if published:
                            jars = list((capture/'repository/example/epoch').glob('*/*-nix-*.jar'))
                            if not jars: raise ValueError('purifier publication created no Nix-versioned JAR')
                            jar = jars[0]; row['purifierJarSha256']=hashlib.sha256(jar.read_bytes()).hexdigest()
                            consumer=work/'purified-consumer';consumer.mkdir()
                            (consumer/'Consumer.java').write_text('public final class Consumer { public static void main(String[] a) { if(example.Epoch.answer()!=42) throw new AssertionError(); } }\n')
                            compiler=Path(sdks[profile['projectCompiler']]['home']); runtime=Path(sdks[profile['projectRuntime']]['home'])
                            if run('purifier-ordinary-compile',[compiler/'bin/javac','-cp',jar,'Consumer.java'],consumer,env):
                                run('purifier-ordinary-execute',[runtime/'bin/java','-cp',str(consumer)+os.pathsep+str(jar),'Consumer'],consumer,env)
                    row['sourcePreserved'] = all((capture/path).read_bytes()==content for path,content in originals.items())
        except Exception as error:
            row['error']=type(error).__name()+': '+str(error)
            print(profile['id'],row['error'],flush=True)
        finally:
            (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0


if __name__ == '__main__': raise SystemExit(main())
