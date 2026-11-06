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
parser.add_argument('--late-private-dependency', action='store_true')
parser.add_argument('--private-edge-kind', choices=('direct','provider','lazy','name'), default='direct')
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
    if args.late_private_dependency and identifier != 'gradle-6.9.4':
        continue
    profile = next(p for p in profiles if p['id'] == identifier)
    project = out / identifier
    epoch.fixture(project, profile, sdk)
    (project / 'aggregate-input.txt').write_text('fixture')
    with (project / 'build.gradle').open('a') as stream:
        stream.write('''
apply plugin:'maven-publish'
def realPublication=publishing.publications.withType(org.gradle.api.publish.maven.MavenPublication).find {true}
if(realPublication==null) realPublication=publishing.publications.create('realUser',org.gradle.api.publish.maven.MavenPublication) {from components.java;artifactId='real-user-epoch'}
def otherRepository=publishing.repositories.maven {name='differentTarget';url=uri('different-repository')}
tasks.register('unconfiguredPublish',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {doLast {throw new GradleException('UNRELATED_PUBLISH_EXECUTED')}}
tasks.register('differentTargetPublish',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {publication=realPublication;repository=otherRepository;doLast {throw new GradleException('DIFFERENT_TARGET_EXECUTED')}}
def purifierRepository=publishing.repositories.getByName('purifier')
tasks.register('sameRepositoryNoPublication',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {repository=purifierRepository;doLast {throw new GradleException('SAME_REPOSITORY_NULL_PUBLICATION_EXECUTED')}}
tasks.register('manualMirrorPublish',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {publication=realPublication;repository=purifierRepository;doLast {throw new GradleException('MANUAL_MIRROR_EXECUTED')}}
tasks.register('aggregateUserDependency') {doLast {file('aggregate-dependency-ran').text='ok'}}
tasks.register('aggregateLazyDependency') {doLast {file('aggregate-lazy-dependency-ran').text='ok'}}
tasks.register('aggregateLateDependency') {doLast {file('aggregate-late-dependency-ran').text='ok'}}
tasks.register('aggregateDynamicBefore') {doLast {throw new GradleException('STALE_DYNAMIC_DEPENDENCY_EXECUTED')}}
tasks.register('aggregateDynamicAfter') {doLast {file('aggregate-dynamic-dependency-ran').text='ok'}}
tasks.register('aggregateInputDependency') {doLast {file('aggregate-input-dependency-ran').text='ok'}}
def inputVisits=0
def inputDependency={ owner -> inputVisits++;[tasks.getByName('aggregateInputDependency')] as Set } as org.gradle.api.tasks.TaskDependency
def lateExpectedDependencies=[]
def lateExpectedPublications=[]
def dynamicTarget='aggregateDynamicBefore'
def nativeAggregate=tasks.getByName('publishAllPublicationsToPurifierRepository')
def mirrorRepository=publishing.repositories.maven {name='explicitMirror';url=uri('explicit-mirror-repository')}
publishing.publications.withType(org.gradle.api.publish.maven.MavenPublication).all { owned ->
 if(owned.name=='purifierLegacyMetadata') {
  // Register lazily during creation: the user dependency is in the native
  // aggregate before the helper snapshots its mutable dependency notation.
  def mirror=tasks.register('aggregateOwnedPublicationMirror',org.gradle.api.publish.maven.tasks.PublishToMavenRepository) {
   publication=owned;repository=mirrorRepository
   doLast {file('aggregate-owned-mirror-ran').text='ok'}
  }
  nativeAggregate.dependsOn(mirror)
  lateExpectedDependencies.add(':aggregateOwnedPublicationMirror');lateExpectedPublications.add(':aggregateOwnedPublicationMirror')
 }
}

nativeAggregate.inputs.files(files(file('aggregate-input.txt')).builtBy(inputDependency))
// The same provider is both an inferred input edge and explicit notation.
// Native dependency resolution must still visit it only once per query.
nativeAggregate.dependsOn(inputDependency)
nativeAggregate.dependsOn('aggregateUserDependency')
nativeAggregate.dependsOn {tasks.named('aggregateLazyDependency')}
nativeAggregate.dependsOn {tasks.named(dynamicTarget)}
nativeAggregate.doLast {
 for(marker in ['aggregate-dependency-ran','aggregate-lazy-dependency-ran','aggregate-late-dependency-ran','aggregate-dynamic-dependency-ran','aggregate-input-dependency-ran']) assert file(marker).text=='ok'
 file('aggregate-action-ran').text='ok'
}
// Capture the untouched native aggregate before the late owned helper exists.
def originalTaskDependency=nativeAggregate.taskDependencies
def originalInputs=nativeAggregate.inputs
def originalNotation=nativeAggregate.dependsOn.toList()
def baselineVisits=inputVisits
def baselineDependencies=nativeAggregate.taskDependencies.getDependencies(nativeAggregate)
def baselineInputVisits=inputVisits-baselineVisits
def expectedDependencies=baselineDependencies*.path.sort()
def expectedPublications=baselineDependencies.findAll {it instanceof org.gradle.api.publish.maven.tasks.PublishToMavenRepository}*.path.sort()
gradle.projectsEvaluated {
 dynamicTarget='aggregateDynamicAfter'
 nativeAggregate.dependsOn('aggregateLateDependency')
 nativeAggregate.doLast {file('aggregate-late-action-ran').text='ok'}
 def owned=publishing.publications.findByName('purifierLegacyMetadata')
 if(owned!=null && System.getenv('LATE_PRIVATE_DEPENDENCY')=='1') {
  def privateName='publishPurifierLegacyMetadataPublicationToPurifierRepository'
  if(System.getenv('PRIVATE_EDGE_KIND')=='provider') nativeAggregate.dependsOn(tasks.named(privateName))
  else if(System.getenv('PRIVATE_EDGE_KIND')=='lazy') nativeAggregate.dependsOn {tasks.named(privateName)}
  else if(System.getenv('PRIVATE_EDGE_KIND')=='name') nativeAggregate.dependsOn(privateName)
  else nativeAggregate.dependsOn(tasks.getByName(privateName))
 }
}

gradle.taskGraph.whenReady { graph ->
 def aggregate=tasks.findByName('publishAllPublicationsToPurifierRepository')
 if(aggregate!=null && System.getenv('SELECTION_RECEIPT')) {
  def isOwned={task -> gradle.ext.has('purifierLegacyMaven') && gradle.ext.purifierLegacyMaven.ownsPublication(project,task.publication)}
  def expected=(expectedPublications+lateExpectedPublications).sort()
  def sameNativeDependency=aggregate.taskDependencies.is(originalTaskDependency)
  def sameNativeInputs=aggregate.inputs.is(originalInputs)
  def actualNotation=aggregate.dependsOn.toList()
  def retainedIndexes=originalNotation.collect { value -> actualNotation.findIndexOf { candidate -> candidate.is(value) } }
  def notationPreserved=retainedIndexes.every {it>=0} && retainedIndexes==retainedIndexes.sort(false)
  assert sameNativeDependency && sameNativeInputs && notationPreserved
  def before=inputVisits
  def actualDependencies=aggregate.taskDependencies.getDependencies(aggregate)*.path.sort()
  def actualInputVisits=inputVisits-before
  def expectedAll=(expectedDependencies.findAll {it!=':aggregateDynamicBefore'}+[':aggregateLateDependency',':aggregateDynamicAfter']+lateExpectedDependencies).sort()
  def actual=graph.allTasks.findAll {it instanceof org.gradle.api.publish.maven.tasks.PublishToMavenRepository}*.path.sort()
  new File(System.getenv('SELECTION_RECEIPT')).text=groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson([expected:expected,actual:actual,expectedDependencies:expectedAll,actualDependencies:actualDependencies,baselineInputVisits:baselineInputVisits,actualInputVisits:actualInputVisits,sameNativeDependency:sameNativeDependency,sameNativeInputs:sameNativeInputs,rawNotationIdentityAndOrderPreserved:notationPreserved,allTasks:graph.allTasks*.path]))
 }
}
''')
    receipt = out / (identifier + '-selection.json')
    env = dict(os.environ, JAVA_HOME=sdk[manager]['home'], GRADLE_USER_HOME=str(out / (identifier + '-home')), XDG_CACHE_HOME=str(out / (identifier + '-cache')), SELECTION_RECEIPT=str(receipt), LATE_PRIVATE_DEPENDENCY='1' if args.late_private_dependency else '0', PRIVATE_EDGE_KIND=args.private_edge_kind)
    command = [str(args.cli.resolve()), 'lock']
    with (out / (identifier + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    if args.late_private_dependency:
        text=(out / (identifier + '.log')).read_text()
        assert result.returncode != 0 and 'owned native deployment entered capture graph' in text, text[-7000:]
        assert not any(line.startswith('> Task') for line in text.splitlines()), text[-7000:]
        rows.append(dict(profile=identifier,command=command,exitCode=result.returncode,expectedRejection='owned native deployment entered capture graph',privateEdgeKind=args.private_edge_kind,taskActionsExecuted=False))
        (out / 'report.json').write_text(json.dumps(rows,indent=2))
        print(identifier,'LATE_PRIVATE_REJECT_PASS',flush=True)
        continue
    assert result.returncode == 0, (out / (identifier + '.log')).read_text()[-7000:]
    selection = json.loads(receipt.read_text())
    assert selection['actual'] == selection['expected'] and selection['actual'], selection
    assert not any('PurifierLegacyMetadataPublication' in task for task in selection['actual']), selection
    assert selection['actualDependencies'] == selection['expectedDependencies'], selection
    assert selection['baselineInputVisits'] == selection['actualInputVisits'] and selection['baselineInputVisits'] > 0, selection
    assert ':publishAllPublicationsToPurifierRepository' in selection['allTasks'], selection
    assert all(not any(name in task for task in selection['allTasks']) for name in ['sameRepositoryNoPublication','manualMirrorPublish','differentTargetPublish','unconfiguredPublish','aggregateDynamicBefore']), selection
    for marker in ['aggregate-dependency-ran','aggregate-lazy-dependency-ran','aggregate-late-dependency-ran','aggregate-dynamic-dependency-ran','aggregate-input-dependency-ran','aggregate-action-ran','aggregate-late-action-ran']:
        assert (project / '.gradle-purifier/lock-run/build' / marker).read_text() == 'ok', marker
    if identifier == 'gradle-6.9.4':
        assert (project / '.gradle-purifier/lock-run/build/aggregate-owned-mirror-ran').read_text()=='ok'
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
print('LATE_PRIVATE_DEPENDENCY_REJECT_BEFORE_ACTIONS_PASS' if args.late_private_dependency else 'NATIVE_PUBLICATION_SELECTION_EQUIVALENCE_PASS', flush=True)
