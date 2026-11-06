# All arguments arrive as NAR-bound JSON metadata. Reconstructed SDK values
# keep their original Nix string contexts throughout resolution and building.
args:
let
  helper = builtins.fetchTree args.helper;
  library = builtins.fetchTree args.library;
  nixpkgs = builtins.fetchTree args.nixpkgs;
  pkgs = import nixpkgs.outPath {
    system = args.system;
    config = { };
    overlays = [ ];
  };
  inherit (pkgs) lib;
  purifier = import helper.outPath { inherit lib; };
  project = if args ? project then builtins.fetchTree args.project else null;
  resolved = import (library.outPath + "/project-sdk-sources.nix") { inherit lib purifier; } {
    inherit (args) declaration declarationHash system;
  };
  build = pkgs.callPackage (library.outPath + "/build-gradle-project.nix") { inherit purifier; } {
    src = project.outPath;
    lockFile = (builtins.fetchTree args.lock).outPath + "/lock.json";
  };
in
if args.mode == "preflight" then
  pkgs.writeText "gradle-project-sdk-source-context.json" (builtins.toJSON resolved.runtimeContext)
else if args.mode == "hash" then
  pkgs.writeText "gradle-project-inputs-hash" (
    import (library.outPath + "/inputs.nix") {
      inherit lib;
      src = project.outPath;
      gradleArguments = args.gradleArguments;
    }
  )
else if args.mode == "build" then
  lib.getAttrFromPath [ args.attribute ] build
else
  throw "gradle-purifier: unsupported pure SDK command"
