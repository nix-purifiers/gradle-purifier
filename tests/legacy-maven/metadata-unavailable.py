#!/usr/bin/env python3
"""Unsupported POM graph: basic lifecycle passes; strict publication fails."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_jvm import bind_fixture_sdks
from fixture_build import command as fixture_build
parser = argparse.ArgumentParser()
for key in ("cli", "sdks", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
parser.add_argument("--case", choices=("pom-edge", "rich-selector"), default="pom-edge")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
out = args.output.resolve()
out.mkdir()
sdk = json.loads(args.sdks.read_text())
spec = importlib.util.spec_from_file_location("epoch", root / "tests/epoch-probes/probe.py")
epoch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(epoch)
profile = next(p for p in json.loads((root / "tests/epoch-probes/profiles.json").read_text())["profiles"] if p["id"] == "gradle-6.9.4")
project = out / "producer"
epoch.fixture(project, profile, sdk)
bind_fixture_sdks(project, 11, [8], args.cli.resolve(), sdk)
if args.case == "rich-selector":
    with (project / "build.gradle").open("a") as stream:
        stream.write("dependencies.add('apiElements','fixture:selector:1.0') { attributes { attribute(org.gradle.api.attributes.Attribute.of('fixture.selector',String),'special') } }\n")
    reason = 'unsupported-component'
else:
    with (project / "build.gradle").open("a") as stream:
        stream.write("""
    uploadArchives.repositories.mavenDeployer.pom.withXml { provider ->
     def d=provider.asNode().appendNode('dependencies').appendNode('dependency')
     d.appendNode('groupId','fixture');d.appendNode('artifactId','pom-only');d.appendNode('version','1.0');d.appendNode('scope','runtime')
    }
    """)
    reason = 'unsupported-pom-graph'

cli = args.cli.resolve()
env = dict(os.environ, JAVA_HOME=sdk["java11"]["home"], GRADLE_USER_HOME=str(out / "gradle-home"), XDG_CACHE_HOME=str(out / "cache"))
rows = []


def run(stage, command, rejection=None):
    command = list(map(str, command))
    with (out / (stage + ".log")).open("w") as log:
        result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    text = (out / (stage + ".log")).read_text()
    if rejection:
        assert result.returncode != 0 and rejection in text, (stage, text[-6000:])
    else:
        assert result.returncode == 0, (stage, text[-6000:])
    rows.append(dict(stage=stage, command=command, exitCode=result.returncode, expectedRejection=rejection))
    (out / "report.json").write_text(json.dumps(rows, indent=2))
    print(stage, result.returncode, flush=True)
    return text


run("fresh-lock", [cli, "lock", "--bind-sdks"])
settings = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in cli.read_text().splitlines() if line.startswith(("lib=", "nixpkgs=", "purifier_source="))}
command = ["nix-build", "--quiet", Path(settings["lib"]) / "command.nix", "--argstr", "nixpkgs", settings["nixpkgs"], "--argstr", "purifierSource", settings["purifier_source"], "--argstr", "src", project, "--argstr", "lockFile", project / "gradle-purifier.lock.json", "--no-out-link"]
run("pure-basic-tests", fixture_build(cli, project, "tests"))
release = Path(run("raw-release", fixture_build(cli, project, "release")).strip().splitlines()[-1])
status = json.loads((release / "epoch/nix-support/legacy-native-metadata.json").read_text())
assert status["state"] == "unsupported" and status["reason"] == reason, status
assert not list((release / "epoch/example/epoch").rglob("*.module"))
run("strict-projection-rejected", fixture_build(cli, project, "publication"), "legacy native metadata unavailable: " + reason)
run("strict-cli-publish-rejected", [cli, "publish", "uploadArchives"], "legacy native metadata unavailable: " + reason)
assert not (project / "repository").exists()
print("UNSUPPORTED_METADATA_BASIC_PASS_STRICT_REJECT_PASS", flush=True)
