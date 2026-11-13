from pathlib import Path
import os,subprocess,json,hashlib,shutil
# SPDX-License-Identifier: MIT
# Exercise real Gradle ownership; SDK receipts are emitted only by the installed init script.
import argparse
parser=argparse.ArgumentParser(description=__doc__)
for name in ['cli','baseline-cli','gradle','java-home','java8-home','results']:
    parser.add_argument('--'+name,type=Path,required=True)
args=parser.parse_args()
W=Path(__file__).resolve().parents[1];O=args.results.resolve();O.mkdir(parents=True);CLI=args.cli.resolve()
def installed_helper(cli):
    values=dict(x.split('=',1) for x in cli.read_text().splitlines() if x.startswith('lib='))
    return Path(values['lib'])/'task-jdks.init.gradle'
new=installed_helper(CLI);old=installed_helper(args.baseline_cli.resolve())
assert new.read_bytes()==(W/'pkgs/build-gradle-project/task-jdks.init.gradle').read_bytes()
gradle=args.gradle.resolve();java=args.java_home.resolve();java8=args.java8_home.resolve()
assert gradle.is_file() and (java/'bin/java').is_file() and (java8/'bin/java').is_file()
observer='''import groovy.json.JsonOutput
import java.security.MessageDigest
def destination = new File(System.getenv('ROOT_TEST_API'))
def record = { phase ->
 def file = new File(System.getenv('GRADLE_PURIFIER_OUTPUT'), 'task-sdks.json')
 def digest = file.isFile() ? MessageDigest.getInstance('SHA-256').digest(file.bytes).encodeHex().toString() : null
 destination << JsonOutput.toJson([phase:phase, top:gradle.parent == null, root:gradle.rootProject.projectDir.canonicalPath, receipt:digest])+'\\n'
}
gradle.projectsLoaded { record('loaded-PHASE') }
gradle.taskGraph.whenReady { record('graph-PHASE') }
'''
before=O/'before.init.gradle';after=O/'after.init.gradle';before.write_text(observer.replace('PHASE','before'));after.write_text(observer.replace('PHASE','after'));rows=[]
def fixture(root,name='fixture',plugin=False,child=False,compiler=17):
 root.mkdir(parents=True);(root/'settings.gradle').write_text(("pluginManagement { includeBuild('../java') }\nplugins { id 'fixture.settings' }\n" if plugin else '')+f"rootProject.name='{name}'\n"+("include 'child'\n" if child else ''))
 (root/'build.gradle').write_text("plugins { id 'java' }\njava { toolchain { languageVersion = JavaLanguageVersion.of("+str(compiler)+") } }\n"+("subprojects { apply plugin:'java' }\n" if child else ''))
 p=root/'src/main/java/Probe.java';p.parent.mkdir(parents=True);p.write_text('public class Probe { public static int value(){ return 17; } }\n')
 if child:
  p=root/'child/src/main/java/Child.java';p.parent.mkdir(parents=True);p.write_text('public class Child {}\n')
 return root

def run(name,boundary,cwd,args,adapter=new,success=True,receipt=True,reason=None):
 out=O/name;out.mkdir();env=os.environ.copy()
 for key in ['JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS']:env.pop(key,None)
 env.update(JAVA_HOME=str(java),GRADLE_USER_HOME=str(O/'gradle-home'),GRADLE_OPTS='-XX:ActiveProcessorCount=2 -Dorg.gradle.workers.max=2',GRADLE_PURIFIER_MODE='lock',GRADLE_PURIFIER_BUILD_ROOT=str(boundary),GRADLE_PURIFIER_OUTPUT=str(out/'sdk'),ROOT_TEST_API=str(out/'api.jsonl'))
 command=[str(gradle),'--offline','--no-daemon','--max-workers','2','-Dorg.gradle.jvmargs=-XX:ActiveProcessorCount=2','-Porg.gradle.java.installations.auto-download=false','-Porg.gradle.java.installations.auto-detect=false','-Porg.gradle.java.installations.paths='+str(java)+','+str(java8),'--init-script',str(before),'--init-script',str(adapter),'--init-script',str(after),*args]
 with (out/'native.log').open('w') as f:p=subprocess.run(command,cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=600)
 text=(out/'native.log').read_text();row={'name':name,'argv':command,'cwd':str(cwd),'boundary':str(boundary),'exit':p.returncode,'expectedSuccess':success,'adapter':str(adapter),'log':str(out/'native.log')};rows.append(row);(O/'commands.json').write_text(json.dumps(rows,indent=2)+'\n');assert (p.returncode==0)==success,(name,text[-5000:]);assert reason is None or reason in text,(name,text[-3000:]);r=out/'sdk/task-sdks.json';assert r.exists()==receipt,(name,r.exists());print(name,'pass',flush=True)
 events=[json.loads(x) for x in (out/'api.jsonl').read_text().splitlines()] if (out/'api.jsonl').exists() else []
 return (json.loads(r.read_text()) if r.exists() else None),events,r
basic=fixture(O/'default-source');a,_,ar=run('default-baseline',basic,basic,['compileJava'],old);b,_,br=run('default-candidate',basic,basic,['compileJava','--rerun-tasks']);assert ar.read_bytes()==br.read_bytes();_,_,rr=run('default-relock',basic,basic,['compileJava','--rerun-tasks']);assert rr.read_bytes()==br.read_bytes();assert len(b['tasks'])==1 and b['tasks'][0]['task']==':compileJava'
full=O/'full-source';selected=fixture(full/'lib/kotlin',plugin=True);included=full/'lib/java';included.mkdir();(included/'settings.gradle').write_text("rootProject.name='java'\n");(included/'build.gradle').write_text("plugins { id 'java-gradle-plugin' }\ngradlePlugin { plugins { proof { id='fixture.settings'; implementationClass='fixture.ProofPlugin' } } }\n");p=included/'src/main/java/fixture/ProofPlugin.java';p.parent.mkdir(parents=True);p.write_text('package fixture; public class ProofPlugin implements org.gradle.api.Plugin<org.gradle.api.initialization.Settings> { public void apply(org.gradle.api.initialization.Settings s){System.out.println("SETTINGS_PLUGIN_APPLIED");}}\n')
_,_,missing=run('nested-baseline',full,full,['-p','lib/kotlin','compileJava'],old,receipt=False)
c,events,_=run('nested-candidate',full,full,['-p','lib/kotlin','compileJava','--rerun-tasks']);assert len(c['tasks'])==1 and c['tasks'][0]['task']==':compileJava';assert 'SETTINGS_PLUGIN_APPLIED' in (O/'nested-candidate/native.log').read_text();assert next(i for i,e in enumerate(events) if not e['top'] and e['phase']=='loaded-before')<next(i for i,e in enumerate(events) if e['top'] and e['phase']=='loaded-before');assert all(e['receipt'] is None for e in events if not e['top'] and e['phase'].startswith('graph-'))
ancestor=fixture(O/'ancestor-source',child=True);c,events,_=run('ancestor-discovery',ancestor,ancestor/'child',['compileJava']);assert c['tasks'][0]['task']==':child:compileJava';assert all(e['root']==str(ancestor.resolve()) for e in events if e['top'])
filtered=O/'filtered-source';shutil.copytree(full,filtered,ignore=shutil.ignore_patterns('.gradle','build'));shutil.rmtree(filtered/'lib/java');run('filtered-sibling-reject',filtered,filtered,['-p','lib/kotlin','compileJava'],success=False,receipt=False,reason='does not exist')
boundary=fixture(O/'boundary');outside=fixture(O/'boundary-sibling');run('outside-root-reject',boundary,boundary,['-p',str(outside),'compileJava'],success=False,receipt=False,reason='outside the copied source boundary');(boundary/'linked').symlink_to(outside,target_is_directory=True);run('symlink-root-reject',boundary,boundary,['-p','linked','compileJava'],success=False,receipt=False,reason='outside the copied source boundary')
# Included non-manager SDKs retain their prior rejection, even with a selected nested root.
oldBuild=(included/'build.gradle').read_text();(included/'build.gradle').write_text(oldBuild+"\njava { toolchain { languageVersion = JavaLanguageVersion.of(8) } }\n");run('included-nonmanager-reject',full,full,['-p','lib/kotlin','compileJava','--rerun-tasks'],success=False,receipt=False,reason='non-manager SDK tasks in nested builds require explicit supported SDK input handling');(included/'build.gradle').write_text(oldBuild)
# Existing full-source definition hashing, not selector parsing, invalidates argument/settings changes.
def definition(args):
 expr='let f=builtins.getFlake "path:'+str(W)+'";p=import f.inputs.nixpkgs {system=builtins.currentSystem;};in import '+str(W)+'/lib-overlays/default/inputs.nix {lib=p.lib;src='+str(full)+';gradleArguments='+json.dumps(args).replace(',','')+';}'
 return subprocess.check_output(['nix','eval','--impure','--raw','--expr',expr],text=True)
h=definition(['-p','lib/kotlin']);assert h!=definition(['-p','lib/java']);settings=selected/'settings.gradle';original=settings.read_bytes();settings.write_bytes(original+b'\n// definition mutation\n');assert h!=definition(['-p','lib/kotlin']);settings.write_bytes(original)
(O/'proof.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=W,text=True).strip(),'cli':str(CLI),'installedAdapter':str(new),'adapterSHA256':hashlib.sha256(new.read_bytes()).hexdigest(),'runs':rows,'defaultReceiptBaselineCandidateRelockByteEqual':True,'nestedSDKReceiptNativeCallback':True,'includedFirstNoReceiptOverwrite':True,'ancestorDiscoveryActualAPI':True,'outsideAndSymlinkRootReject':True,'filteredSiblingFailsNatively':True,'includedNonmanagerGuardPreserved':True,'argsAndSettingsHashMutation':True,'scope':'Real Gradle8.14.6/JDK17 publicAPI ownership smoke; separate actualThrift and boundSDKCLI proof still required.'},indent=2)+'\n');print('API ROOT PROOF PASS',flush=True)
