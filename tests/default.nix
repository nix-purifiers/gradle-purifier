# SPDX-License-Identifier: MIT
#
# The flake's checks. Every one builds in the sandbox; the only network
# access is the fixed-output fetches of locked artifacts. The conformance
# checks that need the network (relock, rebuild, offline and update) are
# CI jobs of the same name (.github/workflows/check.yml).
{
  pkgs,
  # The default package overlay of the flake, which adds the scope
  # `gradle-purifier`, with buildGradleProject, to the package set.
  overlay,
  # The library namespace of the flake: lib.gradle-purifier, with readLock.
  gradlePurifier,
  gradle-purifier,
  purifier,
}:
let
  inherit (pkgs) lib runCommand;
  inherit (gradlePurifier) readLock;

  # Every fixture builds the way a package of a consumer does: it takes the
  # scope `gradle-purifier` that the overlay adds to the package set by
  # name through callPackage, and calls its buildGradleProject.
  withOverlay = pkgs.extend overlay;
  build =
    args: withOverlay.callPackage ({ gradle-purifier }: gradle-purifier.buildGradleProject args) { };

  # A Java library in the Groovy DSL on Gradle 8, with a Maven publication
  # that carries a sources jar, and JUnit tests that run in the build.
  javaLibrary = build {
    src = ./fixtures/java-library;
    doCheck = true;
  };

  snapshotGroovy = build { src = ./fixtures/snapshot-groovy; };

  # A Kotlin build in the Kotlin DSL on Gradle 9: a version catalog, a
  # convention plugin in buildSrc that applies the Kotlin JVM plugin with a
  # Java 21 toolchain, a published library with kotlinx-coroutines and
  # tests, and an application that uses it.
  kotlinApp = build {
    src = ./fixtures/kotlin-app;
    doCheck = true;
  };

  # A Kotlin/Native build with a linuxX64 library, its tests, and an
  # executable. Its lock holds the Kotlin/Native toolchain of x86_64-linux.
  kotlinNative = build {
    src = ./fixtures/kotlin-native;
    doCheck = true;
  };

  # Fails unless every file of `files` (paths relative to `root`) exists.
  expectFiles =
    name: root: files:
    runCommand name { } ''
      ${lib.concatMapStrings (f: ''
        if [ ! -e ${root}/${f} ]; then
          echo "missing: ${f}" >&2
          exit 1
        fi
      '') files}
      touch $out
    '';

  # Fails unless the jar `jar` holds the class file `class`.
  expectClass =
    name: jar: class:
    runCommand name { nativeBuildInputs = [ pkgs.unzip ]; } ''
      unzip -l ${jar} | grep -q ' ${class}$' || { echo "${jar} holds no ${class}" >&2; exit 1; }
      touch $out
    '';

  # The lockfiles of the conformance checks, each the lockfile of the Java
  # library fixture with one change (tests/conformance/lockfiles.nix). The
  # checks read the committed copies, since `nix flake check --no-build`
  # cannot read a file that evaluation writes to the store.
  conformanceLock =
    name:
    let
      file = ./conformance + "/${name}.lock.json";
      expected = (import ./conformance/lockfiles.nix { inherit lib; }).${name};
    in
    assert lib.assertMsg (lib.importJSON file == expected)
      "tests/conformance/${name}.lock.json differs from tests/conformance/lockfiles.nix; run tests/conformance/generate.sh";
    file;

  # A check that `lockFile` fails at evaluation, in readLock and in the
  # builder, with a problem that matches the regular expression `expected`.
  expectProblem =
    name: lockFile: expected:
    let
      problems =
        (readLock {
          src = ./fixtures/java-library;
          inherit lockFile;
        }).problems;
      built =
        builtins.tryEval
          (build {
            src = ./fixtures/java-library;
            inherit lockFile;
          }).packages.greeting.drvPath;
    in
    assert lib.assertMsg (lib.any (p: builtins.match expected p != null) problems)
      "${name}: readLock gave the problems ${builtins.toJSON problems}, none of which matches ${expected}";
    assert lib.assertMsg (!built.success) "${name}: the builder accepted the lockfile";
    runCommand name { } ''
      echo ${lib.escapeShellArg (lib.concatStringsSep "\n" problems)} >$out
    '';
in
{
  runtime-metadata-unit =
    runCommand "runtime-metadata-unit" { nativeBuildInputs = [ pkgs.python3 ]; }
      ''
        mkdir -p tests nix/lib
        cp ${./test_export_runtime.py} tests/test_export_runtime.py
        cp ${../nix/lib/export-runtime.py} nix/lib/export-runtime.py
        PYTHONDONTWRITEBYTECODE=1 python3 tests/test_export_runtime.py ${purifier.jvmRepositoryTool}
        touch $out
      '';
  snapshot-groovy-tests = snapshotGroovy.tests;
  snapshot-groovy-publication =
    expectFiles "snapshot-groovy-publication" snapshotGroovy.packages.snapshot-groovy.maven
      [
        "nix-support/artifacts/maven/descriptor"
        "nix-support/artifacts/maven/module"
        "nix-support/artifacts/maven/snapshot-groovy.jar"
        "nix-support/artifacts/maven/snapshot-groovy-sources.jar"
      ];
  publication-unit = runCommand "publication-unit" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    mkdir -p tests nix/lib
    cp ${./test_normalize_publications.py} tests/test_normalize_publications.py
    cp ${../nix/lib/normalize-publications.py} nix/lib/normalize-publications.py
    python3 tests/test_normalize_publications.py
    touch $out
  '';
  snapshot-groovy-rebuild =
    let
      first = snapshotGroovy.packages.snapshot-groovy;
      again = first.overrideAttrs { PURIFIER_SNAPSHOT_REBUILD = "1"; };
    in
    runCommand "snapshot-groovy-rebuild" { } ''
      diff -r ${first.maven} ${again.maven}
      touch $out
    '';
  # A producer needs a complete runtime view even with no Nix project inputs.
  standalone-projection = pkgs.runCommand "gradle-standalone-projection" { } ''
    test -n "$(find ${javaLibrary.projections.maven}/org/apache/commons/commons-lang3 -name '*.jar')"
    touch $out
  '';
  jvm-projection = javaLibrary.projections.maven;

  composition = import ./composition.nix {
    inherit pkgs build;
    purifier = withOverlay.lib.purifier;
  };
  inherit gradle-purifier;

  java-library = javaLibrary.packages.greeting;
  java-library-tests = javaLibrary.tests;
  # The archives of `assemble` are in `out`, under their version-free keys.
  java-library-archives = expectFiles "java-library-archives" javaLibrary.packages.greeting [
    "share/archives/greeting.jar"
    "share/archives/greeting-sources.jar"
  ];
  java-library-artifacts = expectFiles "java-library-artifacts" javaLibrary.packages.greeting.maven [
    "nix-support/artifacts/maven/greeting.jar"
    "nix-support/artifacts/maven/greeting-sources.jar"
    "nix-support/artifacts/maven/descriptor"
    "nix-support/artifacts/maven/module"
    "example/greeting/1.0.0/greeting-1.0.0.jar"
  ];
  # The release is the Maven output of each project with publications.
  java-library-release = expectFiles "java-library-release" javaLibrary.release [
    "greeting/example/greeting/1.0.0/greeting-1.0.0.pom"
  ];
  java-library-classes =
    expectClass "java-library-classes" javaLibrary.packages.greeting.artifacts.maven."greeting.jar"
      "example/Greeting.class";

  # The file the build fetched from an Ivy repository is in the jar.
  java-library-ivy-resource =
    expectClass "java-library-ivy-resource" javaLibrary.packages.greeting.artifacts.maven."greeting.jar"
      "node-checksums.txt";

  # The Multiplatform library publishes the JVM variant and the root
  # publication that points at it, each with its module metadata.
  kotlin-shared = kotlinApp.packages.shared;
  kotlin-shared-artifacts = expectFiles "kotlin-shared-artifacts" kotlinApp.packages.shared.maven [
    "nix-support/artifacts/jvm/shared-jvm.jar"
    "nix-support/artifacts/jvm/module"
    "nix-support/artifacts/kotlinMultiplatform/shared-kotlin-tooling-metadata.json"
    "nix-support/artifacts/kotlinMultiplatform/module"
  ];
  kotlin-shared-classes =
    expectClass "kotlin-shared-classes" kotlinApp.packages.shared.artifacts.jvm."shared-jvm.jar"
      "example/shared/PunctuationKt.class";

  kotlin-core = kotlinApp.packages.core;
  kotlin-core-classes =
    expectClass "kotlin-core-classes" kotlinApp.packages.core.artifacts.maven."core.jar"
      "example/core/GreeterKt.class";

  kotlin-app = kotlinApp.packages.app;
  # The application runs from its package alone, on the JDK its start
  # script was wrapped with.
  kotlin-app-runs = runCommand "kotlin-app-runs" { } ''
    output=$(${lib.getExe kotlinApp.packages.app})
    if [ "$output" != "Hello, Nix!" ]; then
      echo "the app printed: $output" >&2
      exit 1
    fi
    touch $out
  '';

  # A lockfile made for an older build definition fails, naming the lock.
  conformance-stale =
    expectProblem "conformance-stale" (conformanceLock "stale")
      ".*changed since.*run 'gradle-purifier lock'.*";

  # A lockfile whose download hash was changed by hand fails, because its
  # content no longer matches its contentHash.
  conformance-tampered =
    expectProblem "conformance-tampered" (conformanceLock "tampered")
      ".*does not match its contentHash.*gradle-purifier lock --relock.*";

  # A lockfile of another format version, or one with a field missing,
  # fails, naming the lock. tests/interactive.sh covers a lockfile that is
  # not JSON, which fails Nix evaluation before any check can catch it.
  conformance-malformed = runCommand "conformance-malformed" { } ''
    cat ${
      expectProblem "conformance-malformed-version" (conformanceLock "version")
        ".*lock format version 0.*gradle-purifier lock --relock.*"
    } ${
      expectProblem "conformance-malformed-field" (conformanceLock "field")
        ".*lacks a field.*gradle-purifier lock --relock.*"
    } >$out
  '';

  # A lockfile without a download the build needs passes evaluation and
  # fails the build, which names the lock.
  conformance-missing =
    let
      lockFile = conformanceLock "missing";
      failure =
        pkgs.testers.testBuildFailure
          (build {
            src = ./fixtures/java-library;
            inherit lockFile;
          }).packages.greeting;
    in
    runCommand "conformance-missing" { } ''
      grep -F "does not hold" ${failure}/testBuildFailure.log
      grep -F "run 'gradle-purifier lock --relock'" ${failure}/testBuildFailure.log
      touch $out
    '';

  # A build whose `src` holds only Gradle's own files builds from a
  # lockfile outside it.
  conformance-external =
    let
      external = build {
        src = builtins.path {
          name = "java-library";
          path = ./fixtures/java-library;
          filter = path: _: baseNameOf path != "gradle-purifier.lock.json";
        };
        lockFile = ./fixtures/java-library/gradle-purifier.lock.json;
      };
    in
    expectFiles "conformance-external" external.packages.greeting.maven [
      "example/greeting/1.0.0/greeting-1.0.0.jar"
    ];

  # Spring PetClinic, a Spring Boot build on Gradle 9, from its source on
  # GitHub and a lockfile kept outside it, which the lock-real-world
  # workflow (.github/workflows/lock-real-world.yml) makes.
  petclinic =
    let
      pinned = lib.importJSON ./real-world/petclinic.json;
      petclinic = build {
        src = pkgs.fetchFromGitHub {
          inherit (pinned)
            owner
            repo
            rev
            hash
            ;
        };
        lockFile = ./real-world/petclinic.lock.json;
      };
    in
    expectFiles "petclinic" petclinic.packages.spring-petclinic [
      "share/archives/spring-petclinic.jar"
    ];

  # The recorder's mapping between request paths, remote URLs and paths in
  # the tree of locked downloads (tests/RecorderTest.java).
  recorder-unit =
    runCommand "recorder-unit"
      {
        nativeBuildInputs = [ pkgs.jdk21_headless ];
      }
      ''
        cp ${../src/Recorder.java} Recorder.java
        cp ${./RecorderTest.java} RecorderTest.java
        javac -d classes Recorder.java RecorderTest.java
        java -cp classes RecorderTest
        touch $out
      '';

  gradle-flags-unit = runCommand "gradle-flags-unit" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    mkdir -p tests/gradle-flags-help nix/lib
    cp ${./test_gradle_flags.py} tests/test_gradle_flags.py
    cp -r ${./gradle-flags-help}/. tests/gradle-flags-help/
    cp ${../nix/lib/gradle-flags.py} nix/lib/gradle-flags.py
    PYTHONDONTWRITEBYTECODE=1 python3 tests/test_gradle_flags.py
    touch $out
  '';

  # The command's parsing (tests/cli-unit.sh).
  cli-unit = runCommand "cli-unit" { nativeBuildInputs = [ pkgs.jq ]; } ''
    bash ${./cli-unit.sh} ${lib.getExe gradle-purifier}
    touch $out
  '';
}
# The Kotlin/Native fixture's lock holds the toolchain of x86_64-linux,
# so its checks build there.
// lib.optionalAttrs pkgs.stdenv.hostPlatform.isx86_64 {
  kotlin-native = kotlinNative.packages.kotlin-native;
  # The executable runs from its package, and its tests ran in the build.
  kotlin-native-runs = runCommand "kotlin-native-runs" { } ''
    output=$(${lib.getExe kotlinNative.packages.kotlin-native})
    if [ "$output" != "Hello, Nix!" ]; then
      echo "the executable printed: $output" >&2
      exit 1
    fi
    touch $out
  '';
  kotlin-native-artifacts =
    expectFiles "kotlin-native-artifacts" kotlinNative.packages.kotlin-native.maven
      [
        "nix-support/artifacts/linuxX64/kotlin-native-linuxx64.klib"
        "nix-support/artifacts/linuxX64/module"
        "nix-support/artifacts/kotlinMultiplatform/module"
      ];
}
