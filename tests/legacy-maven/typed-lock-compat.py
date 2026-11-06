#!/usr/bin/env python3
"""Replay a real baseline typed POM-only lock, then explicitly upgrade it."""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
for key in ("cli", "baseline-cli", "sdks", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
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
cli = args.cli.resolve()
env = dict(os.environ, JAVA_HOME=sdk["java11"]["home"], GRADLE_USER_HOME=str(out / "gradle-home"), XDG_CACHE_HOME=str(out / "cache"))
rows = []


def run(stage, command):
    command = list(map(str, command))
    with (out / (stage + ".log")).open("w") as log:
        result = subprocess.run(command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    text = (out / (stage + ".log")).read_text()
    assert result.returncode == 0, (stage, text[-6000:])
    rows.append(dict(stage=stage, command=command, exitCode=result.returncode))
    (out / "report.json").write_text(json.dumps(rows, indent=2))
    print(stage, result.returncode, flush=True)
    return text


run("baseline-typed-capture", [args.baseline_cli.resolve(), "lock"])
lockfile = project / "gradle-purifier.lock.json"
original = lockfile.read_bytes()
lock = json.loads(original)
publication = lock["projects"][0]["publications"][0]
assert publication["kind"] == "legacy-maven-upload" and "nativeMetadata" not in publication
(out / "baseline-typed-lock.json").write_bytes(original)
run("candidate-matching-lock-no-upgrade", [cli, "lock"])
assert lockfile.read_bytes() == original
run("candidate-prefetch", [cli, "prefetch"])
settings = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in cli.read_text().splitlines() if line.startswith(("lib=", "nixpkgs=", "purifier_source="))}
command = ["nix-build", "--quiet", Path(settings["lib"]) / "command.nix", "--argstr", "nixpkgs", settings["nixpkgs"], "--argstr", "purifierSource", settings["purifier_source"], "--argstr", "src", project, "--argstr", "lockFile", lockfile, "--no-out-link"]
run("old-typed-pure-tests", command + ["-A", "tests"])
release = Path(run("old-typed-release", command + ["-A", "release"]).strip().splitlines()[-1])
assert not list((release / "epoch/example/epoch").rglob("*.module"))
assert not (release / "epoch/nix-support/legacy-native-metadata.json").exists()
run("old-typed-native-publish", [cli, "publish", "uploadArchives"])
assert lockfile.read_bytes() == original and not list((project / "repository").rglob("*.module"))
shutil.rmtree(project / "repository")
run("explicit-relock-upgrade", [cli, "lock", "--relock"])
upgraded = json.loads(lockfile.read_text())
assert upgraded["projects"][0]["publications"][0]["nativeMetadata"] == dict(kind="gradle-java-component", version=1)
run("upgraded-pure-tests", command + ["-A", "tests"])
projection = Path(run("upgraded-strict-projection", command + ["-A", "publication"]).strip().splitlines()[-1])
assert list((projection / "repository/example/epoch").rglob("*.module"))
print("REAL_OLD_TYPED_REPLAY_AND_EXPLICIT_NATIVE_METADATA_MIGRATION_PASS", flush=True)
