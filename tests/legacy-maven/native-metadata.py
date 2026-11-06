#!/usr/bin/env python3
"""Native metadata ownership and graph guards on the bounded 6.9.4 profile."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
for key in ("gradle", "sdks", "output"):
    parser.add_argument("--" + key, required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
out = args.output.resolve()
out.mkdir()
sdk = json.loads(args.sdks.read_text())
spec = importlib.util.spec_from_file_location("epoch", root / "tests/epoch-probes/probe.py")
epoch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epoch)
profile = next(p for p in json.loads((root / "tests/epoch-probes/profiles.json").read_text())["profiles"] if p["id"] == "gradle-6.9.4")
metadata = "generateMetadataFileForPurifierLegacyMetadataPublication"
pom = "generatePomFileForPurifierLegacyMetadataPublication"
finish = "purifierLegacyMetadataSnapshot"
cases = {
    "valid": ("", None),
    "publication-early-xml": ("plugins.withId('maven-publish') { publishing.publications.all { it.pom.withXml { } } }", "custom metadata publication creation rules"),
    "publication-early-mapping": ("plugins.withId('maven-publish') { publishing.publications.all { it.versionMapping { allVariants { fromResolutionResult() } } } }", "custom metadata version mapping"),
    "publication-early-gav": ("plugins.withId('maven-publish') { publishing.publications.all { it.groupId='changed' } }", "custom metadata publication creation rules"),
    "module-early-action": ("tasks.withType(org.gradle.api.publish.tasks.GenerateModuleMetadata).all { doFirst { file('HOOK_EXECUTED').text='bad' } }", "custom metadata task actions"),
    "pom-early-action": ("tasks.withType(org.gradle.api.publish.maven.tasks.GenerateMavenPom).all { doLast { file('HOOK_EXECUTED').text='bad' } }", "custom metadata task actions"),
    "finish-early-action": ("tasks.all { if(name=='"+finish+"') doFirst { file('HOOK_EXECUTED').text='bad' } }", "custom metadata snapshot task factory"),
    "module-early-predicate": ("tasks.withType(org.gradle.api.publish.tasks.GenerateModuleMetadata).all { onlyIf { file('HOOK_EXECUTED').text='bad';false } }", "custom metadata task predicates"),
    "module-late-action": ("gradle.taskGraph.whenReady { "+metadata+".doFirst { file('HOOK_EXECUTED').text='bad' } }", "metadata task actions or dependencies changed"),
    "pom-late-xml": ("gradle.taskGraph.whenReady { publishing.publications.purifierLegacyMetadata.pom.withXml { file('HOOK_EXECUTED').text='bad' } }", "metadata POM actions changed"),
    "publication-late-remove": ("gradle.taskGraph.whenReady { publishing.publications.remove(publishing.publications.purifierLegacyMetadata) }", "metadata publication binding changed"),
    "publication-late-gav": ("gradle.taskGraph.whenReady { publishing.publications.purifierLegacyMetadata.version='changed' }", "metadata publication binding changed"),
    "module-late-disabled": ("gradle.taskGraph.whenReady { "+metadata+".enabled=false }", "disabled metadata task"),
    "finish-input-dependency": ("task unexpected { outputs.file(file('custom-output'));doLast { file('HOOK_EXECUTED').text='bad' } };tasks.all { if(name=='"+finish+"') inputs.files(unexpected) }", "metadata snapshot task graph changed"),
    "module-input-dependency": ("task unexpected { outputs.file(file('custom-output'));doLast { file('HOOK_EXECUTED').text='bad' } };tasks.withType(org.gradle.api.publish.tasks.GenerateModuleMetadata).all { inputs.files(unexpected) }", "metadata task graph changed"),
}
selectors = {
    "attributes": "attributes { attribute(org.gradle.api.attributes.Attribute.of('fixture.selector',String),'special') }",
    "capabilities": "capabilities { requireCapability('fixture:requested:1') }",
    "endorse": "endorseStrictVersions()",
    "branch": "version { branch='legacy' }",
    "force": "force=true",
    "changing": "changing=true",
    "target-configuration": "targetConfiguration='special'",
    "preferred-version": "version { prefer('1.0') }",
}
for selector, mutation in selectors.items():
    declaration = "dependencies.add('apiElements','fixture:selector:1.0') { " + mutation + " }"
    cases['rich-initial-' + selector] = (declaration, 'unsupported-status')
    cases['rich-late-' + selector] = ("gradle.taskGraph.whenReady { " + declaration + " }", 'native Java component changed')
for selector, mutation in {
    'global-exclusions': "configurations.apiElements.exclude(group:'fixture',module:'excluded')",
    'variant-capabilities': "configurations.apiElements.outgoing.capability('fixture:api:1')",
}.items():
    cases['rich-initial-' + selector] = (mutation, 'unsupported-status')
    cases['rich-late-' + selector] = ("gradle.taskGraph.whenReady { " + mutation + " }", 'Cannot declare capability' if selector == 'variant-capabilities' else 'native Java component changed')
rows = []
for name, (extra, rejection) in cases.items():
    directory = out / name
    epoch.fixture(directory, profile, sdk)
    with (directory / "build.gradle").open("a") as stream:
        stream.write("\n" + extra + "\n")
    init = out / (name + ".init.gradle")
    init.write_text("gradle.ext.purifierLegacyMaven=[mode:'lock',output:System.getenv('METADATA_OUTPUT'),bindings:[],captureMetadata:true,artifacts:[:]]\napply from:'" + str(root / "nix/lib/legacy-maven.init.gradle") + "'\ngradle.beforeProject { p -> p.plugins.withId('maven') { gradle.ext.purifierLegacyMaven.register(p) } }\n")
    command = ["bash", str(args.gradle.resolve()), "--offline", "--no-daemon", "--init-script", str(init), finish]
    env = dict(os.environ, JAVA_HOME=sdk["java11"]["home"], GRADLE_USER_HOME=str(out / "gradle-home"), XDG_CACHE_HOME=str(out / "cache"), METADATA_OUTPUT=str(out / (name + "-output")))
    with (out / (name + ".log")).open("w") as log:
        result = subprocess.run(command, cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    text = (out / (name + ".log")).read_text()
    assert not (directory / "HOOK_EXECUTED").exists(), name
    if rejection == 'unsupported-status':
        assert result.returncode == 0, (name, text[-6000:])
        status = json.loads((out / (name + "-output/epoch/maven/nix-support/legacy-native-metadata.json")).read_text())
        assert status['state'] == 'unsupported' and status['reason'] == 'unsupported-component', status
        assert not list((out / (name + '-output/epoch/maven')).rglob('*.module'))
    elif rejection:
        assert result.returncode != 0 and rejection in text, (name, text[-6000:])
    else:
        assert result.returncode == 0, text[-6000:]
        status = json.loads((out / (name + "-output/epoch/maven/nix-support/legacy-native-metadata.json")).read_text())
        assert status["state"] == "complete", status
    rows.append(dict(case=name, command=command, exitCode=result.returncode, expectedRejection=rejection))
    (out / "report.json").write_text(json.dumps(rows, indent=2))
    print(name, result.returncode, flush=True)
(out / "source-context.json").write_text(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / "nix/lib").glob("legacy-*.gradle")}, indent=2))
print("NATIVE_METADATA_OWNERSHIP_GUARDS_PASS", flush=True)
