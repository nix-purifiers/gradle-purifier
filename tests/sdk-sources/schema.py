#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pure source-context reconstruction must reject serialized SDK forgeries."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode=True
p=argparse.ArgumentParser(description=__doc__)
for name in ('cli','positive','output'):p.add_argument('--'+name,type=Path,required=True)
a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
values=dict(re.findall(r'^(lib|nixpkgs|purifier_source)=(.+)$',a.cli.resolve().read_text(),re.M))
helper=Path(values['lib'])/'project-sdk-sources.py'
project=out/'project';shutil.copytree(a.positive,project,ignore=shutil.ignore_patterns('.gradle','.gradle-purifier','build','repository','test-started'))
original=json.loads((a.positive/'gradle-purifier.lock.json').read_text());rows=[]
def evaluate(name,lock,accepted):
 lock=copy.deepcopy(lock);lock.pop('contentHash',None);body=json.dumps(lock,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();lock['contentHash']='sha256:'+hashlib.sha256(body).hexdigest();file=out/(name+'-lock.json');file.write_text(json.dumps(lock,indent=2)+'\n')
 cmd=[sys.executable,helper,'build','--helper',values['purifier_source'],'--nixpkgs',values['nixpkgs'],'--project',project,'--lock',file,'--attribute','sdkContext','--root',out/(name+'-root')]
 with (out/(name+'.log')).open('w') as log:r=subprocess.run([str(x) for x in cmd],stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,XDG_CACHE_HOME=str(out/'cache')),timeout=300)
 rows.append(dict(case=name,exitCode=r.returncode,accepted=accepted));(out/'stages.json').write_text(json.dumps(rows,indent=2)+'\n');print(name,r.returncode,flush=True)
 assert (r.returncode==0)==accepted,(name,(out/(name+'.log')).read_text()[-3500:])
 if not accepted:assert 'gradle-purifier:' in (out/(name+'.log')).read_text()
evaluate('valid',original,True)
cases=[]
for field,value in [('output','/nix/store/'+'0'*32+'-foreign-sdk'),('home','/nix/store/'+'0'*32+'-foreign-sdk/home'),('resolver','unknown-v1'),('identityVersion','unknown-v1'),('attribute',['lib','unavailable']),('selectionKind','derivation-v1'),('id','renamed')]:
 d=copy.deepcopy(original);d['projectJdks']['x86_64-linux']['sdks'][0][field]=value;cases.append(('sdk-'+field,d))
for field,value in [('owner','another-owner'),('rev','0'*40),('narHash','sha256-'+'A'*43+'=')]:
 d=copy.deepcopy(original);d['projectJdks']['x86_64-linux']['sdks'][0]['source'][field]=value;cases.append(('source-'+field,d))
d=copy.deepcopy(original);d['projectJdks']['x86_64-linux']['sdks'][0]['flakeLock']['sha256']='d'*64;cases.append(('source-lock-hash',d))
for field,value in [('sdk','f'*64),('executable','/nix/store/'+'0'*32+'-foreign/bin/java'),('runtimeHome','/nix/store/'+'0'*32+'-foreign/home'),('identityHash','d'*64),('version','unknown-v1')]:
 d=copy.deepcopy(original);d['projectJdks']['x86_64-linux']['manager'][field]=value;cases.append(('manager-'+field,d))
for field in ['version','manager','declarationHash']:
 d=copy.deepcopy(original);d['projectJdks']['x86_64-linux'].pop(field);cases.append(('missing-'+field,d))
d=copy.deepcopy(original);d.pop('projectJdks');cases.append(('declaration-without-context',d))
d=copy.deepcopy(original);d['projectJdks']['aarch64-linux']=d['projectJdks'].pop('x86_64-linux');cases.append(('unsupported-system',d))
d=copy.deepcopy(original);d['projectJdks']['x86_64-linux']['roles'][0]['extra']='ignored';cases.append(('unknown-role-field',d))
for name,lock in cases:evaluate(name,lock,False)
(out/'result.json').write_text(json.dumps(dict(status='passed',cases=len(rows),scope='Pure/no-IFD sdkContext reconstruction; serialized paths never supply SDK inputs'),indent=2)+'\n')
