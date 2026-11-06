#!/usr/bin/env python3
"""Pinned native Maven semantics and the actual lazy legacy companion."""
from pathlib import Path
import argparse, hashlib, importlib.util, json, os, subprocess, sys, xml.etree.ElementTree as ET
sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--sdk-info', required=True, type=Path)
parser.add_argument('--gradle', required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
out = args.output.resolve(); out.mkdir()
sdks = json.loads(args.sdk_info.read_text())
spec = importlib.util.spec_from_file_location('epoch', root/'tests/epoch-probes/probe.py')
epoch = importlib.util.module_from_spec(spec); spec.loader.exec_module(epoch)
profile = next(p for p in json.loads((root/'tests/epoch-probes/profiles.json').read_text())['profiles'] if p['id']=='gradle-6.9.4')
rows = []
init = out/'native.init.gradle'
init.write_text('''import groovy.json.JsonOutput
gradle.ext.purifierLegacyMaven=[mode:'lock',output:System.getenv('NATIVE_OUTPUT'),bindings:[],artifacts:[:]]
apply from: new File(System.getenv('LEGACY_COMPANION'))
allprojects { p -> p.plugins.withId('maven') { gradle.ext.purifierLegacyMaven.register(p) } }
gradle.projectsEvaluated {
 def descriptor=gradle.ext.purifierLegacyMaven.describe(gradle.rootProject)
 new File(System.getenv('NATIVE_DESCRIPTOR')).text=JsonOutput.toJson(descriptor)
}
''')
cases = {
 'default': ('', ('example','epoch','1.0'), None),
 'explicit': ("uploadArchives.repositories.mavenDeployer { pom.groupId='override.group';pom.artifactId='override-artifact';pom.version='2.3' }", ('override.group','override-artifact','2.3'), None),
 'literal-sentinels': ("uploadArchives.repositories.mavenDeployer { pom.groupId='unknown';pom.artifactId='empty-project';pom.version='0' }", ('example','epoch','1.0'), None),
 'classifier': ("jar.archiveClassifier='tests'", ('example','epoch','1.0'), 'requires exactly one unclassified JAR'),
 'extension': ("jar.archiveExtension='zip'", ('example','epoch','1.0'), 'requires exactly one unclassified JAR'),
 'callback-coordinate': ("uploadArchives.repositories.mavenDeployer { pom.whenConfigured { p -> p.model.groupId='callback.group' } }", ('example','epoch','1.0'), 'native snapshot POM coordinates changed'),
}
for name,(extra,coordinates,rejection) in cases.items():
 work=out/name;work.mkdir();project=work/'project';epoch.fixture(project,profile,sdks)
 with (project/'build.gradle').open('a') as f:f.write('\n'+extra+'\n')
 env=dict(os.environ,JAVA_HOME=sdks['java11']['home'],GRADLE_USER_HOME=str(out/'gradle-home'),NATIVE_OUTPUT=str(work/'snapshot'),NATIVE_DESCRIPTOR=str(work/'descriptor.json'),LEGACY_COMPANION=str(root/'nix/lib/legacy-maven.init.gradle'))
 def run(stage,tasks,script=False):
  cmd=['bash',str(args.gradle.resolve()),'--offline','--no-daemon']+(['--init-script',str(init)] if script else [])+tasks
  with (work/(stage+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
  rows.append(dict(case=name,stage=stage,command=cmd,exitCode=r.returncode));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,stage,r.returncode,flush=True);return r.returncode,(work/(stage+'.log')).read_text()
 rc,log=run('original',['clean','check','uploadArchives']);assert rc==0,log[-4000:]
 rc,log=run('snapshot',['purifierLegacySnapshot'],True)
 if rejection:
  assert rc!=0 and rejection in log,log[-4000:]
  # Original remote/local target is never rescheduled by the snapshot run.
  assert 'Task :uploadArchives' not in log
 else:
  assert rc==0,log[-4000:];group,artifact,version=coordinates;sub=Path(group.replace('.','/'))/artifact/version
  expected=project/'repository'/sub;actual=work/'snapshot/epoch/maven'/sub
  for suffix in ['jar','pom']:
   filename=f'{artifact}-{version}.{suffix}';assert (expected/filename).read_bytes()==(actual/filename).read_bytes(),filename
  d=json.loads((work/'descriptor.json').read_text())[0];assert [d['groupId'],d['artifactId'],d['version']]==list(coordinates)
context={'profile':profile,'sdks':sdks,'sourceHashes':{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=root,text=True).splitlines() if (root/p).is_file()},'scope':'Native semantics and actual companion only; full lifecycle separately required'}
(out/'context.json').write_text(json.dumps(context,indent=2));print('NATIVE_LEGACY_SEMANTICS_PASS',flush=True)
