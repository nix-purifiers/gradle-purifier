# SPDX-License-Identifier: MIT
#
# The `gradle-purifier` library namespace: lib.gradle-purifier.readLock
# (lib-overlays/default/read-lock.nix), the lock in memory that the contract of the
# nix-purifiers describes. The builder needs a package set, so it is the
# package `buildGradleProject` that the default package overlay adds.
{ closure-lib, ... }:
{

  overlay = final: prev: {
    gradle-purifier = (prev.gradle-purifier or { }) // {
      readLock = import ./read-lock.nix { lib = closure-lib; };
    };
  };

}
