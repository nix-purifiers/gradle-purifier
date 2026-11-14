#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The conformance-update check. On a copy of the update fixture, whose
# gradle.lockfile pins commons-lang3 3.12.0 under the dynamic version
# 3.+, `gradle-purifier update org.apache.commons:commons-lang3` must move
# gradle.lockfile and gradle-purifier.lock.json to a newer version, and
# `gradle-purifier prefetch` must then accept both lockfiles, which it
# does only when the lock is current for the build definition.
#
# It needs Nix and the network.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
command=$(nix build --no-link --print-out-paths "$root#gradle-purifier")/bin/gradle-purifier
work=$(mktemp -d "${TMPDIR:-/tmp}/gradle-purifier-update.XXXXXX")
trap 'rm -rf "$work"' EXIT
export XDG_CACHE_HOME=$work/cache
export GRADLE_USER_HOME=$work/home

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

(cd "$root/tests/fixtures/update" && git ls-files -z) | while IFS= read -r -d '' file; do
  mkdir -p "$work/update/$(dirname "$file")"
  cp "$root/tests/fixtures/update/$file" "$work/update/$file"
done
cd "$work/update"
# The command keeps the caller's installed runtime; select the lock's major
# explicitly rather than whatever Java the runner image puts on PATH.
JAVA_HOME=$(python3 "$root/tests/fixture_jvm.py" --cli "$command" \
  --major "$(jq -r .javaMajor gradle-purifier.lock.json)" --root "$work/fixture-jvm" | jq -r .home)
export JAVA_HOME

grep -q 'commons-lang3:3.12.0=' gradle.lockfile || fail "the fixture does not pin commons-lang3 3.12.0"
"$command" update org.apache.commons:commons-lang3 || fail "update failed"

if grep -q 'commons-lang3:3.12.0=' gradle.lockfile; then
  fail "update left gradle.lockfile at commons-lang3 3.12.0"
fi
grep -q 'commons-lang3:3\.' gradle.lockfile || fail "gradle.lockfile names no commons-lang3"
if jq -e '.artifacts[] | select((.url // "") | endswith("commons-lang3-3.12.0.jar"))' gradle-purifier.lock.json >/dev/null; then
  fail "update left gradle-purifier.lock.json at commons-lang3 3.12.0"
fi
"$command" prefetch || fail "prefetch did not accept the lockfiles of the update"
echo "update moved commons-lang3 to $(sed -n 's/^org.apache.commons:commons-lang3:\([^=]*\)=.*/\1/p' gradle.lockfile)" >&2
echo "update tests passed" >&2
