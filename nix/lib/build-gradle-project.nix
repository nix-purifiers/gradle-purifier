# buildGradleProject: the sandboxed builds of a Gradle build, from the
# lockfile `gradle-purifier lock` writes.
#
# The file is a package for callPackage: the first argument set is what it
# takes from the package set, and the result is the builder. The flake's
# default package overlay (`overlays.default`) adds the scope
# `gradle-purifier` to the package set, and the scope holds it as
# `buildGradleProject`, built with the nix-purifiers library that the
# flake pins. A package written for callPackage takes the scope by name:
#
#   { gradle-purifier }:
#   (gradle-purifier.buildGradleProject { src = ./.; }).packages.app
#
# `gradle-purifier.overrideScope` replaces what the scope shares, and
# `gradle-purifier.buildGradleProject.override` replaces what the builder
# takes from the set.
# The builder returns
#
#   packages.<id>  a package per project the lock names. Its `out` holds
#                  the programs (an installed application, with bin/ and
#                  lib/, and Kotlin/Native executables in bin/) and the
#                  archives `assemble` makes, in share/archives. Its
#                  `maven` output, when the project has Maven publications,
#                  holds them in Maven layout.
#   tests          the `check` task of every project, in the sandbox
#   release        the Maven publications of every project, which
#                  `gradle-purifier publish` uploads
#   fetches        the entries of the nix-purifiers library that the build
#                  fetches
#
# and, for other uses, `lock` (the lock as read-lock.nix reads it),
# `repository` (the tree of every locked download), `distribution` (the
# Gradle distribution of the lock, unpacked), `konan` (the Kotlin/Native
# toolchain) and `jdk`.
{
  lib,
  stdenv,
  stdenvNoCC,
  makeWrapper,
  unzip,
  zlib,
  linkFarm,
  callPackage,
  # What the library's fetchEntry takes by name.
  fetchurl,
  runCommand,
  zstd,
  python3,
  writeText,
  # The JDKs a build can run on; the default JDK of a build is the headless
  # one of the Java major version its lock was made with.
  javaPackages,
  # The nix-purifiers library, which fetches the locked downloads and lays
  # them out; the overlay passes the one that the flake pins.
  purifier,
}:
{
  # The root of the Gradle build.
  src,
  # The lockfile. The default is the one `gradle-purifier lock` writes at
  # the build root. A build kept external passes one from outside `src`.
  lockFile ? src + "/gradle-purifier.lock.json",
  # The fetchers that replace nixpkgs' own, such as a fetchurl that sends
  # credentials. They go to the library's fetchEntry and tree.
  fetchers ? { },
  # Nix-built JVM dependencies, projected into a native repository.
  projectDependencies ? purifier.lockedProjectInputs {
    inputs = (builtins.fromJSON (builtins.readFile lockFile)).projectInputs or [ ];
    system = stdenvNoCC.hostPlatform.system;
  },
  # The JDK that runs Gradle and the applications. The default is nixpkgs'
  # headless JDK of the Java major version the lock was made with.
  jdk ? null,
  # Further JDKs for the build's Java toolchains, when a toolchain asks for
  # another Java version than `jdk`. Gradle downloads no toolchain in the
  # sandbox.
  toolchainJdks ? [ ],
  # Runs the checks of each project (`check`) in its package build.
  doCheck ? false,
  # Filters the build root before it is copied into the store, as the
  # `filter` of builtins.path. The default leaves out Gradle's state,
  # build outputs and editor state.
  srcFilter ? null,
}:
let
  # The packages that the fetchEntry and tree of the library take by name.
  fetcherArgs = {
    inherit
      fetchurl
      runCommand
      unzip
      zstd
      python3
      writeText
      ;
  };

  # The lock in memory (read-lock.nix). checkLock fails evaluation with
  # its problems: a stale, tampered, malformed or incomplete lockfile.
  lock = purifier.checkLock (import ./read-lock.nix { inherit lib; } { inherit src lockFile; });
  inherit (lock) gradle;
  # The downloads this system needs.
  entries = purifier.entriesFor stdenv.hostPlatform.system lock;
  konanEntries = lib.filter (e: lib.elem e gradle.konanEntries) entries;
  hasKonan = gradle.konanEntries != [ ];

  # A directory named build is a build output when a build script or a
  # settings script sits beside it.
  isBuildOutput =
    path:
    let
      parent = dirOf path;
    in
    lib.any (script: builtins.pathExists (parent + "/${script}")) [
      "build.gradle"
      "build.gradle.kts"
      "settings.gradle"
      "settings.gradle.kts"
    ];

  defaultSrcFilter =
    path: type:
    let
      name = baseNameOf path;
    in
    !(
      (
        type == "directory"
        && (lib.elem name [
          ".gradle"
          ".gradle-purifier"
          ".kotlin"
          ".idea"
        ])
      )
      || (type == "directory" && name == "build" && isBuildOutput path)
      || name == ".git"
      || lib.hasPrefix "result" name
    );

  filteredSrc =
    if builtins.isPath src then
      builtins.path {
        path = src;
        name = "gradle-source";
        filter = if srcFilter != null then srcFilter else defaultSrcFilter;
      }
    else
      src;

  defaultJdk =
    javaPackages.compiler."openjdk${toString gradle.javaMajor}".headless
      or (throw "gradle-purifier: javaPackages has no openjdk${toString gradle.javaMajor} for the Java the lock was made with; pass `jdk`.");
  jdk' = if jdk != null then jdk else defaultJdk;
  javaHome = j: j.home or "${j}/lib/openjdk";
  hasProjectJdks = gradle.projectJdks != null;
  selectedProjectJdks =
    if hasProjectJdks then
      (import ./project-jdks.nix { inherit lib purifier; }) {
        contexts = gradle.projectJdks;
        system = stdenv.hostPlatform.system;
      }
    else
      {
        jdks = [ ];
        roles = [ ];
      };
  explicitToolchainJdks = map (
    sdk:
    assert
      (
        builtins.getContext (toString sdk) != { }
        && builtins.getContext (javaHome sdk) != { }
        && lib.hasPrefix "${builtins.storeDir}/" (javaHome sdk)
      )
      || throw "gradle-purifier: toolchainJdks must carry explicit Nix package context, not an ambient host path";
    sdk
  ) toolchainJdks;
  allToolchainJdks = explicitToolchainJdks ++ selectedProjectJdks.jdks;
  hasSdkContext = hasProjectJdks || explicitToolchainJdks != [ ];
  sdkContext = writeText "gradle-project-sdk-context.json" (
    builtins.toJSON (
      if hasSdkContext then
        {
          legacy = !hasProjectJdks;
          explicitHomes = map javaHome explicitToolchainJdks;
          inherit (selectedProjectJdks) roles;
          managerHome = javaHome jdk';
          paths = map javaHome ([ jdk' ] ++ allToolchainJdks);
        }
      else
        null
    )
  );

  fetchEntry = purifier.fetchEntry (fetcherArgs // fetchers);

  repository = purifier.tree (
    fetcherArgs
    // {
      name = "gradle-purifier-repository";
      # Gradle's Groovy runtime discovery canonicalizes jars before parsing
      # their filenames. Store-object symlinks add a hash to that basename.
      link = false;
      inherit entries;
    }
    // fetchers
  );

  provided = purifier.jvmProjections {
    repository = null;
    dependencies = projectDependencies;
    inherit runCommand python3;
    name = "gradle-provided-dependencies";
  };

  # Kotlin/Native's toolchain, when the lock holds one (see konan.nix).
  konan = callPackage ./konan.nix {
    archives = map (entry: {
      name = lib.removeSuffix ".tar.gz" (baseNameOf entry.path);
      src = fetchEntry entry;
    }) konanEntries;
  };
  # The Kotlin/Native name of the host, whose dynamic linker the programs
  # Kotlin/Native links name.
  konanHost = if stdenv.hostPlatform.isAarch64 then "linux_arm64" else "linux_x64";
  konanSetup =
    assert
      gradle.konanHost == konanHost
      || throw "gradle-purifier: the lock holds the Kotlin/Native toolchain of ${gradle.konanHost}, which does not run on ${konanHost}; make the lock on ${konanHost}.";
    ''
      # Kotlin/Native finds its toolchain already extracted, and downloads
      # nothing. It writes its distribution and its caches beside it.
      export KONAN_DATA_DIR="$TMPDIR/konan"
      mkdir -p "$KONAN_DATA_DIR/dependencies"
      for dependency in ${konan}/*; do
        ln -s "$dependency" "$KONAN_DATA_DIR/dependencies/"
      done
      cp ${konan}/.extracted "$KONAN_DATA_DIR/dependencies/.extracted"
      # The compiler loads JNI libraries of its distribution, which need
      # these.
      export LD_LIBRARY_PATH=${
        lib.makeLibraryPath [
          stdenv.cc.cc.lib
          zlib
        ]
      }
      # The programs Kotlin/Native links name nixpkgs' dynamic linker, so
      # the tests run in the sandbox.
      export GRADLE_PURIFIER_KONAN_PROPERTIES="dynamicLinker.${konanHost}=${stdenv.cc.bintools.dynamicLinker}"
    '';

  distribution = stdenvNoCC.mkDerivation {
    name = "gradle-${gradle.gradleVersion}";
    src = fetchEntry (
      lib.findFirst (e: e.path == gradle.distribution)
        (throw "gradle-purifier: ${toString lockFile} names the Gradle distribution ${gradle.distribution}, which it does not hold; run 'gradle-purifier lock --relock'.")
        entries
    );
    nativeBuildInputs = [ unzip ];
    dontConfigure = true;
    dontBuild = true;
    unpackPhase = ''
      runHook preUnpack
      unzip -q "$src"
      runHook postUnpack
    '';
    installPhase = ''
      runHook preInstall
      mv gradle-* "$out"
      runHook postInstall
    '';
    # The distribution is jars and two start scripts; fixup only rewrites
    # the start scripts' shebangs.
    dontStrip = true;
  };

  initScript = ./purifier.init.gradle;
  portal = "https/plugins.gradle.org/m2";

  # The Gradle invocation shared by every build; the lock run (`gradle-purifier
  # lock`) runs Gradle the same way, so a lock made with it holds what this
  # needs. A build that needs a file the lock does not hold fails with what
  # to run.
  runGradle = tasks: ''
    export HOME="$TMPDIR/home"
    export GRADLE_USER_HOME="$TMPDIR/gradle-home"
    export GRADLE_PURIFIER_MODE=sandbox
    export GRADLE_PURIFIER_BUILD_ROOT="$PWD"
    export GRADLE_PURIFIER_REPOSITORY=file://${repository}
    export GRADLE_PURIFIER_RESOURCES=${
      writeText "gradle-external-resources.json" (builtins.toJSON (gradle.resources or [ ]))
    }
    ${lib.optionalString (projectDependencies != [ ]) ''
      export GRADLE_PURIFIER_PROVIDED_REPOSITORY=${provided.maven}
      export GRADLE_PURIFIER_PROVIDED_CATALOG=${provided.maven.catalog}
    ''}
    export GRADLE_PURIFIER_OUTPUT="$TMPDIR/purifier-output"
    export GRADLE_PURIFIER_CHECK=${if doCheck then "1" else "0"}
    ${lib.optionalString hasSdkContext ''
      export GRADLE_PURIFIER_JDK_CONTEXT=${sdkContext}
    ''}
    export JAVA_HOME=${javaHome jdk'}
    mkdir -p "$HOME" "$GRADLE_USER_HOME"
    ${lib.optionalString hasKonan konanSetup}
    GRADLE_PURIFIER_MODE="" ${python3}/bin/python ${./gradle-flags.py} sandbox "$TMPDIR/gradle-help.txt" \
      --gradle ${distribution}/bin/gradle \
      --arguments-json ${lib.escapeShellArg (builtins.toJSON gradle.gradleArguments)} > "$TMPDIR/gradle-flags.txt"
    mapfile -t gradleFlags < "$TMPDIR/gradle-flags.txt"
    status=0
    ${distribution}/bin/gradle \
      --init-script ${initScript} \
      --init-script ${./task-jdks.init.gradle} \
      -Dorg.gradle.internal.plugins.portal.url.override=file://${repository}/${portal} \
      -Dorg.gradle.native=false \
      -Djava.io.tmpdir="$TMPDIR" \
      -Porg.gradle.java.installations.auto-download=false \
      -Porg.gradle.java.installations.auto-detect=false \
      -Porg.gradle.java.installations.paths=${
        lib.concatMapStringsSep "," javaHome ([ jdk' ] ++ allToolchainJdks)
      } \
      -Pkotlin.compiler.execution.strategy=in-process \
      ${lib.escapeShellArgs gradle.gradleArguments} \
      ${lib.escapeShellArgs tasks} --offline "''${gradleFlags[@]}" --stacktrace </dev/null 2> >(tee "$TMPDIR/gradle-errors" >&2) || status=$?
    wait
    if [ "$status" -ne 0 ]; then
      if grep -q -e 'Could not find' -e 'Could not resolve' "$TMPDIR/gradle-errors" \
        && grep -q -F ${repository} "$TMPDIR/gradle-errors"; then
        echo "gradle-purifier: the build needs a file that ${baseNameOf (toString lockFile)} does not hold (Gradle names it above); run 'gradle-purifier lock --relock'." >&2
      fi
      exit "$status"
    fi
  '';

  # The build links each artifact of a publication, its POM and its Gradle
  # module metadata at names without the version of the project:
  # <linkDirectory>/<publication>/<key>, …/descriptor and …/module.
  linkDirectory = "nix-support/artifacts";

  mkPackage =
    project:
    let
      hasApplication = project.application != null;
      executables = project.executables or [ ];
      hasExecutables = executables != [ ];
      hasPublications = project.publications != [ ];
      archives = project.archives or [ ];
      archiveDirectory = "share/archives";
      mainProgram =
        if hasApplication then
          project.application.name
        else if builtins.length executables == 1 then
          builtins.head executables
        else
          null;
      artifactFile =
        package: publication: key:
        "${package.maven}/${linkDirectory}/${publication}/${key}";
      taskPath = if project.path == ":" then ":purifierBuild" else "${project.path}:purifierBuild";
    in
    stdenvNoCC.mkDerivation (finalAttrs: {
      name = project.id;
      src = filteredSrc;
      outputs = [
        "out"
      ]
      ++ lib.optional hasPublications "maven"
      ++ [
        "runtime"
        "localPublications"
      ];
      nativeBuildInputs = [ makeWrapper ];
      dontConfigure = true;
      # The outputs are jars, POMs and start scripts; fixup has nothing to
      # patch, and it must not rewrite the jars.
      dontFixup = true;
      buildPhase = ''
        runHook preBuild
        ${runGradle [ taskPath ]}
                  ${python3}/bin/python ${./export-runtime.py} ${purifier.jvmRepositoryTool} \
            "$GRADLE_PURIFIER_OUTPUT/${project.id}/runtime-data.json" \
            ${repository} "$runtime" "$localPublications" ${provided.maven.catalog}
        runHook postBuild
      '';
      installPhase = ''
        runHook preInstall
        built="$GRADLE_PURIFIER_OUTPUT/${project.id}"
        mkdir -p "$out"
        ${lib.optionalString hasApplication ''
          cp -r "$built/application/." "$out/"
          rm -f "$out"/bin/*.bat
          wrapProgram "$out/bin/${project.application.name}" --set-default JAVA_HOME ${javaHome jdk'}
        ''}
        ${lib.optionalString hasExecutables ''
          mkdir -p "$out/bin"
          for executable in ${lib.escapeShellArgs executables}; do
            if [ ! -f "$built/bin/$executable" ]; then
              echo "gradle-purifier: the lock names the executable $executable, which this build of ${project.id} did not link; run 'gradle-purifier lock'." >&2
              exit 1
            fi
            cp "$built/bin/$executable" "$out/bin/$executable"
          done
        ''}
        ${lib.optionalString (archives != [ ]) ''
          mkdir -p "$out/${archiveDirectory}"
          for archive in ${lib.escapeShellArgs archives}; do
            if [ ! -f "$built/archives/$archive" ]; then
              echo "gradle-purifier: the lock names the archive $archive, which this build of ${project.id} did not make; run 'gradle-purifier lock'." >&2
              exit 1
            fi
            cp "$built/archives/$archive" "$out/${archiveDirectory}/$archive"
          done
        ''}
        ${lib.optionalString hasPublications ''
          cp -r "$built/maven" "$maven"
          # A SNAPSHOT publish uses timestamped filenames. Preserve every
          # artifact under its logical version before removing the clock data.
          ${python3}/bin/python ${./normalize-publications.py} "$maven"
          # The version listings of a Maven repository hold the time of the
          # publication, which differs from one build to the next.
          find "$maven" -name 'maven-metadata*' -delete
          link() {
            # $1 publication, $2 key, $3 the artifact's directory, $4 its
            # file name after "<artifactId>-<version>", and $5 "optional"
            # for a file the build may leave out.
            local dir=$3 version
            version=$(find "$dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | head -n 1)
            local file="$dir/$version/$(basename "$dir")-$version$4"
            if [ ! -f "$file" ] && [ "''${5:-}" = optional ]; then
              return 0
            fi
            if [ ! -f "$file" ]; then
              echo "gradle-purifier: the lock names $1/$2, which this build of ${project.id} did not publish; run 'gradle-purifier lock'." >&2
              exit 1
            fi
            mkdir -p "$maven/${linkDirectory}/$1"
            ln -s "$file" "$maven/${linkDirectory}/$1/$2"
          }
          ${lib.concatMapStrings (
            publication:
            let
              dir = "$maven/${lib.replaceStrings [ "." ] [ "/" ] publication.groupId}/${publication.artifactId}";
              suffix = a: (if a.classifier != null then "-${a.classifier}" else "") + ".${a.extension}";
            in
            ''
              link ${publication.name} descriptor "${dir}" .pom
              link ${publication.name} module "${dir}" .module optional
            ''
            + lib.concatMapStrings (a: ''
              link ${publication.name} ${lib.escapeShellArg a.key} "${dir}" ${lib.escapeShellArg (suffix a)}
            '') publication.artifacts
          ) project.publications}
        ''}
        runHook postInstall
      '';
      passthru = {
        inherit repository distribution lock;
        gradleProject = project;
        projections = purifier.jvmProjections {
          repository = publicationRepository;
          producer = publicationRepository;
          inherit runtimeRepository;
          dependencies = projectDependencies;
          inherit runCommand python3;
          name = "gradle-jvm-publications";
        };
        # The artifact files of the package by publication and key, such
        # as artifacts.maven."core-sources.jar". They are known from the
        # lock alone, before the package is built.
        artifacts = builtins.listToAttrs (
          map (publication: {
            inherit (publication) name;
            value = builtins.listToAttrs (
              map (a: {
                name = a.key;
                value = artifactFile finalAttrs.finalPackage publication.name a.key;
              }) publication.artifacts
            );
          }) project.publications
        );
        # The archives of `assemble` by key, such as archives."app.jar":
        # the file name of the archive without the project's version.
        archives = lib.genAttrs archives (key: "${finalAttrs.finalPackage}/${archiveDirectory}/${key}");
      };
      meta = {
        description = "${project.path} of a Gradle build, built by gradle-purifier";
      }
      // lib.optionalAttrs (mainProgram != null) { inherit mainProgram; };
    });

  packages = builtins.mapAttrs (_: mkPackage) lock.outputs;

  # The `check` task of every project of the build, in the sandbox.
  tests = stdenvNoCC.mkDerivation {
    name = "${gradle.rootProjectName}-tests";
    src = filteredSrc;
    dontConfigure = true;
    buildPhase = ''
      runHook preBuild
      ${runGradle [ "check" ]}
      runHook postBuild
    '';
    installPhase = ''
      runHook preInstall
      mkdir -p "$out"
      runHook postInstall
    '';
  };

  # Native Maven repository, without the per-project release directory layer.
  publicationRepository = runCommand "${gradle.rootProjectName}-jvm-publications" { } ''
    mkdir $out
    ${lib.concatMapStrings
      (package: ''
        cp -r --no-preserve=mode ${package.maven}/. $out/
        cp -r --no-preserve=mode ${package.localPublications}/. $out/
      '')
      (
        builtins.filter (package: package.gradleProject.publications != [ ]) (builtins.attrValues packages)
      )
    }
    rm -rf $out/nix-support
  '';

  # What `gradle-purifier publish` uploads: the Maven output of every
  # project with publications, by project id.
  release = linkFarm "${gradle.rootProjectName}-release" (
    lib.mapAttrsToList (id: package: {
      name = id;
      path = package.maven;
    }) (lib.filterAttrs (_: package: package.gradleProject.publications != [ ]) packages)
  );
  runtimeRepository =
    runCommand "${gradle.rootProjectName}-runtime-repository" { nativeBuildInputs = [ python3 ]; }
      ''
        mkdir -p "$out"
        ${python3}/bin/python - "$out" ${
          lib.escapeShellArgs (map (package: toString package.runtime) (builtins.attrValues packages))
        } <<'PYTHON'
        import json, shutil, sys
        from pathlib import Path
        destination = Path(sys.argv[1])
        modules = {}
        for source in map(Path, sys.argv[2:]):
            graph = json.loads((source / 'purifier-runtime-modules.json').read_text())
            for module in graph['modules']:
                key = (module['group'], module['name'])
                if key in modules and modules[key] != module:
                    raise ValueError('conflicting selected Gradle runtime component: ' + str(key))
                modules[key] = module
            for path in source.rglob('*'):
                if path.is_file() and path.name != 'purifier-runtime-modules.json':
                    target = destination / path.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists() and target.read_bytes() != path.read_bytes():
                        raise ValueError('conflicting Gradle runtime metadata: ' + str(target))
                    shutil.copyfile(path, target)
        (destination / 'purifier-runtime-modules.json').write_text(json.dumps({'modules': list(modules.values())}, sort_keys=True))
        PYTHON
      '';
  rootProjections = purifier.jvmProjections {
    repository = publicationRepository;
    inherit runtimeRepository;
    dependencies = projectDependencies;
    inherit runCommand python3;
    name = "gradle-jvm-publications";
  };
  publication = runCommand "gradle-publication-repository" { } ''
    mkdir -p $out
    ln -s ${rootProjections.maven} $out/repository
    ln -s ${rootProjections.maven.catalog} $out/catalog.json
  '';
in
{
  projections = rootProjections;
  inherit publication;
  inherit
    packages
    tests
    release
    lock
    repository
    distribution
    konan
    ;
  fetches = lock.entries;
  jdk = jdk';
  inherit sdkContext;
}
