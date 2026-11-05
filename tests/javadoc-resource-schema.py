#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rehashed external-role failures must be rejected by readLock before replay."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--results',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parent.parent
    out=args.results.resolve();out.mkdir(parents=True,exist_ok=False);project=args.project.resolve()
    original=json.loads((project/'gradle-purifier.lock.json').read_text())
    expression='''let f=builtins.getFlake ("path:"+builtins.getEnv "JAVADOC_SOURCE");
      p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[];};
      read=import (/. + builtins.getEnv "JAVADOC_SOURCE" + "/nix/lib/read-lock.nix") {lib=p.lib;};
      in (read {src=/. + builtins.getEnv "JAVADOC_PROJECT";lockFile=/. + builtins.getEnv "JAVADOC_LOCK";}).problems'''
    env=dict(os.environ,JAVADOC_SOURCE=str(root),JAVADOC_PROJECT=str(project))
    def read(file):
        return json.loads(subprocess.check_output(['nix','eval','--json','--impure','--expr',expression],env=dict(env,JAVADOC_LOCK=str(file)),text=True,timeout=30))
    assert read(project/'gradle-purifier.lock.json')==[]
    positives=[]
    for name,suffix in [('escaped-space-directory','escaped%20space/'),('literal-percent-directory','literal%2520percent/')]:
        lock=copy.deepcopy(original);role=lock['resources'][0]
        link=role['linkUrl'].rstrip('/')+'/'+suffix
        role.update(linkUrl=link,artifactUrl=link+'package-list')
        payload=json.dumps({k:v for k,v in lock.items() if k!='contentHash'},sort_keys=True,separators=(',',':'),ensure_ascii=False)
        lock['contentHash']='sha256:'+hashlib.sha256(payload.encode()).hexdigest()
        file=out/(name+'.lock.json');file.write_text(json.dumps(lock,indent=2)+'\n')
        assert read(file)==[],name
        positives.append(name)
    cases=[]
    def reject(name,mutate,needle='invalid external resource role'):
        lock=copy.deepcopy(original);mutate(lock)
        payload=json.dumps({k:v for k,v in lock.items() if k!='contentHash'},sort_keys=True,separators=(',',':'),ensure_ascii=False)
        lock['contentHash']='sha256:'+hashlib.sha256(payload.encode()).hexdigest()
        file=out/(name+'.lock.json');file.write_text(json.dumps(lock,indent=2)+'\n')
        problems=read(file);assert problems and any(needle in message for message in problems),(name,problems)
        (out/(name+'.problems.json')).write_text(json.dumps(problems,indent=2)+'\n')
        cases.append(dict(case=name,rejectedAt='readLock',expectedDiagnostic=needle))
    def change(**fields):return lambda lock:lock['resources'][0].update(fields)
    reject('duplicate-identical-role',lambda lock:lock['resources'].append(dict(lock['resources'][0])), 'duplicate external resource roles')
    reject('duplicate-conflicting-role',lambda lock:lock['resources'].append(dict(lock['resources'][0],hash='sha256-'+__import__('base64').b64encode(hashlib.sha256(b'other').digest()).decode())), 'duplicate external resource roles')
    reject('unknown-attribute',change(unrecognized='unexpected'))
    reject('missing-hash',lambda lock:lock['resources'][0].pop('hash'))
    reject('malformed-hash',change(hash='not-a-hash'))
    reject('wrong-hash-algorithm',change(hash='sha512-'+'A'*43+'='))
    reject('unknown-kind',change(kind='unrecognized-resource'))
    reject('invalid-build-context',change(build='../outside'))
    reject('invalid-task-context',change(task='javadoc'))
    for name,link in [('file-url','file:///tmp/'),('credential-url','https://token@example.invalid/api/'),
            ('query-url','https://example.invalid/api/?v=1'),('fragment-url','https://example.invalid/api/#part'),
            ('traversal-url','https://example.invalid/api/../'),('encoded-traversal-url','https://example.invalid/api/%2e%2e/'),
            ('invalid-port','http://127.0.0.1:65536/api/'),
            ('unsupported-ipv6','http://[::1]:8080/api/'),('malformed-ipv6','http://[:::]/api/')]:
        reject(name,change(linkUrl=link,artifactUrl=link+'package-list'))
    reject('non-native-metadata-path',lambda lock:lock['resources'][0].update(artifactUrl=lock['resources'][0]['linkUrl']+'extra/package-list'))
    (out/'report.json').write_text(json.dumps(dict(validOriginalAccepted=True,encodedDirectoryPositives=positives,rehashedNegatives=cases,
        scope='Role schema and identity validation only; no native SDK/project compatibility claim'),indent=2)+'\n')
    print('Rehashed Javadoc resource schema negatives passed:',len(cases))


if __name__=='__main__':main()
