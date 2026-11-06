#!/usr/bin/env python3
"""Verify typed legacy publications at the public readLock boundary."""
import argparse,copy,hashlib,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output.resolve();out.mkdir();original=json.loads((a.project/'gradle-purifier.lock.json').read_text());rows=[]
expr='''let f=builtins.getFlake ("path:"+builtins.getEnv "LEGACY_SOURCE");p=import f.inputs.nixpkgs {system=builtins.currentSystem;config={};overlays=[];};read=import (/. + builtins.getEnv "LEGACY_SOURCE" + "/nix/lib/read-lock.nix") {lib=p.lib;};in (read {src=/. + builtins.getEnv "LEGACY_PROJECT";lockFile=/. + builtins.getEnv "LEGACY_LOCK";}).problems'''
def run(name,change,expected):
 data=copy.deepcopy(original);change(data);payload=json.dumps({k:v for k,v in data.items() if k!='contentHash'},sort_keys=True,separators=(',',':'),ensure_ascii=False);data['contentHash']='sha256:'+hashlib.sha256(payload.encode()).hexdigest();file=out/(name+'.lock.json');file.write_text(json.dumps(data,indent=2));r=subprocess.run(['nix','eval','--json','--impure','--expr',expr],env=dict(os.environ,LEGACY_SOURCE=str(root),LEGACY_PROJECT=str(a.project.resolve()),LEGACY_LOCK=str(file)),capture_output=True,text=True,timeout=45);(out/(name+'.log')).write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr;problems=json.loads(r.stdout);assert bool(problems)==expected,(name,problems);rows.append(dict(case=name,problems=problems));(out/'report.json').write_text(json.dumps(rows,indent=2));print(name,'rejected' if problems else 'accepted',flush=True)
def edit(field,value):return lambda d:d['projects'][0]['publications'][0].__setitem__(field,value)
run('valid',lambda d:None,False)
for name,field,value in [('unknown-kind','kind','unknown'),('wrong-task','uploadTask',':other'),('wrong-configuration','configuration','runtimeClasspath'),('path-coordinate','groupId','../escape'),('wrong-name','name','other'),('unknown-field','unknown',True),('classified','artifacts',[{'classifier':'sources','extension':'jar'}]),('wrong-extension','artifacts',[{'classifier':None,'extension':'zip'}]),('missing-version','version',None)]:run(name,edit(field,value),True)
run('missing-kind',lambda d:d['projects'][0]['publications'][0].pop('kind'),True)
run('duplicate-task',lambda d:d['projects'][0]['publications'].append(copy.deepcopy(d['projects'][0]['publications'][0])),True)
run('wrong-native-version',lambda d:d.__setitem__('distribution',d['distribution'].replace('6.9.4','6.9.3')),True)
for name,path in [('missing-owner-colon','owner'),('owner-traversal',':../escape'),('owner-dot',':.'),('owner-dotdot',':..'),('owner-empty-segment',':a::b'),('owner-control',':a\\nb')]:
 def owner(d,path=path):
  d['projects'][0]['path']=path;d['projects'][0]['publications'][0]['uploadTask']=path+':uploadArchives'
 run(name,owner,True)
run('duplicate-owner',lambda d:d['projects'].append(copy.deepcopy(d['projects'][0])),True)
def collision(d):
 first=d['projects'][0];first['path']=':a-b';first['publications'][0]['uploadTask']=':a-b:uploadArchives';second=copy.deepcopy(first);second['path']=':a:b';second['publications'][0]['uploadTask']=':a:b:uploadArchives';second['publications'][0]['artifactId']='different';d['projects'].append(second)
run('output-id-collision',collision,True)
for name,value in [('unsafe-root-slash','../escape'),('unsafe-root-dotdot','..'),('unsafe-root-control','a'+chr(10)+'b')]:
 run(name,lambda d,value=value:d.__setitem__('rootProjectName',value),True)

for label,capability in [('null',None),('wrong-kind',dict(kind='other',version=1)),('wrong-version',dict(kind='gradle-java-component',version=2)),('float-version',dict(kind='gradle-java-component',version=1.0)),('extra-key',dict(kind='gradle-java-component',version=1,available=True))]:
 run('native-metadata-'+label,edit('nativeMetadata',capability),True)
run('native-metadata-valid',edit('nativeMetadata',dict(kind='gradle-java-component',version=1)),False)
run('old-typed-absent-native-metadata',lambda d:d['projects'][0]['publications'][0].pop('nativeMetadata',None),False)
print('PUBLIC_LEGACY_SCHEMA_PASS',flush=True)
