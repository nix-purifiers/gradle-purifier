#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Add reversible, path-safe authority aliases to the immutable replay tree."""
import argparse,ipaddress,json,re
from pathlib import Path
from urllib.parse import urlsplit,unquote
NAMESPACE='.gradle-purifier-replay-v1'
def authority_alias(url):
 uri=urlsplit(url)
 if uri.scheme not in ['http','https'] or uri.port is None:return None
 authority=uri.netloc
 host=uri.hostname or ''
 labels=host[:-1].split('.') if host.endswith('.') else host.split('.')
 host_ok=bool(host) and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?',label) for label in labels)
 if host_ok and re.fullmatch(r'[0-9.]+',host):
  try:ipaddress.IPv4Address(host)
  except ValueError:host_ok=False
 if (not host_ok or any(ord(c)<32 or ord(c)==127 for c in url) or uri.username is not None or uri.password is not None or '%' in authority
     or uri.query or uri.fragment or not 0<=uri.port<=65535
     or authority.rsplit(':',1)[1]!=str(uri.port)
     or any(c in unquote(uri.path) for c in ['\\','\x00','\r','\n'])
     or any(p in ['.','..'] for p in unquote(uri.path).split('/'))):
  raise ValueError('unsupported explicit-port repository URL')
 return uri.scheme,authority,authority.encode('utf-8').hex()
def project(source,output,urls):
 if (source/NAMESPACE).exists() or (source/NAMESPACE).is_symlink():raise ValueError('reserved replay namespace already exists')
 output.mkdir()
 for child in source.iterdir():
  (output/child.name).symlink_to(child,target_is_directory=child.is_dir())
 aliases={}
 for url in urls:
  alias=authority_alias(url)
  if alias is None:continue
  scheme,authority,encoded=alias;relative=Path(NAMESPACE)/scheme/encoded
  origin=source/scheme/authority
  if not origin.is_dir():raise ValueError('locked repository authority is missing')
  if str(relative) in aliases:
   if aliases[str(relative)]!=str(origin):raise ValueError('replay alias collision')
   continue
  destination=output/relative;destination.parent.mkdir(parents=True,exist_ok=True);destination.symlink_to(origin,target_is_directory=True);aliases[str(relative)]=str(origin)
 (output/NAMESPACE/'manifest.json').write_text(json.dumps({'version':1,'encoding':'exact-authority-utf8-hex','aliases':aliases},sort_keys=True,indent=2)+'\n')
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path);parser.add_argument('urls',type=Path);args=parser.parse_args();project(args.source,args.destination,json.loads(args.urls.read_text()))
