# SPDX-License-Identifier: MIT
#
# The gradle-purifier command (src/gradle-purifier.sh), with the download
# recorder (src/Recorder.java) and the Nix library (nix/lib) it runs.
{
  lib,
  # The nixpkgs source of the package set, which `gradle-purifier publish`
  # builds the packages with.
  path,
  sdkSource ? null,
  sdkSourcePath ? null,
  writeText,
  # The source of the nix-purifiers library, at the revision this flake
  # pins; the default package overlay passes it.
  purifierSource ? throw "gradle-purifier: the command needs purifierSource, the source of the nix-purifiers library",
  stdenv,
  stdenvNoCC,
  writeShellApplication,
  jdk21_headless,
  coreutils,
  findutils,
  gnused,
  jq,
  python3,
  rsync,
  unzip,
}:
let
  # The JDK the command runs Gradle and the recorder on when JAVA_HOME is
  # unset.
  jdk = jdk21_headless;

  recorder = stdenvNoCC.mkDerivation {
    name = "gradle-purifier-recorder";
    src = ../../src/Recorder.java;
    dontUnpack = true;
    nativeBuildInputs = [ jdk ];
    buildPhase = ''
      runHook preBuild
      # javac wants a public class in a file of its name.
      cp "$src" Recorder.java
      # Gradle 9 runs on Java 17 and later, so the recorder does too.
      javac --release 17 -Xlint:all -Werror -d "$out" Recorder.java
      runHook postBuild
    '';
    dontInstall = true;
  };

  nixLib = ../../nix/lib;
  sdkDescriptor = writeText "gradle-sdk-source.json" (
    builtins.toJSON (
      if sdkSource != null && sdkSourcePath != null && toString path == toString sdkSourcePath then
        {
          source = sdkSource;
          nixpkgs = toString path;
          system = stdenv.hostPlatform.system;
        }
      else
        null
    )
  );
in
writeShellApplication {
  name = "gradle-purifier";
  runtimeInputs = [
    coreutils
    findutils
    gnused
    jq
    python3
    rsync
    unzip
  ];
  text =
    builtins.replaceStrings
      [
        "@lib@"
        "@recorder@"
        "@java@"
        "@lockVersion@"
        "@sdkDescriptor@"
        "@nixpkgs@"
        "@purifier@"
        "@dynamicLinker@"
      ]
      [
        "${nixLib}"
        "${recorder}"
        "${jdk.home or "${jdk}/lib/openjdk"}"
        (toString (import ../../nix/lib/lock-version.nix))
        "${sdkDescriptor}"
        "${path}"
        "${purifierSource}"
        stdenv.cc.bintools.dynamicLinker
      ]
      (builtins.readFile ../../src/gradle-purifier.sh);
  derivationArgs.passthru = {
    inherit recorder nixLib;
  };
  meta = {
    description = "Locks the downloads of a Gradle build and runs Gradle offline on them";
    mainProgram = "gradle-purifier";
    license = lib.licenses.mit;
  };
}
