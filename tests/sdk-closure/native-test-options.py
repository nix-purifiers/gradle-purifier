#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Native effective-Test runtime identity and failed-copy/late-change controls."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
for name in ['cli','cache','sdks','output']:parser.add_argument('--'+name,type=Path,required=True)
a=parser.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);sdk=json.loads(a.sdks.read_text());cli=a.cli.resolve();lib=Path(re.search(r'^lib=(.+)$',cli.read_text(),re.M).group(1))
spec=importlib.util.spec_from_file_location('epoch',ROOT/'tests/epoch-probes/probe.py');epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch);profile=next(p for p in json.loads((ROOT/'tests/epoch-probes/profiles.json').read_text())['profiles'] if p['id']=='gradle-7.6.6');a.cache=a.cache.resolve();a.cache.mkdir(parents=True,exist_ok=True);gradle=epoch.distribution(profile,a.cache);dist=gradle.parent.parent;java8=sdk['java8']['home'];java17=sdk['java17']['home'];rows=[]
(out/'context.json').write_text(json.dumps(dict(sourceRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),sdk=sdk,profile=profile,cli=str(cli),lib=str(lib),scope='Native API and guard controls only; late-hook execution can precede rejection',sourceHashes={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines() if (ROOT/p).is_file()}),indent=2)+'\n')
cases=[('native-default',True,java17,None),('explicit-before-graph',True,java8,None),('provider-before-graph',True,java8,None),('late-explicit-graph',False,None,'changed its SDK after graph selection'),('late-provider-same-home',False,None,'changed its SDK after graph selection'),('late-explicit-after-beforeTask',False,java8,'changed its SDK during execution'),('overridden-process-copy',False,None,'unsupported native Test fork-options implementation'),('overridden-execution-copy',False,None,'unsupported native Test fork-options implementation'),('overridden-execution-spec',False,None,'unsupported native Test execution-spec implementation'),('native-missing-executable',False,None,'configured executable does not exist'),('provider-error',False,None,'NATIVE_PROVIDER_THROW_SENTINEL')]
for name,success,actual_home,needle in cases:
 work=out/name;work.mkdir();(work/'settings.gradle').write_text("rootProject.name='native-test-options'\n");expected=actual_home or java17
 build="""apply plugin: 'java'
sourceCompatibility='1.8'
targetCompatibility='1.8'
compileJava.options.fork=true
compileJava.options.forkOptions.executable='JAVA8/bin/javac'
compileTestJava.options.fork=true
compileTestJava.options.forkOptions.executable='JAVA8/bin/javac'
dependencies { testImplementation files('DIST/lib/junit-4.13.2.jar','DIST/lib/hamcrest-core-1.3.jar') }
test { systemProperty 'proof.runtime.file','MARKER'; systemProperty 'proof.expected.executable','EXPECTED/bin/java' }
""".replace('JAVA8',java8).replace('DIST',str(dist)).replace('MARKER',str(work/'runtime.properties')).replace('EXPECTED',expected)
 if name=='explicit-before-graph':build+="\ntest.executable='"+java8+"/bin/java'\n"
 if name=='provider-before-graph':build+='\ntest.javaLauncher.set(javaToolchains.launcherFor { languageVersion=JavaLanguageVersion.of(8) })\n'
 if name=='late-explicit-graph':build+="\ngradle.taskGraph.whenReady { test.executable='"+java8+"/bin/java' }\n"
 if name=='late-provider-same-home':build+='\ngradle.taskGraph.whenReady { test.javaLauncher.set(javaToolchains.launcherFor { languageVersion=JavaLanguageVersion.of(17) }) }\n'
 if name=='late-explicit-after-beforeTask':build+="\ngradle.taskGraph.beforeTask { t -> if(t.name=='test') t.executable='"+java8+"/bin/java' }\n"
 task_name='test'
 if name=='native-missing-executable':build+="\ntest.executable='"+str(work/'missing-sdk/bin/java')+"'\n"
 if name=='provider-error':build+="\ntest.javaLauncher.set(providers.provider { throw new GradleException('NATIVE_PROVIDER_THROW_SENTINEL') })\n"
 overrides={
  'overridden-process-copy':"org.gradle.api.tasks.testing.Test copyTo(org.gradle.process.ProcessForkOptions sink) { sink.setExecutable('"+java17+"/bin/java'); this }",
  'overridden-execution-copy':"org.gradle.api.tasks.testing.Test copyTo(org.gradle.process.JavaForkOptions sink) { super.copyTo(sink); sink.setExecutable('"+java8+"/bin/java'); this }",
  'overridden-execution-spec':'protected org.gradle.api.internal.tasks.testing.JvmTestExecutionSpec createTestExecutionSpec() { super.createTestExecutionSpec() }'}
 if name in overrides:
  build='abstract class ControlledCopyTest extends org.gradle.api.tasks.testing.Test { @Override '+overrides[name]+' }\n'+build
  build+="\ntasks.register('runtimeProbe', ControlledCopyTest) { dependsOn testClasses; testClassesDirs=sourceSets.test.output.classesDirs; classpath=sourceSets.test.runtimeClasspath; systemProperty 'proof.runtime.file','"+str(work/'runtime.properties')+"'; systemProperty 'proof.expected.executable','"+expected+"/bin/java' }\n"
  task_name='runtimeProbe'
 (work/'build.gradle').write_text(build);(work/'gradle.properties').write_text('org.gradle.java.installations.auto-detect=false\norg.gradle.java.installations.auto-download=false\norg.gradle.java.installations.paths='+java8+','+java17+'\n')
 source=work/'src/test/java/RuntimeIdentityTest.java';source.parent.mkdir(parents=True);source.write_text('''import java.io.*;
import java.util.Properties;
import org.junit.Test;
import static org.junit.Assert.*;
public class RuntimeIdentityTest {
 @Test public void runtimeIdentity() throws Exception {
  String actual=new File("/proc/self/exe").getCanonicalPath();
  Properties p=new Properties();p.setProperty("actual.executable",actual);
  p.setProperty("java.home",System.getProperty("java.home"));
  try(OutputStream out=new FileOutputStream(System.getProperty("proof.runtime.file"))) {p.store(out,"runtime identity only");}
  assertEquals(new File(System.getProperty("proof.expected.executable")).getCanonicalPath(),actual);
 }
}
''')
 env=dict(os.environ,JAVA_HOME=java17,GRADLE_USER_HOME=str(out/'gradle-home'),GRADLE_PURIFIER_MODE='lock',GRADLE_PURIFIER_OUTPUT=str(work/'output'))
 command=['bash',str(gradle),'--offline','--no-daemon','--init-script',str(lib/'task-jdks.init.gradle'),task_name]
 with (work/'run.log').open('w') as log:r=subprocess.run(command,cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
 text=(work/'run.log').read_text();marker=work/'runtime.properties';row=dict(case=name,exitCode=r.returncode,expectedSuccess=success,runtimeOccurred=marker.exists(),expectedReason=needle);rows.append(row);(out/'report.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,r.returncode,marker.exists(),flush=True);assert (r.returncode==0)==success,(name,text[-4000:])
 if needle:assert needle in text,(name,text[-4000:])
 if actual_home:
  assert marker.exists();props=dict(line.split('=',1) for line in marker.read_text().splitlines() if '=' in line and not line.startswith('#'));assert props['actual.executable']==str(Path(actual_home)/'bin/java');row['actualExecutable']=props['actual.executable'];xml=ET.parse(work/'build/test-results/test/TEST-RuntimeIdentityTest.xml').getroot();assert xml.attrib['tests']=='1' and xml.attrib['failures']=='0' and xml.attrib['errors']=='0'
 else:assert not marker.exists()
 if success:
  receipt=json.loads((work/'output/task-sdks.json').read_text());selected=next(v for v in receipt['tasks'] if v['task']==':test');assert selected['executable']==str(Path(actual_home)/'bin/java');assert selected.get('selectionProvenance')==('native-effective-fork-options' if name=='native-default' else 'configured-native-executable' if name=='explicit-before-graph' else None)
 (out/'report.json').write_text(json.dumps(rows,indent=2)+'\n')
# Exercise exact production sink/validator code with malformed values. These
# are unit controls, not claims of malformed output from the pinned native API.
text=(lib/'task-jdks.init.gradle').read_text()
helper=text[text.index('def validateNativeTestExecutable ='):text.index('def sameSelection =')]
probe=out/'helper-guards.init.gradle'
unit=r"""
def forkInterface=Class.forName('org.gradle.process.ProcessForkOptions',false,getClass().classLoader)
def good=System.getenv('SDK17_HOME')+'/bin/java'
def other=System.getenv('SDK8_HOME')+'/bin/java'
def guards=[]
def rejects={ name,needle,operation ->
 try {operation();throw new GradleException('GUARD_ACCEPTED_'+name)}
 catch(GradleException error) {assert error.message.contains(needle);guards<<[case:name,rejected:true]}
}
[[],[null],[good,null],[good,other]].eachWithIndex {value,index -> rejects('absent-or-ambiguous-'+index,'absent or ambiguous') {validateNativeTestExecutable(value)} }
['java','/tmp/java','/usr/bin/javac'].eachWithIndex {value,index -> rejects('malformed-'+index,'malformed native Test effective executable') {validateNativeTestExecutable([value])} }
[[],[null]].each {prefix -> assert validateNativeTestExecutable(prefix+[good]).absolutePath==good }
assert validateNativeTestExecutable([good,good]).absolutePath==good
rejects('unsupported-operation','unsupported native Test fork-options copy operation') {nativeForkOptionsTarget(forkInterface,[]).environment([:])}
rejects('operation-overflow','unsupported native Test fork-options copy operations') {def target=nativeForkOptionsTarget(forkInterface,[]);20.times {target.setExecutable(good)}}
rejects('empty-value','malformed native Test effective executable') {nativeForkOptionsTarget(forkInterface,[]).setExecutable('')}
rejects('object-value','malformed native Test effective executable') {nativeForkOptionsTarget(forkInterface,[]).setExecutable(new File(good))}
rejects('oversized-value','malformed native Test effective executable') {nativeForkOptionsTarget(forkInterface,[]).setExecutable('x'*4097)}
class ReceiverWithoutCopy {}
rejects('missing-api-receiver','API is unavailable') {nativeTestExecutable(new ReceiverWithoutCopy())}
def values=[];def target=nativeForkOptionsTarget(forkInterface,values)
target.setExecutable(null);target.setWorkingDir(new File('.'));target.setEnvironment([DISCARDED:'not retained']);target.setExecutable(good)
assert values==[null,good]
new File(System.getenv('HELPER_GUARD_RECEIPT')).text=groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(guards))
"""
probe.write_text('import org.gradle.api.tasks.testing.Test\n'+helper+unit)
with (out/'helper-guards.log').open('w') as log:
 r=subprocess.run(['bash',str(gradle),'--offline','--no-daemon','--init-script',str(probe),'help'],cwd=out/'native-default',env=dict(os.environ,JAVA_HOME=java17,GRADLE_USER_HOME=str(out/'guard-home'),SDK8_HOME=java8,SDK17_HOME=java17,HELPER_GUARD_RECEIPT=str(out/'helper-guards.json')),stdout=log,stderr=subprocess.STDOUT,timeout=90)
assert r.returncode==0,(out/'helper-guards.log').read_text()[-3000:]
guards=json.loads((out/'helper-guards.json').read_text());assert len(guards)==13
rows.append(dict(case='production-helper-unit-guards',rejectedCases=len(guards),productionHelperSha256=hashlib.sha256(helper.encode()).hexdigest()))
(out/'report.json').write_text(json.dumps(rows,indent=2)+'\n')
