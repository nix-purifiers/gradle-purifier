#!/usr/bin/env python3
"""Independent old-profile task, metadata, SDK schema and strict-entry guards."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fixture_jvm import bind_fixture_sdks
from fixture_build import command as fixture_build
import argparse,copy,hashlib,importlib.util,json,os,shutil,subprocess
p=argparse.ArgumentParser()
for name in ['cli','sdks','cache','project','output']:p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();R=Path(__file__).resolve().parents[2];O=a.output.resolve();O.mkdir();sdk=json.loads(a.sdks.read_text());cli=a.cli.resolve();project=a.project.resolve();values=dict(x.split('=',1) for x in cli.read_text().splitlines() if x.startswith(('lib=','nixpkgs=','purifier_source=')));library=Path(values['lib']);spec=importlib.util.spec_from_file_location('epoch',R/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((R/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-5.6.4');gradle=epoch.distribution(profile,a.cache.resolve());rows=[];env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(O/'gradle-home'),XDG_CACHE_HOME=str(O/'cache'))
read_lock=library.parents[1]/'lib-overlays/default/read-lock.nix'
def run(name,cmd,cwd,reason=None,extra=None):
 cmd=[str(x) for x in cmd]
 with (O/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=cwd,env=dict(env,**(extra or {})),stdout=f,stderr=subprocess.STDOUT,timeout=300)
 text=(O/(name+'.log')).read_text();rows.append(dict(case=name,exitCode=r.returncode,expectedRejection=reason));(O/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert (r.returncode!=0)==bool(reason) and (reason is None or reason in text),text[-4500:];assert 'UNEXPECTED_GUARD_ACTION_EXECUTED' not in text
 return text
cases={
 'extra-action':("gradle.allprojects { p -> p.plugins.withId('maven') { p.tasks.getByName('uploadArchives').doLast { throw new GradleException('UNEXPECTED_GUARD_ACTION_EXECUTED') } } }",'custom Upload actions are unsupported',[]),
 'deployment-hook':("gradle.allprojects { p -> p.plugins.withId('maven') { p.afterEvaluate { p.tasks.getByName('uploadArchives').repositories.withType(org.gradle.api.artifacts.maven.MavenDeployer).all { beforeDeployment { throw new GradleException('UNEXPECTED_GUARD_ACTION_EXECUTED') } } } } }",'custom deployment hooks are unsupported',[]),
 'implicit-compiler':("gradle.allprojects { p -> p.afterEvaluate { p.tasks.getByName('compileJava').options.fork=false } }",'compiler requires an explicit forked immutable SDK',[]),
 'conflicting-compiler':("gradle.allprojects { p -> p.afterEvaluate { p.tasks.getByName('compileJava').options.forkOptions.javaHome=file('"+sdk['java11']['home']+"') } }",'compiler executable and javaHome select conflicting SDKs',[]),
 'absent-javadoc':('', 'has no selected SDK',['javadoc']),
 'enabled-onlyif-false':("gradle.allprojects { p -> p.afterEvaluate { p.tasks.getByName('test').enabled=true;p.tasks.getByName('test').onlyIf { false } } }",'cannot resolve SDK role for :test',[]),
 'unsupported-maven-publish':("gradle.allprojects { p -> p.pluginManager.apply('maven-publish') }",'Gradle5.6.4 maven-publish metadata is unsupported',[])}
for name,(body,reason,tasks) in cases.items():
 P=O/name;epoch.fixture(P,profile,sdk);bind_fixture_sdks(P,11,[8],cli,sdk);source={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file()};init=O/(name+'.init.gradle');init.write_text(body);run(name,[cli,'lock','--bind-sdks','--','--init-script',init]+tasks,P,reason);assert all(hashlib.sha256((P/n).read_bytes()).hexdigest()==h for n,h in source.items())
for name,context,reason in [('incoming-capability',"[captureMetadata:false,bindings:[[path:':',publication:[nativeMetadata:[kind:'gradle-java-component',version:1]]]]]",'supplied nativeMetadata capability is unsupported'),('strict-capture-request','[captureMetadata:true,bindings:[]]','strict native metadata capture is unsupported')]:
 P=O/name;epoch.fixture(P,profile,sdk);init=O/(name+'.init.gradle');init.write_text("gradle.ext.purifierLegacyMaven="+context+"\napply from: '"+str(library/'legacy-maven.init.gradle')+"'\n");run(name,[gradle,'--offline','--no-daemon','--init-script',init,'help'],P,reason)
# Exercise the actual common-init protocol, not only an injected helper context.
P=O/'strict-common-init-request';epoch.fixture(P,profile,sdk)
run('strict-common-init-request',[gradle,'--offline','--no-daemon','--init-script',library/'purifier.init.gradle','help'],P,'strict native metadata capture is unsupported',dict(GRADLE_PURIFIER_MODE='lock',GRADLE_PURIFIER_REPOSITORY=(O/'empty-repository').as_uri(),GRADLE_PURIFIER_OUTPUT=str(O/'strict-request-output'),GRADLE_PURIFIER_LEGACY_METADATA_CAPTURE='1',GRADLE_PURIFIER_LEGACY_PUBLICATIONS='[]'))
original=json.loads((project/'gradle-purifier.lock.json').read_text());expr='let p=import (/. + builtins.getEnv "GUARD_NIXPKGS") {};read=import (/. + builtins.getEnv "GUARD_READ_LOCK") {lib=p.lib;};in (read {src=/. + builtins.getEnv "GUARD_PROJECT";lockFile=/. + builtins.getEnv "GUARD_LOCK";}).problems';schemas=[]
for name,change,bad in [('valid',lambda d:None,False),('incoming-capability',lambda d:d['projects'][0]['publications'][0].__setitem__('nativeMetadata',dict(kind='gradle-java-component',version=1)),True),('old-unbound',lambda d:[x.__setitem__('publications',[]) for x in d['projects']],False)]:
 d=copy.deepcopy(original);change(d);payload=json.dumps({k:v for k,v in d.items() if k!='contentHash'},sort_keys=True,separators=(',',':'),ensure_ascii=False);d['contentHash']='sha256:'+hashlib.sha256(payload.encode()).hexdigest();lock=O/(name+'.lock.json');lock.write_text(json.dumps(d,indent=2));evalenv=dict(env,GUARD_NIXPKGS=values['nixpkgs'],GUARD_READ_LOCK=str(read_lock),GUARD_PROJECT=str(project),GUARD_LOCK=str(lock));r=subprocess.run(['nix','eval','--json','--impure','--expr',expr],env=evalenv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60);(O/('schema-'+name+'.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr;problems=json.loads(r.stdout);assert bool(problems)==bad,(name,problems);schemas.append(dict(case=name,problems=problems));(O/'schema-report.json').write_text(json.dumps(schemas,indent=2))
 if name=='old-unbound':
  base=['nix-build','--no-out-link',str(library/'command.nix'),'--argstr','nixpkgs',values['nixpkgs'],'--argstr','purifierSource',values['purifier_source'],'--argstr','src',project,'--argstr','lockFile',lock]
  run('old-unbound-basic-release',fixture_build(cli,project,'release',lock),project)
  for attr in ['projections.maven','packages.epoch.projections.maven']:run('old-unbound-'+attr,fixture_build(cli,project,attr,lock),project,'Gradle5.6.4 strict legacy Maven projection')
  run('old-unbound-cli-publish',[cli,'publish','--lockfile',lock,'uploadArchives'],project,'Gradle5.6.4 strict legacy Maven publish')
(O/'context.json').write_text(json.dumps(dict(scope='Exact Gradle5.6.4 guard negatives and old-lock strict rejection',cli=str(cli),sourceHelperHashes={n:hashlib.sha256((read_lock if n=='read-lock.nix' else library/n).read_bytes()).hexdigest() for n in ['legacy-maven.init.gradle','task-jdks.init.gradle','read-lock.nix','validate-legacy-native-metadata.py']}),indent=2))
