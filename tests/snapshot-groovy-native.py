#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Relock a SNAPSHOT/Groovy project and replay it through native Gradle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    cli = str(Path(sys.argv[1]).resolve())
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="gradle-snapshot-groovy-") as tmp:
        work = Path(tmp)
        source = work / "project"
        shutil.copytree(root / "tests/fixtures/snapshot-groovy", source,
                        ignore=shutil.ignore_patterns(".gradle", ".gradle-purifier", "build"))
        (work / "home").mkdir()
        env = dict(os.environ, HOME=str(work / "home"), GRADLE_USER_HOME=str(work / "gradle"))

        def run(command):
            subprocess.run(command, cwd=source, env=env, check=True, timeout=300)

        lock = source / "gradle-purifier.lock.json"
        before = lock.read_bytes()
        run([cli, "lock", "--relock", "--java", str(json.loads(before)["javaMajor"])])
        assert lock.read_bytes() == before, "relock changed unchanged dependency selections"
        from fixture_jvm import fixture_jvm
        selected = fixture_jvm(cli, json.loads(before)["javaMajor"], work / "fixture-jvm")
        env["JAVA_HOME"] = selected["home"]
        run([cli, "prefetch"])
        gradle = str(source / ".gradle-purifier/distribution/bin/gradle")
        run([gradle, "--offline", "--no-daemon", "clean", "check"])
        report = source / "build/test-results/test/TEST-example.GreetingTest.xml"
        assert 'failures="0"' in report.read_text()


if __name__ == "__main__":
    main()
