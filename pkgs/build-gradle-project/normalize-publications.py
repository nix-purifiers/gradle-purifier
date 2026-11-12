#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Normalize freshly published timestamped snapshots before dropping metadata."""
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def component(value):
    if (not value or value in (".", "..") or "/" in value or "\\" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"invalid Maven publication filename component: {value!r}")
    return value


def normalize(root):
    for metadata in sorted(Path(root).rglob("maven-metadata.xml")):
        directory = metadata.parent
        version = directory.name
        if not version.endswith("-SNAPSHOT"):
            continue
        model = ET.parse(metadata).getroot()
        artifact = component(model.findtext("{*}artifactId"))
        if artifact != directory.parent.name or model.findtext("{*}version") != version:
            raise ValueError(f"snapshot metadata does not match publication path: {metadata}")
        entries = model.findall("{*}versioning/{*}snapshotVersions/{*}snapshotVersion")
        if not entries:
            raise ValueError(f"snapshot metadata has no artifact versions: {metadata}")
        for entry in entries:
            selected = component(entry.findtext("{*}value"))
            if not re.fullmatch(re.escape(version[:-len("SNAPSHOT")]) + r"\d{8}\.\d{6}-\d+", selected):
                raise ValueError(f"invalid timestamped version in {metadata}: {selected}")
            extension = component(entry.findtext("{*}extension"))
            classifier = entry.findtext("{*}classifier")
            suffix = ("-" + component(classifier) if classifier else "") + "." + extension
            source = directory / (artifact + "-" + selected + suffix)
            destination = directory / (artifact + "-" + version + suffix)
            if not source.is_file():
                raise ValueError(f"snapshot metadata names a missing publication: {source}")
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise ValueError(f"conflicting snapshot publications: {destination}")
            source.replace(destination)
            for digest in (".md5", ".sha1", ".sha256", ".sha512"):
                checksum = source.with_name(source.name + digest)
                if checksum.is_file():
                    checksum.replace(destination.with_name(destination.name + digest))


if __name__ == "__main__":
    normalize(sys.argv[1])
