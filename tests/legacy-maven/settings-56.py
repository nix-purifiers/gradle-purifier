#!/usr/bin/env python3
"""Native settings grammar and source selection, including real online controls."""
from pathlib import Path
import os,json,subprocess,threading,http.server,hashlib,argparse,importlib.util
R=Path(__file__).resolve().parents[2];p=argparse.ArgumentParser()
for name in ['sdks','cache','output']:p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();O=a.output.resolve();O.mkdir();sdk=json.loads(a.sdks.read_text());spec=importlib.util.spec_from_file_location('epoch',R/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((R/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-5.6.4');gradle=epoch.distribution(profile,a.cache.resolve());requests=[]
class H(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  requests.append(self.path);self.send_response(404);self.end_headers()
 def do_HEAD(self):
  requests.append(self.path);self.send_response(404);self.end_headers()
 def log_message(self,*a):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),H);threading.Thread(target=server.serve_forever,daemon=True).start();url='http://127.0.0.1:'+str(server.server_port);rows=[]
cases=[('different-name',"rootProject.name = 'other-project'\n",True),('nested-includes',"/*header*/ rootProject.name = \"multi\";\ninclude('app', ':parent:child') //line\n",True),('empty','',True),('comment-only','// nothing\n/* safe */\n',True),('remote-plugin',"pluginManagement { repositories { maven { url '"+url+"' } } }\n",False),('remote-buildscript',"buildscript { repositories { maven { url '"+url+"' } }; dependencies { classpath 'example:sentinel:1' } }\n",False),('sentinel',"new File('SETTINGS_EXECUTED').text = 'yes'\n",False),('trailing-code',"rootProject.name='safe';new File('SETTINGS_EXECUTED').text='yes'\n",False),('interpolation','rootProject.name = \"${1+1}\"\n',False),('escape',"rootProject.name = 'safe\\u0027'\n",False),('unicode-comment','// \\u000a new File(\"SETTINGS_EXECUTED\").text=\"yes\"\n',False),('unterminated-comment','/* unfinished',False),('path-traversal',"include ':..:bad'\n",False),('bom',"\ufeffrootProject.name='safe'\n",False),('crlf',"rootProject.name='safe'\r\n",False),('symlink',"rootProject.name='linked'\n",False),('kotlin',"rootProject.name='safe'\n",False),('explicit-settings',"rootProject.name='explicit'\n",False),('search-upwards',"rootProject.name='ancestor'\ninclude ':child'\n",True),('project-dir',"rootProject.name='selected'\n",True),('missing-settings',None,False)]
try:
 for name,text,positive in cases:
  P=O/name;P.mkdir();setting=P/'settings.gradle';extra=[];cwd=P
  if text is not None:setting.write_text(text)
  if name=='remote-plugin':(P/'build.gradle').write_text("plugins { id 'diagnostic.remote' version '1' }\n")
  if name=='symlink':setting.rename(P/'linked-settings');setting.symlink_to('linked-settings')
  if name=='kotlin':(P/'settings.gradle.kts').write_text('error("KOTLIN_EXECUTED")')
  if name=='explicit-settings':extra=['-c',str(setting)]
  if name=='search-upwards':cwd=P/'child';cwd.mkdir()
  if name=='project-dir':
   selected=P/'selected';selected.mkdir();setting.rename(selected/'settings.gradle');extra=['-p',str(selected)];setting=selected/'settings.gradle'
  report=O/(name+'-selection.json');env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(O/'gradle-home'),GRADLE_PURIFIER_SETTINGS_REPORT=str(report));cmd=[str(gradle),'--offline','--no-daemon','--init-script',str(R/'nix/lib/legacy-settings-5.6.init.gradle')]+extra+['projects'];before=len(requests)
  if name in ['remote-plugin','remote-buildscript']:
   cmd.remove('--offline')
   native=[str(gradle),'--no-daemon','projects']
   with (O/(name+'-native-control.log')).open('w') as f:control=subprocess.run(native,cwd=P,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=180)
   assert control.returncode!=0 and len(requests)>before,(name,requests)
   (O/(name+'-native-counter.json')).write_text(json.dumps({'exitCode':control.returncode,'requests':requests[before:]},indent=2))
   before=len(requests)
  with (O/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=180)
  log=(O/(name+'.log')).read_text();row=dict(case=name,expectedPositive=positive,exitCode=r.returncode,remoteRequests=len(requests)-before,sentinelExecuted=any(P.rglob('SETTINGS_EXECUTED')),beforeEvaluationRejection='gradle-purifier: Gradle5.6.4 settings supports' in log,sourcePreserved=setting.read_bytes()==text.encode() if text is not None else True,selection=json.loads(report.read_text()) if report.exists() else None);rows.append(row);(O/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert (r.returncode==0)==positive and row['remoteRequests']==0 and not row['sentinelExecuted'] and row['sourcePreserved'] and (positive or row['beforeEvaluationRejection']),log[-3500:]
finally:
 server.shutdown();(O/'requests.json').write_text(json.dumps(requests));(O/'source-context.json').write_text(json.dumps({'initSha256':hashlib.sha256((R/'nix/lib/legacy-settings-5.6.init.gradle').read_bytes()).hexdigest(),'driverSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'scope':'Native-only grammar/selection; no capture integration or support promotion'},indent=2))
