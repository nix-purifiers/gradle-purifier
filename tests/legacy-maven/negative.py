#!/usr/bin/env python3
"""Exercise actual legacy publication guards with real Nix publication inputs."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, shutil, subprocess, sys
sys.dont_write_bytecode=True
p=argparse.ArgumentParser()
for k in ['project','publication','sdks','gradle','output']:p.add_argument('--'+k,type=Path,required=True)
p.add_argument('--case',action='append',dest='selected_cases')
a=p.parse_args();driverHash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();root=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir();sdk=json.loads(a.sdks.read_text());locked=json.loads((a.project/'gradle-purifier.lock.json').read_text());publication=next(x for x in locked['projects'][0]['publications'] if x.get('kind')=='legacy-maven-upload');rows=[]
spec=importlib.util.spec_from_file_location('epoch',root/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((root/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-6.9.4')
init=out/'guard.init.gradle';init.write_text('''import groovy.json.JsonSlurper
import groovy.json.JsonOutput
gradle.ext.purifierLegacyMaven=[mode:'publish',output:null,bindings:new JsonSlurper().parseText(System.getenv('LEGACY_BINDINGS')),artifacts:[epoch:System.getenv('NIX_PUBLICATION')],repository:System.getenv('NIX_REPOSITORY'),catalog:System.getenv('NIX_CATALOG')]
apply from: new File(System.getenv('LEGACY_COMPANION'))
allprojects { p -> gradle.ext.purifierLegacyMaven.register(p) }
gradle.projectsEvaluated { gradle.rootProject.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { doFirst { throw new GradleException('NATIVE_COMPILATION_DURING_LEGACY_PUBLISH') } } }
gradle.taskGraph.whenReady { graph -> new File(System.getenv('GUARD_GRAPH')).text=JsonOutput.toJson(graph.allTasks*.path) }
''')
getter="uploadArchives.repositories.withType(org.gradle.api.artifacts.maven.MavenDeployer).toList()[0]"
cases={
 'valid':('',None),
 'helper-before-task-disabled':("gradle.projectsEvaluated { gradle.taskGraph.beforeTask { t -> if(t.name=='purifierLegacyDeployInput1')t.enabled=false } }",'disabled Upload'),
 'helper-before-task-predicate':("gradle.projectsEvaluated { gradle.taskGraph.beforeTask { t -> if(t.name=='purifierLegacyDeployInput1')t.onlyIf { false } } }",'custom Upload predicates'),
 'helper-after-graph-first':("gradle.projectsEvaluated { gradle.taskGraph.whenReady { purifierLegacyDeployInput1.doFirst { file('HOOK_EXECUTED').text='bad' } } }",'deployment task actions or dependencies changed'),
 'helper-after-graph-disabled':("gradle.projectsEvaluated { gradle.taskGraph.whenReady { purifierLegacyDeployInput1.enabled=false } }",'disabled Upload'),
 'helper-after-graph-predicate':("gradle.projectsEvaluated { gradle.taskGraph.whenReady { purifierLegacyDeployInput1.onlyIf { file('HOOK_EXECUTED').text='bad';false } } }",'custom Upload predicates'),
 'helper-late-first':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.doFirst { file('HOOK_EXECUTED').text='bad' } }",'deployment task actions or dependencies changed'),
 'helper-late-last':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.doLast { file('HOOK_EXECUTED').text='bad' } }",'deployment task actions or dependencies changed'),
 'helper-replaced-actions':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.actions=[] }",'deployment task actions or dependencies changed'),
 'helper-late-dependency':("task unexpected { doLast { file('HOOK_EXECUTED').text='bad' } };gradle.taskGraph.whenReady { purifierLegacyDeployInput1.dependsOn unexpected }",'deployment task actions or dependencies changed'),
 'helper-late-predicate':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.onlyIf { file('HOOK_EXECUTED').text='bad';false } }",'custom Upload predicates'),
 'helper-late-disabled':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.enabled=false }",'disabled Upload'),
 'helper-late-configuration':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.configuration=configurations.archives }",'inputs changed before deployment'),
 'helper-late-artifact':("gradle.taskGraph.whenReady { artifacts.add('purifierLegacyInputArtifacts1',file('extra.jar')) { name='extra';type='jar';extension='jar' } }",'inputs changed before deployment'),
 'helper-late-repository':("gradle.taskGraph.whenReady { purifierLegacyDeployInput1.repositories.clear() }",'deployment target changed'),

 'unsupported-metadata':('', 'refusing to discard metadata'),
 'unmapped-parent':('', 'refusing to omit descriptors'),
 'custom-protocol-provider':("uploadArchives.repositories.mavenDeployer { addProtocolProviderJars([file('custom-provider.jar')]) }",'custom protocol providers'),
 'replaced-actions':("uploadArchives.actions=[]",'custom Upload actions'),
 'late-dependency':("task unexpected;gradle.taskGraph.whenReady { uploadArchives.dependsOn unexpected }",'Upload actions or dependencies changed'),
 'late-action':("gradle.taskGraph.whenReady { uploadArchives.doLast { file('HOOK_EXECUTED').text='bad' } }",'Upload actions or dependencies changed'),
 'late-predicate':("gradle.taskGraph.whenReady { uploadArchives.onlyIf { true } }",'custom Upload predicates'),
 'custom-dependency':("task unexpected { doLast { file('HOOK_EXECUTED').text='bad' } };uploadArchives.dependsOn unexpected",'custom Upload dependencies'),
 'custom-first':("uploadArchives.doFirst { file('HOOK_EXECUTED').text='bad' }",'custom Upload actions'),
 'custom-last':("uploadArchives.doLast { file('HOOK_EXECUTED').text='bad' }",'custom Upload actions'),
 'custom-predicate':("uploadArchives.onlyIf { true }",'custom Upload predicates'),
 'disabled-upload':("uploadArchives.enabled=false",'disabled Upload'),
 'snapshot':("version='1.0-SNAPSHOT'",'release-only scope'),
 'reserved-task':("task purifierLegacyDeployInput1",'reserved publication task/configuration collision'),
 'reserved-configuration':("configurations.create('purifierLegacyArtifacts')",'reserved publication task/configuration collision'),
 'custom-hook':("uploadArchives.repositories.mavenDeployer { beforeDeployment { file('HOOK_EXECUTED').text='bad' } }",'custom deployment hooks'),
 'late-hook':("gradle.taskGraph.whenReady { "+getter+".beforeDeployment { file('HOOK_EXECUTED').text='bad' } }",'custom deployment hooks'),
 'custom-filter':("uploadArchives.repositories.mavenDeployer { filter { artifact,file -> true } }",'custom POM filters'),
 'named-filter':("uploadArchives.repositories.mavenDeployer { addFilter('other') { artifact,file -> true } }",'custom POM filters'),
 'extra-artifact':("task extraJar(type:Jar) { archiveClassifier='extra' };artifacts { archives extraJar }",'exactly one unclassified JAR'),
 'late-artifact':("gradle.taskGraph.whenReady { artifacts.add('purifierLegacyArtifacts',file('extra.jar')) { name='extra';type='jar';extension='jar' } }",'inputs changed before deployment'),
 'changed-configuration':("gradle.taskGraph.whenReady { uploadArchives.configuration=configurations.archives }",'inputs changed before deployment'),
 'coordinate-mutation':("gradle.taskGraph.whenReady { "+getter+".pom.version='2.0' }",'unsupported frozen POM method or mutation'),
 'unknown-getter':("gradle.taskGraph.whenReady { "+getter+".pom.getScopeMappings() }",'unsupported frozen POM method or mutation'),
 'unknown-callback':("gradle.taskGraph.whenReady { "+getter+".pom.whenConfigured { } }",'unsupported frozen POM method or mutation'),
 'custom-pom':("def d="+getter+";def old=d.pom;d.pom=java.lang.reflect.Proxy.newProxyInstance(org.gradle.api.artifacts.maven.MavenPom.classLoader,[org.gradle.api.artifacts.maven.MavenPom] as Class[],{ proxy,method,args -> method.invoke(old,args) } as java.lang.reflect.InvocationHandler)",'unsupported deployer or POM implementation'),
 'missing-input':('', 'missing Nix publication input'),
 'changed-binding':('', 'publication binding changed'),
}
if a.selected_cases:assert all(name in cases for name in a.selected_cases),a.selected_cases
for name,(extra,rejection) in cases.items():
 if a.selected_cases and name not in a.selected_cases:continue
 w=out/name;w.mkdir();project=w/'project';epoch.fixture(project,profile,sdk)
 with (project/'build.gradle').open('a') as f:f.write('\n'+extra+'\n')
 bindings=[dict(path=':',publication=publication)]
 if name=='changed-binding':bindings[0]['publication']=dict(publication,configuration='other')
 projection=a.publication.resolve()
 if name in ['unsupported-metadata','unmapped-parent']:
  projection=w/'projection';shutil.copytree(a.publication.resolve(),projection)
  for copied in [projection,*projection.rglob('*')]:copied.chmod(0o755 if copied.is_dir() else 0o644)
  if name=='unsupported-metadata':
   pom=next((projection/'repository').rglob('epoch-*.pom'));pom.with_suffix('.module').write_text('{}')
  else:
   catalogfile=projection/'catalog.json';catalog=json.loads(catalogfile.read_text());catalog['publications'].append(dict(group='fixture',name='parent',version='1.0'));catalogfile.write_text(json.dumps(catalog))
 env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(out/'gradle-home'),LEGACY_BINDINGS=json.dumps(bindings),NIX_PUBLICATION=str(a.publication.resolve()),NIX_REPOSITORY=str(w/'absent' if name=='missing-input' else projection/'repository'),NIX_CATALOG=str(projection/'catalog.json'),LEGACY_COMPANION=str(root/'nix/lib/legacy-maven.init.gradle'),GUARD_GRAPH=str(w/'graph.json'))
 cmd=['bash',str(a.gradle.resolve()),'--offline','--no-daemon','--init-script',str(init),'uploadArchives']
 with (w/'native.log').open('w') as log:r=subprocess.run(cmd,cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
 text=(w/'native.log').read_text();rows.append(dict(case=name,command=cmd,exitCode=r.returncode,expectedRejection=rejection));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True)
 if rejection:
  assert r.returncode!=0 and rejection in text,text[-4000:];assert not (project/'repository').exists() and not (project/'HOOK_EXECUTED').exists()
  if name.startswith('helper-before-task-'):assert ':purifierLegacyDeployInput1 SKIPPED' in text and ':uploadArchives FAILED' in text,text[-4000:]
 else:
  assert r.returncode==0,text[-4000:];assert json.loads((w/'graph.json').read_text())==[':purifierLegacyDeployInput1',':uploadArchives'];assert not (project/'build/classes').exists()
  for module in json.loads((a.publication/'catalog.json').read_text())['modules']:
   relative=Path(module['group'].replace('.','/'))/module['name']/module['version'];prefix=module['name']+'-'+module['version']
   for ext in ['jar','pom']:assert (project/'repository'/relative/(prefix+'.'+ext)).read_bytes()==(a.publication/'repository'/relative/(prefix+'.'+ext)).read_bytes()
(out/'context.json').write_text(json.dumps(dict(driverSha256=driverHash,selectedCases=a.selected_cases,profile=profile,sdks=sdk,nixPublication=str(a.publication.resolve()),companionSha256=hashlib.sha256((root/'nix/lib/legacy-maven.init.gradle').read_bytes()).hexdigest(),scope='Actual companion/native guards. Complete CLI+Nix lifecycle separately required.'),indent=2));print('NATIVE_PRODUCTION_GUARDS_PASS',flush=True)
