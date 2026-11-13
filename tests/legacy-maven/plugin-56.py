#!/usr/bin/env python3
"""Capture a native project plugin and replay with its origin stopped."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_jvm import bind_fixture_sdks
import os,json,subprocess,shutil,hashlib,threading,http.server,zipfile,argparse,importlib.util
R=Path(__file__).resolve().parents[2];p=argparse.ArgumentParser()
for name in ['cli','sdks','cache','output']:p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();O=a.output.resolve();O.mkdir();sdk=json.loads(a.sdks.read_text());spec=importlib.util.spec_from_file_location('epoch',R/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((R/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-5.6.4');gradle=Path(epoch.distribution(profile,a.cache.resolve()));D=gradle.parents[1];repo=O/'repository';impl=repo/'diagnostic/plugin-impl/1';impl.mkdir(parents=True);src=O/'PluginImpl.java';src.write_text("package diagnostic; public class PluginImpl implements org.gradle.api.Plugin<org.gradle.api.Project> {public void apply(org.gradle.api.Project p) {p.getPluginManager().apply(\"java\"); org.gradle.api.Task t=p.getTasks().create(\"pluginCheck\");t.doLast(new org.gradle.api.Action<org.gradle.api.Task>() {public void execute(org.gradle.api.Task t) {System.out.println(\"NATIVE_PLUGIN_EXECUTED_42\");}});p.getTasks().getByName(\"check\").dependsOn(t);}}")
classes=O/'classes';classes.mkdir();cp=':'.join(str(p) for p in D.rglob('*.jar'));r=subprocess.run([sdk['java8']['home']+'/bin/javac','-source','8','-target','8','-cp',cp,'-d',str(classes),str(src)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);(O/'plugin-compile.log').write_text(r.stdout);assert r.returncode==0,r.stdout
with zipfile.ZipFile(impl/'plugin-impl-1.jar','w') as z:
 for f in classes.rglob('*.class'):z.write(f,str(f.relative_to(classes)))
 z.writestr('META-INF/gradle-plugins/diagnostic.local.properties','implementation-class=diagnostic.PluginImpl\n')
(impl/'plugin-impl-1.pom').write_text('<project><modelVersion>4.0.0</modelVersion><groupId>diagnostic</groupId><artifactId>plugin-impl</artifactId><version>1</version></project>');marker=repo/'diagnostic/local/diagnostic.local.gradle.plugin/1';marker.mkdir(parents=True);(marker/'diagnostic.local.gradle.plugin-1.pom').write_text('<project><modelVersion>4.0.0</modelVersion><groupId>diagnostic.local</groupId><artifactId>diagnostic.local.gradle.plugin</artifactId><version>1</version><packaging>pom</packaging><dependencies><dependency><groupId>diagnostic</groupId><artifactId>plugin-impl</artifactId><version>1</version></dependency></dependencies></project>');requests=[]
class H(http.server.SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(repo),**kw)
 def do_GET(self):requests.append(self.path);super().do_GET()
 def do_HEAD(self):requests.append(self.path);super().do_HEAD()
 def log_message(self,*a):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=server.serve_forever,daemon=True).start();init=O/'project-plugin-repository.init.gradle';init.write_text("gradle.settingsEvaluated { s -> s.pluginManagement.repositories.clear();s.pluginManagement.repositories.maven { url 'http://127.0.0.1:"+str(server.server_port)+"' } }\n");P=O/'producer';epoch.fixture(P,profile,sdk);cli=a.cli.resolve();library=next(line.split('=',1)[1] for line in cli.read_text().splitlines() if line.startswith('lib='));c={'cli':str(cli),'storeLibrary':library}
(P/'build.gradle').write_text("plugins { id 'diagnostic.local' version '1' }\n"+(P/'build.gradle').read_text());bind_fixture_sdks(P, 11, [8], cli, sdk)
source={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file()};env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(O/'capture-gradle-home'),XDG_CACHE_HOME=str(O/'cache'));env['PATH']=sdk['java11']['home']+'/bin:'+env['PATH'];rows=[]
def run(name,cmd,extra=None):
 with (O/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=P,env=dict(env,**(extra or {})),stdout=f,stderr=subprocess.STDOUT,timeout=600)
 rows.append(dict(stage=name,command=cmd,exitCode=r.returncode,serverRequestsSoFar=len(requests)));(O/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert r.returncode==0,(O/(name+'.log')).read_text()[-4500:]
try:
 run('native-plugin-check',[str(D/'bin/gradle'),'--no-daemon','--init-script',str(init),'clean','check'])
 assert 'NATIVE_PLUGIN_EXECUTED_42' in (O/'native-plugin-check.log').read_text() and requests
 run('capture',[c['cli'],'lock','--bind-sdks','--','--init-script',str(init)])
 lock=json.loads((P/'gradle-purifier.lock.json').read_text());assert any('plugin-impl-1.jar' in a.get('url','') for a in lock['artifacts'])
 cold={'GRADLE_USER_HOME':str(O/'cold-gradle-home')};run('prefetch-online-materialize-downloads',[c['cli'],'prefetch'],cold)
 server.shutdown();server.server_close();stopped=True
 run('prefetch-origin-stopped',[c['cli'],'prefetch'],cold);assert not (O/'cold-gradle-home/caches').exists();
 if (P/'build').exists():shutil.rmtree(P/'build')
 run('cold-offline-origin-stopped',['unshare','-rn','sh','-c','ip link set lo up && exec "$@"','plugin-cold',str(P/'.gradle-purifier/distribution/bin/gradle'),'--offline','--no-daemon','--init-script',str(init),'clean','check'],cold)
 assert 'NATIVE_PLUGIN_EXECUTED_42' in (O/'cold-offline-origin-stopped.log').read_text()
 assert all(hashlib.sha256((P/n).read_bytes()).hexdigest()==h for n,h in source.items())
 (O/'context.json').write_text(json.dumps({'scope':'Private5.6 native project plugin marker/implementation replay, settings unchanged declarative; no general settings plugin support','producerSourceHashes':source,'sourcePreserved':True,'onlinePrefetchMaterializedLockedDownloads':True,'originStoppedBeforeSecondPrefetchAndColdReplay':True,'coldInitialCachesAbsent':True,'coldNetworkNamespace':True,'repositoryInitSha256':hashlib.sha256(init.read_bytes()).hexdigest(),'privateLibrary':c['storeLibrary']},indent=2))
finally:
 server.shutdown();server.server_close();(O/'requests.json').write_text(json.dumps(requests,indent=2))
