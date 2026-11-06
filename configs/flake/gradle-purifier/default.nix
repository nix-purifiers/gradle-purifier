# SPDX-License-Identifier: MIT
{ closure-lib, ... }:
{
  config,
  inputs,
  lib,
  ...
}:
{

  imports = [
    inputs.flake-parts.flakeModules.partitions
    closure-lib.caisson.modules.flake."purifier/purifier-packages"
  ];

  # The flake publishes as its packages the derivations of the scope
  # `pkgs.gradle-purifier` that the default package overlay adds.
  purifier.scope = "gradle-purifier";

  debug = false;
  caisson = {
    libOverlays.exported = libOverlays: {
      inherit (libOverlays) default;
    };

    # The package set applies the `default` entry of this flake's package
    # overlay registry (pkg-overlays/) by default, and the flake exports it
    # as `pkgOverlays` and as the plain `overlays.default`.
  };

  # `nix run github:nix-purifiers/gradle-purifier -- lock` locks the Gradle
  # build in the current directory, and `-- prefetch` sets up ./gradlew to
  # build it from the locked downloads.
  perSystem =
    { config, pkgs, ... }:
    {
      packages.default = config.packages.gradle-purifier;
      devShells.default = pkgs.mkShell {
        packages = [ config.packages.gradle-purifier ];
      };
    };

  partitionedAttrs.checks = "checks";
  partitionedAttrs.formatter = "formatter";

  partitions.formatter = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem.treefmt.programs.nixfmt.enable = true;
      };
  };

  partitions.checks = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, self, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem =
          { pkgs, system, ... }:
          {
            checks = import ../../../tests {
              inherit pkgs;
              overlay = self.overlays.default;
              gradlePurifier = self.lib.gradle-purifier;
              purifier = self.lib.purifier;
              inherit (self.packages.${system}) gradle-purifier;
            };
            treefmt.programs.nixfmt.enable = true;
          };
      };
  };

}
