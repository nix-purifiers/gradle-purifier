#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_jvm import bind_fixture_sdks
from fixture_build import command as fixture_build
import argparse,hashlib,importlib.util,json,os,shutil,subprocess,zipfile
sys.dont_write_bytecode=True
R=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description='Original unchanged Gradle6.9.4 legacy uploadArchives lifecycle')
for key in ['cli','sdks','cache','output']:parser.add_argument('--'+key,type=Path,required=True)
args=parser.parse_args();O=args.output.resolve();O.mkdir();sdks=json.loads(args.sdks.read_text());cli=args.cli.resolve();assert cli.is_file()
profile=next(p for p in json.loads((R/'tests/epoch-probes/profiles.json').read_text())['profiles'] if p['id']=='gradle-6.9.4');assert profile['publication']=='legacy-upload'
spec=importlib.util.spec_from_file_location('epoch',R/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);gradle=epoch.distribution(profile,args.cache.resolve())
env=dict(os.environ,JAVA_HOME=sdks['java11']['home'],XDG_CACHE_HOME=str(O/'cache'),GRADLE_USER_HOME=str(O/'gradle-home'));env['PATH']=sdks['java11']['home']+'/bin:'+env['PATH'];stages=[];projects={};outcome=dict(status='running',scope='One unchanged existing6.9.4/java11/java8 control, original legacy uploadArchives publication; no version range claim')
context=dict(sourceRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip(),baselineMain='ed6857830b719861d90550d0fcf15975b9b0c423',cli=str(cli),profile=profile,sdks={k:sdks[k] for k in ['java8','java11']},sourceHashes={p:hashlib.sha256((R/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=R,text=True).splitlines() if (R/p).is_file()});(O/'context.json').write_text(json.dumps(context,indent=2)+'\n')
class StopObservation(Exception):pass
def flush():
 outcome['stages']=stages;outcome['fixtureSourcePreserved']=all(hashlib.sha256((project/p).read_bytes()).hexdigest()==h for project,files in projects.items() for p,h in files.items());(O/'report.json').write_text(json.dumps(outcome,indent=2)+'\n')
def fixture(name):
 p=O/name;epoch.fixture(p,profile,sdks);bind_fixture_sdks(p,11,[8],cli,sdks);projects[p]={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() for f in p.rglob('*') if f.is_file()};return p
def run(name,command,cwd,extra=None,kind='purifier'):
 command=[str(c) for c in command]
 with (O/(name+'.log')).open('w') as log:proc=subprocess.run(command,cwd=cwd,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT,timeout=300)
 row=dict(stage=name,command=command,exitCode=proc.returncode,kind=kind);stages.append(row);flush();print(name,proc.returncode,flush=True)
 if proc.returncode:
  outcome.update(status=kind+'-failed',failedStage=name,rawLog=str(O/(name+'.log')));flush();raise StopObservation()
 return (O/(name+'.log')).read_text()
def consumer(label,jar):
 p=O/label;p.mkdir();(p/'Consumer.java').write_text('public class Consumer { public static void main(String[] args) { if(example.Epoch.answer()!=42) throw new AssertionError(); } }\n');run(label+'-compile',[Path(sdks['java8']['home'])/'bin/javac','-cp',jar,'Consumer.java'],p,kind='native' if label.startswith('native') else 'purifier');run(label+'-execute',[Path(sdks['java8']['home'])/'bin/java','-cp',str(p)+os.pathsep+str(jar),'Consumer'],p,kind='native' if label.startswith('native') else 'purifier')
def mainclass(jar):
 with zipfile.ZipFile(jar) as z:return z.read('example/Epoch.class')
try:
 native=fixture('native');run('native-version',['bash',gradle,'--version'],native,kind='native');run('native-build-check-upload',['bash',gradle,'--no-daemon','clean','check','uploadArchives'],native,kind='native');jar=native/'repository/example/epoch/1.0/epoch-1.0.jar';assert int.from_bytes(mainclass(jar)[6:8],'big')==52;consumer('native-consumer',jar)
 project=fixture('capture');run('purifier-lock',[cli,'lock','--bind-sdks'],project);lock=project/'gradle-purifier.lock.json';first=lock.read_bytes();(O/'captured-lock.json').write_bytes(first);run('purifier-relock',[cli,'lock','--bind-sdks','--relock'],project);assert lock.read_bytes()==first
 cold=fixture('cold');(cold/'gradle-purifier.lock.json').write_bytes(first);cold_home=O/'cold-home';cold_home.mkdir();cold_env=dict(GRADLE_USER_HOME=str(O/'cold-gradle-home'),XDG_CACHE_HOME=str(cold_home/'cache'));run('cold-relocated-relock',[cli,'lock','--bind-sdks','--relock'],cold,cold_env);assert (cold/'gradle-purifier.lock.json').read_bytes()==first;run('cold-prefetch',[cli,'prefetch'],cold,cold_env);assert not (Path(cold_env['GRADLE_USER_HOME'])/'caches').exists();assert not (cold/'build').exists();unshare=shutil.which('unshare');assert unshare and shutil.which('ip');run('cold-network-namespace-build-check',[unshare,'-rn','sh','-c','ip link set lo up && exec "$@"','legacy-offline','bash',cold/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','clean','check'],cold,cold_env)
 expr='''let f=builtins.getFlake ("path:"+builtins.getEnv "LEGACY_SOURCE");p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "LEGACY_PROJECT";}).ATTR''';nixenv=dict(cold_env,LEGACY_SOURCE=str(R),LEGACY_PROJECT=str(cold));run('sandbox-tests',fixture_build(cli,cold,'tests'),cold,nixenv);release=Path(run('nix-release',fixture_build(cli,cold,'release'),cold,nixenv).strip().splitlines()[-1]);nixJar=release/'epoch/example/epoch/1.0/epoch-1.0.jar';nixPom=release/'epoch/example/epoch/1.0/epoch-1.0.pom';assert nixJar.is_file() and nixPom.is_file();assert int.from_bytes(mainclass(nixJar)[6:8],'big')==52;expected={hashlib.sha256(nixJar.read_bytes()).hexdigest()};outcome['nixReleaseJarSha256']=next(iter(expected));outcome['nixReleasePomSha256']=hashlib.sha256(nixPom.read_bytes()).hexdigest()
 if (cold/'build').exists():shutil.rmtree(cold/'build')
 assert not (cold/'build').exists();guard=O/'publish-compile-sentinel.init.gradle';guard.write_text("gradle.projectsEvaluated { gradle.rootProject.allprojects { p -> p.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { doFirst { throw new GradleException('NATIVE_COMPILATION_DURING_LEGACY_PUBLISH') } } } }\n");run('purifier-publish-original-uploadArchives',[cli,'publish','uploadArchives','--init-script',guard],cold,cold_env)
 deployed=[]
 for p in (cold/'repository/example/epoch').glob('*/*.jar'):
  try:mainclass(p)
  except KeyError:continue
  deployed.append(p)
 assert len(deployed)==1,deployed;actual=hashlib.sha256(deployed[0].read_bytes()).hexdigest();outcome['deployedJarSha256']=actual
 if actual not in expected:outcome.update(status='purifier-failed',failedStage='legacy-deployed-jar-is-not-nix-release');flush();raise StopObservation()
 projection=(cold/'.gradle-purifier/publication').resolve();catalog=json.loads((projection/'catalog.json').read_text());module=next(m for m in catalog['modules'] if m['group']=='example' and m['name']=='epoch');version=module['version'];projected=projection/'repository/example/epoch'/version;assert deployed[0].name=='epoch-'+version+'.jar';assert deployed[0].read_bytes()==(projected/deployed[0].name).read_bytes();assert (deployed[0].parent/('epoch-'+version+'.pom')).read_bytes()==(projected/('epoch-'+version+'.pom')).read_bytes();consumer('purified-consumer',deployed[0]);outcome.update(status='passed',legacyTask='uploadArchives',nixArtifactActuallyDeployed=True,projectedVersion=version)
except StopObservation:pass
except Exception as error:outcome.update(status='controller-error',error=type(error).__name__+': '+str(error));raise
finally:flush()
raise SystemExit(0 if outcome['status']=='passed' else 1)
