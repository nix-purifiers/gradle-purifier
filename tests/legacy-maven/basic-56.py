#!/usr/bin/env python3
"""Bounded Gradle5.6.4 capture, cold replay, SDK execution and strict rejection."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_jvm import bind_fixture_sdks
from fixture_build import command as fixture_build
import argparse,hashlib,importlib.util,json,os,shutil,subprocess,zipfile
p=argparse.ArgumentParser()
for name in ['cli','sdks','cache','output']:p.add_argument('--'+name,type=Path,required=True)
p.add_argument('--sdk',action='store_true');a=p.parse_args();R=Path(__file__).resolve().parents[2];O=a.output.resolve();O.mkdir();P=O/'producer';sdk=json.loads(a.sdks.read_text());cli=a.cli.resolve();spec=importlib.util.spec_from_file_location('epoch',R/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(x for x in json.loads((R/'tests/epoch-probes/profiles.json').read_text())['profiles'] if x['id']=='gradle-5.6.4');gradle=Path(epoch.distribution(profile,a.cache.resolve()));epoch.fixture(P,profile,sdk);distribution=gradle.parents[1];rows=[]
env=dict(os.environ,JAVA_HOME=sdk['java11']['home'],GRADLE_USER_HOME=str(O/'capture-gradle-home'),XDG_CACHE_HOME=str(O/'cache'));env['PATH']=sdk['java11']['home']+'/bin:'+env['PATH']
def run(name,cmd,extra=None,bad=None):
 cmd=[str(x) for x in cmd]
 with (O/(name+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=P,env=dict(env,**(extra or {})),stdout=f,stderr=subprocess.STDOUT,timeout=600)
 text=(O/(name+'.log')).read_text();rows.append(dict(stage=name,command=cmd,exitCode=r.returncode,expectedRejection=bad));(O/'report.json').write_text(json.dumps(rows,indent=2));print(name,r.returncode,flush=True)
 assert (r.returncode!=0)==bool(bad) and (bad is None or bad in text),text[-5000:]
 return text
if a.sdk:
 libs=P/'libs';libs.mkdir()
 for name in ['junit-4.12.jar','hamcrest-core-1.3.jar']:shutil.copy2(distribution/'lib/plugins'/name,libs/name)
 (P/'src/test/java/example/NativeJUnitCheck.java').write_text('package example;public class NativeJUnitCheck {@org.junit.Test public void runtime(){org.junit.Assert.assertTrue(System.getProperty("java.version").startsWith("1.8."));System.out.println("NATIVE_JUNIT_JAVA8_EXECUTED");}}')
 doclet=P/'doclet-classes';doclet.mkdir();source=O/'ProbeDoclet.java';source.write_text('package diagnostic;public class ProbeDoclet {public static int optionLength(String o){return o.equals("-d")||o.equals("-doctitle")||o.equals("-windowtitle")?2:0;}public static boolean start(com.sun.javadoc.RootDoc r){if(!System.getProperty("java.version").startsWith("1.8."))throw new AssertionError();System.out.println("NATIVE_JAVADOC_JAVA8_EXECUTED");return true;}}');run('compile-doclet',[Path(sdk['java8']['home'])/'bin/javac','-cp',sdk['java8']['home']+'/lib/tools.jar','-d',doclet,source])
 with (P/'build.gradle').open('a') as f:f.write("\ndependencies { testCompile files('libs/junit-4.12.jar','libs/hamcrest-core-1.3.jar') }\ntest { enabled=true; executable='"+sdk['java8']['home']+"/bin/java';testLogging.showStandardStreams=true }\njavadoc { executable='"+sdk['java8']['home']+"/bin/javadoc';options.doclet='diagnostic.ProbeDoclet';options.docletpath=[file('doclet-classes')] }\n")
bind_fixture_sdks(P, 11, [8], cli, sdk)
source={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file()};args=['javadoc'] if a.sdk else [];run('native-check-upload',[gradle,'--offline','--no-daemon','clean','check','uploadArchives']+args);run('capture',[cli,'lock','--bind-sdks']+(['--']+args if args else []));first_lock=(P/'gradle-purifier.lock.json').read_bytes();run('explicit-relock',[cli,'lock','--bind-sdks','--relock']+(['--']+args if args else []));assert (P/'gradle-purifier.lock.json').read_bytes()==first_lock;shutil.copy2(P/'.gradle-purifier/lock-run/gradle-version.txt',O/'native-gradle-version.txt');cold=dict(GRADLE_USER_HOME=str(O/'cold-gradle-home'));run('prefetch',[cli,'prefetch'],cold);assert not (O/'cold-gradle-home/caches').exists();shutil.rmtree(P/'build');run('cold-offline',['unshare','-rn','sh','-c','ip link set lo up && exec "$@"','legacy56-cold',P/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','clean','check']+args,cold)
values=dict(x.split('=',1) for x in cli.read_text().splitlines() if x.startswith(('lib=','nixpkgs=','purifier_source=')));base=['nix-build','--no-out-link',values['lib']+'/command.nix','--argstr','nixpkgs',values['nixpkgs'],'--argstr','purifierSource',values['purifier_source'],'--argstr','src',P,'--argstr','lockFile',P/'gradle-purifier.lock.json']
for name,attr,extra in [('pure-tests','tests',[]),('pure-release','release',[]),('rebuild-tests','tests',['--check']),('rebuild-producer','packages.epoch',['--check'])]:run(name,fixture_build(cli,P,attr,extra=extra))
for attr in ['projections.maven','packages.epoch.projections.maven']:run('reject-'+attr,fixture_build(cli,P,attr),bad='Gradle5.6.4 strict legacy Maven projection')
run('reject-publish',[cli,'publish','uploadArchives'],bad='Gradle5.6.4 strict legacy Maven publish')
if not a.sdk:
 validation_context=Path(run('sdk-validation-context',fixture_build(cli,P,'sdkContext')).strip().splitlines()[-1])
 init=O/'enable-test.init.gradle';init.write_text("gradle.projectsEvaluated { gradle.rootProject.tasks.getByName('test').enabled=true }\n");run('reject-enabling-disabled-test',['unshare','-rn','sh','-c','ip link set lo up && exec "$@"','legacy56-enable',P/'.gradle-purifier/distribution/bin/gradle','--offline','--no-daemon','-Dgradle.purifier.mode=interactive','--init-script',Path(values['lib'])/'task-jdks.init.gradle','--init-script',init,'test'],dict(cold,GRADLE_PURIFIER_JDK_CONTEXT=str(validation_context)),bad='cannot resolve SDK role for :test')
assert all(hashlib.sha256((P/n).read_bytes()).hexdigest()==h for n,h in source.items());markers=['EPOCH TEST PASSED']+(['NATIVE_JUNIT_JAVA8_EXECUTED','NATIVE_JAVADOC_JAVA8_EXECUTED'] if a.sdk else [])
for name in ['native-check-upload','capture','cold-offline','pure-tests','rebuild-tests']:assert all(m in (O/(name+'.log')).read_text() for m in markers)
# Preserve the actual native and Nix outputs so CI artifacts can verify parity.
release=Path((O/'pure-release.log').read_text().strip().splitlines()[-1]);native=P/'repository/example/epoch/1.0';pure=release/'epoch/example/epoch/1.0';capture=P/'.gradle-purifier/lock-run';mirror=capture/'output/epoch/maven/example/epoch/1.0';payloads=O/'payloads';payloads.mkdir()
def jar_payload(path):
 with zipfile.ZipFile(path) as z:
  major=int.from_bytes(z.read('example/Epoch.class')[6:8],'big')
  return {name:hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist() if not name.endswith('/')},major
native_payload,native_major=jar_payload(native/'epoch-1.0.jar');pure_payload,pure_major=jar_payload(pure/'epoch-1.0.jar');assert native_payload==pure_payload and native_major==pure_major==52;assert (native/'epoch-1.0.pom').read_bytes()==(pure/'epoch-1.0.pom').read_bytes();assert (capture/'build/build/libs/epoch-1.0.jar').read_bytes()==(mirror/'epoch-1.0.jar').read_bytes()
for name,path in [('native.jar',native/'epoch-1.0.jar'),('native.pom',native/'epoch-1.0.pom'),('pure.jar',pure/'epoch-1.0.jar'),('pure.pom',pure/'epoch-1.0.pom'),('capture-original.jar',capture/'build/build/libs/epoch-1.0.jar'),('capture-mirror.jar',mirror/'epoch-1.0.jar')]:shutil.copy2(path,payloads/name)
parity=dict(nativePureJarPayloadEqual=True,nativePurePomBytesEqual=True,captureOriginalMirrorJarBytesEqual=True,classMajor=52,crossBuildRawJarByteParityClaim=False,payloadSha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in payloads.iterdir()})
(O/'context.json').write_text(json.dumps(dict(profile=profile,sourceHashes=source,sourcePreserved=True,actualRuntimeAssertions=markers,coldInitialCachesAbsent=True,coldNetworkNamespace=True,disabledTestRuntimeCredit=False,cli=str(cli),cliSha256=hashlib.sha256(cli.read_bytes()).hexdigest(),sdk=sdk,strictMetadataSupported=False,explicitRelockByteIdentical=True,nativeGradleVersionOutputSha256=hashlib.sha256((O/'native-gradle-version.txt').read_bytes()).hexdigest(),payloadParity=parity),indent=2))
