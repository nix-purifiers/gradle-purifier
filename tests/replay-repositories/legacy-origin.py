#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Capture once with released code, prove its failure, replay unchanged lock."""
from pathlib import Path
import argparse,functools,http.server,importlib.util,json,os,shutil,subprocess,sys,threading
sys.dont_write_bytecode=True
p=argparse.ArgumentParser()
for k in ['cli','baseline-cli','sdks','cache','output']:p.add_argument('--'+k,type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir();sdk=json.loads(a.sdks.read_text());repo=out/'origin';dep=repo/'fixture/helper/1.0';dep.mkdir(parents=True);source=out/'Helper.java';source.write_text('package fixture;public final class Helper { public static int value(){return 41;} }');classes=out/'helper-classes';classes.mkdir();j8=Path(sdk['java8']['home']);rows=[];env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],HOME=str(out/'home'),GRADLE_USER_HOME=str(out/'baseline-gradle-home'));Path(env['HOME']).mkdir()
def run(name,cmd,cwd=out,extra=None,success=True):
 cmd=list(map(str,cmd))
 with (out/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=cwd,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT,timeout=300)
 rows.append(dict(stage=name,command=cmd,exitCode=r.returncode,expectedSuccess=success));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True);assert (r.returncode==0)==success,(out/(name+'.log')).read_text()[-4000:];return (out/(name+'.log')).read_text()
run('helper-compile',[j8/'bin/javac','-d',classes,source]);run('helper-jar',[j8/'bin/jar','cf',dep/'helper-1.0.jar','-C',classes,'.']);(dep/'helper-1.0.pom').write_text('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>fixture</groupId><artifactId>helper</artifactId><version>1.0</version></project>')
class Handler(http.server.SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(repo)));threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 spec=importlib.util.spec_from_file_location('epoch',root/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((root/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-6.9.4');gradle=epoch.distribution(profile,a.cache.resolve());project=out/'project';epoch.fixture(project,profile,sdk);f=project/'build.gradle';f.write_text(f.read_text()+"\nrepositories { maven { url 'http://127.0.0.1:%s';allowInsecureProtocol=true } };dependencies { compile 'fixture:helper:1.0' }\n"%server.server_port);(project/'src/main/java/example/Epoch.java').write_text('package example;public final class Epoch { public static int answer(){return fixture.Helper.value()+1;} }');run('native-check',['bash',gradle,'--no-daemon','clean','check'],project);shutil.rmtree(project/'build');run('released-capture',[a.baseline_cli.resolve(),'lock'],project);lock=(project/'gradle-purifier.lock.json').read_bytes();run('released-prefetch',[a.baseline_cli.resolve(),'prefetch'],project);server.shutdown();server.server_close();log=run('released-cold-failure',[shutil.which('unshare'),'-rn','sh','-c','ip link set lo up && exec "$@"','released-port','bash',project/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','clean','check'],project,success=False);assert 'package fixture does not exist' in log;assert (project/'gradle-purifier.lock.json').read_bytes()==lock
 run('candidate-old-lock-replay',[sys.executable,root/'tests/replay-repositories/old-lock.py','--cli',a.cli.resolve(),'--project',project,'--sdks',a.sdks.resolve(),'--output',out/'candidate']);assert (project/'gradle-purifier.lock.json').read_bytes()==lock
 (out/'context.json').write_text(json.dumps(dict(profile=profile,sdks=sdk,originStopped=True,baselineCli=str(a.baseline_cli.resolve()),candidateCli=str(a.cli.resolve()),oldLockUnchanged=True,scope='Original legacy source with dependency: native control, released failure, corrected cold OS namespace and pure Nix check. No legacy publication support claim.'),indent=2));print('RELEASED_FAILURE_AND_OLD_LOCK_REPLAY_PASS',flush=True)
finally:server.shutdown();server.server_close()
