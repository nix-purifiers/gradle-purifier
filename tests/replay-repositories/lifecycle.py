#!/usr/bin/env python3
"""Real Nix explicit-port repository replay and ordinary transitive consumers."""
from pathlib import Path
import argparse, functools, hashlib, http.server, importlib.util, json, os, shutil, subprocess, sys, threading
sys.dont_write_bytecode=True
p=argparse.ArgumentParser()
for k in ['cli','sdks','cache','maven','output']:p.add_argument('--'+k,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[2];source_snapshot={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=root,text=True).splitlines() if (root/p).is_file()};out=a.output.resolve();out.mkdir();sdk=json.loads(a.sdks.read_text());cli=a.cli.resolve();maven=a.maven.resolve();rows=[];env=dict(os.environ,JAVA_HOME=sdk['java21']['home'],HOME=str(out/'home'),GRADLE_USER_HOME=str(out/'gradle-home'));Path(env['HOME']).mkdir()
spec=importlib.util.spec_from_file_location('epoch',root/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((root/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-8.14.3');gradle=epoch.distribution(profile,a.cache.resolve());j8=Path(sdk['java8']['home'])
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
dependencies { implementation 'fixture:helper:1.0' }
def xmlMarker=file("$buildDir/withXml-count")
publishing.publications.proof.pom {
 name='Port repository metadata fixture';description='Native metadata'
 licenses { license { name='Fixture license';distribution='repo' } }
 withXml { xml -> xmlMarker.parentFile.mkdirs();xmlMarker.text=((xmlMarker.exists()?xmlMarker.text.toInteger():0)+1).toString();xml.asNode().appendNode('properties').appendNode('native.fixture.marker','withXml preserved') }
}
'''%url
sourceHashes={}
def fixture(name):
 project=out/name;epoch.fixture(project,profile,sdk);f=project/'build.gradle';f.write_text(f.read_text()+extra);(project/'src/main/java/example/Epoch.java').write_text('package example; public final class Epoch { public static int answer(){return fixture.Helper.value()+1;} }');sourceHashes[name]={str(x.relative_to(project)):hashlib.sha256(x.read_bytes()).hexdigest() for x in project.rglob('*') if x.is_file()};return project
try:
 native=fixture('native');run('native',['bash',gradle,'--no-daemon','clean','check','publish'],native)
 capture=fixture('capture');run('capture',[cli,'lock'],capture);lock=(capture/'gradle-purifier.lock.json').read_bytes();(out/'captured-lock.json').write_bytes(lock)
 for name in ['withXml-count']:assert (capture/'.gradle-purifier/lock-run/build/build'/name).read_text()=='1'
 run('relock',[cli,'lock','--relock'],capture);assert (capture/'gradle-purifier.lock.json').read_bytes()==lock
 cold=fixture('cold');(cold/'gradle-purifier.lock.json').write_bytes(lock);home=out/'cold-home';home.mkdir();coldenv=dict(HOME=str(home),GRADLE_USER_HOME=str(out/'cold-gradle-home'),XDG_CACHE_HOME=str(home/'cache'));run('cold-relocated-relock',[cli,'lock','--relock'],cold,coldenv);assert (cold/'gradle-purifier.lock.json').read_bytes()==lock
 run('prefetch',[cli,'prefetch'],cold,coldenv);assert not (Path(coldenv['GRADLE_USER_HOME'])/'caches').exists() and not (cold/'build').exists()
 server.shutdown();server.server_close();(out/'server-stopped.json').write_text(json.dumps({'url':url,'stoppedBefore':'cold namespace replay/Nix builds/publish/consumers','relockUsesNativeNetworkResolution':True}))
 classpath=out/'classpath.init.gradle';classpath.write_text("allprojects { p -> p.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { t -> t.doFirst { def output=new File(System.getenv('ACTUAL_CLASSPATHS'));def rows=output.isFile()?new groovy.json.JsonSlurper().parse(output):[:];rows[t.path]=t.classpath.files*.absolutePath;output.text=groovy.json.JsonOutput.toJson(rows) } } }")
 run('cold-network-namespace-check',[shutil.which('unshare'),'-rn','sh','-c','ip link set lo up && exec "$@"','legacy-metadata','bash',cold/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','--init-script',classpath,'clean','check'],cold,dict(coldenv,ACTUAL_CLASSPATHS=str(out/'cold-classpaths.json')))
 paths=json.loads((out/'cold-classpaths.json').read_text());assert any('.gradle-purifier-replay-v1' in x for x in paths[':compileJava']);assert all(':' not in x for x in paths[':compileJava']);shutil.copyfile(cold/'build/tmp/compileJava/java-compiler-args.txt',out/'cold-javac-args.txt')
 expression='''let f=builtins.getFlake ("path:"+builtins.getEnv "LEGACY_SOURCE");p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "LEGACY_PROJECT";}).ATTR''';nixenv=dict(coldenv,LEGACY_SOURCE=str(root),LEGACY_PROJECT=str(cold));run('sandbox-tests',['nix','build','--impure','--no-link','--print-build-logs','--expr',expression.replace('ATTR','tests')],cold,nixenv);release=Path(run('nix-release',['nix','build','--impure','--no-link','--print-out-paths','--expr',expression.replace('ATTR','release')],cold,nixenv).strip().splitlines()[-1]);nixrepo=release/'epoch';relative=Path('example/epoch/1.0');nativepom=native/'repository'/relative/'epoch-1.0.pom';nixpom=nixrepo/relative/'epoch-1.0.pom';assert nativepom.read_bytes()==nixpom.read_bytes()
 projection=Path(run('nix-publication-projection',['nix','build','--impure','--no-link','--print-out-paths','--expr',expression.replace('ATTR','publication')],cold,nixenv).strip().splitlines()[-1]);catalog=json.loads((projection/'catalog.json').read_text());module=next(m for m in catalog['modules'] if m['group']=='example' and m['name']=='epoch');version=module['version'];relative=Path('example/epoch')/version;nixrepo=projection/'repository';prefix='epoch-'+version
 shutil.rmtree(cold/'build');guard=out/'sentinel.init.gradle';guard.write_text("gradle.projectsEvaluated { gradle.rootProject.tasks.withType(org.gradle.api.tasks.compile.JavaCompile).all { doFirst { throw new GradleException('NATIVE_COMPILATION_DURING_LEGACY_PUBLISH') } } };gradle.taskGraph.whenReady { graph -> new File(System.getenv('PUBLISH_GRAPH')).text=groovy.json.JsonOutput.toJson(graph.allTasks*.path) }")
 run('publish-original-task',[cli,'publish','publishProofPublicationToMavenRepository','--init-script',guard],cold,dict(coldenv,PUBLISH_GRAPH=str(out/'publish-graph.json')));assert not any('compileJava' in task or 'jar' == task.rsplit(':',1)[-1] for task in json.loads((out/'publish-graph.json').read_text()));assert not (cold/'build/classes').exists()
 for name in ['withXml-count']:assert not (cold/'build'/name).exists()
 for suffix in ['jar','pom']:assert (cold/'repository'/relative/(prefix+'.'+suffix)).read_bytes()==(nixrepo/relative/(prefix+'.'+suffix)).read_bytes()
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
 for name,files in sourceHashes.items():assert all(hashlib.sha256((out/name/f).read_bytes()).hexdigest()==h for f,h in files.items())
 context=dict(profile=profile,sdks=sdk,cli=str(cli),maven=str(maven),helperRepositoryUrl=url,serverStoppedBeforeColdBuild=True,consumerDirectDependency='example:epoch:'+version,consumerTransitiveDependency='fixture:helper:1.0',consumerNetworkIsolationClaim=False,sourcePreserved=True,nixJarSha256=hashlib.sha256((nixrepo/relative/(prefix+'.jar')).read_bytes()).hexdigest(),nixPomSha256=hashlib.sha256((nixrepo/relative/(prefix+'.pom')).read_bytes()).hexdigest(),fixtureHashes=sourceHashes,sourceHashes=source_snapshot);(out/'context.json').write_text(json.dumps(context,indent=2));print('PORT_REPLAY_METADATA_LIFECYCLE_PASS',flush=True)
finally:
 server.shutdown();server.server_close()
