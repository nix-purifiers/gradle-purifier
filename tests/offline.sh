#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The conformance-offline check, with the command's error cases. On a
# copy of each fixture, with an empty Gradle user home of its own,
# `gradle-purifier prefetch` realizes the lock; then ./gradlew builds and
# tests the fixture, and runs its program, with the network cut off. The
# network is cut in a network namespace of its own when the system lets a
# user make one (unshare -rn), and every JVM also gets a proxy that
# refuses. Gradle's download cache must stay empty. An IDE import through
# the Gradle tooling API, and `gradle-purifier publish`, work the same
# way. Each mistake in a lockfile fails with a message that names the
# command that fixes it.
#
# It needs Nix, which fetches the locked downloads, and the network for
# `prefetch`.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
command=$(nix build --no-link --print-out-paths "$root#gradle-purifier")/bin/gradle-purifier
work=$(mktemp -d "${TMPDIR:-/tmp}/gradle-purifier-offline.XXXXXX")
# The IDE import starts a Gradle daemon, which outlives the test and
# writes into its Gradle user home; it stops before the directory goes.
cleanup() {
  # Daemon arguments need not include the user-home path. Stop each native
  # registry explicitly before waiting for remaining test-owned processes.
  for fixture in java-library kotlin-app; do
    if [[ -x $work/$fixture/.gradle-purifier/distribution/bin/gradle ]]; then
      GRADLE_USER_HOME="$work/$fixture-home" \
        JAVA_HOME="$(jdk_home "$fixture")" \
        "$work/$fixture/.gradle-purifier/distribution/bin/gradle" --offline --stop >/dev/null 2>&1 || true
    fi
  done
  pkill -TERM -f -- "$work" 2>/dev/null || true
  for _ in {1..50}; do
    pgrep -f -- "$work" >/dev/null || break
    sleep 0.1
  done
  # A daemon can take longer than the grace period to stop. Do not race
  # its final cache writes against removing the directory.
  pkill -KILL -f -- "$work" 2>/dev/null || true
  for _ in {1..50}; do
    pgrep -f -- "$work" >/dev/null || break
    sleep 0.1
  done
  rm -rf "$work"
}
trap cleanup EXIT

export XDG_CACHE_HOME=$work/cache
refusing_proxy="-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=9 -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=9"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

if unshare -rn true 2>/dev/null; then
  # A new network namespace has only a loopback interface, and it is
  # down; Gradle needs it, for the address its daemon and file locks
  # listen on.
  cut=(unshare -rn sh -c 'ip link set lo up && exec "$@"' sh)
else
  cut=()
  echo "offline: the system makes no network namespace for this user; the refusing proxy alone cuts Gradle off" >&2
fi

# The home of the JDK that `prefetch` realized for the copy of fixture
# $1: lib/openjdk in nixpkgs' OpenJDK on Linux, the JDK itself in its Zulu
# JDK on Darwin.
jdk_home() {
  local jdk=$work/$1/.gradle-purifier/jdk
  if [ -d "$jdk/lib/openjdk" ]; then echo "$jdk/lib/openjdk"; else echo "$jdk"; fi
}

# Runs `gradle-purifier <arguments>` in the copy of fixture $1, with the
# Gradle user home of that copy.
purifier() {
  local fixture=$1
  shift
  (cd "$work/$fixture" && GRADLE_USER_HOME="$work/$fixture-home" "$command" "$@" 2>&1)
}

# Runs ./gradlew <arguments> in the copy of fixture $1 with the network
# cut off, on the JDK that `prefetch` realized, and prints its output.
# Gradle runs without a daemon, so nothing writes into the directory of
# the test once the test ends.
gradlew() {
  local fixture=$1
  shift
  (
    cd "$work/$fixture"
    GRADLE_USER_HOME="$work/$fixture-home" \
      JAVA_HOME="$(jdk_home "$fixture")" \
      JAVA_TOOL_OPTIONS="$refusing_proxy -Djava.io.tmpdir=${TMPDIR:-/tmp}" \
      ${cut[@]+"${cut[@]}"} ./gradlew --no-daemon "$@" 2>&1
  )
}

# The module cache of Gradle holds what it downloads; a build that reads
# only the locked downloads (file: repositories) leaves it empty.
expect_no_downloads() {
  local cache=$work/$1-home/caches/modules-2/files-2.1
  if [ -d "$cache" ] && [ -n "$(ls -A "$cache")" ]; then
    fail "$1: Gradle downloaded into its cache: $(ls "$cache")"
  fi
}

# Each copy holds the files of the fixture that git tracks, as a clean
# checkout does.
copy_fixture() {
  local fixture=$1 name=${2:-$1}
  (cd "$root/tests/fixtures/$fixture" && git ls-files -z) | while IFS= read -r -d '' file; do
    mkdir -p "$work/$name/$(dirname "$file")"
    cp "$root/tests/fixtures/$fixture/$file" "$work/$name/$file"
  done
}

# The fixtures keep the wrapper properties only; the wrapper script and
# its jar come from the `wrapper` task of the Gradle distribution that the
# prefetch of java-library realized.
install_wrapper() {
  cp "$work/wrapper/gradlew" "$work/$1/gradlew"
  cp "$work/wrapper/gradle/wrapper/gradle-wrapper.jar" "$work/$1/gradle/wrapper/gradle-wrapper.jar"
}

fixtures=(java-library kotlin-app)
# The Kotlin/Native fixture's lock holds the toolchain of x86_64-linux.
[ "$(uname -m)" = x86_64 ] && fixtures+=(kotlin-native)

for fixture in "${fixtures[@]}"; do
  copy_fixture "$fixture"
  echo "== $fixture: prefetch" >&2
  output=$(purifier "$fixture" prefetch) || { echo "$output"; fail "$fixture: prefetch failed"; }
done

echo "== the wrapper script" >&2
mkdir -p "$work/wrapper"
echo "rootProject.name = 'wrapper'" >"$work/wrapper/settings.gradle"
(
  cd "$work/wrapper"
  GRADLE_USER_HOME="$work/wrapper-home" \
    JAVA_HOME="$(jdk_home java-library)" \
    "$work/java-library/.gradle-purifier/distribution/bin/gradle" --no-daemon --offline wrapper >/dev/null 2>&1
) || fail "the wrapper task of the Gradle distribution failed"
for fixture in "${fixtures[@]}"; do
  install_wrapper "$fixture"
done

echo "== java-library: build offline" >&2
output=$(gradlew java-library build) || { echo "$output"; fail "java-library: build failed"; }
grep -q 'BUILD SUCCESSFUL' <<<"$output" || fail "java-library: no successful build"
expect_no_downloads java-library

echo "== java-library: an IDE imports the build offline through the tooling API" >&2
# The import runs the way Buildship and jdtls import a build: with the
# Gradle distribution of the wrapper, and the Gradle user home that
# `prefetch` set up.
fetch() {
  nix --extra-experimental-features nix-command store prefetch-file --json "$1" | jq -r '.storePath'
}
tooling=$(fetch https://repo.gradle.org/gradle/libs-releases/org/gradle/gradle-tooling-api/9.1.0/gradle-tooling-api-9.1.0.jar)
slf4j=$(fetch https://maven-central.storage-download.googleapis.com/maven2/org/slf4j/slf4j-api/2.0.16/slf4j-api-2.0.16.jar)
jdk=$(jdk_home java-library)
"$jdk/bin/javac" -cp "$tooling" -d "$work/tooling" "$root/tests/ToolingImport.java"
output=$(JAVA_TOOL_OPTIONS="$refusing_proxy" ${cut[@]+"${cut[@]}"} "$jdk/bin/java" -cp "$work/tooling:$tooling:$slf4j" ToolingImport \
  "$work/java-library" "$work/java-library/.gradle-purifier/distribution" "$work/java-library-home" 2>&1) \
  || { echo "$output"; fail "java-library: the IDE import failed"; }
grep -q "$(printf '^commons-lang3-3.17.0.jar\tcommons-lang3-3.17.0-sources.jar$')" <<<"$output" \
  || { echo "$output"; fail "java-library: the IDE import has no commons-lang3 with its sources"; }
expect_no_downloads java-library

echo "== java-library: publish what Nix built" >&2
rm -rf "$work/java-library/build"
output=$(purifier java-library publish publishMavenPublicationToLocalRepository) || { echo "$output"; fail "java-library: publish failed"; }
published=$(find "$work/java-library/build/local-repository/example/greeting" -mindepth 1 -maxdepth 1 -type d -name '1.0.0-nix-*')
version=$(basename "$published")
built=$work/java-library/.gradle-purifier/publication/repository/example/greeting/$version
for suffix in .jar -sources.jar .module .pom; do
  file=greeting-$version$suffix
  [ -f "$published/$file" ] || fail "java-library: publish sent no $file"
  cmp -s "$published/$file" "$built/$file" || fail "java-library: the published $file is not the Nix projection"
done
grep -q 'published-with-gradle-metadata' "$published/greeting-$version.pom" || fail "java-library: POM lost its module metadata marker"
if [ -d "$work/java-library/build/classes" ]; then
  echo "$output"
  fail "java-library: publish compiled the project"
fi

echo "== kotlin-app: build and run offline" >&2
output=$(gradlew kotlin-app build :app:run) || { echo "$output"; fail "kotlin-app: build failed"; }
# The JVM's notice of JAVA_TOOL_OPTIONS on stderr may share a line with
# the greeting.
grep -q 'Hello, Nix!$' <<<"$output" || { echo "$output"; fail "kotlin-app: the app did not greet"; }
expect_no_downloads kotlin-app

if [ "$(uname -m)" = x86_64 ]; then
  echo "== kotlin-native: build, test and run offline on the locked toolchain" >&2
  output=$(gradlew kotlin-native build runReleaseExecutableLinuxX64) || { echo "$output"; fail "kotlin-native: build failed"; }
  grep -q 'Hello, Nix!$' <<<"$output" || { echo "$output"; fail "kotlin-native: the executable did not greet"; }
  expect_no_downloads kotlin-native
fi

# Each mistake below makes the command or the build fail with a message
# that says what is wrong and what to run. Each works on a fresh copy of
# java-library, whose lockfile it changes.
mistake() {
  rm -rf "$work/mistake" "$work/mistake-home"
  copy_fixture java-library mistake
  install_wrapper mistake
}
lock=$work/mistake/gradle-purifier.lock.json
# Sets the contentHash of the lockfile again, as `gradle-purifier lock`
# does, so that only the change under test is wrong.
seal() {
  local digest
  digest=$(jq -j -S -c 'del(.contentHash)' "$lock" | sha256sum | cut -d ' ' -f 1)
  jq --arg c "sha256:$digest" '.contentHash = $c' "$lock" >"$lock.new"
  mv "$lock.new" "$lock"
}

echo "== a lockfile that is not JSON" >&2
mistake
echo '{ "version": 1, ' >"$lock"
if output=$(purifier mistake prefetch); then fail "prefetch accepted a lockfile that is not JSON"; fi
grep -q 'not valid JSON.*lock --relock' <<<"$output" || { echo "$output"; fail "no message for a lockfile that is not JSON"; }

echo "== a lockfile edited by hand" >&2
mistake
jq '.gradleArguments = ["--info"]' "$root/tests/fixtures/java-library/gradle-purifier.lock.json" >"$lock"
if output=$(purifier mistake prefetch); then fail "prefetch accepted an edited lockfile"; fi
grep -q 'does not match its contentHash.*lock --relock' <<<"$output" || { echo "$output"; fail "no message for an edited lockfile"; }

echo "== a hash that does not match the download" >&2
mistake
# The changed entry downloads again, so it is the small file from
# nodejs.org, which serves the test without a rate limit.
jq '(.artifacts[] | select((.url // "") | endswith("SHASUMS256.txt")) | .hash) = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="' \
  "$root/tests/fixtures/java-library/gradle-purifier.lock.json" >"$lock"
seal
if output=$(purifier mistake prefetch); then fail "prefetch accepted a wrong hash"; fi
grep -q 'hash mismatch' <<<"$output" || { echo "$output"; fail "Nix reported no hash mismatch"; }
grep -q 'restore the lockfile.*lock --relock' <<<"$output" || { echo "$output"; fail "no message for a wrong hash"; }

echo "== a download missing from the lockfile" >&2
mistake
jq 'del(.artifacts[] | select((.url // "") | endswith("commons-text-1.13.0.jar")))' \
  "$root/tests/fixtures/java-library/gradle-purifier.lock.json" >"$lock"
seal
output=$(purifier mistake prefetch) || { echo "$output"; fail "prefetch failed on a lockfile without a download"; }
if output=$(gradlew mistake build); then fail "a build without a download it needs ran"; fi
grep -q 'commons-text' <<<"$output" || { echo "$output"; fail "Gradle did not name the missing file"; }
grep -q "run 'gradle-purifier lock'" <<<"$output" || { echo "$output"; fail "no message for a missing download"; }

echo "== a build definition changed since the lock" >&2
mistake
echo '// changed' >>"$work/mistake/build.gradle"
if output=$(purifier mistake prefetch); then fail "prefetch accepted a stale lockfile"; fi
grep -q "changed since.*run 'gradle-purifier lock'" <<<"$output" || { echo "$output"; fail "no message for a stale lockfile"; }

echo "offline tests passed" >&2
