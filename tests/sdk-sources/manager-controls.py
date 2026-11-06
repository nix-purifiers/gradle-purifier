#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mandatory source manager for zero tasks and all-manager SDK task roles."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
p = argparse.ArgumentParser(description=__doc__)
for name in ('cli','positive','output'): p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
source=json.loads((a.positive/'gradle-purifier.sdks.json').read_text())
positive=json.loads((a.positive/'gradle-purifier.lock.json').read_text())['projectJdks']['x86_64-linux']
rows=[]
for name, sdk_id in [('zero-tasks','compiler'),('all-manager','manager')]:
 project=out/name;(project/'gradle/wrapper').mkdir(parents=True)
 declaration=copy.deepcopy(source);declaration['sdks']=[s for s in declaration['sdks'] if s['id']==sdk_id];declaration['manager']=sdk_id
 (project/'gradle-purifier.sdks.json').write_text(json.dumps(declaration,indent=2)+'\n')
 (project/'gradle/wrapper/gradle-wrapper.properties').write_bytes((a.positive/'gradle/wrapper/gradle-wrapper.properties').read_bytes())
 (project/'settings.gradle').write_text("rootProject.name='source-manager-control'\n")
 home=next(s['home'] for s in positive['sdks'] if s['id']==sdk_id)
 if name=='zero-tasks':(project/'build.gradle').write_text('// An actual manager is mandatory without SDK tasks.\n')
 else:
  (project/'src/main/java').mkdir(parents=True)
  (project/'src/main/java/ManagerOnly.java').write_text('public class ManagerOnly { public static void main(String[] a) { System.out.println("ALL_MANAGER_SOURCE_OK"); } }\n')
  (project/'build.gradle').write_text('''plugins { id 'java' }
tasks.withType(JavaCompile) { options.fork=true; options.forkOptions.javaHome=file('HOME'); options.forkOptions.executable='HOME/bin/javac' }
test.enabled=false
tasks.register('verifyManager',JavaExec) { classpath=sourceSets.main.runtimeClasspath; mainClass='ManagerOnly'; executable='HOME/bin/java' }
check.dependsOn verifyManager
'''.replace('HOME',home))
 originals={str(f.relative_to(project)):f.read_bytes() for f in project.rglob('*') if f.is_file()}
 env=dict(os.environ,XDG_CACHE_HOME=str(out/(name+'-cache')),GRADLE_USER_HOME=str(out/(name+'-gradle-home')))
 for stage, command in [('capture',[a.cli,'lock']),('relock',[a.cli,'lock','--relock']),('prefetch',[a.cli,'prefetch'])]:
  with (out/(name+'-'+stage+'.log')).open('w') as log:r=subprocess.run([str(x) for x in command],cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
  assert r.returncode==0,(name,stage,(out/(name+'-'+stage+'.log')).read_text()[-4000:])
  raw=(project/'gradle-purifier.lock.json').read_bytes()
  if stage=='capture':first=raw
  assert raw==first
 context=json.loads(first)['projectJdks']['x86_64-linux'];assert context['version']=='jvm-sdk-context-v2' and len(context['sdks'])==1
 assert context['manager']['sdk']==context['sdks'][0]['identityHash']
 if name=='zero-tasks':
  assert context['roles']==[]
  assert context['manager']['runtimeHome']!=context['sdks'][0]['home']
 else:
  assert len(context['roles'])>=2 and all(r['sdk']==context['manager']['sdk'] for r in context['roles'])
  assert 'ALL_MANAGER_SOURCE_OK' in (out/(name+'-capture.log')).read_text()
 runtime=json.loads((project/'.gradle-purifier/sdkContext').read_text())
 requisites=set(subprocess.check_output(['nix-store','--query','--requisites',str(project/'.gradle-purifier/sdkContext')],text=True).splitlines())
 assert set(runtime['sourceInputs']+[context['sdks'][0]['output']])<=requisites
 sdk_context_output=(project/'.gradle-purifier/sdkContext').resolve()
 with (project/'build.gradle').open('a') as f:f.write('\n// Project-only reference must not enter SDK closure: '+str(sdk_context_output)+'\n')
 for stage,command in [('changed-project-relock',[a.cli,'lock','--relock']),('changed-project-prefetch',[a.cli,'prefetch'])]:
  with (out/(name+'-'+stage+'.log')).open('w') as log:r=subprocess.run([str(x) for x in command],cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
  assert r.returncode==0,(name,stage,(out/(name+'-'+stage+'.log')).read_text()[-4000:])
 assert (project/'.gradle-purifier/sdkContext').resolve()==sdk_context_output,'project snapshot leaked into SDK closure'
 (project/'build.gradle').write_bytes(originals['build.gradle'])
 (project/'gradle-purifier.lock.json').write_bytes(first)
 assert all((project/f).read_bytes()==raw for f,raw in originals.items())
 rows.append(dict(case=name,status='passed',roles=len(context['roles']),manager=context['manager'],sourceCount=len(set(runtime['sourceInputs'])),relockByteIdentical=True,sourcePreserved=True))
 print(name,'PASS',flush=True)
(out/'result.json').write_text(json.dumps(dict(status='passed',cases=rows),indent=2)+'\n')
