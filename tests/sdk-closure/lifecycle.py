#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded Gradle 8.14.3 SDK-closure regression; no new version matrix."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli',type=Path,required=True)
    parser.add_argument('--baseline-cli',type=Path,required=True)
    parser.add_argument('--legacy-capture-cli',type=Path,required=True,help='immutable pre-caller-runtime CLI that genuinely captures legacy-v1 SDK roles')
    parser.add_argument('--sdk-receipt',type=Path,required=True,help='context.json from sdk-telemetry/probe.py')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    sdk_context=json.loads(args.sdk_receipt.read_text());sdks=sdk_context['sdks']
    spec=importlib.util.spec_from_file_location('epoch',ROOT/'tests/epoch-probes/probe.py')
    epoch=importlib.util.module_from_spec(spec);spec.loader.exec_module(epoch)
    profile=sdk_context['profile']
    home=out/'home';home.mkdir()
    env=dict(os.environ,JAVA_HOME=sdks['java21']['home'],HOME=str(home),GRADLE_USER_HOME=str(home/'.gradle'))
    env['PATH']=env['JAVA_HOME']+'/bin:'+env['PATH']
    result=dict(scope='Existing 8.14.3 authentic legacy-v1 split21/8 replay, plain caller-runtime capture and same21 byte control; explicit source-v2 split21/8 binding remains a separate outstanding lane',stages=[])
    (out/'context.json').write_text(json.dumps(dict(cli=str(args.cli.resolve()),baselineCli=str(args.baseline_cli.resolve()),
        sdkReceipt=sdk_context,legacyCaptureCli=str(args.legacy_capture_cli.resolve()),sourceHashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [
            'src/gradle-purifier.sh','pkgs/build-gradle-project/task-jdks.init.gradle','pkgs/build-gradle-project/capture-project-jdks.py',
            'pkgs/build-gradle-project/capture-project-jdk.nix','pkgs/build-gradle-project/project-jdks.nix','lib-overlays/default/read-lock.nix',
            'pkgs/build-gradle-project/default.nix','pkgs/build-gradle-project/user.init.gradle','pkgs/default.nix',
            'pkgs/gradle-purifier/default.nix','tests/sdk-closure/lifecycle.py']},sourceRevision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()),indent=2)+'\n')
    def run(stage,command,cwd,extra=None,success=True):
        command=[str(x) for x in command]
        with (out/(stage+'.log')).open('w') as log:
            process=subprocess.run(command,cwd=cwd,env=dict(env,**(extra or {})),stdout=log,stderr=subprocess.STDOUT,timeout=240)
        row=dict(stage=stage,command=command,exitCode=process.returncode,expectedSuccess=success)
        result['stages'].append(row);(out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
        print(stage,process.returncode,flush=True)
        assert (process.returncode==0)==success,stage
    def up_to_date_capture(label,project,cli):
        variables={line.split('=',1)[0]:line.split('=',1)[1] for line in cli.read_text().splitlines() if line.startswith(('lib=','sdk_descriptor='))}
        library=Path(variables['lib']);work=project/'.gradle-purifier/lock-run'
        selected=(out/(label+'-selected-sdk.json')).read_bytes()
        diagnostic=out/(label+'-up-to-date-sdk');diagnostic.mkdir()
        distribution=project/'.gradle-purifier/distribution/bin/gradle'
        extra=dict(GRADLE_PURIFIER_MODE='lock',GRADLE_PURIFIER_REPOSITORY='file://'+str(project/'.gradle-purifier/repository'),
            GRADLE_PURIFIER_OUTPUT=str(diagnostic),GRADLE_USER_HOME=str(work/'gradle-home'))
        run(label+'-capture-up-to-date',[shutil.which('bash'),distribution,'--offline','--no-daemon',
            '--init-script',library/'purifier.init.gradle','--init-script',library/'task-jdks.init.gradle','purifierLock'],work/'build',extra)
        assert (diagnostic/'task-sdks.json').read_bytes()==selected,'task outcomes changed stable SDK selection'
        outcomes=json.loads((diagnostic/'task-sdk-observations.json').read_text())
        assert any(row['upToDate'] for row in outcomes.values())
        rebound=diagnostic/'bound.json'
        run(label+'-bind-up-to-date',[sys.executable,library/'capture-project-jdks.py',diagnostic/'task-sdks.json',variables['sdk_descriptor'],rebound],project)
        locked=json.loads((project/'gradle-purifier.lock.json').read_text())
        assert json.loads(rebound.read_text())==locked.get('projectJdks',{})
        result[label+'UpToDateSelectionStable']=True
    cli=args.cli.resolve();baseline=args.baseline_cli.resolve();legacy=args.legacy_capture_cli.resolve()
    project=out/'split';epoch.fixture(project,profile,sdks)
    originals={str(p.relative_to(project)):p.read_bytes() for p in project.rglob('*') if p.is_file()}
    run('split-v1-baseline-capture',[legacy,'lock'],project)
    (out/'split-selected-sdk.json').write_bytes((project/'.gradle-purifier/lock-run/output/task-sdks.json').read_bytes())
    lock_path=project/'gradle-purifier.lock.json';first=lock_path.read_bytes()
    locked=json.loads(first);assert 'projectJdks' in locked
    run('split-v1-baseline-relock',[legacy,'lock','--relock'],project);assert lock_path.read_bytes()==first
    # Current ordinary capture deliberately records dependency resolution only.
    caller=out/'split-caller-runtime';epoch.fixture(caller,profile,sdks)
    run('split-caller-runtime-capture',[cli,'lock'],caller)
    caller_lock=caller/'gradle-purifier.lock.json';caller_first=caller_lock.read_bytes()
    assert 'projectJdks' not in json.loads(caller_first)
    run('split-caller-runtime-relock',[cli,'lock','--relock'],caller)
    assert caller_lock.read_bytes()==caller_first
    assert lock_path.read_bytes()==first, 'candidate caller capture changed retained authentic v1 lock'
    result['plainCaptureDoesNotBindSDKs']=True
    run('split-prefetch',[cli,'prefetch'],project)
    # Runtime diagnostic state must not affect a sandboxed producer identity.
    identity_expression='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
      p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};
      in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "SDK_PROJECT";}).tests.drvPath'''
    identities=[]
    state=project/'.gradle-purifier/runtime-diagnostic-variance.json'
    for value in ['cold','warm']:
        state.write_text(json.dumps(dict(diagnostic=value)))
        identities.append(subprocess.check_output(['nix','eval','--impure','--raw','--expr',identity_expression],
            cwd=project,env=dict(env,SDK_SOURCE=str(ROOT),SDK_PROJECT=str(project)),text=True))
    assert identities[0]==identities[1]
    result['diagnosticStateExcludedFromProducerIdentity']=True
    up_to_date_capture('split',project,cli)
    gradle=project/'.gradle-purifier/distribution/bin/gradle'
    run('split-offline',[shutil.which('bash'),gradle,'--offline','--no-daemon','clean','check'],project,dict(GRADLE_PURIFIER_SDK_TRACE='1'))
    run('split-offline-warm',[shutil.which('bash'),gradle,'--offline','--no-daemon','check'],project,dict(GRADLE_PURIFIER_SDK_TRACE='1'))
    assert 'GRADLE-SDK-VALIDATED :compileJava/compiler '+sdks['java8']['home'] in (out/'split-offline.log').read_text()
    expression='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
      p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};
      in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "SDK_PROJECT";}).tests'''
    run('split-sandbox',['nix','build','--impure','--no-link','--print-build-logs','--expr',expression],project,
        dict(SDK_SOURCE=str(ROOT),SDK_PROJECT=str(project)))
    run('split-publish',[cli,'publish','publishProofPublicationToMavenRepository'],project)
    jars=list((project/'repository/example/epoch').glob('*/*-nix-*.jar'));assert jars
    consumer=out/'ordinary';consumer.mkdir();(consumer/'Consumer.java').write_text(
        'public class Consumer {public static void main(String[] a) {if(example.Epoch.answer()!=42)throw new AssertionError();}}\n')
    jdk8=Path(sdks['java8']['home'])
    run('ordinary-compile',[jdk8/'bin/javac','-cp',jars[0],'Consumer.java'],consumer)
    run('ordinary-java8',[jdk8/'bin/java','-cp',str(consumer)+os.pathsep+str(jars[0]),'Consumer'],consumer)
    data=(project/'build/classes/java/main/example/Epoch.class').read_bytes();assert int.from_bytes(data[6:8],'big')==52
    result['classMajor']=52
    assert all((project/name).read_bytes()==data for name,data in originals.items())
    run('unobserved-role-rejected',[shutil.which('bash'),gradle,'--offline','--no-daemon','javadoc'],project,success=False)
    assert 'SDK role :javadoc/javadoc was not selected by the lock' in (out/'unobserved-role-rejected.log').read_text()
    # Effective SDK changes remain invalid even when that other SDK is available.
    build_file=project/'build.gradle';native_definition=build_file.read_text()
    try:
        build_file.write_text(native_definition.replace(sdks['java8']['home'],sdks['java21']['home']))
        run('recorded-role-sdk-change-rejected',[shutil.which('bash'),gradle,'--offline','--no-daemon','check'],project,success=False)
        assert 'selected an SDK that differs from its immutable lock' in (out/'recorded-role-sdk-change-rejected.log').read_text()
    finally:build_file.write_text(native_definition)
    # Manager-only locks keep their established format and exact byte identity.
    same=out/'same-manager';same_profile=dict(profile,projectCompiler='java21',projectRuntime='java21')
    epoch.fixture(same,same_profile,sdks)
    run('same-baseline-capture',[baseline,'lock'],same)
    (out/'same-selected-sdk.json').write_bytes((same/'.gradle-purifier/lock-run/output/task-sdks.json').read_bytes())
    same_lock=same/'gradle-purifier.lock.json';before=same_lock.read_bytes()
    run('same-new-cold-relock',[cli,'lock','--relock'],same);assert same_lock.read_bytes()==before
    run('same-new-warm-relock',[cli,'lock','--relock'],same);assert same_lock.read_bytes()==before
    assert 'projectJdks' not in json.loads(before)
    run('same-prefetch',[cli,'prefetch'],same)
    up_to_date_capture('same',same,cli)
    same_gradle=same/'.gradle-purifier/distribution/bin/gradle'
    run('same-offline',[shutil.which('bash'),same_gradle,'--offline','--no-daemon','check'],same)
    run('same-publish',[cli,'publish','publishProofPublicationToMavenRepository'],same)
    assert lock_path.read_bytes()==first, 'candidate replay changed the authentic v1 lock'
    result.update(legacyV1BaselineRelockByteIdentical=True,legacyV1CandidateReplayLockUnchanged=True,
                  sameBaselineColdWarmByteIdentical=True,sourcePreserved=True,
                  sourceV2Strict21And8Binding='outstanding: requires genuine published manager21 adapter source')
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
