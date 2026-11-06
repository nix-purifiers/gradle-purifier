#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Replay the exact failing port-bearing lock without relocking or an origin."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess
p=argparse.ArgumentParser()
for k in ['cli','project','sdks','output']:p.add_argument('--'+k,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir();project=out/'project';project.mkdir();sdk=json.loads(a.sdks.read_text());original=a.project.resolve()
for f in ['build.gradle','settings.gradle','gradle-purifier.lock.json']:(project/f).write_bytes((original/f).read_bytes())
for d in ['src','gradle']:
 if (original/d).exists():shutil.copytree(original/d,project/d)
lock=(project/'gradle-purifier.lock.json').read_bytes();sourcehash={str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in project.rglob('*') if f.is_file()};home=out/'home';home.mkdir();env=dict(os.environ,HOME=str(home),JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(out/'gradle-home'),XDG_CACHE_HOME=str(home/'cache'));rows=[]
def run(name,cmd,extra=None):
 cmd=list(map(str,cmd))
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=project,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT,timeout=300)
 rows.append(dict(stage=name,command=cmd,exitCode=r.returncode));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert r.returncode==0,(out/(name+'.log')).read_text()[-4000:];return (out/(name+'.log')).read_text()
run('prefetch-old-lock',[a.cli.resolve(),'prefetch']);assert not (Path(env['GRADLE_USER_HOME'])/'caches').exists() and not (project/'build').exists();diagnostic=out/'classpath.init.gradle';diagnostic.write_text("allprojects { p -> p.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { t -> t.doFirst { new File(System.getenv('PORT_CLASSPATH')).text=groovy.json.JsonOutput.toJson(t.classpath.files*.absolutePath) } } }")
run('cold-network-namespace',[shutil.which('unshare'),'-rn','sh','-c','ip link set lo up && exec "$@"','old-lock-port','bash',project/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','--init-script',diagnostic,'clean','check'],dict(PORT_CLASSPATH=str(out/'actual-classpath.json')));shutil.copyfile(project/'build/tmp/compileJava/java-compiler-args.txt',out/'actual-javac-args.txt');paths=json.loads((out/'actual-classpath.json').read_text());helper=next(Path(x) for x in paths if x.endswith('helper-1.0.jar'));assert '.gradle-purifier-replay-v1' in str(helper) and ':' not in str(helper);assert (project/'gradle-purifier.lock.json').read_bytes()==lock
expr='''let f=builtins.getFlake ("path:"+builtins.getEnv "PORT_SOURCE");p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "PORT_PROJECT";}).ATTR''';run('pure-nix-tests',['nix','build','--no-link','--impure','--print-build-logs','--expr',expr.replace('ATTR','tests')],dict(PORT_SOURCE=str(root),PORT_PROJECT=str(project)))
for f,h in sourcehash.items():assert hashlib.sha256((project/f).read_bytes()).hexdigest()==h
(out/'context.json').write_text(json.dumps(dict(scope='Existing exact6.9 legacy producer source+lock only: cold compile/check and Nix tests, no new publication claim',sourcePreserved=True,lockByteIdentical=True,lockSha256=hashlib.sha256(lock).hexdigest(),helperSha256=hashlib.sha256(helper.read_bytes()).hexdigest(),actualClasspath=paths,originUnavailable=True,sdks=sdk,sourceHashes=sourcehash),indent=2));print('OLD_LOCK_PORT_REPLAY_PASS',flush=True)
