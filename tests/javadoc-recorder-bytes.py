#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Real loopback recorder proof: only requested UTF-8 Javadoc metadata is inline."""
import argparse
import base64
import hashlib
import http.server
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cli',type=Path)
    parser.add_argument('--results',type=Path,required=True)
    args=parser.parse_args();out=args.results.resolve();out.mkdir(parents=True,exist_ok=False)
    variables={line.split('=',1)[0]:line.split('=',1)[1] for line in args.cli.read_text().splitlines() if line.startswith(('recorder=','default_java='))}
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    marker='X-Gradle-Purifier-Resource';binary=b'\xff\x00\x80';text=b'external\n'
    requests=[]
    payloads={'/ordinary/package-list':binary,'/ordinary/element-list':binary,
        '/marked/element-list':binary,'/docs/package-list':text,'/both/package-list':text,
        '/maven-metadata.xml':b'<metadata/>\n'}
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(dict(path=self.path,marker=self.headers.get(marker)))
            body=payloads[self.path];self.send_response(200)
            # A remote response cannot opt an ordinary download into inline capture.
            self.send_header(marker,'javadoc-link-metadata');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*unused):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    remote_port=server.server_address[1];record=out/'records.tsv';ports=out/'ports'
    env=dict(os.environ)
    for name in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','GRADLE_PURIFIER_PINNED']:env.pop(name,None)
    command=[variables['default_java']+'/bin/java','-Djava.net.preferIPv4Stack=true','-cp',variables['recorder'],'Recorder',str(record),str(ports)]
    with (out/'recorder.log').open('w') as log:
        process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+15
            while not ports.exists():
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('owned recorder did not start')
                time.sleep(.05)
            recorder_port=int(ports.read_text().split()[0])
            for path,marked in [('/ordinary/package-list',False),('/ordinary/element-list',False),('/marked/element-list',True),('/docs/package-list',True),('/both/package-list',False),('/both/package-list',True),('/maven-metadata.xml',False)]:
                url=f'http://127.0.0.1:{recorder_port}/http/127.0.0.1:{remote_port}'+path
                req=urllib.request.Request(url,headers={marker:'javadoc-link-metadata'} if marked else {})
                with opener.open(req,timeout=10) as response:actual=response.read()
                assert actual==payloads[path],(path,actual.hex())
            deadline=time.monotonic()+3
            while len(record.read_text().splitlines())<7:
                if time.monotonic()>deadline:raise AssertionError('owned recorder did not flush all records')
                time.sleep(.02)
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            server.shutdown();server.server_close()
    rows=[line.split('\t') for line in record.read_text().splitlines()]
    for path in ['/ordinary/package-list','/ordinary/element-list','/marked/element-list']:
        row=next(row for row in rows if row[0].endswith(path))
        assert row[1]!='inline'
        assert row[2]=='sha256-'+base64.b64encode(hashlib.sha256(binary).digest()).decode()
    for path in ['/docs/package-list','/maven-metadata.xml']:
        row=next(row for row in rows if row[0].endswith(path));assert row[1]=='inline'
        assert base64.b64decode(row[2])==payloads[path]
    assert all(row['marker'] is None for row in requests),'internal request marker was forwarded remotely'
    encoded=subprocess.check_output(['bash','-c','source "$1"; artifacts_json "$2"','test',str(args.cli.resolve()),str(record)],text=True)
    exported=json.loads(encoded);(out/'artifacts.json').write_text(json.dumps(exported,indent=2)+'\n')
    for entry in exported:
        if 'path' in entry:
            expected=text if entry['path'].endswith('/package-list') else payloads['/maven-metadata.xml']
            assert entry['contents'].encode()==expected
        else:assert entry['hash']=='sha256-'+base64.b64encode(hashlib.sha256(binary).digest()).decode()
    both=next(entry for entry in exported if entry.get('path','').endswith('/both/package-list'))
    assert both['contents'].encode()==text
    (out/'report.json').write_text(json.dumps(dict(binaryHex=binary.hex(),binaryTransportExact=True,
        unmarkedSameBasenameHashed=True,markedInvalidUtf8Hashed=True,markedUtf8InlineExact=True,
        requestMarkerNotForwarded=True,responseMarkerCannotOptIn=True,legacyMavenMetadataPreserved=True,
        inlineRolePreferredOverOrdinarySameUrl=True,ownedRecorderReaped=process.returncode is not None,requests=requests),indent=2)+'\n')
    print('Recorder binary/metadata loopback controls passed')


if __name__=='__main__':main()
