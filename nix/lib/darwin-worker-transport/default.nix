# Rebuild one source component for explicitly matched Darwin build SDKs.
{
  lib,
  runCommand,
  fetchurl,
  python3,
  javaPackages,
}:
{ upstreamDistribution, distributionEntry }:
let
  points = builtins.fromJSON (builtins.readFile ./points.json);
  matched = lib.findFirst (
    point:
    distributionEntry.url == point.distributionUrl && distributionEntry.hash == point.distributionHash
  ) null (builtins.attrValues points);
  source = fetchurl {
    url = matched.immutableSourceUrl;
    hash = matched.sourceSRI;
  };
  compiler = javaPackages.compiler.openjdk17.headless;
  descriptor = builtins.toFile "gradle-worker-transport-point.json" (builtins.toJSON matched);
  effective =
    runCommand "gradle-${matched.version}-purifier-darwin-worker-ipv4"
      {
        nativeBuildInputs = [ python3 ];
        GRADLE_TRANSPORT_JAVAC = "${compiler.home}/bin/javac";
        GRADLE_TRANSPORT_JAVAP = "${compiler.home}/bin/javap";
        GRADLE_TRANSPORT_COMPILER_ROOT = toString compiler;
      }
      ''
        python ${./derive.py} ${upstreamDistribution} ${source} ${descriptor} "$out"
      '';
in
if matched == null then
  {
    distribution = upstreamDistribution;
    variant = null;
    provenance = null;
  }
else
  {
    distribution = effective;
    variant = "purifier-darwin-worker-ipv4-source-component-v1";
    provenance = "${effective}/nix-support/purifier-worker-transport/manifest.json";
  }
