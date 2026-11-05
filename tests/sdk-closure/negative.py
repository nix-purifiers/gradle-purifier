#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reject SDK identity/path/lock changes using the existing split-SDK receipt."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli',type=Path,required=True)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    project=args.project.resolve();cli=args.cli.resolve()
    variables={line.split('=',1)[0]:line.split('=',1)[1] for line in cli.read_text().splitlines() if line.startswith(('lib=','sdk_descriptor='))}
    descriptor=json.loads(Path(variables['sdk_descriptor']).read_text())
    receipt=json.loads((project/'.gradle-purifier/lock-run/output/task-sdks.json').read_text())
    spec=importlib.util.spec_from_file_location('capture',ROOT/'nix/lib/capture-project-jdks.py')
    capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(capture)
    (out/'source-hashes.json').write_text(json.dumps({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['nix/lib/capture-project-jdks.py','nix/lib/capture-project-jdk.nix','nix/lib/project-jdks.nix','nix/lib/read-lock.nix','nix/lib/task-jdks.init.gradle','nix/lib/build-gradle-project.nix','tests/sdk-closure/negative.py']},indent=2)+'\n')
    evidence=[]
    def rejected(name,value,needle):
        try:capture.capture(value,descriptor,ROOT/'nix/lib/capture-project-jdk.nix')
        except ValueError as error:
            assert needle in str(error),(name,str(error));evidence.append(dict(case=name,rejected=True,reason=str(error)))
        else:raise AssertionError(name+' was accepted')
    row=next(r for r in receipt['tasks'] if r['role']=='compiler')
    host=copy.deepcopy(receipt);host['tasks']=[dict(row,home='/usr/lib/jvm/ambient',canonicalHome='/usr/lib/jvm/ambient')]
    rejected('ambient-home',host,'unsupported ambient')
    alias=out/'sdk-alias';alias.symlink_to(row['home'])
    for name,target in [('alias-initial',row['home']),('alias-retargeted',receipt['managerHome'])]:
        alias.unlink();alias.symlink_to(target)
        aliased=copy.deepcopy(receipt);aliased['tasks']=[dict(row,home=str(alias),canonicalHome=str(alias.resolve()),
            executable=str(alias/'bin/javac'),canonicalExecutable=str((alias/'bin/javac').resolve()))]
        rejected(name,aliased,'ambient or mutable')
    # A different Nix output with identical release metadata is not the pinned SDK.
    fake_expr='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
      p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[];};
      in p.runCommand "different-sdk-release-metadata" {release="${p.jdk8_headless}/lib/openjdk/release";} ''
        mkdir -p "$out/lib/openjdk/bin"
        cp "$release" "$out/lib/openjdk/release"
        touch "$out/lib/openjdk/bin/java" "$out/lib/openjdk/bin/javac"
      '' '''
    env=dict(os.environ,SDK_SOURCE=str(ROOT),SDK_PROJECT=str(project))
    fake=Path(subprocess.check_output(['nix','build','--impure','--no-link','--print-out-paths','--expr',fake_expr],env=env,text=True).strip())/'lib/openjdk'
    assert (fake/'release').read_bytes()==(Path(row['home'])/'release').read_bytes()
    other=copy.deepcopy(receipt);other['tasks']=[dict(row,home=str(fake),canonicalHome=str(fake),
        executable=str(fake/'bin/javac'),canonicalExecutable=str(fake/'bin/javac'))]
    rejected('same-release-different-output',other,'does not match the pinned Nix SDK output')
    original=json.loads((project/'gradle-purifier.lock.json').read_text())
    evaluate='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
      p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[f.overlays.default];};
      in (p.gradle-purifier.buildGradleProject {src=/. + builtins.getEnv "SDK_PROJECT";
        lockFile=/. + builtins.getEnv "SDK_LOCK";}).sdkContext.drvPath'''
    def nix_reject(name,lock,needle,rehash=True,build=False,read_boundary=False):
        if rehash:
            payload=json.dumps({k:v for k,v in lock.items() if k!='contentHash'},sort_keys=True,separators=(',',':'),ensure_ascii=False)
            lock['contentHash']='sha256:'+hashlib.sha256(payload.encode()).hexdigest()
        file=out/(name+'.lock.json');file.write_text(json.dumps(lock,indent=2)+'\n')
        if read_boundary:
            boundary='''let f=builtins.getFlake ("path:"+builtins.getEnv "SDK_SOURCE");
              p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[];};
              read=import (/. + builtins.getEnv "SDK_SOURCE" + "/nix/lib/read-lock.nix") {lib=p.lib;};
              in (read {src=/. + builtins.getEnv "SDK_PROJECT";lockFile=/. + builtins.getEnv "SDK_LOCK";}).problems'''
            observed=subprocess.check_output(['nix','eval','--json','--impure','--expr',boundary],
                env=dict(env,SDK_LOCK=str(file)),text=True,timeout=30)
            (out/(name+'.read-lock.json')).write_text(observed)
            problems=json.loads(observed)
            assert problems and any(needle in problem for problem in problems),(name,problems)
        expression=evaluate.replace('.sdkContext.drvPath','.tests') if build else evaluate
        command=['nix','build','--no-link','--print-build-logs'] if build else ['nix','eval','--raw']
        result=subprocess.run([*command,'--impure','--expr',expression],env=dict(env,SDK_LOCK=str(file)),text=True,capture_output=True,timeout=240)
        (out/(name+'.log')).write_text(result.stdout+result.stderr)
        assert result.returncode!=0 and needle in result.stderr,(name,result.stderr[-2000:])
        evidence.append(dict(case=name,rejected=True,reason=needle))
    changed=copy.deepcopy(original);key=next(iter(changed['projectJdks']));changed['projectJdks'][key]['sdks'][0]['home']='/nix/store/tampered'
    nix_reject('record-hash',changed,'does not match its contentHash',rehash=False)
    changed=copy.deepcopy(original);context=changed['projectJdks'][key];old=context['sdks'][0]['identityHash'];context['sdks'][0]['identityHash']='0'*64
    for role in context['roles']:
        if role['sdk']==old:role['sdk']='0'*64
    nix_reject('nix-identity',changed,'project SDK Nix identity differs')
    changed=copy.deepcopy(original);changed['projectJdks']={'wrong-platform':changed['projectJdks'][key]}
    nix_reject('wrong-platform',changed,'project SDK lock has no context')
    changed=copy.deepcopy(original);context=changed['projectJdks'][key]
    context['sdks'].append(copy.deepcopy(context['sdks'][0]))
    nix_reject('duplicate-sdk-identity',changed,'duplicate project SDK identities',read_boundary=True)
    for name,conflicting in [('duplicate-identical-role',False),('duplicate-conflicting-role',True)]:
        changed=copy.deepcopy(original);context=changed['projectJdks'][key];role=copy.deepcopy(context['roles'][0])
        if conflicting:role['sdk']=next(sdk['identityHash'] for sdk in context['sdks'] if sdk['identityHash']!=role['sdk'])
        context['roles'].append(role)
        nix_reject(name,changed,'duplicate project SDK task roles',read_boundary=True)
    changed=copy.deepcopy(original);changed['projectJdks'][key]['roles'][0]['sdk']='f'*64
    nix_reject('unknown-role-sdk',changed,'role referencing an unknown SDK identity',read_boundary=True)
    changed=copy.deepcopy(original);context=changed['projectJdks'][key];old=context['sdks'][0]['identityHash']
    context['sdks'][0]['identityHash']='short-hash'
    for role in context['roles']:
        if role['sdk']==old:role['sdk']='short-hash'
    nix_reject('malformed-sdk-identity',changed,'malformed project SDK contexts',read_boundary=True)
    changed=copy.deepcopy(original);changed['projectJdks'][key]['sdks'][0]['source']['narHash']='sha256-not-valid'
    nix_reject('malformed-sdk-source-hash',changed,'malformed project SDK contexts',read_boundary=True)
    changed=copy.deepcopy(original);del changed['projectJdks']
    nix_reject('legacy-lock-foreign-sdk',changed,'non-manager SDK absent from this lock',build=True)
    # Existing explicit typed SDK input API still authorizes the legacy use.
    legacy_file=out/'legacy-lock-foreign-sdk.lock.json'
    explicit=evaluate.replace('}).sdkContext.drvPath','toolchainJdks=[p.jdk8_headless];}).tests')
    positive=subprocess.run(['nix','build','--no-link','--print-build-logs','--impure','--expr',explicit],
        env=dict(env,SDK_LOCK=str(legacy_file)),text=True,capture_output=True,timeout=240)
    (out/'legacy-explicit-nix-sdk.log').write_text(positive.stdout+positive.stderr)
    assert positive.returncode==0,positive.stderr[-2000:]
    evidence.append(dict(case='legacy-explicit-nix-sdk',rejected=False,expectedSuccess=True))
    # Supplying a Nix-context-bearing package with an ambient home is invalid.
    ambient=evaluate.replace('}).sdkContext.drvPath','toolchainJdks=[{outPath=toString p.jdk8_headless;home="/usr/lib/jvm/ambient";}];}).sdkContext.drvPath')
    invalid=subprocess.run(['nix','eval','--raw','--impure','--expr',ambient],
        env=dict(env,SDK_LOCK=str(legacy_file)),text=True,capture_output=True,timeout=30)
    (out/'explicit-ambient-home.log').write_text(invalid.stdout+invalid.stderr)
    assert invalid.returncode!=0 and 'toolchainJdks must carry explicit Nix package context' in invalid.stderr
    evidence.append(dict(case='explicit-ambient-home',rejected=True,reason='toolchainJdks must carry explicit Nix package context'))

    (out/'summary.json').write_text(json.dumps(dict(scope='Negative validation only; synthetic release metadata is not a runnable SDK or compatibility point',cases=evidence),indent=2)+'\n')
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
