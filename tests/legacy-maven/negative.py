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
 'helper-configuration-action':("configurations.all { c -> if(c.name=='purifierLegacyInputArtifacts1') purifierLegacyDeployInput1.doFirst { file('HOOK_EXECUTED').text='bad' } }",'custom Upload actions'),
 'owner-configuration-action':("configurations.all { c -> if(c.name=='purifierLegacyArtifacts') uploadArchives.doFirst { file('HOOK_EXECUTED').text='bad' } }",'custom Upload actions'),
 'helper-configuration-dependency':("task unexpected;configurations.all { c -> if(c.name=='purifierLegacyInputArtifacts1') purifierLegacyDeployInput1.dependsOn { file('HOOK_EXECUTED').text='bad';unexpected } }",'custom Upload dependencies'),
 'owner-late-action-method':("gradle.taskGraph.whenReady { def a=uploadArchives.actions.find { it.getClass().name=='org.gradle.api.internal.project.taskfactory.StandardTaskAction' };def f=a.getClass().getDeclaredField('method');f.accessible=true;f.set(a,org.gradle.api.tasks.Upload.getDeclaredMethod('isUploadDescriptor')) }",'unsupported native Upload action binding'),
 'helper-late-action-method':("gradle.taskGraph.whenReady { def a=purifierLegacyDeployInput1.actions.find { it.getClass().name=='org.gradle.api.internal.project.taskfactory.StandardTaskAction' };def f=a.getClass().getDeclaredField('method');f.accessible=true;f.set(a,org.gradle.api.tasks.Upload.getDeclaredMethod('isUploadDescriptor')) }",'unsupported native Upload action binding'),
 'owner-late-warning-delegate':("gradle.taskGraph.whenReady { def a=uploadArchives.actions.find { it.getClass().name=='org.gradle.api.internal.AbstractTask$TaskActionWrapper' };def f=a.getClass().getDeclaredField('action');f.accessible=true;f.set(a,{ t -> file('HOOK_EXECUTED').text='bad' } as org.gradle.api.Action) }",'custom Upload warning action'),

 'helper-early-first':("tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.doFirst { file('HOOK_EXECUTED').text='bad' } }",'custom Upload actions'),
 'helper-early-last':("tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.doLast { file('HOOK_EXECUTED').text='bad' } }",'custom Upload actions'),
 'helper-early-dependency':("task unexpected { doLast { file('HOOK_EXECUTED').text='bad' } };tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.dependsOn unexpected }",'custom Upload dependencies'),
 'helper-early-input-dependency':("task unexpected { outputs.file(file('custom-output'));doLast { file('HOOK_EXECUTED').text='bad';file('custom-output').text='x' } };tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.inputs.files(unexpected) }",'Upload task graph dependencies changed'),
 'owner-input-dependency':("task unexpected { outputs.file(file('custom-output'));doLast { file('HOOK_EXECUTED').text='bad';file('custom-output').text='x' } };uploadArchives.inputs.files(unexpected)",'Upload task graph dependencies changed'),
 'helper-early-finalizer':("task unexpected { doLast { file('HOOK_EXECUTED').text='bad' } };tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.finalizedBy unexpected }",'custom Upload finalizers'),
 'owner-finalizer':("task unexpected { doLast { file('HOOK_EXECUTED').text='bad' } };uploadArchives.finalizedBy unexpected",'custom Upload finalizers'),
 'helper-late-finalizer':("task unexpected { doLast { file('HOOK_EXECUTED').text='bad' } };gradle.taskGraph.whenReady { purifierLegacyDeployInput1.finalizedBy unexpected }",'custom Upload finalizers'),
 'helper-early-disabled':("tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.enabled=false }",'disabled Upload'),
 'helper-early-predicate':("tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.onlyIf { file('HOOK_EXECUTED').text='bad';false } }",'custom Upload predicates'),
 'helper-early-configuration':("tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.configuration=configurations.archives }",'custom generated Upload configuration'),
 'helper-early-repository':("tasks.withType(org.gradle.api.tasks.Upload).all { t -> if(t.name=='purifierLegacyDeployInput1') t.repositories.mavenDeployer { repository(url:uri('unexpected-repository')) } }",'custom generated Upload configuration or repositories'),
 'typed-owner-replacement':("afterEvaluate { uploadArchives.actions.remove(0);uploadArchives.doFirst({ t -> file('HOOK_EXECUTED').text='bad' } as org.gradle.api.Action) }",'custom Upload warning action'),
 'owner-action-method':("afterEvaluate { def action=uploadArchives.actions.last();def field=action.getClass().getDeclaredField('method');field.accessible=true;field.set(action,org.gradle.api.tasks.Upload.getDeclaredMethod('isUploadDescriptor')) }",'unsupported native Upload action binding'),

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
if publication.get('nativeMetadata'):
 cases['foreign-prefix-jar']=('', 'invalid artifact prefix')
 cases['noncanonical-module-digest']=('', 'variant artifact digest differs')
if a.selected_cases:assert all(name in cases for name in a.selected_cases),a.selected_cases
for name,(extra,rejection) in cases.items():
 if a.selected_cases and name not in a.selected_cases:continue
 if publication.get('nativeMetadata') and name in ('unsupported-metadata','unmapped-parent'):rejection='invalid projected module metadata' if name=='unsupported-metadata' else 'missing Nix publication input'
 w=out/name;w.mkdir();project=w/'project';epoch.fixture(project,profile,sdk)
 with (project/'build.gradle').open('a') as f:f.write('\n'+extra+'\n')
 bindings=[dict(path=':',publication=publication)]
 if name=='changed-binding':bindings[0]['publication']=dict(publication,configuration='other')
 projection=a.publication.resolve()
 if name in ['unsupported-metadata','unmapped-parent','foreign-prefix-jar','noncanonical-module-digest']:
  projection=w/'projection';shutil.copytree(a.publication.resolve(),projection)
  for copied in [projection,*projection.rglob('*')]:copied.chmod(0o755 if copied.is_dir() else 0o644)
  if name=='unsupported-metadata':
   pom=next((projection/'repository').rglob('epoch-*.pom'));pom.with_suffix('.module').write_text('{}')
  elif name=='foreign-prefix-jar':
   jar=next((projection/'repository').rglob('epoch-*.jar'));prefix=jar.stem;jar.with_name('x'*len(prefix)+'-sources.jar').write_bytes(jar.read_bytes())
  elif name=='noncanonical-module-digest':
   module=next((projection/'repository').rglob('epoch-*.module'));document=json.loads(module.read_text());document['variants'][0]['files'][0]['sha256']='0'+document['variants'][0]['files'][0]['sha256'];module.write_text(json.dumps(document))
  else:
   catalogfile=projection/'catalog.json';catalog=json.loads(catalogfile.read_text());catalog['publications'].append(dict(group='fixture',name='parent',version='1.0'));catalogfile.write_text(json.dumps(catalog))
 env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(out/'gradle-home'),LEGACY_BINDINGS=json.dumps(bindings),NIX_PUBLICATION=str(a.publication.resolve()),NIX_REPOSITORY=str(w/'absent' if name=='missing-input' else projection/'repository'),NIX_CATALOG=str(projection/'catalog.json'),LEGACY_COMPANION=str(root/'pkgs/build-gradle-project/legacy-maven.init.gradle'),GUARD_GRAPH=str(w/'graph.json'))
 cmd=['bash',str(a.gradle.resolve()),'--offline','--no-daemon','--init-script',str(init),'uploadArchives']
 with (w/'native.log').open('w') as log:r=subprocess.run(cmd,cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
 text=(w/'native.log').read_text();rows.append(dict(case=name,command=cmd,exitCode=r.returncode,expectedRejection=rejection));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True)
 if rejection:
  assert r.returncode!=0 and rejection in text,text[-4000:];assert not (project/'repository').exists() and not (project/'unexpected-repository').exists() and not (project/'HOOK_EXECUTED').exists()
  if name.startswith('helper-before-task-'):assert ':purifierLegacyDeployInput1 SKIPPED' in text and ':uploadArchives FAILED' in text,text[-4000:]
 else:
  assert r.returncode==0,text[-4000:];assert json.loads((w/'graph.json').read_text())==[':purifierLegacyDeployInput1',':uploadArchives'];assert not (project/'build/classes').exists()
  for module in json.loads((a.publication/'catalog.json').read_text())['modules']:
   relative=Path(module['group'].replace('.','/'))/module['name']/module['version'];prefix=module['name']+'-'+module['version']
   for ext in ['jar','pom']:assert (project/'repository'/relative/(prefix+'.'+ext)).read_bytes()==(a.publication/'repository'/relative/(prefix+'.'+ext)).read_bytes()
(out/'context.json').write_text(json.dumps(dict(driverSha256=driverHash,selectedCases=a.selected_cases,profile=profile,sdks=sdk,nixPublication=str(a.publication.resolve()),companionSha256=hashlib.sha256((root/'pkgs/build-gradle-project/legacy-maven.init.gradle').read_bytes()).hexdigest(),scope='Actual companion/native guards. Complete CLI+Nix lifecycle separately required.'),indent=2));print('NATIVE_PRODUCTION_GUARDS_PASS',flush=True)
