# What the gradle-purifier command builds: buildGradleProject of the build
# at `src`, with the nixpkgs and the nix-purifiers library that the command
# was built with, from arguments the command passes as strings. The
# command builds its `repository`, `konan`, `distribution`, `jdk` and
# `release`, so it runs the same code as the flake's builder, checks of
# the lockfile included.
{
  # A nixpkgs source directory.
  nixpkgs,
  # The source of the nix-purifiers library.
  purifierSource,
  # The build root, and the lockfile, as absolute paths.
  src,
  lockFile,
}:
let
  pkgs = import nixpkgs { };
  inherit (pkgs) lib;
  purifier = import purifierSource { inherit lib; };
in
pkgs.callPackage ./default.nix { inherit purifier; } {
  src = /. + src;
  lockFile = /. + lockFile;
}
