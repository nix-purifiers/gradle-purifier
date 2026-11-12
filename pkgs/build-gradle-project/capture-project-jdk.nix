# Match an observed native SDK to the command's immutable nixpkgs source.
{
  nixpkgs,
  major,
  system,
  sourceJson,
}:
let
  source = builtins.fromJSON sourceJson;
  sourceTree = builtins.fetchTree source;
  pkgs = import sourceTree.outPath {
    inherit system;
    config = { };
    overlays = [ ];
  };
  sdk = pkgs.javaPackages.compiler.${"openjdk" + toString major}.headless;
  context = builtins.getContext (toString sdk);
in
assert
  toString sourceTree.outPath == nixpkgs
  || throw "gradle-purifier: SDK source does not reproduce the command nixpkgs source";
{
  attribute = [
    "javaPackages"
    "compiler"
    ("openjdk" + toString major)
    "headless"
  ];
  output = toString sdk;
  home = sdk.home or "${sdk}/lib/openjdk";
  identityHash = builtins.hashString "sha256" (builtins.toJSON context);
}
