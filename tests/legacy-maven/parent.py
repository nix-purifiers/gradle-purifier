#!/usr/bin/env python3
"""An unpublished legacy parent must not hide its child's native publication."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_jvm import bind_fixture_sdks
import argparse,hashlib,importlib.util,json,os,shutil,subprocess
sys.dont_write_bytecode=True
p=argparse.ArgumentParser()
for key in ['cli','sdks','gradle','output']:p.add_argument('--'+key,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir();sdk=json.loads(a.sdks.read_text());cli=a.cli.resolve();gradle=a.gradle.resolve();rows=[]
spec=importlib.util.spec_from_file_location('epoch',root/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((root/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-6.9.4')
project=out/'project';project.mkdir();child=project/'child';epoch.fixture(child,profile,sdk);(child/'settings.gradle').unlink();shutil.move(str(child/'gradle'),str(project/'gradle'));(project/'settings.gradle').write_text("rootProject.name='parent';include 'child'\n");(project/'build.gradle').write_text("apply plugin:'java'\napply plugin:'maven'\ntasks.withType(JavaCompile).all { options.fork=true;options.forkOptions.executable='%s/bin/javac' }\n"%sdk['java8']['home'])
bind_fixture_sdks(project, 11, [8], cli, sdk)
source={str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in project.rglob('*') if f.is_file()};env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(out/'gradle-home'),XDG_CACHE_HOME=str(out/'cache'))
def run(name,cmd):
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(list(map(str,cmd)),cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
 rows.append(dict(stage=name,command=list(map(str,cmd)),exitCode=r.returncode));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert r.returncode==0,(out/(name+'.log')).read_text()[-4000:]
run('native-child',['bash',gradle,'--offline','--no-daemon','clean','check',':child:uploadArchives']);run('capture',[cli,'lock','--bind-sdks']);lock=project/'gradle-purifier.lock.json';first=lock.read_bytes();data=json.loads(first);assert next(x for x in data['projects'] if x['path']==':')['publications']==[];bindings=next(x for x in data['projects'] if x['path']==':child')['publications'];assert len(bindings)==1 and bindings[0]['uploadTask']==':child:uploadArchives';run('relock',[cli,'lock','--bind-sdks','--relock']);assert lock.read_bytes()==first
# Preserve the native repository as a distinct receipt, then require deployment
# from Nix with compilation forbidden in the original publishing task graph.
(child/'repository').rename(out/'native-repository');guard=out/'guard.init.gradle';guard.write_text("gradle.projectsEvaluated { gradle.rootProject.allprojects { p -> p.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { doFirst { throw new GradleException('NATIVE_COMPILATION_DURING_LEGACY_PUBLISH') } } } }")
run('publish-child',[cli,'publish',':child:uploadArchives','--init-script',guard]);projection=(project/'.gradle-purifier/publication').resolve();catalog=json.loads((projection/'catalog.json').read_text());module=next(m for m in catalog['modules'] if m['group']==bindings[0]['groupId'] and m['name']==bindings[0]['artifactId']);relative=Path(module['group'].replace('.','/'))/module['name']/module['version'];prefix=module['name']+'-'+module['version']
for ext in ['jar','pom']:assert (child/'repository'/relative/(prefix+'.'+ext)).read_bytes()==(projection/'repository'/relative/(prefix+'.'+ext)).read_bytes()
assert not (project/'repository').exists();assert all(hashlib.sha256((project/f).read_bytes()).hexdigest()==h for f,h in source.items());(out/'context.json').write_text(json.dumps(dict(profile=profile,sdks=sdk,sourceHashes=source,unconfiguredParentPublication=[],childBinding=bindings[0],projection=str(projection),status='passed'),indent=2));print('UNCONFIGURED_PARENT_CHILD_PUBLICATION_PASS',flush=True)
