# SPDX-License-Identifier: MIT
#
# The `gradle-purifier` package scope, which the default package overlay
# adds to the package set as `pkgs.gradle-purifier`. Its packages take the
# nix-purifiers library that this flake pins.
{
  callPackage,
  inputs,
  lib,
}:
{
  # The builder: a package whose value builds a Gradle build from its
  # lockfile (pkgs/build-gradle-project/default.nix).
  buildGradleProject = callPackage ./build-gradle-project {
    inherit (lib) purifier;
  };

  # The command: `gradle-purifier lock`, `update`, `prefetch` and
  # `publish`.
  gradle-purifier = callPackage ./gradle-purifier {
    purifierSource = inputs.purifier.outPath;
    sdkSourcePath = inputs.nixpkgs.outPath;
    sdkSource =
      if inputs.nixpkgs ? rev && inputs.nixpkgs ? narHash then
        {
          type = "github";
          owner = "NixOS";
          repo = "nixpkgs";
          inherit (inputs.nixpkgs) rev narHash;
        }
      else
        null;
  };
}
