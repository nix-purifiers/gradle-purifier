#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Prove a genuine compiler-capability error using ordinary native Gradle."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gradle', type=Path, required=True)
parser.add_argument('--jre', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
work = args.output.resolve()
work.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('local_jvms', ROOT / 'pkgs/build-gradle-project/local-jvms.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)
jre = args.jre.resolve()
runtime = observer.observe(jre)
assert 'javac' not in runtime['tools'], 'this control requires a real JRE without javac'
source = work / 'project'
(source / 'src/main/java').mkdir(parents=True)
(source / 'settings.gradle').write_text("rootProject.name='native-missing-compiler'\n")
(source / 'build.gradle').write_text(
    "plugins { id 'java' }\njava { toolchain { languageVersion = JavaLanguageVersion.of(" +
    str(runtime['javaMajor']) + ") } }\n")
(source / 'gradle.properties').write_text(
    'org.gradle.java.installations.fromEnv=NATIVE_JRE_ONLY\n'
    'org.gradle.java.installations.auto-detect=false\n'
    'org.gradle.java.installations.auto-download=false\n')
(source / 'src/main/java/Main.java').write_text('public class Main {}\n')
for name in ['tmp', 'cache', 'gradle-home']:
    (work / name).mkdir()
env = dict(os.environ, JAVA_HOME=str(jre), NATIVE_JRE_ONLY=str(jre),
           GRADLE_USER_HOME=str(work / 'gradle-home'), XDG_CACHE_HOME=str(work / 'cache'),
           TMPDIR=str(work / 'tmp'), TMPPREFIX=str(work / 'tmp/prefix-'),
           JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2 -Djava.io.tmpdir=' + str(work / 'tmp'))
for name in list(env):
    if name.startswith('GRADLE_PURIFIER_'):
        env.pop(name)
if hasattr(os, 'sched_getaffinity'):
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
command = [str(args.gradle), '--offline', '--no-daemon', '--max-workers=2', 'compileJava']
report = {'scope': 'ordinary native compiler capability negative; no purifier init or runtime allowlist',
          'status': 'running', 'runtime': runtime, 'argv': command, 'deadlineSeconds': 300}


def save():
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


try:
    save()
    started = time.monotonic()
    logfile = work / 'native.log'
    with logfile.open('w') as log:
        process = subprocess.Popen(command, cwd=source, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise AssertionError('native capability test exceeded its deadline')
    text = logfile.read_text()
    report.update(exitCode=code, elapsedSeconds=round(time.monotonic() - started, 3),
                  logSha256=hashlib.sha256(logfile.read_bytes()).hexdigest())
    assert code != 0 and "property 'javaCompiler'" in text and 'Cannot find a Java installation' in text, 'native Gradle did not identify missing compiler capability'
    assert 'languageVersion=' + str(runtime['javaMajor']) in text
    info_command = command[:-1] + ['javaToolchains']
    with (work / 'toolchains.log').open('w') as log:
        process = subprocess.Popen(info_command, cwd=source, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            info_code = process.wait(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise AssertionError('native toolchain inspection exceeded its deadline')
    info = (work / 'toolchains.log').read_text()
    assert info_code == 0 and str(jre) in info and re.search(r'Is JDK:\s+false', info), 'native toolchain report must recognize the installed JRE without compiler'
    report.update(toolchainReportSha256=observer.digest(work / 'toolchains.log'), nativeRecognizedJre=True)
    assert not (work / 'gradle-home/init.d').exists()
    assert not (source / 'build/classes/java/main/Main.class').exists()
    report.update(status='passed', compilerCapabilityRejectedByNativeGradle=True, purifierInitInstalled=False)
except BaseException as error:
    report.update(status='failed', error=str(error))
    raise
finally:
    save()
