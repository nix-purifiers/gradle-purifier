#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native URI mapping and collision-free immutable authority projection."""
from pathlib import Path
import argparse,importlib.util,json,os,subprocess,sys
sys.dont_write_bytecode=True
p=argparse.ArgumentParser();p.add_argument('--gradle',type=Path,required=True);p.add_argument('--java-home',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir();spec=importlib.util.spec_from_file_location('replay',root/'nix/lib/replay-repositories.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
urls=['http://127.0.0.1:8081/repository/','https://EXAMPLE.com:443/repository/','http://example.com:80/repository/','http://example.com:8081/repository/','http://hex-3132372e302e302e31.example:8081/repository/','http://example-com.example:8081/repository/']
invalid=['http://[::1]:8081/repository/','http://example_com:80/repository/','http://user:secret@example.com:80/repository/','http://example%2ecom:80/repository/','http://example.com:080/repository/','http://example.com:80/repository/?x=1','http://example.com:80/repository/#fragment','http://example.com:80/a/../repository/','http://example.com:80/a/%2e%2e/repository/','http://example.com:80/a/%5crepository/','http://example.com:70000/repository/']
source=out/'raw';source.mkdir()
for url in urls:
 scheme,authority,encoded=module.authority_alias(url);f=source/scheme/authority/'repository/fixture/helper/1.0/helper-1.0.jar';f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(url.encode())
projected=out/'projected';module.project(source,projected,urls);manifest=json.loads((projected/module.NAMESPACE/'manifest.json').read_text());assert len(manifest['aliases'])==len(urls)
for url in urls:
 scheme,authority,encoded=module.authority_alias(url);relative=Path(module.NAMESPACE)/scheme/encoded/'repository/fixture/helper/1.0/helper-1.0.jar';assert ':' not in str(relative);assert (projected/relative).read_bytes()==(source/scheme/authority/'repository/fixture/helper/1.0/helper-1.0.jar').read_bytes();assert bytes.fromhex(encoded).decode()==authority
for url in invalid:
 try:module.authority_alias(url)
 except ValueError:pass
 else:raise AssertionError('invalid URL accepted: '+url)
collision=out/'collision';collision.mkdir();(collision/module.NAMESPACE).mkdir()
try:module.project(collision,out/'collision-result',urls)
except ValueError as error:assert 'reserved replay namespace' in str(error)
else:raise AssertionError('reserved namespace accepted')
text=(root/'nix/lib/purifier.init.gradle').read_text();body='class PurifierRepositoryReplay {'+text.split('class PurifierRepositoryReplay {',1)[1].split('\ndef standIn =',1)[0];cases=out/'urls.json';cases.write_text(json.dumps(dict(valid=urls,invalid=invalid)));init=out/'mapping.init.gradle';init.write_text('import groovy.json.JsonSlurper\nimport groovy.json.JsonOutput\n'+body+'''
def cases=new JsonSlurper().parse(new File(System.getenv('MAPPING_CASES')))
def rows=[]
cases.valid.each { url ->
 def source=URI.create(url);def authority=source.rawAuthority
 def file=PurifierRepositoryReplay.mapped('file:///immutable/repository',source).toString()
 def expected='file:///immutable/repository/.gradle-purifier-replay-v1/'+source.scheme+'/'+authority.getBytes('UTF-8').encodeHex()+source.rawPath
 assert file==expected
 assert PurifierRepositoryReplay.mapped('http://127.0.0.1:9999',source).toString()=='http://127.0.0.1:9999/'+source.scheme+'/'+authority+source.rawPath
 rows.add([url:url,mapped:file])
}
cases.invalid.each { url ->
 def rejected=false
 try { PurifierRepositoryReplay.mapped('file:///immutable/repository',URI.create(url)) }
 catch(Exception error) { rejected=true }
 assert rejected : url
}
assert PurifierRepositoryReplay.mapped('file:///immutable/repository',URI.create('https://example.com/repository/')).toString()=='file:///immutable/repository/https/example.com/repository/'
new File(System.getenv('MAPPING_RECEIPT')).text=JsonOutput.toJson(rows)
''');project=out/'project';project.mkdir();(project/'settings.gradle').write_text("rootProject.name='mapping'");(project/'build.gradle').write_text('');env=dict(os.environ,JAVA_HOME=a.java_home,GRADLE_USER_HOME=str(out/'gradle-home'),MAPPING_CASES=str(cases),MAPPING_RECEIPT=str(out/'native-uris.json'));cmd=['bash',str(a.gradle.resolve()),'--offline','--no-daemon','--init-script',str(init),'help'];r=subprocess.run(cmd,cwd=project,env=env,capture_output=True,text=True,timeout=180);(out/'native.log').write_text(r.stdout+r.stderr);assert r.returncode==0,(r.stdout+r.stderr)[-4000:];(out/'report.json').write_text(json.dumps(dict(nativeCommand=cmd,exitCode=r.returncode,positiveAuthorities=len(urls),invalidUrls=len(invalid),collisionRejected=True,oldNoPortRouteUnchanged=True,manifest=manifest),indent=2));print('NATIVE_REPLAY_MAPPING_PASS',flush=True)
