// SPDX-License-Identifier: MIT

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import java.util.Objects;

/**
 * Unit tests of the recorder's mapping between request paths, remote URLs and paths in the tree
 * of locked downloads. The `recorder-unit` flake check compiles it against the recorder and runs
 * it; it exits non-zero on the first failure.
 */
public final class RecorderTest {

  private static int failures;

  public static void main(String[] args) {
    // A request path names the remote URL: scheme, authority, path.
    expect(
        "remote of a Maven Central path",
        URI.create("https://repo.maven.apache.org/maven2/org/x/x/1/x-1.jar"),
        Recorder.remoteOf("/https/repo.maven.apache.org/maven2/org/x/x/1/x-1.jar"));
    expect(
        "remote with a port",
        URI.create("http://localhost:8081/repository/maven/a.pom"),
        Recorder.remoteOf("/http/localhost:8081/repository/maven/a.pom"));
    expect(
        "remote keeps an encoded path as it is",
        URI.create("https://host/a%20b/c.jar"),
        Recorder.remoteOf("/https/host/a%20b/c.jar"));
    expect("no remote for another scheme", null, Recorder.remoteOf("/ftp/host/x"));
    expect("no remote for a path without a host", null, Recorder.remoteOf("/https"));
    expect("no remote for a bare host", null, Recorder.remoteOf("/https/host"));

    // A remote URL is at <scheme>/<authority><path> in the tree, decoded as a file name.
    expect(
        "tree path of a Maven Central URL",
        "https/repo.maven.apache.org/maven2/org/x/x/1/x-1.jar",
        Recorder.treePathOf(URI.create("https://repo.maven.apache.org/maven2/org/x/x/1/x-1.jar")));
    expect(
        "tree path keeps the port",
        "http/localhost:8081/r/a.pom",
        Recorder.treePathOf(URI.create("http://localhost:8081/r/a.pom")));
    expect(
        "tree path decodes the URL path",
        "https/host/a b/c.jar",
        Recorder.treePathOf(URI.create("https://host/a%20b/c.jar")));

    // A checksum file describes the file next to it.
    expect(
        "a .sha1 describes its file",
        URI.create("https://host/m2/a/1/a-1.jar"),
        Recorder.describedBy(URI.create("https://host/m2/a/1/a-1.jar.sha1")));
    expect(
        "a .sha512 describes its file",
        URI.create("https://host/m2/a/1/a-1.pom"),
        Recorder.describedBy(URI.create("https://host/m2/a/1/a-1.pom.sha512")));
    expect(
        "a jar describes nothing",
        null,
        Recorder.describedBy(URI.create("https://host/m2/a/1/a-1.jar")));

    // Version listings change with every release, so the lock holds their contents.
    expect(
        "maven-metadata.xml is mutable",
        true,
        Recorder.isMutable(URI.create("https://host/m2/org/a/maven-metadata.xml")));
    expect(
        "its checksum is mutable",
        true,
        Recorder.isMutable(URI.create("https://host/m2/org/a/maven-metadata.xml.sha1")));
    expect(
        "a POM is not mutable",
        false,
        Recorder.isMutable(URI.create("https://host/m2/org/a/1/a-1.pom")));
    expect("generic element-list stays hashed", false,
        Recorder.isMutable(URI.create("https://host/api/element-list")));
    expect("generic package-list stays hashed", false,
        Recorder.isMutable(URI.create("https://host/api/package-list")));

    expect("marked Javadoc metadata is selected", true,
        Recorder.isJavadocMetadata(URI.create("https://host/api/package-list"), "javadoc-link-metadata"));
    expect("unmarked metadata is ordinary", false,
        Recorder.isJavadocMetadata(URI.create("https://host/api/package-list"), null));
    expect("marker cannot inline another filename", false,
        Recorder.isJavadocMetadata(URI.create("https://host/api/body.bin"), "javadoc-link-metadata"));
    expect("non-UTF8 metadata cannot be inline text", false,
        Recorder.isUtf8(new byte[] {(byte) 0xff, 0, (byte) 0x80}));
    expect("UTF8 metadata can be inline text", true,
        Recorder.isUtf8("external\n".getBytes(StandardCharsets.UTF_8)));

    // Maven Central's files, under either of its names, have Google's mirror.
    expect(
        "mirror of repo.maven.apache.org",
        URI.create("https://maven-central.storage-download.googleapis.com/maven2/org/a/1/a-1.jar"),
        Recorder.mirrorOf(URI.create("https://repo.maven.apache.org/maven2/org/a/1/a-1.jar")));
    expect(
        "mirror of repo1.maven.org",
        URI.create("https://maven-central.storage-download.googleapis.com/maven2/org/a/1/a-1.pom"),
        Recorder.mirrorOf(URI.create("https://repo1.maven.org/maven2/org/a/1/a-1.pom")));
    expect(
        "no mirror for the plugin portal",
        null,
        Recorder.mirrorOf(URI.create("https://plugins.gradle.org/m2/org/a/1/a-1.pom")));

    // A pinned version listing is served as pinned, and its checksum files match it.
    String listing = "https/h/org/a/maven-metadata.xml";
    Map<String, byte[]> pinned = Map.of(listing, "<metadata/>\n".getBytes(StandardCharsets.UTF_8));
    expect(
        "a pinned file",
        "<metadata/>\n",
        text(Recorder.pinnedContents(pinned, listing)));
    expect(
        "the SHA-1 of a pinned file is its hexadecimal digest",
        hex("SHA-1", "<metadata/>\n"),
        text(Recorder.pinnedContents(pinned, listing + ".sha1")));
    expect(
        "the MD5 of a pinned file is its hexadecimal digest",
        hex("MD5", "<metadata/>\n"),
        text(Recorder.pinnedContents(pinned, listing + ".md5")));
    expect("an unpinned file", null, Recorder.pinnedContents(pinned, "https/h/org/a/1/a-1.jar"));
    expect(
        "the checksum of an unpinned file",
        null,
        Recorder.pinnedContents(pinned, "https/h/org/a/1/a-1.jar.sha1"));

    if (failures > 0) {
      System.err.println(failures + " recorder tests failed");
      System.exit(1);
    }
    System.out.println("recorder tests passed");
  }

  private static String text(byte[] bytes) {
    return bytes == null ? null : new String(bytes, StandardCharsets.UTF_8);
  }

  private static String hex(String algorithm, String text) {
    try {
      return java.util.HexFormat.of()
          .formatHex(
              MessageDigest.getInstance(algorithm).digest(text.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException e) {
      throw new IllegalStateException(e);
    }
  }

  private static void expect(String name, Object expected, Object actual) {
    if (!Objects.equals(expected, actual)) {
      failures++;
      System.err.println("FAIL " + name + ": expected " + expected + ", got " + actual);
    }
  }
}
