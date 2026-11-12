#!/usr/bin/env python3
"""Real Nix legacy publication metadata, cold replay and ordinary consumers."""
from pathlib import Path
import argparse, functools, hashlib, http.server, importlib.util, json, os, shutil, subprocess, sys, threading
sys.dont_write_bytecode=True
p=argparse.ArgumentParser()
for k in ['cli','sdks','cache','maven','output']:p.add_argument('--'+k,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[2];source_snapshot={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=root,text=True).splitlines() if (root/p).is_file()};out=a.output.resolve();out.mkdir();sdk=json.loads(a.sdks.read_text());cli=a.cli.resolve();maven=a.maven.resolve();rows=[];(out/'source-context.json').write_text(json.dumps({'sourceRevision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'sourceHashes':source_snapshot,'cli':str(cli)},indent=2));env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],XDG_CACHE_HOME=str(out/'cache'),GRADLE_USER_HOME=str(out/'gradle-home'))
spec=importlib.util.spec_from_file_location('epoch',root/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((root/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-6.9.4');gradle=epoch.distribution(profile,a.cache.resolve());j8=Path(sdk['java8']['home'])
def run(name,cmd,cwd=out,extra=None):
 cmd=list(map(str,cmd))
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=cwd,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT,timeout=300)
 rows.append(dict(stage=name,command=cmd,exitCode=r.returncode));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert r.returncode==0,(out/(name+'.log')).read_text()[-5000:];return (out/(name+'.log')).read_text()
repo=out/'helper-repository';dep=repo/'fixture/helper/1.0';dep.mkdir(parents=True);src=out/'helper-src/fixture';src.mkdir(parents=True);(src/'Helper.java').write_text('package fixture; public final class Helper { public static int value(){return 41;} }');classes=out/'helper-classes';classes.mkdir();run('helper-compile',[j8/'bin/javac','-d',classes,src/'Helper.java']);run('helper-jar',[j8/'bin/jar','cf',dep/'helper-1.0.jar','-C',classes,'.']);(dep/'helper-1.0.pom').write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>fixture</groupId><artifactId>helper</artifactId><version>1.0</version></project>')
class Handler(http.server.SimpleHTTPRequestHandler):
 def log_message(self,format,*args):
  with (out/'helper-server.log').open('a') as f:f.write(format%args+'\n')
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(repo)));threading.Thread(target=server.serve_forever,daemon=True).start();url='http://127.0.0.1:'+str(server.server_port)
extra='''
repositories { maven { url '%s';allowInsecureProtocol=true } }
dependencies { compile 'fixture:helper:1.0' }
def whenMarker=file("$buildDir/whenConfigured-count");def xmlMarker=file("$buildDir/withXml-count")
uploadArchives.repositories.mavenDeployer {
 pom.project { name 'Legacy metadata fixture';description 'Native metadata';licenses { license { name 'Fixture license';distribution 'repo' } } }
 pom.whenConfigured { model -> whenMarker.parentFile.mkdirs();whenMarker.text=((whenMarker.exists()?whenMarker.text.toInteger():0)+1).toString();model.model.description='whenConfigured preserved' }
 pom.withXml { xml -> xmlMarker.parentFile.mkdirs();xmlMarker.text=((xmlMarker.exists()?xmlMarker.text.toInteger():0)+1).toString();xml.asNode().appendNode('properties').appendNode('native.fixture.marker','withXml preserved') }
}
'''%url
sourceHashes={}
def fixture(name):
 project=out/name;epoch.fixture(project,profile,sdk);f=project/'build.gradle';f.write_text(f.read_text()+extra);(project/'src/main/java/example/Epoch.java').write_text('package example; public final class Epoch { public static int answer(){return fixture.Helper.value()+1;} }');sourceHashes[name]={str(x.relative_to(project)):hashlib.sha256(x.read_bytes()).hexdigest() for x in project.rglob('*') if x.is_file()};return project
try:
 native=fixture('native');run('native',['bash',gradle,'--no-daemon','clean','check','uploadArchives'],native)
 capture=fixture('capture');run('capture',[cli,'lock'],capture);lock=(capture/'gradle-purifier.lock.json').read_bytes();(out/'captured-lock.json').write_bytes(lock)
 for name in ['whenConfigured-count','withXml-count']:assert (capture/'.gradle-purifier/lock-run/build/build'/name).read_text()=='1'
 run('relock',[cli,'lock','--relock'],capture);assert (capture/'gradle-purifier.lock.json').read_bytes()==lock
 cold=fixture('cold');(cold/'gradle-purifier.lock.json').write_bytes(lock);home=out/'cold-home';home.mkdir();coldenv=dict(GRADLE_USER_HOME=str(out/'cold-gradle-home'),XDG_CACHE_HOME=str(home/'cache'));run('cold-relocated-relock',[cli,'lock','--relock'],cold,coldenv);assert (cold/'gradle-purifier.lock.json').read_bytes()==lock
 run('prefetch',[cli,'prefetch'],cold,coldenv);assert not (Path(coldenv['GRADLE_USER_HOME'])/'caches').exists() and not (cold/'build').exists()
 server.shutdown();server.server_close();(out/'server-stopped.json').write_text(json.dumps({'url':url,'stoppedBefore':'cold namespace replay/Nix builds/publish/consumers','relockUsesNativeNetworkResolution':True}))
 run('cold-network-namespace-check',[shutil.which('unshare'),'-rn','sh','-c','ip link set lo up && exec "$@"','legacy-metadata','bash',cold/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','clean','check'],cold,coldenv)
 expression='''let f=builtins.getFlake ("path:"+builtins.getEnv "LEGACY_SOURCE");p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "LEGACY_PROJECT";}).ATTR''';nixenv=dict(coldenv,LEGACY_SOURCE=str(root),LEGACY_PROJECT=str(cold));run('sandbox-tests',['nix','build','--impure','--no-link','--print-build-logs','--expr',expression.replace('ATTR','tests')],cold,nixenv);release=Path(run('nix-release',['nix','build','--impure','--no-link','--print-out-paths','--expr',expression.replace('ATTR','release')],cold,nixenv).strip().splitlines()[-1]);nixrepo=release/'epoch';relative=Path('example/epoch/1.0');nativepom=native/'repository'/relative/'epoch-1.0.pom';nixpom=nixrepo/relative/'epoch-1.0.pom';markerSpec=importlib.util.spec_from_file_location('native_metadata_validator',root/'pkgs/build-gradle-project/validate-legacy-native-metadata.py');markerModule=importlib.util.module_from_spec(markerSpec);markerSpec.loader.exec_module(markerModule);assert nixpom.read_bytes().count(markerModule.NATIVE_MARKER)==1;assert nativepom.read_bytes()==nixpom.read_bytes().replace(markerModule.NATIVE_MARKER,b'',1)
 # Match the immutable builder packaged with this CLI. A separately evaluated
 # flake overlay can have a distinct producer derivation identity.
 settings={line.split('=',1)[0]:line.split('=',1)[1] for line in cli.read_text().splitlines() if line.startswith(('lib=','nixpkgs=','purifier_source='))}
 projection=Path(run('nix-publication-projection',['nix-build','--quiet',str(Path(settings['lib'])/'command.nix'),'--argstr','nixpkgs',settings['nixpkgs'],'--argstr','purifierSource',settings['purifier_source'],'--argstr','src',str(cold),'--argstr','lockFile',str(cold/'gradle-purifier.lock.json'),'-A','publication','--no-out-link'],cold,coldenv).strip().splitlines()[-1]);catalog=json.loads((projection/'catalog.json').read_text());module=next(m for m in catalog['modules'] if m['group']=='example' and m['name']=='epoch');version=module['version'];relative=Path('example/epoch')/version;nixrepo=projection/'repository';prefix='epoch-'+version
 shutil.rmtree(cold/'build');guard=out/'sentinel.init.gradle';guard.write_text("gradle.projectsEvaluated { gradle.rootProject.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { doFirst { throw new GradleException('NATIVE_COMPILATION_DURING_LEGACY_PUBLISH') } } };gradle.taskGraph.whenReady { graph -> new File(System.getenv('PUBLISH_GRAPH')).text=groovy.json.JsonOutput.toJson(graph.allTasks*.path) }")
 run('publish-original-uploadArchives',[cli,'publish','uploadArchives','--init-script',guard],cold,dict(coldenv,PUBLISH_GRAPH=str(out/'publish-graph.json')));assert json.loads((out/'publish-graph.json').read_text())==[':purifierLegacyDeployInput1',':uploadArchives'];assert not (cold/'build/classes').exists()
 for name in ['whenConfigured-count','withXml-count']:assert not (cold/'build'/name).exists()
 for suffix in ['jar','pom','module']:assert (cold/'repository'/relative/(prefix+'.'+suffix)).read_bytes()==(nixrepo/relative/(prefix+'.'+suffix)).read_bytes()
 for suffix in ['jar','pom']:assert (cold/'repository/fixture/helper/1.0'/('helper-1.0.'+suffix)).read_bytes()==(nixrepo/'fixture/helper/1.0'/('helper-1.0.'+suffix)).read_bytes()
 # Independent consumers use only the projected publication's declared dependency.
 code='public class Consumer { public static void main(String[] args){if(example.Epoch.answer()!=42)throw new AssertionError();System.out.println("TRANSITIVE_METADATA_42");} }'
 g=out/'gradle-consumer';(g/'src/main/java').mkdir(parents=True);(g/'src/main/java/Consumer.java').write_text(code);(g/'settings.gradle').write_text("rootProject.name='consumer'");(g/'build.gradle').write_text("""apply plugin:'java'
sourceCompatibility='1.8';targetCompatibility='1.8'
repositories { maven { url uri('%s') } }
dependencies { implementation 'example:epoch:%s' }
compileJava.options.fork=true;compileJava.options.forkOptions.executable='%s/bin/javac'
task verify(type:JavaExec,dependsOn:classes) { main='Consumer';classpath=sourceSets.main.runtimeClasspath;executable='%s/bin/java' }
"""%(cold/'repository',version,j8,j8));run('ordinary-gradle-consumer',['bash',gradle,'--offline','--no-daemon','clean','verify'],g,dict(GRADLE_USER_HOME=str(out/'consumer-gradle-home')));assert 'TRANSITIVE_METADATA_42' in (out/'ordinary-gradle-consumer.log').read_text()
 m=out/'maven-consumer';(m/'src/main/java').mkdir(parents=True);(m/'src/main/java/Consumer.java').write_text(code);(m/'settings.xml').write_text('<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"/>');(m/'pom.xml').write_text('''<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>fixture</groupId><artifactId>consumer</artifactId><version>1.0</version><repositories><repository><id>projected</id><url>%s</url></repository></repositories><dependencies><dependency><groupId>example</groupId><artifactId>epoch</artifactId><version>%s</version></dependency></dependencies><build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.8.1</version><configuration><source>8</source><target>8</target><fork>true</fork><executable>%s/bin/javac</executable></configuration></plugin></plugins></build></project>'''%((cold/'repository').as_uri(),version,j8));local=out/'maven-local';assert not local.exists();run('ordinary-maven-consumer',[maven,'-B','-s',m/'settings.xml','-Dmaven.repo.local='+str(local),'compile','org.apache.maven.plugins:maven-dependency-plugin:3.8.1:build-classpath','-Dmdep.outputFile=resolved-classpath.txt'],m,dict(JAVA_HOME=sdk['java17']['home']));cp=(m/'resolved-classpath.txt').read_text().strip();assert prefix+'.jar' in cp and 'helper-1.0.jar' in cp;run('ordinary-maven-java8-runtime',[j8/'bin/java','-cp',str(m/'target/classes')+':'+cp,'Consumer'],m);assert 'TRANSITIVE_METADATA_42' in (out/'ordinary-maven-java8-runtime.log').read_text()
 for suffix in ['jar','pom']:assert (local/relative/(prefix+'.'+suffix)).read_bytes()==(nixrepo/relative/(prefix+'.'+suffix)).read_bytes()
 # The downloaded helper's literal version is a soft Maven request. Keep
 # those authoritative bytes unchanged and prove ordinary competing selection.
 helper2=cold/'repository/fixture/helper/2.0';helper2.mkdir(parents=True)
 (src/'Helper.java').write_text('package fixture; public final class Helper { public static int value(){return 99;} }')
 run('competing-helper-compile',[j8/'bin/javac','-d',classes,src/'Helper.java'])
 run('competing-helper-jar',[j8/'bin/jar','cf',helper2/'helper-2.0.jar','-C',classes,'.'])
 (helper2/'helper-2.0.pom').write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>fixture</groupId><artifactId>helper</artifactId><version>2.0</version></project>')
 assert b'<version>1.0</version>' in (nixrepo/relative/(prefix+'.pom')).read_bytes()
 (g/'src/main/java/Consumer.java').write_text(code.replace('!=42','!=100').replace('TRANSITIVE_METADATA_42','SOFT_CONFLICT_100'))
 with (g/'build.gradle').open('a') as f:f.write("\ndependencies { implementation 'fixture:helper:2.0' }\n")
 run('ordinary-gradle-competing-version',['bash',gradle,'--offline','--no-daemon','clean','verify'],g,dict(GRADLE_USER_HOME=str(out/'competing-gradle-home')))
 assert 'SOFT_CONFLICT_100' in (out/'ordinary-gradle-competing-version.log').read_text()
 (m/'src/main/java/Consumer.java').write_text(code.replace('!=42','!=100').replace('TRANSITIVE_METADATA_42','SOFT_CONFLICT_100'))
 pom=m/'pom.xml';pom.write_text(pom.read_text().replace('</dependencies>','<dependency><groupId>fixture</groupId><artifactId>helper</artifactId><version>2.0</version></dependency></dependencies>'))
 conflictlocal=out/'competing-maven-local';assert not conflictlocal.exists()
 run('ordinary-maven-competing-version',[maven,'-B','-s',m/'settings.xml','-Dmaven.repo.local='+str(conflictlocal),'clean','compile','org.apache.maven.plugins:maven-dependency-plugin:3.8.1:build-classpath','-Dmdep.outputFile=resolved-competing-classpath.txt'],m,dict(JAVA_HOME=sdk['java17']['home']))
 conflictcp=(m/'resolved-competing-classpath.txt').read_text().strip();assert 'helper-2.0.jar' in conflictcp and 'helper-1.0.jar' not in conflictcp
 run('ordinary-maven-competing-java8-runtime',[j8/'bin/java','-cp',str(m/'target/classes')+':'+conflictcp,'Consumer'],m)
 assert 'SOFT_CONFLICT_100' in (out/'ordinary-maven-competing-java8-runtime.log').read_text()
 assert (cold/'repository'/relative/(prefix+'.pom')).read_bytes()==(nixrepo/relative/(prefix+'.pom')).read_bytes()
 for name,files in sourceHashes.items():assert all(hashlib.sha256((out/name/f).read_bytes()).hexdigest()==h for f,h in files.items())
 context=dict(profile=profile,sdks=sdk,cli=str(cli),maven=str(maven),helperRepositoryUrl=url,serverStoppedBeforeColdBuild=True,consumerDirectDependency='example:epoch:'+version,consumerTransitiveDependency='fixture:helper:1.0',consumerNetworkIsolationClaim=False,downloadedVersionSemantics="soft 1.0 preserved; direct competing 2.0 selected by Gradle and Maven, runtime100",sourcePreserved=True,nixJarSha256=hashlib.sha256((nixrepo/relative/(prefix+'.jar')).read_bytes()).hexdigest(),nixPomSha256=hashlib.sha256((nixrepo/relative/(prefix+'.pom')).read_bytes()).hexdigest(),fixtureHashes=sourceHashes,sourceHashes=source_snapshot);(out/'context.json').write_text(json.dumps(context,indent=2));print('REAL_NIX_METADATA_LIFECYCLE_PASS',flush=True)
finally:
 server.shutdown();server.server_close()
