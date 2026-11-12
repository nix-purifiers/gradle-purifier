# Testing

The tests come in two layers.

1. **Flake checks** (`tests/default.nix`) run in the sandbox under
   `nix flake check`:
   - `gradle-purifier` builds the command, which compiles the recorder
     with warnings as errors and runs shellcheck on the script.
   - `recorder-unit` (`tests/RecorderTest.java`) and `cli-unit`
     (`tests/cli-unit.sh`) test the mapping of URLs, the pinned version
     listings, the entries made from the records of the recorder, the
     content hash and the parsing of the command.
   - `conformance-stale`, `-tampered`, `-malformed` and `-missing` give
     the builder the lockfile of the Java library fixture with one
     change each, from `tests/conformance/` (written by `lockfiles.nix`
     through `generate.sh`). The first three must fail evaluation with a
     problem that names the fix; the missing download must fail the
     build with a message that names `gradle-purifier lock`.
     `conformance-external` builds the Java library from a `src` without
     its lockfile and a `lockFile` outside it.
   - The `java-library*` checks build `tests/fixtures/java-library` and
     run its tests. The fixture is a Groovy-DSL build on Gradle 8 with a
     Maven publication and a sources jar, a dynamic version (resolved
     through an inline `maven-metadata.xml`), a library from an included
     build, and a file from an Ivy repository that serves plain files by
     pattern.
   - The `kotlin-*` checks build `tests/fixtures/kotlin-app` and run its
     tests. The fixture is a Kotlin-DSL build on Gradle 9 with a version
     catalog, a convention plugin in buildSrc that applies the Kotlin JVM
     plugin with a Java 21 toolchain, plugins from the plugin portal, a
     published Kotlin library, a Kotlin Multiplatform library with a JVM
     target, and an application. `kotlin-app-runs` runs the application
     from its package.
   - The `kotlin-native*` checks, on x86_64-linux, build
     `tests/fixtures/kotlin-native`, a Kotlin/Native build with a linuxX64
     library, its tests and a release executable, locked on JDK 17.
2. **CI jobs** run what needs the network or Nix outside the sandbox, on
   both systems:
   - `conformance-relock` relocks every fixture
     (`tests/relock-fixtures.sh --relock`) and fails when a lockfile
     changes.
   - `conformance-rebuild` builds the fixtures and builds them again with
     `nix build --rebuild`, which fails when the outputs differ.
   - `conformance-offline` (`tests/offline.sh`) runs `prefetch` on a copy
     of each fixture with an empty Gradle user home, then `./gradlew` in
     a network namespace without a network: the builds and their tests
     must run, the programs must run, and Gradle's download cache must
     stay empty. `gradle-purifier publish` must publish the artifacts and
     module metadata Nix built, byte for byte, without compiling the
     project. An import through the Gradle tooling API
     (`tests/ToolingImport.java`, the EclipseProject model that Buildship
     and jdtls use) must resolve the classpath with sources from the
     lock. A lockfile that is not JSON, an edited lockfile, a wrong hash,
     a missing download and a changed build definition must each fail
     with a message that names the command to run.
   - `conformance-update` (`tests/update.sh`) updates commons-lang3 in a
     copy of `tests/fixtures/update`, whose `gradle.lockfile` pins 3.12.0,
     and checks that both lockfiles move and that `prefetch` accepts
     them.

The lockfiles of the fixtures are test data. `tests/relock-fixtures.sh`
renews them; it needs the network.

`tests/signing-native.py` captures and builds a Maven publication with the
Signing plugin without a private key. Both interactive publication and CLI
publication must still reject the absent key. The test then generates an
ephemeral key, publishes through the CLI with native compilation poisoned,
compares the deployed files with the Nix release and verifies every detached
signature using GnuPG. CI runs this on both Linux architectures and retains
logs and the report. The key stays outside the fixture's source tree.
The fixture includes UTF-8 Javadoc text, so its Java 17 sandboxed release
also checks that forked documentation tools inherit a UTF-8 locale.

`runtime-metadata-unit` checks the runtime exporter with the pinned shared
JVM repository tool, including queried BOM sidecars, coordinate/path
rejections, duplicate metadata and provided-input authority. The composition
check also compares the projected JUnit 5.11.2 BOM metadata with the exact
locked download. Retaining an imported BOM's metadata does not add it to the
selected runtime components or fetch the artifacts named by its constraints.

The Gradle 6.9.4 legacy Maven controls distinguish old typed POM-only locks
from the native Java metadata protocol added on fresh capture or explicit
relock. Native ownership tests cover creation callbacks, task actions and
implicit graph edges; rich selectors remain unavailable to strict projection.
An unsupported POM graph must still lock, test and produce its original raw
JAR/POM, while strict projection and CLI publication fail before deployment.
The provided-input control requires Gradle to reject a real competing version
through projected native module constraints, and checks Maven's observed
behavior separately. These are bounded profile controls, not a version range
or a new catalog support point.
