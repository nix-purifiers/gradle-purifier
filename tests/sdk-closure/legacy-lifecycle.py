#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One unchanged existing 7.6.6/manager17/compiler8/runtime8 lifecycle control."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
from fixture_jvm import bind_fixture_sdks
from fixture_build import command as fixture_build
parser=argparse.ArgumentParser(description=__doc__)
for name in ['cli','cache','sdks','output']:parser.add_argument('--'+name,type=Path,required=True)
args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
sdks=json.loads(args.sdks.read_text());cli=args.cli.resolve()
spec=importlib.util.spec_from_file_location('epoch',ROOT/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch)
profile=next(p for p in json.loads((ROOT/'tests/epoch-probes/profiles.json').read_text())['profiles'] if p['id']=='gradle-7.6.6')
args.cache=args.cache.resolve();args.cache.mkdir(parents=True,exist_ok=True);gradle=epoch.distribution(profile,args.cache)
context=dict(scope='One existing fixture/profile only; skipped Test supplies selection, not runtime execution credit',profile=profile,sdks=sdks,
 sourceRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),cli=str(cli),
 sourceHashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines() if (ROOT/p).is_file()})
(out/'context.json').write_text(json.dumps(context,indent=2)+'\n');stages=[]
env=dict(os.environ,JAVA_HOME=sdks['java17']['home'],HOME=str(out/'home'),GRADLE_USER_HOME=str(out/'gradle-home'));Path(env['HOME']).mkdir()
def run(stage,command,cwd,extra=None):
 command=[str(x) for x in command]
 with (out/(stage+'.log')).open('w') as log:r=subprocess.run(command,cwd=cwd,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT,timeout=300)
 stages.append(dict(stage=stage,command=command,exitCode=r.returncode));(out/'summary.json').write_text(json.dumps(dict(scope=context['scope'],stages=stages),indent=2)+'\n');print(stage,r.returncode,flush=True);assert r.returncode==0,stage
 return (out/(stage+'.log')).read_text()
def class_major(jar):
 with zipfile.ZipFile(jar) as z:return int.from_bytes(z.read('example/Epoch.class')[6:8],'big')
def consumer(label,jar):
 project=out/label;project.mkdir();(project/'Consumer.java').write_text('public class Consumer { public static void main(String[] a) { if(example.Epoch.answer()!=42) throw new AssertionError(); } }\n')
 run(label+'-compile',[Path(sdks['java8']['home'])/'bin/javac','-cp',jar,'Consumer.java'],project)
 run(label+'-execute',[Path(sdks['java8']['home'])/'bin/java','-cp',str(project)+os.pathsep+str(jar),'Consumer'],project)
native=out/'native';epoch.fixture(native,profile,sdks)
run('native-build-check-publish',['bash',gradle,'--no-daemon','clean','check','publish'],native)
native_jar=native/'repository/example/epoch/1.0/epoch-1.0.jar';assert class_major(native_jar)==52;consumer('native-consumer',native_jar)
project=out/'capture';epoch.fixture(project,profile,sdks);bind_fixture_sdks(project,17,[8],cli,sdks);originals={str(p.relative_to(project)):p.read_bytes() for p in project.rglob('*') if p.is_file()}
run('capture',[cli,'lock','--bind-sdks'],project);lock=project/'gradle-purifier.lock.json';first=lock.read_bytes();data=json.loads(first);sdk_context=data['projectJdks']['x86_64-linux'];assert sdk_context['version']=='jvm-sdk-context-v2';assert not any(r['task']==':test' for r in sdk_context['roles'])
observations=json.loads((project/'.gradle-purifier/lock-run/output/task-sdk-observations.json').read_text());assert observations[':epochTest']['executed'] and not observations[':epochTest']['skipped'] and not observations[':epochTest']['failed'];assert ':test' not in observations and ':test SKIPPED' in (out/'capture.log').read_text()
(out/'captured-lock.json').write_bytes(first)
(out/'task-sdks.json').write_bytes((project/'.gradle-purifier/lock-run/output/task-sdks.json').read_bytes())
(out/'task-sdk-observations.json').write_text(json.dumps(observations,indent=2)+'\n')
run('relock',[cli,'lock','--bind-sdks','--relock'],project);assert lock.read_bytes()==first
cold=out/'cold';epoch.fixture(cold,profile,sdks);bind_fixture_sdks(cold,17,[8],cli,sdks);(cold/'gradle-purifier.lock.json').write_bytes(first)
cold_home=out/'cold-home';cold_home.mkdir();cold_env=dict(HOME=str(cold_home),GRADLE_USER_HOME=str(out/'cold-gradle-home'),XDG_CACHE_HOME=str(cold_home/'cache'))
run('cold-relocated-relock',[cli,'lock','--bind-sdks','--relock'],cold,cold_env);assert (cold/'gradle-purifier.lock.json').read_bytes()==first
run('cold-prefetch',[cli,'prefetch'],cold,cold_env)
assert not (Path(cold_env['GRADLE_USER_HOME'])/'caches').exists(),'native Gradle cache was warm before the cold offline control'
assert not (cold/'build').exists(),'project outputs were warm before the cold offline control'
unshare=shutil.which('unshare');ip=shutil.which('ip');assert unshare and ip,'network namespace tools required'
run('namespace-available',[unshare,'-rn','sh','-c','ip link set lo up'],cold,cold_env)
run('cold-network-namespace-build-check',[unshare,'-rn','sh','-c','ip link set lo up && exec "$@"','sdk-offline','bash',cold/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','clean','check'],cold,cold_env)
assert 'EPOCH TEST PASSED' in (out/'cold-network-namespace-build-check.log').read_text()
expression='''let f=builtins.getFlake ("path:"+builtins.getEnv "LEGACY_SDK_SOURCE");
 p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};
 in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "LEGACY_SDK_PROJECT";}).tests'''
run('sandbox-tests',fixture_build(cli,cold,'tests'),cold,dict(cold_env,LEGACY_SDK_SOURCE=str(ROOT),LEGACY_SDK_PROJECT=str(cold)))
run('publish',[cli,'publish','publishProofPublicationToMavenRepository'],cold,cold_env)
jars=list((cold/'repository/example/epoch').glob('*/*-nix-*.jar'));assert len(jars)==1,jars;jar=jars[0];assert class_major(jar)==52;consumer('purified-consumer',jar)
with zipfile.ZipFile(native_jar) as a,zipfile.ZipFile(jar) as b:assert a.read('example/Epoch.class')==b.read('example/Epoch.class')
shutil.copyfile(native_jar,out/'native-epoch.jar');shutil.copyfile(jar,out/'published-epoch.jar')
assert all((project/p).read_bytes()==v and (cold/p).read_bytes()==v for p,v in originals.items())
run('stop-test-owned-daemon',['bash',cold/'.gradle-purifier/distribution/bin/gradle','--offline','--stop'],cold,cold_env)
(out/'result.json').write_text(json.dumps(dict(passed=True,profile=profile,stages=len(stages),classMajor=52,sourcePreserved=True,relockByteIdentical=True,coldRelocatedRelockByteIdentical=True,coldGradleCacheEmpty=True,networkNamespace=True,assertingJava8TaskExecuted=True,ordinaryJava8ConsumersExecuted=True,disabledTestRuntimeExecutionClaim=False,publicationJarSha256=hashlib.sha256(jar.read_bytes()).hexdigest()),indent=2)+'\n')
