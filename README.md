# gradle-purifier

gradle-purifier keeps Gradle's development workflow on locked,
Nix-resolved dependencies. Use the normal build and test commands in your
working tree; building the project itself in the Nix sandbox is an
optional next step.

## Quickstart

From the project root:

```zsh
nix shell 'github:nix-purifiers/gradle-purifier'
gradle-purifier lock
gradle-purifier prefetch
./gradlew build
```

Commit the lockfiles that the lock command writes, including the build
tool's own lockfile when it has one. Keep using the project's usual build,
test and editor commands with this setup. Nix provides the locked
dependencies; the development commands above run outside the Nix sandbox.
No project Nix files are needed for this workflow.

Sandboxed builds of the whole project are an optional
[next step](#building-the-project-with-nix).

For persistent use, add these packages through your usual Nix configuration.


## Development loop

After each lock, run `prefetch` at the build root:

```zsh
nix shell 'github:nix-purifiers/gradle-purifier'
gradle-purifier prefetch
```

It realizes in the Nix store the locked downloads and the Gradle
distribution of the wrapper and, for a Kotlin/Native build, the patched
toolchain. It keeps the project's installed JVMs and does not acquire a JDK. Their garbage-collector roots go in `.gradle-purifier/` beside
the lockfile. It also installs an init script in the `init.d` directory
of the Gradle user home, which Gradle applies to every build. For the
builds of this root, the script points every repository at the locked
downloads. It leaves every other build alone. After that, the build tool
works as usual:

```zsh
./gradlew build
```

`prefetch` links the distribution where `./gradlew` looks for it, so the
wrapper downloads nothing. Gradle reads the locked files where they are,
so its own download cache stays empty. A build that needs a download the
lockfile does not hold fails, and the init script names
`gradle-purifier lock`. When the lockfile changed since the last
prefetch, the init script says so and the build resolves the usual way
until `prefetch` runs again. Native Gradle keeps selecting the installed runtime and toolchains.
Their ordinary version, target and compiler requirements still apply.
Replacing a host runtime alone does not require relocking dependencies.

Gradle keeps a lockfile of its own only in a build that turns on its
dependency locking. Without one, `gradle-purifier.lock.json` records the
resolution as well as the downloads: it holds the version listings
(`maven-metadata.xml`) that dynamic versions resolve through, and `lock`
resolves from them again. In a build with dependency locking, `lock`
keeps the resolution of Gradle's lockfiles as they are.

Gradle's dependency locking writes a lockfile for each project, for the
settings and for each buildscript, rather than one file. Gradle's
lockfiles therefore stay in the tree: the builder takes no argument for
a copy of them kept elsewhere, and the commands take no
`--tool-lockfile`. A build kept external keeps only
`gradle-purifier.lock.json` outside the tree.

These are the commands:

- `gradle-purifier lock` runs Gradle and records the downloads. It makes
  the lock again only when the build definition changed: the settings and
  build scripts, `gradle.properties`, the version catalogs, the wrapper
  properties, Gradle's own lockfiles, and the files of `buildSrc` and of
  included builds. `--relock` makes it again regardless. A dynamic
  version, such as `3.17.+`, resolves as it did in the last lock, from
  the version listings that the lockfile holds. A relock of an unchanged
  build reproduces the lockfile byte for byte. Capture and relock use
  `JAVA_HOME` or `java` on `PATH`. Only an explicit `--java 17` requests
  a packaged nixpkgs JDK; the bundled implementation JVM never replaces
  the installed project runtime. Gradle arguments after `--` go into
  the lockfile and into every Gradle run of the lock.
- `gradle-purifier update [<group>:<artifact>...]` locks again with the
  newest versions that the dynamic versions allow, for every package or
  for the packages named (`group:*` names a whole group). In a build that
  uses Gradle's dependency locking, it runs Gradle with `--write-locks`,
  or `--update-locks` for the packages named, and writes Gradle's
  lockfiles back into the build.
- `gradle-purifier prefetch` prepares the development loop, as above.
- `gradle-purifier publish` uploads the `release` that Nix built (see
  below).

Capture and Nix builds skip the Gradle Signing plugin's `Sign` tasks, so
they need no private release key and their outputs contain no detached
signatures. Interactive Gradle commands keep the build's signing policy.
`gradle-purifier publish` signs the Nix-built artifacts using the signing
configuration and credentials of the native build at deployment time.

For a build kept external, each command takes `--lockfile <path>`, or the
environment variable `GRADLE_PURIFIER_LOCKFILE`. The dev shell of the
flake that passes `lockFile` to the builder can set the variable to the
same path.

### Publishing

```zsh
nix shell 'github:nix-purifiers/gradle-purifier'
gradle-purifier publish
```

The command builds the `release` with Nix, from the lockfile, then runs
the `publish` task of Gradle (or the tasks given after `publish`) with the
artifacts and the Gradle module metadata of every Maven publication
replaced by the files Nix built. The publishing repositories and
credentials of the build apply, and nothing is compiled outside the
sandbox. Gradle generates the POM from the build, as it does for any
publish.

## Dependencies from another purified project

Pass a producer's flake package to `lock`:

```zsh
gradle-purifier lock \
  --input 'github:owner/library/<rev>#library'
gradle-purifier prefetch
```

The producer must export a Maven view through `projections.maven`. Its
catalog selects the supplied modules automatically, including their Nix-derived
versions. Keep ordinary dependency declarations in the project; no version
rewrite table is needed. Repeat `--input` for additional producers.

The lock records the producer's pinned Nix source, rather than listing its
artifacts as downloads. Relocking retains that pin; `update` refreshes it.
Conflicting providers of the same module are rejected.

For a Nix build definition, the corresponding builder argument is
`projectDependencies = [ producer.packages.${system}.library ];`. By default,
the builder reads the inputs recorded in the lock.

Projected repositories use immutable, Nix-derived versions. The purifier's
publish command deploys those coordinates and supplied dependencies, with
POM references rewritten so ordinary Maven consumers can resolve them.

Publications also include dependencies from Gradle's included builds. When a
Nix input changes a transitive edge, the affected intermediate descriptors get
immutable versions too.

## Building the project with Nix

Once the native development workflow is working, you can also build and
test the whole project in the Nix sandbox. For this step, keep a flake or
another Nix build definition in the project's repository, or maintain an
external packaging repository that points at its sources and lockfiles.
The builder consumes the same lockfiles as the development workflow.

A flake is optional and can live in the project or in another repository:

```nix
# flake.nix
{
  inputs.gradle-purifier.url =
    "github:nix-purifiers/gradle-purifier";
  inputs.nixpkgs.follows = "gradle-purifier/nixpkgs";

  outputs =
    { nixpkgs, gradle-purifier, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ gradle-purifier.overlays.default ];
      };
    in
    {
      packages.${system}.app =
        (pkgs.gradle-purifier.buildGradleProject { src =
          ./.; })
        .packages.app;
    };
}
```

`gradle-purifier.overrideScope` replaces what the scope shares. The
builder takes what it needs from the package set by name, such as
`javaPackages` and `stdenvNoCC`, and
`gradle-purifier.buildGradleProject.override` replaces any of them. The
builder's result has these attributes:

- `packages.<id>` holds a package per project, named after the project:
  the name of the root project for the root project, and the project path
  with `-` for `:` otherwise (`:libs:core` is `libs-core`). Programs go
  to `bin/` of `out`: the distribution of the `application` plugin, with
  its start script set to run on the JDK of the build, and the release
  executables of Kotlin/Native. The archives that `assemble` makes go to
  `share/archives/` of `out`, named without the version of the project,
  and `packages.<id>.archives."<key>"` is the path of one. Maven
  publications go to the `maven` output, in Maven layout, with each
  artifact linked without the version at
  `nix-support/artifacts/<publication>/<artifactId>[-<classifier>].<ext>`,
  and `packages.<id>.artifacts.<publication>."<key>"` is the path of one.
- `tests` runs the `check` task of every project in the sandbox.
- `release` holds the Maven output of every project with publications,
  which `gradle-purifier publish` uploads.
- `fetches` lists the downloads of the build as entries of the
  nix-purifiers library, for SBOM, license and vulnerability tools.

On Darwin, sandboxed Nix builds allow local networking for Gradle's daemon
and file-lock listener. The builder appends `-Djava.net.preferIPv4Stack=true`
to `JAVA_TOOL_OPTIONS` inside the build: Seatbelt rejects the JVM's
IPv4-mapped IPv6 loopback connections. It affects JVMs that inherit that
environment and makes explicit IPv6 sockets unavailable there.
Process-isolated Gradle workers sanitize their environment. For the exact
official Gradle 8.14.6 and 9.9.0 binary ZIP URL/hash pairs, Darwin Nix builds
therefore use a derived Gradle SDK: one worker component is compiled from
pinned official source to propagate only an explicitly true parent
`java.net.preferIPv4Stack` property to those workers. Other environment
filtering remains intact. Unknown distributions keep their official SDK and
can still encounter this worker transport limitation.

`upstreamDistribution` is the unchanged locked distribution, while
`distribution` is the effective Nix build SDK. `distributionVariant` and
`distributionProvenance` identify the derivative and its manifest, including
the official ZIP, source commit/hash, patch, compiler, class and component
hashes. A derivative is not the unchanged official artifact. `fetches` still
lists locked upstream inputs; the derivative's fixed source input is recorded
separately in its provenance. External network access remains denied. Capture,
prefetch, and ordinary working-tree commands outside Nix builds keep their
normal JVM stack selection. Linux builds keep their normal stack selection.
The Tier 2 host isolation test uses the same setting only within its explicit
Darwin Seatbelt network cut.

The builder takes these arguments besides `src`, the root of the build:

- `lockFile` is the lockfile; the default is `gradle-purifier.lock.json`
  in `src`. A build kept external passes a lockfile from outside `src`,
  and `src` then needs only the files of Gradle.
- `fetchers` replaces the fetchers of the nix-purifiers library, such as
  a `fetchurl` that sends credentials to a company repository.
- `jdk` is the immutable Nix SDK for a sandboxed build. Undeclared projects
  retain the packaged default by captured Java major. Host paths are never
  sandbox SDK inputs.
- `toolchainJdks` lists the JDKs for Java toolchains that ask for another
  Java version. Gradle provisions no toolchain in the sandbox.
- `doCheck` runs the `check` task of each project in its package build.
- `srcFilter` filters the build root before it is copied into the store.

Local adoption keeps the installed Gradle runtime and native SDK discovery.
It does not add host binary, patch, vendor or architecture admission gates to
the dependency lock. Replacing a compatible host SDK does not require relock;
a changed dependency graph or build definition may still require it.
The existing `javaMajor` field remains a convenience for optional sandboxed
SDK selection, not a native runtime allowlist.

The private `.gradle-purifier/native-runtime.properties` file reports the
currently running manager JVM on a best-effort basis. It is host-local
state, outside lock hashes, and proves no compiler or task execution coverage.
Native Gradle enforces the actual compiler, toolchain and JVM target constraints.
A Java 21 library cannot satisfy a Java 17 consumer merely because both SDKs
are installed; a JRE cannot compile Java without a separate compiler SDK.

Fully sandboxed builds use immutable Nix SDKs. Explicit `jdk` and
`toolchainJdks` arguments must carry Nix package context, not host paths.
A present source SDK binding retains its strict source NAR, SDK identity,
manager and role checks. A declaration that has not been bound does not block
local dependency fetching; sandboxed execution needs `--bind-sdks` or an
explicit immutable `jdk`. Bundled helpers only compute paths and record
requests; they never replace the project runtime.

### Project-owned JVM SDK sources

On `x86_64-linux`, `gradle-purifier lock --bind-sdks` explicitly resolves
`gradle-purifier.sdks.json` for immutable external SDK selection. For example, this declaration selects an explicit
manager adapter from the published shared library:

```json
{
  "version": 1,
  "system": "x86_64-linux",
  "sdks": [{
    "id": "manager",
    "source": {
      "type": "github",
      "owner": "nix-purifiers",
      "repo": "purifier",
      "rev": "f444063c705056a605583bd273cc8147f57808ee",
      "narHash": "sha256-NaeaVq9B520WgzmRSMLGdN2zqnFlvM/wfZ8hECHhYqY="
    },
    "selectionKind": "jvm-sdk-adapter-v1",
    "attribute": ["lib", "jvmSdkAdapters", "x86_64-linux", "openjdk17"]
  }],
  "manager": "manager"
}
```

Add SDK selections for the native project's compiler, JUnit, JavaExec and
Javadoc roles. Task-only selections may use `derivation-v1` and a derivation
with an explicit context-bearing `home`. The manager requires a source-owned
`jvm-sdk-adapter-v1` record declaring its executable and runtime home. This
backend accepts immutable GitHub sources with immutable GitHub transitive
inputs. Local mutable flakes and inferred runtime layouts are unsupported.

The declaration participates in the build-definition hash. Change its pins
explicitly and run `gradle-purifier lock --relock --bind-sdks`; relocking validates those
pins rather than moving them. `--java` and direct builder JDK overrides cannot
be combined with this explicit binding mode. Explicit SDK binding and sandboxed
replay use pure evaluation with import-from-derivation disabled.

The versioned `jvm-sdk-context-v2` lock records a mandatory actual Gradle
manager, including builds with no SDK tasks or only manager-SDK tasks. SDK
identity covers exact output, home and Nix contexts. Manager identity also
covers executable and runtime home contexts; Java version strings alone never
select a replacement. Replay reconstructs source-owned values and retains SDK
and source-tree GC roots. An actual manager or selected task SDK mismatch
fails before the task executes. Disabled Test tasks supply no source-mode role.

Original native SDK selections and project files are preserved. Explicitly
bound capture and sandbox launches supply startup discovery defaults when no original native
discovery configuration is present. Existing native discovery properties and
arguments take precedence. Ordinary Gradle and IDE commands must already have
working native SDK discovery, such as `org.gradle.java.installations.paths` in
the original project or user Gradle configuration. Configure actual declared
SDK homes, disable unwanted automatic downloads, and select the declared
manager. Prefetch does not rewrite project or global Gradle properties.
Missing native discovery remains an error; an init script cannot reliably add
it after Gradle has initialized its toolchain services.

This opt-in mechanism establishes no Java 6/7 or Gradle version range claim.
Compatibility records require the corresponding native project workflow proof.

`lib.gradle-purifier.readLock { src, lockFile }` reads the lock into the
shape that every purifier shares: `entries`, `outputs` and `problems`.

### One-off builds without a project Nix file

For an ad hoc build, the builder can also be evaluated directly from the
project root. Replace the example package name with one from your project:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/gradle-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.gradle-purifier.buildGradleProject { src = ./.; })
.packages.app'
```

This uses the purifier's pinned nixpkgs, matching the lock command above.
To build with ambient `<nixpkgs>` from your Nix search path instead,
replace `import purifier.inputs.nixpkgs` with `import <nixpkgs>`.
Both forms need no Nix files in your project. The build tool and language
versions must match the lockfile; with ambient nixpkgs, lock with the
purifier from that package set or pass a compatible toolchain to the builder.

## Working offline

The locked repository keeps regular files at their native artifact names.
Gradle's Groovy compiler discovery resolves symlinks before inspecting jar
names, so symlinks directly to individual Nix store objects are insufficient.
Sandboxed Maven publications normalize timestamped SNAPSHOT filenames from
their emitted metadata before removing timestamp metadata. POM, module and
classifier artifacts remain required where the lock declares them.

Repositories with explicit DNS or IPv4 ports replay through immutable directory
aliases whose authority names contain no classpath separator. This keeps forked
Java compilers and runtimes from splitting a dependency path at the port's colon.
The original URLs, lock identities and artifact bytes remain unchanged; existing
locks gain the aliases when prefetched with the updated purifier.

After `prefetch`, `./gradlew`, a Gradle on the `PATH` and an IDE build
with the network cut off. Gradle reads the locked files from the Nix
store, so its own download cache stays empty.

## Editor

An IDE imports a Gradle build with its own Gradle run, through the Gradle
tooling API: IntelliJ IDEA, and Eclipse and VS Code through Buildship and
jdtls. The init script that `prefetch` installs applies to those runs too,
so the import reads the locked downloads. The lock run resolves the
sources and the javadoc of every library, so an IDE that attaches them
finds them in the lock. An IDE set to use the Gradle wrapper finds the
distribution that `prefetch` linked. One set to a local distribution can
use `.gradle-purifier/distribution` beside the lockfile.

## Troubleshooting

Every problem with a lockfile fails in the command and at Nix evaluation,
with a message that names the fix.

| Problem | Message | Fix |
| --- | --- | --- |
| The build definition changed since the lock | "the build definition … changed since … was made" | `gradle-purifier lock` |
| The lockfile was edited by hand | "does not match its contentHash, so it was edited" | `gradle-purifier lock --relock`, or restore it from version control |
| The lockfile is not JSON, has another format version or lacks a field | "not valid JSON", "has lock format version …", "lacks a field" | `gradle-purifier lock --relock` |
| A download does not match its hash | Nix reports a hash mismatch | restore the lockfile, or `gradle-purifier lock --relock` if the remote changed the file |
| The build needs a file the lockfile does not hold | "the build needs a file that … does not hold" | `gradle-purifier lock --relock` |
| A dynamic version asks for a version the lock never saw | "a dynamic version asks for a version that the version listings of the lockfile do not have" | `gradle-purifier update <group>:<artifact>` |
| The lockfile changed since the last prefetch | "changed since 'gradle-purifier prefetch' ran" | `gradle-purifier prefetch` |

## Supported language repositories

The lock records every download through Gradle's repositories: Maven and
Ivy repositories of settings scripts, of `pluginManagement`, of
`dependencyResolutionManagement`, of buildscripts and of projects, in the
root build, in `buildSrc` and in included builds, and the Gradle plugin
portal. To do that, the lock run copies the build to
`.gradle-purifier/lock-run` and runs it through an init script that points
every repository at a recorder on the loopback address, which fetches
each file and records its URL and hash. The lock run builds, tests and
publishes (into a scratch directory) every project, and resolves every
resolvable configuration with sources and javadoc, so the lockfile holds
what any package build or editor needs.

Some files are recorded in a particular way:

- When Gradle compares a file it holds from one repository with the
  checksum another repository serves, the recorder fetches the whole
  file, so every repository's copy is in the lockfile.
- A `maven-metadata.xml`, which Gradle reads for dynamic versions and
  snapshots, changes whenever a new version is published, so the
  lockfile holds its contents.
- A file of Maven Central gets Google's mirror of Maven Central as a
  mirror, which Nix tries when Maven Central fails. With
  `GRADLE_PURIFIER_CENTRAL_MIRROR=1`, the lock run downloads from the
  mirror. The lockfile names Maven Central either way.
- Kotlin/Native downloads its toolchain (LLVM, a GCC toolchain with a
  glibc sysroot, libffi) from download.jetbrains.com with its own
  downloader. The recorder gives that downloader an HTTPS tunnel to the
  download hosts of JetBrains only, and the command records each archive
  the downloader names. In the sandbox, the builder unpacks the archives,
  patches their programs with `autoPatchelf`, and links programs with the
  dynamic linker of nixpkgs. A toolchain runs on the machine it was made
  for, so the lockfile records the host, and a build with Kotlin/Native
  targets builds on the system its lock was made on, x86_64-linux or
  aarch64-linux. Lock it on each system that builds it, with a lockfile
  per system kept outside the tree. Kotlin/Native 2.2 crashes the JVM on JDK 21, so lock
  such a build with `--java 17`. On NixOS, the lock run needs a
  conventional dynamic loader, such as
  [nix-ld](https://github.com/nix-community/nix-ld) provides, because it
  runs the toolchain before anything can patch it.

External links on standard Javadoc tasks are captured as pinned `element-list`
or `package-list` inputs. Replay retains the original public documentation URL,
including when the metadata server is unavailable. `lock --relock` preserves the
pinned metadata; `update` refreshes it. These links currently require
credential-free HTTP(S) directory URLs with DNS names or IPv4 hosts, without
queries, fragments or traversal segments. IPv6 hosts are rejected before capture
or replay because the repository mapper does not support their authority syntax.
Custom doclets and other remote Javadoc inputs are outside this capture path.

For capture, sandboxed builds and publication, Gradle controls come from the
selected distribution's native `--help` under its selected JDK. The selector recognizes the observed flat and categorized
help layouts. Failed, unknown, ambiguous or incomplete help probes stop explicitly
before the build. Explicit daemon/cache enable arguments are rejected, and required
disable flags follow user arguments. Capture and sandboxed builds disable supported
daemon/cache features. Publication preserves inherited daemon and build-cache
settings while disabling the configuration cache when supported. Prefetch materializes the locked toolchain and
installs its init script; it does not run this probe or replace the native launcher.

The interactive missing-download hint uses the native Flow APIs when all required
classes are available. Older runtimes use a separate completion listener. Both
adapters preserve the native failure and print the same hint once; errors applying
a present Flow adapter remain failures. Companions are loaded beside the immutable
common init script only for the root interactive build.

Native help selection adapts CLI controls only. The measured Gradle 2.14.1 control still fails on
unavailable init-script APIs; older publication APIs remain separate compatibility
work. Successful help inspection does not establish a historical support range.

The lock refuses these:

- **Other downloads outside the repositories.** Gradle's own HTTP client gets a
  proxy that refuses every connection, so a plugin that downloads with
  its own client fails in the lock run rather than later in the sandbox.
  The file needs a repository that the build declares.
- **Kotlin/JS targets.** Yarn installs npm packages from the npm registry
  outside Gradle, so a Kotlin/JS target fails at its first download.
- **The configuration cache** is off in the lock run and in the sandbox,
  because the tasks of the init script read the project model.

A lock run sends the `Authorization` header of a repository on to it, so
a lock can be made from a repository that needs credentials. The Nix
build then needs a `fetchers.fetchurl` that supplies them.

## Examples

For day-to-day work on these projects, use the native-tool setup and
commands in [Quickstart](#quickstart) and [Development loop](#development-loop).
The Nix expressions below show optional sandboxed packaging of those
projects.

[Spring PetClinic](https://github.com/spring-projects/spring-petclinic)
is a Spring Boot application with a Gradle wrapper and a Java 17
toolchain. In a clone of it, lock it on Java 17:

```zsh
nix shell 'github:nix-purifiers/gradle-purifier'
gradle-purifier lock --java 17
git add gradle-purifier.lock.json
```

Build the Spring Boot jar from the clone's directory, with no Nix files
added to it, then run it on Java 17:

```zsh
nix build --impure --expr 'let
  purifier = builtins.getFlake "github:nix-purifiers/gradle-purifier";
  pkgs = import purifier.inputs.nixpkgs {
    system = builtins.currentSystem;
    overlays = [ purifier.overlays.default ];
  };
in
(pkgs.gradle-purifier.buildGradleProject { src = ./.; })
.packages.spring-petclinic'
java -jar result/share/archives/spring-petclinic.jar
```

For the development loop, `prefetch` sets up the wrapper, and the usual
commands work offline:

```zsh
nix shell 'github:nix-purifiers/gradle-purifier'
gradle-purifier prefetch
./gradlew bootRun
```

## Credits

The purifier idea was inspired by nixcloud's
[cargo (libnix)](https://github.com/nixcloud/cargo).
