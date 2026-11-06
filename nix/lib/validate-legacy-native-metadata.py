# SPDX-License-Identifier: MIT
"""Validate a raw producer's required native metadata before strict projection."""
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NATIVE_MARKER = b"""  <!-- This module was also published with a richer model, Gradle metadata,  -->
  <!-- which should be used instead. Do not delete the following line which  -->
  <!-- is to indicate to Gradle or any Gradle module metadata file consumer  -->
  <!-- that they should prefer consuming it instead. -->
  <!-- do_not_remove: published-with-gradle-metadata -->
"""
CAPABILITY = {"kind": "gradle-java-component", "version": 1}


def validate(repository, publication):
    if publication.get("nativeMetadata") != CAPABILITY:
        raise ValueError("invalid legacy native metadata capability")
    repository = Path(repository)
    status = json.loads((repository / "nix-support/legacy-native-metadata.json").read_text())
    gav = (publication["groupId"], publication["artifactId"], publication["version"])
    if (status.get("version") != 1 or status.get("capability") != CAPABILITY
            or tuple(status.get(k) for k in ("group", "name", "artifactVersion")) != gav
            or status.get("state") != "complete" or status.get("reason") is not None):
        raise ValueError("legacy native metadata unavailable: " + str(status.get("reason")))
    directory = repository / gav[0].replace(".", "/") / gav[1] / gav[2]
    prefix = gav[1] + "-" + gav[2]
    payloads = {kind: (directory / (prefix + "." + kind)).read_bytes()
                for kind in ("pom", "module", "jar")}
    for kind, data in payloads.items():
        if status.get(kind + "Sha256") != hashlib.sha256(data).hexdigest():
            raise ValueError("native metadata producer digest differs: " + kind)
    pom = ET.fromstring(payloads["pom"])
    if tuple(pom.findtext("{*}" + k) for k in ("groupId", "artifactId", "version")) != gav:
        raise ValueError("native metadata POM coordinates differ")
    if payloads["pom"].count(b"do_not_remove: published-with-gradle-metadata") != 1:
        raise ValueError("native metadata POM marker missing or duplicated")
    if (payloads["pom"].count(NATIVE_MARKER) != 1
            or hashlib.sha256(payloads["pom"].replace(NATIVE_MARKER, b"", 1)).hexdigest() != status.get("originalPomSha256")):
        raise ValueError("native POM original body binding differs")
    module = json.loads(payloads["module"])
    if (module.get("formatVersion") != "1.1"
            or tuple(module.get("component", {}).get(k) for k in ("group", "module", "version")) != gav
            or "buildId" in module.get("createdBy", {}).get("gradle", {})):
        raise ValueError("native metadata module binding differs")
    variants = module.get("variants")
    if not isinstance(variants, list) or len(variants) != 2 or {v.get("name") for v in variants} != {"apiElements", "runtimeElements"}:
        raise ValueError("unsupported native metadata variants")
    for variant in variants:
        files = variant.get("files")
        if not isinstance(files, list) or len(files) != 1:
            raise ValueError("unsupported native metadata files")
        file = files[0]
        if (file.get("name") != prefix + ".jar" or file.get("url") != prefix + ".jar"
                or file.get("size") != len(payloads["jar"])):
            raise ValueError("native metadata artifact binding differs")
        for algorithm in ("md5", "sha1", "sha256", "sha512"):
            value = file.get(algorithm)
            if (not isinstance(value, str) or value != hashlib.new(algorithm, payloads["jar"]).hexdigest()):
                raise ValueError("native metadata artifact digest differs: " + algorithm)


if __name__ == "__main__":
    if sys.argv[1] == "--release-lock":
        release = Path(sys.argv[2])
        lock = json.loads(Path(sys.argv[3]).read_text())
        for project in lock["projects"]:
            identifier = lock["rootProjectName"] if project["path"] == ":" else project["path"][1:].replace(":", "-")
            for publication in project["publications"]:
                if "nativeMetadata" in publication:
                    validate(release / identifier, publication)
    else:
        validate(sys.argv[1], json.loads(sys.argv[2]))
