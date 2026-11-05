#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Adapter-only SDK selection controls; the full legacy fixture remains unsupported."""
from pathlib import Path
import argparse,hashlib
import importlib.util,json,os,subprocess,re
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--cli',type=Path,required=True)
parser.add_argument('--cache',type=Path,required=True)
parser.add_argument('--sdks',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
repo=Path(__file__).resolve().parents[2];out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);cli=args.cli.resolve();variables={k:v for k,v in re.findall(r'^(lib|sdk_descriptor)=(.+)$',cli.read_text(),re.M)};lib=Path(variables['lib']);descriptor=json.loads(Path(variables['sdk_descriptor']).read_text());spec=importlib.util.spec_from_file_location('epoch',repo/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);spec=importlib.util.spec_from_file_location('capture',repo/'nix/lib/capture-project-jdks.py');capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(capture);sdks=json.loads(args.sdks.read_text());profile=next(p for p in json.loads((repo/'tests/epoch-probes/profiles.json').read_text())['profiles'] if p['id']=='gradle-7.6.6');args.cache=args.cache.resolve();args.cache.mkdir(parents=True,exist_ok=True);gradle=epoch.distribution(profile,args.cache)
context=dict(scope='Native SDK adapter only; no complete legacy purifier lifecycle credit',sdkInputs=sdks,profile=profile,
 sourceRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),cli=str(cli),immutableLib=str(lib),
 sourceHashes={p:hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=repo,text=True).splitlines() if (repo/p).is_file()})
(out/'context.json').write_text(json.dumps(context,indent=2)+'\n')
alias=out/'mutable-sdk';alias.symlink_to(sdks['java8']['home']);rows=[]
# Retain the unchanged full purifier failure: selecting explicit native roles
# here does not certify the disabled Test task or the complete legacy lifecycle.
full=out/'preserved-full-barrier';full.mkdir();epoch.fixture(full/'project',profile,sdks)
full_env=dict(os.environ,JAVA_HOME=sdks['java17']['home'],GRADLE_USER_HOME=str(full/'gradle-home'),HOME=str(full/'home'))
Path(full_env['HOME']).mkdir()
with (full/'run.log').open('w') as log:
 failure=subprocess.run([str(cli),'lock'],cwd=full/'project',env=full_env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
assert failure.returncode!=0 and ':test has no selected SDK' in (full/'run.log').read_text()
rows.append(dict(case='unchanged-full-fixture-unknown-test',exitCode=failure.returncode,rejected=True))
(out/'report.json').write_text(json.dumps(rows,indent=2)+'\n')
print('unchanged full fixture barrier PASS',flush=True)
for name in ['exact-immutable','alias-initial','alias-retargeted','provider-conflict']:
 if name=='alias-retargeted':alias.unlink();alias.symlink_to(sdks['java17']['home'])
 case=out/name;case.mkdir();project=case/'project';epoch.fixture(project,profile,sdks);build=project/'build.gradle';text=build.read_text()
 if name.startswith('alias'):text=text.replace(sdks['java8']['home'],str(alias))
 if name=='provider-conflict':text+='\ncompileJava.javaCompiler.set(javaToolchains.compilerFor { languageVersion = JavaLanguageVersion.of(17) })\n'
 build.write_text(text);env=dict(os.environ,JAVA_HOME=sdks['java17']['home'],GRADLE_USER_HOME=str(case/'home'),GRADLE_PURIFIER_MODE='lock',GRADLE_PURIFIER_OUTPUT=str(case/'output'));cmd=['bash',str(gradle),'--offline','--no-daemon','--init-script',str(lib/'task-jdks.init.gradle'),':compileJava',':compileTestJava',':epochTest']
 with (case/'run.log').open('w') as log:r=subprocess.run(cmd,cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
 if name=='provider-conflict':
  assert r.returncode!=0 and 'conflicting provider and explicit SDKs' in (case/'run.log').read_text();result=dict(case=name,rejected=True,reason='conflicting provider and explicit SDKs')
 else:
  assert r.returncode==0,(name,(case/'run.log').read_text()[-3000:]);receipt=json.loads((case/'output/task-sdks.json').read_text());assert len(receipt['tasks'])==3 and all(t['selectionProvenance']=='configured-native-executable' for t in receipt['tasks'])
  try:context=capture.capture(receipt,descriptor,repo/'nix/lib/capture-project-jdk.nix')
  except ValueError as e:
   assert name.startswith('alias') and 'unsupported ambient or mutable SDK home' in str(e),(name,e);result=dict(case=name,rejected=True,reason=str(e))
  else:
   assert name=='exact-immutable';(case/'context.json').write_text(json.dumps(context,indent=2)+'\n');result=dict(case=name,accepted=True,roles=context['x86_64-linux']['roles']);assert all(r['provenance']=='configured-native-executable' for r in result['roles'])
 rows.append(result);(out/'report.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,'PASS',flush=True)

# The public lock reader deliberately accepts the new provenance only for
# compiler/launcher roles. Unknown provenance never becomes a typed SDK input.
import copy
receipt=json.loads((out/'exact-immutable/output/task-sdks.json').read_text())
accepted=json.loads((out/'exact-immutable/context.json').read_text())
def rejected_receipt(name,value,needle):
 try:capture.capture(value,descriptor,repo/'nix/lib/capture-project-jdk.nix')
 except ValueError as error:
  assert needle in str(error),(name,error);rows.append(dict(case=name,rejected=True,reason=str(error)))
 else:raise AssertionError(name+' accepted')
changed=copy.deepcopy(receipt);changed['tasks'][0]['selectionProvenance']='invented-selection'
rejected_receipt('unknown-provenance',changed,'unsupported SDK selection provenance')
changed=copy.deepcopy(receipt);changed['tasks'][0]['role']='javadoc'
rejected_receipt('unsupported-explicit-role',changed,'unsupported for javadoc')
fake_expr='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
 p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[];};
 in p.runCommand "different-sdk-release-metadata" {release="${p.jdk8_headless}/lib/openjdk/release";} ''
 mkdir -p "$out/lib/openjdk/bin"
 cp "$release" "$out/lib/openjdk/release"
 touch "$out/lib/openjdk/bin/java" "$out/lib/openjdk/bin/javac"
 '' '''
env=dict(os.environ,SDK_SOURCE=str(repo))
fake=Path(subprocess.check_output(['nix','build','--impure','--no-link','--print-out-paths','--expr',fake_expr],env=env,text=True).strip())/'lib/openjdk'
assert (fake/'release').read_bytes()==(Path(sdks['java8']['home'])/'release').read_bytes()
changed=copy.deepcopy(receipt)
for row in changed['tasks']:
 binary='javac' if row['role']=='compiler' else 'java'
 row.update(home=str(fake),canonicalHome=str(fake),executable=str(fake/'bin'/binary),canonicalExecutable=str(fake/'bin'/binary))
rejected_receipt('same-release-wrong-output',changed,'does not match the pinned Nix SDK output')
original=json.loads((repo/'tests/fixtures/java-library/gradle-purifier.lock.json').read_text())
def boundary(name,contexts,valid):
 lock=copy.deepcopy(original);lock['projectJdks']=contexts
 payload=json.dumps({k:v for k,v in lock.items() if k!='contentHash'},sort_keys=True,separators=(',',':'),ensure_ascii=False)
 lock['contentHash']='sha256:'+hashlib.sha256(payload.encode()).hexdigest()
 file=out/(name+'.lock.json');file.write_text(json.dumps(lock,indent=2)+'\n')
 expr='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
 p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[];};
 read=import (/. + builtins.getEnv "SDK_SOURCE" + "/nix/lib/read-lock.nix") {lib=p.lib;};
 in (read {src=/. + builtins.getEnv "SDK_SOURCE" + "/tests/fixtures/java-library";
 lockFile=/. + builtins.getEnv "SDK_LOCK";}).problems'''
 problems=json.loads(subprocess.check_output(['nix','eval','--json','--impure','--expr',expr],env=dict(env,SDK_LOCK=str(file)),text=True))
 (out/(name+'.problems.json')).write_text(json.dumps(problems,indent=2)+'\n')
 assert (problems==[])==valid,(name,problems)
 rows.append(dict(case=name,accepted=valid,problems=problems))
boundary('public-explicit-provenance',accepted,True)
changed=copy.deepcopy(accepted);changed['x86_64-linux']['roles'][0]['provenance']='invented-selection'
boundary('public-unknown-provenance',changed,False)
changed=copy.deepcopy(accepted);changed['x86_64-linux']['roles'][0]['role']='javadoc'
boundary('public-unsupported-explicit-role',changed,False)
(out/'report.json').write_text(json.dumps(rows,indent=2)+'\n')
print('public provenance boundaries PASS',flush=True)
