#!/usr/bin/env python3
"""Native aggregate selection with unrelated tasks and the owned legacy helper."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
for key in ('cli', 'sdks', 'output'):
    parser.add_argument('--' + key, type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
out = args.output.resolve()
out.mkdir()
sdk = json.loads(args.sdks.read_text())
spec = importlib.util.spec_from_file_location('epoch', root / 'tests/epoch-probes/probe.py')
epoch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epoch)
profiles = json.loads((root / 'tests/epoch-probes/profiles.json').read_text())['profiles']
rows = []
for identifier, manager in [('gradle-6.9.4', 'java11'), ('gradle-7.6.6', 'java17')]:
    profile = next(p for p in profiles if p['id'] == identifier)
    project = out / identifier
    epoch.fixture(project, profile, sdk)
    with (project / 'build.gradle').open('a') as stream:
        stream.write('''
apply plugin:'maven-publish'
def realPublication=publishing.publications.withType(org.gradle.api.publish.maven.MavenPublication).find {true}
if(realPublication==null) realPublication=publishing.publications.create('realUser',org.gradle.api.publish.maven.MavenPublication) {from components.java;artifactId='real-user-epoch'}
def otherRepository=publishing.repositories.maven {name='differentTarget';url=uri('different-repository')}
tasks.register('unconfiguredPublish',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {doLast {throw new GradleException('UNRELATED_PUBLISH_EXECUTED')}}
tasks.register('differentTargetPublish',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {publication=realPublication;repository=otherRepository;doLast {throw new GradleException('DIFFERENT_TARGET_EXECUTED')}}
gradle.taskGraph.whenReady { graph ->
 def aggregate=tasks.findByName('publishAllPublicationsToPurifierRepository')
 if(aggregate!=null && System.getenv('SELECTION_RECEIPT')) {
  def isOwned={task -> gradle.ext.has('purifierLegacyMaven') && gradle.ext.purifierLegacyMaven.ownsPublication(project,task.publication)}
  def expected=aggregate.taskDependencies.getDependencies(aggregate).findAll {it instanceof org.gradle.api.publish.maven.tasks.PublishToMavenRepository && !isOwned(it)}*.path.sort()
  def actual=graph.allTasks.findAll {it instanceof org.gradle.api.publish.maven.tasks.PublishToMavenRepository}*.path.sort()
  new File(System.getenv('SELECTION_RECEIPT')).text=groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson([expected:expected,actual:actual,allTasks:graph.allTasks*.path]))
 }
}
''')
    receipt = out / (identifier + '-selection.json')
    env = dict(os.environ, JAVA_HOME=sdk[manager]['home'], GRADLE_USER_HOME=str(out / (identifier + '-home')), XDG_CACHE_HOME=str(out / (identifier + '-cache')), SELECTION_RECEIPT=str(receipt))
    command = [str(args.cli.resolve()), 'lock']
    with (out / (identifier + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    assert result.returncode == 0, (out / (identifier + '.log')).read_text()[-7000:]
    selection = json.loads(receipt.read_text())
    assert selection['actual'] == selection['expected'] and selection['actual'], selection
    assert not any('PurifierLegacyMetadataPublication' in task for task in selection['actual']), selection
    assert not (project / 'different-repository').exists()
    lock = json.loads((project / 'gradle-purifier.lock.json').read_text())
    publications = lock['projects'][0]['publications']
    assert any(p.get('kind') != 'legacy-maven-upload' for p in publications)
    settings = {line.split('=',1)[0]:line.split('=',1)[1] for line in args.cli.resolve().read_text().splitlines() if line.startswith(('lib=','nixpkgs=','purifier_source='))}
    nix = ['nix-build','--quiet',str(Path(settings['lib'])/'command.nix'),'--argstr','nixpkgs',settings['nixpkgs'],'--argstr','purifierSource',settings['purifier_source'],'--argstr','src',str(project),'--argstr','lockFile',str(project/'gradle-purifier.lock.json'),'--no-out-link']
    nixStages = []
    for attribute in ['tests','packages.epoch.maven']:
        stage = identifier + '-' + attribute.replace('.','-')
        with (out / (stage + '.log')).open('w') as log:
            native = subprocess.run(nix + ['-A',attribute], cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
        assert native.returncode == 0, (out / (stage + '.log')).read_text()[-7000:]
        nixStages.append(dict(attribute=attribute,command=nix+['-A',attribute],exitCode=native.returncode))
    rows.append(dict(profile=identifier, command=command, exitCode=result.returncode, nixStages=nixStages, selection=selection, realUserPublicationRetained=True, unrelatedTasksExcluded=True))
    (out / 'report.json').write_text(json.dumps(rows, indent=2))
    print(identifier, 'PASS', flush=True)
print('NATIVE_PUBLICATION_SELECTION_EQUIVALENCE_PASS', flush=True)
