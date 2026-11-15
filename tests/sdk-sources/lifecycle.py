#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native project-owned SDK sources: compiler, JUnit, JavaExec and Javadoc."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cli', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
cli = args.cli.resolve()
source_names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
(out/'source-context.json').write_text(json.dumps(dict(
    sourceRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    sourceHashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_names if name},
    cli=str(cli), cliSha256=hashlib.sha256(cli.read_bytes()).hexdigest()),indent=2)+'\n')
variables = dict(re.findall(r'^(lib|nixpkgs|purifier_source)=(.+)$', cli.read_text(), re.M))
library = Path(variables['lib'])
helper = library / 'project-sdk-sources.py'
source = dict(type='github', owner='nix-purifiers', repo='purifier',
              rev='f444063c705056a605583bd273cc8147f57808ee',
              narHash='sha256-NaeaVq9B520WgzmRSMLGdN2zqnFlvM/wfZ8hECHhYqY=')
declaration = dict(version=1, system='x86_64-linux', manager='manager', sdks=[
    dict(id=name, source=source, selectionKind='jvm-sdk-adapter-v1',
         attribute=['lib', 'jvmSdkAdapters', 'x86_64-linux', adapter])
    for name, adapter in [('compiler', 'openjdk8'), ('manager', 'openjdk17')]])
(out / 'declaration.json').write_text(json.dumps(declaration, indent=2)+'\n')
env = dict(os.environ, XDG_CACHE_HOME=str(out/'cache'), GRADLE_USER_HOME=str(out/'gradle-home'))
stages = []


def run(name, command, project, extra=None, accepted=True):
    command = [str(x) for x in command]
    with (out/(name+'.log')).open('w') as log:
        result = subprocess.run(command, cwd=project, env=dict(env, **(extra or {})),
                                stdout=log, stderr=subprocess.STDOUT, timeout=600)
    stages.append(dict(name=name, argv=command, exitCode=result.returncode, accepted=accepted))
    (out/'stages.json').write_text(json.dumps(stages, indent=2)+'\n')
    print(name, result.returncode, flush=True)
    assert (result.returncode == 0) == accepted, (name, (out/(name+'.log')).read_text()[-5000:])
    return (out/(name+'.log')).read_text()


common = ['--helper', variables['purifier_source'], '--nixpkgs', variables['nixpkgs']]
manifest_path = run('source-acquisition', [sys.executable, helper, 'preflight', *common,
    '--declaration', out/'declaration.json', '--root', out/'sdk-root'], ROOT).strip().splitlines()[-1]
manifest = json.loads(Path(manifest_path).read_text())
sdk8_declaration = dict(declaration, manager='compiler')
(out/'sdk8-adapter.json').write_text(json.dumps(sdk8_declaration, indent=2)+'\n')
sdk8_manifest_path = run('sdk8-adapter-acquisition', [sys.executable, helper, 'preflight', *common,
    '--declaration', out/'sdk8-adapter.json', '--root', out/'sdk8-adapter-root'], ROOT).strip().splitlines()[-1]
sdk8_runtime = json.loads(Path(sdk8_manifest_path).read_text())['manager']
sdk8 = next(s['home'] for s in manifest['sdks'] if s['id'] == 'compiler')
sdk17 = next(s['home'] for s in manifest['sdks'] if s['id'] == 'manager')
project = out/'project'
(project/'src/main/java/example').mkdir(parents=True)
(project/'src/test/java/example').mkdir(parents=True)
(project/'gradle/wrapper').mkdir(parents=True)
(project/'settings.gradle').write_text("rootProject.name='source-sdk-proof'\n")
(project/'gradle/wrapper/gradle-wrapper.properties').write_text('distributionUrl=https\\://services.gradle.org/distributions/gradle-7.6.6-bin.zip\n')
(project/'gradle-purifier.sdks.json').write_bytes((out/'declaration.json').read_bytes())
(project/'build.gradle').write_text('''plugins { id 'java'; id 'maven-publish' }
group='example'; version='1.0'
repositories { mavenCentral() }
dependencies { testImplementation 'junit:junit:4.13.2' }
sourceCompatibility='1.8'; targetCompatibility='1.8'
tasks.withType(JavaCompile) {
  options.fork=true
  options.forkOptions.javaHome=file('SDK8')
  options.forkOptions.executable='SDK8/bin/javac'
}
test { executable='SDK8/bin/java'; doFirst { file('test-started').text='yes' } }
javadoc { javadocTool.set(javaToolchains.javadocToolFor { languageVersion=JavaLanguageVersion.of(8) }) }
tasks.register('verifyRuntime',JavaExec) {
  classpath=sourceSets.main.runtimeClasspath; mainClass='example.Main'; executable='SDK8/bin/java'
}
check.dependsOn verifyRuntime, javadoc
publishing {
 publications { proof(MavenPublication) { from components.java } }
 repositories { maven { url=uri('repository') } }
}
'''.replace('SDK8', sdk8))
(project/'gradle.properties').write_text('org.gradle.java.installations.auto-detect=false\norg.gradle.java.installations.auto-download=false\n'
    + 'org.gradle.java.installations.paths='+sdk8+','+sdk17+'\n')
(project/'src/main/java/example/Main.java').write_text('''package example;
/** SDK source fixture. */
public class Main {
 public static int answer() { return 42; }
 public static void main(String[] args) throws Exception {
  if (!System.getProperty("java.version").startsWith("1.8.0_")) throw new AssertionError();
  if (!new java.io.File("/proc/self/exe").getCanonicalPath().equals(EXPECTED_EXE)) throw new AssertionError();
  if (!new java.io.File(System.getProperty("java.home")).getCanonicalPath().equals(EXPECTED_HOME)) throw new AssertionError();
  if (answer()!=42) throw new AssertionError();
  System.out.println("SOURCE SDK JAVAEXEC PASSED");
 }
}
'''.replace('EXPECTED_EXE',json.dumps(sdk8_runtime['executable'])).replace('EXPECTED_HOME',json.dumps(sdk8_runtime['runtimeHome'])))
(project/'src/test/java/example/MainTest.java').write_text('''package example;
import org.junit.Test;
import static org.junit.Assert.*;
public class MainTest {
 @Test public void actualJava8Runtime() throws Exception {
  assertTrue(System.getProperty("java.version").startsWith("1.8.0_"));
  assertEquals(EXPECTED_EXE, new java.io.File("/proc/self/exe").getCanonicalPath());
  assertEquals(EXPECTED_HOME, new java.io.File(System.getProperty("java.home")).getCanonicalPath());
  assertEquals(42, Main.answer());
 }
}
'''.replace('EXPECTED_EXE',json.dumps(sdk8_runtime['executable'])).replace('EXPECTED_HOME',json.dumps(sdk8_runtime['runtimeHome'])))
originals = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob('*') if p.is_file()}
run('capture', [cli, 'lock', '--bind-sdks'], project)
lock = project/'gradle-purifier.lock.json'
first = lock.read_bytes()
context = json.loads(first)['projectJdks']['x86_64-linux']
assert context['version'] == 'jvm-sdk-context-v2' and context['manager'] == manifest['manager']
assert context['declarationHash'] == hashlib.sha256(originals['gradle-purifier.sdks.json']).hexdigest()
roles = {(r['task'], r['role']): r for r in context['roles']}
for key in [(':compileJava', 'compiler'), (':compileTestJava', 'compiler'),
            (':test', 'launcher'), (':verifyRuntime', 'launcher'), (':javadoc', 'javadoc')]:
    sdk = next(s for s in context['sdks'] if s['identityHash'] == roles[key]['sdk'])
    assert sdk['home'] == sdk8, key
observations = json.loads((project/'.gradle-purifier/lock-run/output/task-sdk-observations.json').read_text())
for task in [':compileJava', ':compileTestJava', ':test', ':verifyRuntime', ':javadoc']:
    assert observations[task]['executed'] and not observations[task]['skipped'] and not observations[task]['failed'], task
run('relock', [cli, 'lock', '--bind-sdks', '--relock'], project)
assert lock.read_bytes() == first
cold = out/'relocated'
cold.mkdir()
for name, contents in originals.items():
    destination = cold/name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contents)
(cold/'gradle-purifier.lock.json').write_bytes(first)
cold_env = dict(GRADLE_USER_HOME=str(out/'cold-gradle-home'), XDG_CACHE_HOME=str(out/'cold-cache'), JAVA_HOME=sdk17)
run('relocated-relock', [cli, 'lock', '--bind-sdks', '--relock'], cold, cold_env)
assert (cold/'gradle-purifier.lock.json').read_bytes() == first
run('prefetch', [cli, 'prefetch'], cold, cold_env)
assert not (out/'cold-gradle-home/caches').exists() and not (cold/'build').exists()
namespace = ['unshare', '-rn', 'sh', '-c', 'ip link set lo up && exec "$@"', 'sdk-source-offline']
gradle = cold/'.gradle-purifier/distribution/bin/gradle'
text = run('cold-offline-check', [*namespace, 'bash', gradle, '--offline', '--no-daemon', 'clean', 'check'], cold, cold_env)
assert 'SOURCE SDK JAVAEXEC PASSED' in text
xml = (cold/'build/test-results/test/TEST-example.MainTest.xml').read_text()
assert 'tests="1"' in xml and 'failures="0"' in xml
assert (cold/'build/docs/javadoc/example/Main.html').is_file()
run('pure-tests', [sys.executable, helper, 'build', *common, '--project', cold/'.gradle-purifier/pure-project',
    '--lock', cold/'gradle-purifier.lock.json', '--attribute', 'tests', '--root', out/'pure-tests'], cold, cold_env)
runtime_context = json.loads((cold/'.gradle-purifier/project-sdk-source-context').read_text())
requisites = set(subprocess.check_output(['nix-store','--query','--requisites',str(cold/'.gradle-purifier/project-sdk-source-context')],text=True).splitlines())
assert set(runtime_context['sourceInputs']+[sdk['output'] for sdk in context['sdks']]) <= requisites
run('publish', [cli,'publish','publishProofPublicationToMavenRepository'], cold, cold_env)
jars = list((cold/'repository/example/source-sdk-proof').glob('*/*-nix-*.jar'))
assert len(jars)==1, jars
with zipfile.ZipFile(jars[0]) as jar:
    published_class = jar.read('example/Main.class')
assert int.from_bytes(published_class[6:8],'big')==52
assert published_class == (cold/'build/classes/java/main/example/Main.class').read_bytes()
consumer = out/'ordinary-consumer'
consumer.mkdir()
(consumer/'Consumer.java').write_text('public class Consumer { public static void main(String[] a) { if(example.Main.answer()!=42) throw new AssertionError(); } }\n')
run('ordinary-consumer-compile',[Path(sdk8)/'bin/javac','-cp',jars[0],'Consumer.java'],consumer)
run('ordinary-consumer-run',[Path(sdk8)/'bin/java','-cp',str(consumer)+os.pathsep+str(jars[0]),'Consumer'],consumer)
shutil.copyfile(jars[0],out/'published.jar')
assert all((project/name).read_bytes() == data and (cold/name).read_bytes() == data for name, data in originals.items())
(out/'captured-lock.json').write_bytes(first)
(out/'result.json').write_text(json.dumps(dict(status='passed', source=source, sdk8=sdk8, managerSdk=sdk17,
    relockByteIdentical=True, relocatedRelockByteIdentical=True, nativeJUnitExecuted=True,
    nativeJavadocExecuted=True, nativeJavaExecExecuted=True, sourcePreserved=True,
    nativePublishExecuted=True, ordinaryJava8ConsumerExecuted=True, publishedClassBytesEqual=True,
    sourceClosureRetained=True,
    scope='Exact Gradle7.6.6 with published SDK8/17 adapter inputs on x86_64-linux; no range claim'), indent=2)+'\n')
