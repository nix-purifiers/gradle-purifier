# Probe-only Java runtimes. Never selected by the production CLI/builder.
{ pkgs }:
let
  legacy =
    {
      version,
      url,
      sha256,
    }:
    assert pkgs.stdenv.hostPlatform.system == "x86_64-linux";
    pkgs.stdenv.mkDerivation {
      pname = "gradle-epoch-zulu";
      inherit version;
      src = pkgs.fetchurl { inherit url sha256; };
      nativeBuildInputs = [ pkgs.autoPatchelfHook ];
      buildInputs = [
        pkgs.stdenv.cc.cc.lib
        pkgs.zlib
      ];
      # These probes exercise headless Java/javac/jar, not AWT, audio or GUI.
      autoPatchelfIgnoreMissingDeps = true;
      installPhase = ''mkdir -p "$out"; cp -a . "$out/"'';
      dontStrip = true;
    };
in
{
  java6 = legacy {
    version = "6.0.119";
    url = "https://cdn.azul.com/zulu/bin/zulu6.22.0.3-ca-jdk6.0.119-linux_x64.tar.gz";
    sha256 = "dd4765b45d9f1fc319a6e5be0bbc79371582daa8fd77872d51072f16deca2625";
  };
  java7 = legacy {
    version = "7.0.352";
    url = "https://cdn.azul.com/zulu/bin/zulu7.56.0.11-ca-jdk7.0.352-linux_x64.tar.gz";
    sha256 = "8a7387c1ed151474301b6553c6046f865dc6c1e1890bcf106acc2780c55727c8";
  };
  java8 = pkgs.jdk8_headless;
  java11 = pkgs.jdk11_headless;
  java17 = pkgs.jdk17_headless;
  java21 = pkgs.jdk21_headless;
}
