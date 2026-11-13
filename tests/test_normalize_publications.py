# SPDX-License-Identifier: MIT
import importlib.util
from pathlib import Path
import tempfile
import unittest

script = Path(__file__).resolve().parent.parent / "pkgs/build-gradle-project/normalize-publications.py"
spec = importlib.util.spec_from_file_location("normalize_publications", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SnapshotPublicationTests(unittest.TestCase):
    def fixture(self, root, selected="1.0-20261104.000001-1", content=b"jar"):
        directory = root / "example/library/1.0-SNAPSHOT"
        directory.mkdir(parents=True)
        entries = []
        for classifier, extension in [("", "pom"), ("", "jar"), ("sources", "jar"), ("", "module")]:
            suffix = ("-" + classifier if classifier else "") + "." + extension
            file = directory / ("library-" + selected + suffix)
            file.write_bytes(content)
            file.with_name(file.name + ".sha1").write_text("checksum")
            entries.append(f"<snapshotVersion><classifier>{classifier}</classifier>"
                           f"<extension>{extension}</extension><value>{selected}</value></snapshotVersion>")
        (directory / "maven-metadata.xml").write_text(
            "<metadata><artifactId>library</artifactId><version>1.0-SNAPSHOT</version>"
            "<versioning><snapshotVersions>" + "".join(entries) + "</snapshotVersions></versioning></metadata>")
        return directory

    def test_same_artifacts_at_different_times_have_same_names_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.fixture(root / "a")
            b = self.fixture(root / "b", "1.0-20261105.000002-7")
            module.normalize(root / "a")
            module.normalize(root / "b")
            def artifacts(path):
                return {p.name: p.read_bytes() for p in path.iterdir() if p.name != "maven-metadata.xml"}
            self.assertEqual(artifacts(a), artifacts(b))
            self.assertIn("library-1.0-SNAPSHOT-sources.jar", artifacts(a))
            self.assertIn("library-1.0-SNAPSHOT.module.sha1", artifacts(a))

    def test_missing_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(Path(tmp))
            next(directory.glob("*.pom")).unlink()
            with self.assertRaisesRegex(ValueError, "missing publication"):
                module.normalize(tmp)

    def test_conflicting_base_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.fixture(Path(tmp))
            (directory / "library-1.0-SNAPSHOT.pom").write_bytes(b"conflict")
            with self.assertRaisesRegex(ValueError, "conflicting"):
                module.normalize(tmp)

    def test_invalid_metadata_is_rejected(self):
        for replacement in ["../escaped", "2.0-20261104.000001-1"]:
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as tmp:
                directory = self.fixture(Path(tmp))
                metadata = directory / "maven-metadata.xml"
                metadata.write_text(metadata.read_text().replace("1.0-20261104.000001-1", replacement))
                with self.assertRaises(ValueError):
                    module.normalize(tmp)


if __name__ == "__main__":
    unittest.main()
