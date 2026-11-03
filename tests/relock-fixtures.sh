#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Renews the lockfiles of the test fixtures with the gradle-purifier
# command of this checkout. It needs the network. The conformance-relock
# CI job runs it with --relock and fails when a lockfile changes.
#
#   tests/relock-fixtures.sh            locks the fixtures whose build
#                                       definition changed
#   tests/relock-fixtures.sh --relock   locks every fixture
#
# The lock of the Kotlin/Native fixture holds the toolchain of
# x86_64-linux, so that fixture is locked on x86_64 only.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
command=$(nix build --no-link --print-out-paths "$root#gradle-purifier")/bin/gradle-purifier
# The lock fetches the files of Maven Central from its mirror, and names
# Maven Central in the lockfile.
export GRADLE_PURIFIER_CENTRAL_MIRROR=1

for fixture in "$root"/tests/fixtures/*/; do
  java=()
  case $(basename "$fixture") in
    kotlin-native)
      [ "$(uname -m)" = x86_64 ] || continue
      # Kotlin/Native's compiler 2.2 crashes on JDK 21, so its fixture
      # locks on JDK 17.
      java=(--java 17)
      ;;
  esac
  echo "== ${fixture#"$root"/}" >&2
  # ${a[@]+"${a[@]}"}: bash 3.2, the bash of macOS, takes "${a[@]}" of an
  # empty array for an unset variable under set -u.
  (cd "$fixture" && "$command" lock ${java[@]+"${java[@]}"} "$@")
done
