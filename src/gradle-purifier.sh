# SPDX-License-Identifier: MIT
#
# The gradle-purifier command. pkgs/gradle-purifier substitutes the @…@
# names and runs it as a writeShellApplication, which sets errexit,
# nounset and pipefail.
#
#   gradle-purifier lock [--relock] [--java <major>] [--lockfile <path>] [-- <gradle arguments>]
#       records every download of the build in the lockfile
#   gradle-purifier update [--java <major>] [--lockfile <path>] [<group>:<artifact>...] [-- <gradle arguments>]
#       moves dynamic versions, and Gradle's own lockfiles, to the newest
#       versions, then records the downloads as lock does
#   gradle-purifier prefetch [--lockfile <path>]
#       realizes the locked downloads, the JDK, the Gradle distribution and
#       the Kotlin/Native toolchain, and installs the init script that makes
#       ./gradlew, a Gradle on the PATH and an IDE read them, offline
#   gradle-purifier publish [--lockfile <path>] [<gradle arguments>]
#       publishes what Nix built from the lockfile, through the build's
#       own publish tasks
#
# Each command runs at the root of a Gradle build. The lockfile is
# gradle-purifier.lock.json there, or the path that --lockfile or
# GRADLE_PURIFIER_LOCKFILE names, for a build kept external. The GC roots
# lie beside the lockfile, in .gradle-purifier.

# A failure inside $(…) stops the command too.
shopt -s inherit_errexit

lib=@lib@
recorder=@recorder@
default_java=@java@
lock_version=@lockVersion@
# The nixpkgs and the nix-purifiers library the command builds with.
sdk_descriptor=@sdkDescriptor@
nixpkgs=@nixpkgs@
purifier_source=@purifier@
# The dynamic linker the programs Kotlin/Native links name, from that
# package set.
dynamic_linker=@dynamicLinker@

portal=https/plugins.gradle.org/m2
# The hosts Kotlin/Native downloads its toolchain from: its
# dependenciesUrl, and the CDN that redirects to.
konan_hosts=(download.jetbrains.com download-cdn.jetbrains.com)

die() {
  echo "gradle-purifier: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  gradle-purifier lock [--relock] [--java <major>] [--lockfile <path>] [-- <gradle arguments>]
      Records every file the build downloads, by URL and hash, and the
      outputs of every project, in gradle-purifier.lock.json. The lock is
      made again only when the build definition changed, unless --relock
      is given. Gradle runs on nixpkgs' JDK of the major version --java
      names, or of the Java the lockfile was made with; a first lock runs
      on JAVA_HOME, or on the command's JDK 21. The gradle
      arguments are recorded and passed to every Gradle run of the lock.
      A dynamic version, such as 3.17.+, resolves as it did in the lock
      before, from the version listings the lockfile holds.
  gradle-purifier update [--java <major>] [--lockfile <path>] [<group>:<artifact>...] [-- <gradle arguments>]
      Locks again with the newest versions that the dynamic versions of
      the build allow: of every package, or of the packages named
      (group:* names every artifact of a group). In a build with Gradle's
      dependency locking, it runs Gradle with --write-locks, or with
      --update-locks for the packages named, and writes the Gradle
      lockfiles back into the build. It keeps the gradle arguments of the
      lock unless it is given others.
  gradle-purifier prefetch [--lockfile <path>]
      Realizes in the Nix store everything the development loop and an IDE
      need: the locked downloads, the JDK, the Gradle distribution (where
      ./gradlew looks for it) and a Kotlin/Native toolchain. It installs an
      init script in the init.d of the Gradle user home that makes the
      builds of this root read them, so ./gradlew build, a Gradle on the
      PATH and an IDE work with the network cut off. Run it after each
      lock.
  gradle-purifier publish [--lockfile <path>] [<gradle arguments>]
      Builds the release with Nix from the lockfile, and runs the build's
      publish tasks (publish, unless gradle arguments name others) with the
      files Nix built in place of the ones Gradle would make.

The lockfile is gradle-purifier.lock.json at the build root, or the path
--lockfile or GRADLE_PURIFIER_LOCKFILE names.
EOF
}

# The build root, the lockfile and the directory of GC roots, set by
# set_paths.
root=
lockfile=
state=

# Sets root, lockfile and state, from the --lockfile argument $1 (or "").
set_paths() {
  if [ ! -f settings.gradle ] && [ ! -f settings.gradle.kts ]; then
    die "run this at the root of a Gradle build (the directory with settings.gradle or settings.gradle.kts)"
  fi
  root=$PWD
  local given=${1:-${GRADLE_PURIFIER_LOCKFILE:-}}
  if [ -n "$given" ]; then
    lockfile=$(realpath -m -- "$given")
  else
    lockfile=$root/gradle-purifier.lock.json
  fi
  state=$(dirname "$lockfile")/.gradle-purifier
}

# The home of the JDK at the store path $1: lib/openjdk in nixpkgs'
# OpenJDK on Linux, the store path itself in its Zulu JDK on Darwin.
jdk_home() {
  if [ -d "$1/lib/openjdk" ]; then
    printf '%s/lib/openjdk' "$1"
  else
    printf '%s' "$1"
  fi
}

# The home of nixpkgs' headless JDK of the major version $1, built with
# Nix (GC root in the state directory).
nixpkgs_jdk() {
  local out
  mkdir -p "$state"
  out=$(nix-build --quiet --out-link "$state/jdk-$1" "$nixpkgs" \
    -A "javaPackages.compiler.openjdk$1.headless") || die "nixpkgs has no headless OpenJDK $1"
  jdk_home "$out"
}

# The JDK to run the lock on: nixpkgs' JDK of the major version $1 when it
# is given; otherwise JAVA_HOME when it is set, and the command's own JDK
# when it is not.
java_home() {
  if [ -n "${1:-}" ]; then
    nixpkgs_jdk "$1"
  elif [ -n "${JAVA_HOME:-}" ]; then
    echo "$JAVA_HOME"
  else
    echo "$default_java"
  fi
}

# The distribution URL of the wrapper properties, with the escapes of the
# properties format removed.
distribution_url() {
  local properties=gradle/wrapper/gradle-wrapper.properties
  [ -f "$properties" ] || die "the build has no $properties; add a wrapper with 'gradle wrapper'"
  local url
  url=$(sed -n 's/^[[:space:]]*distributionUrl[[:space:]]*[=:][[:space:]]*//p' "$properties" | tail -n 1 | tr -d '\r')
  [ -n "$url" ] || die "$properties sets no distributionUrl"
  printf '%s' "${url//\\/}"
}

# The path of a URL in the tree of locked downloads.
tree_path() {
  local url=$1
  local scheme=${url%%://*}
  printf '%s/%s' "$scheme" "${url#*://}"
}

# The hash of the build definition at the build root with the gradle
# arguments of the JSON list $1, as nix/lib/inputs.nix computes it; the
# builder computes it the same way.
inputs_hash() {
  nix-instantiate --eval --strict --json \
    --expr "{ src, args }: import $lib/inputs.nix { lib = (import $nixpkgs { }).lib; src = /. + src; gradleArguments = builtins.fromJSON args; }" \
    --argstr src "$root" --argstr args "$1" | jq -r .
}

# The contentHash of the lockfile $1: SHA-256 of its JSON without
# contentHash, with sorted keys and no spaces, as builtins.toJSON writes
# it; read-lock.nix checks it.
content_hash() {
  printf 'sha256:%s' "$(jq -j -S -c 'del(.contentHash)' "$1" | sha256sum | cut -d ' ' -f 1)"
}

# Unpacks the Gradle distribution zip $1 into the user's cache, once, and
# prints its Gradle home.
unpack_distribution() {
  local zip=$1
  local cache=${XDG_CACHE_HOME:-$HOME/.cache}/gradle-purifier/distributions
  local name
  name=$(basename "$(readlink -f "$zip")")
  local dir=$cache/$name
  if [ ! -d "$dir" ]; then
    mkdir -p "$cache"
    local tmp
    tmp=$(mktemp -d "$cache/.unpack.XXXXXX")
    unzip -q "$zip" -d "$tmp"
    mv "$tmp" "$dir"
  fi
  local home
  home=$(find "$dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  [ -x "$home/bin/gradle" ] || die "$zip holds no bin/gradle"
  printf '%s' "$home"
}

# Copies the build at the current directory to $1, leaving out version
# control, Gradle's state, IDE files and the build directory of every
# project.
copy_build() {
  local target=$1
  local excludes=(--exclude=/.git --exclude=.gradle --exclude=.gradle-purifier --exclude=.kotlin --exclude=.idea --exclude=/result*)
  local script dir
  while IFS= read -r script; do
    dir=$(dirname "${script#./}")
    if [ "$dir" = . ]; then excludes+=(--exclude=/build/); else excludes+=(--exclude="/$dir/build/"); fi
  done < <(find . \( -name .git -o -name .gradle -o -name build -o -name node_modules \) -prune -o \
    -type f \( -name 'build.gradle' -o -name 'build.gradle.kts' -o -name 'settings.gradle' -o -name 'settings.gradle.kts' \) -print)
  mkdir -p "$target"
  rsync -a --delete "${excludes[@]}" ./ "$target/"
}

# Kotlin/Native downloads its toolchain (LLVM, a GCC toolchain with a
# sysroot, and more) with its own downloader, and deletes each archive
# once it has extracted it. The downloader names every download in the
# build's output, as "Downloading dependency <url> to <file>". Records
# each such URL from the Gradle output $1 as a download into the records
# file $2, fetching it into the store for its hash.
record_konan_dependencies() {
  local log=$1 records=$2
  local url hash
  while IFS= read -r url; do
    echo "gradle-purifier: fetching $url" >&2
    hash=$(nix --extra-experimental-features nix-command store prefetch-file --json --hash-type sha256 "$url" | jq -r '.hash')
    printf '%s\t%s\t%s\n' "$(tree_path "$url")" "$url" "$hash" >>"$records"
  done < <(konan_download_urls "$log")
}

# The URLs Kotlin/Native's downloader names in the Gradle output $1, each
# once.
konan_download_urls() {
  sed -n 's/^Downloading dependency \(https\?:\/\/[^ ]*\) to .*/\1/p' "$1" | sort -u
}

# The lockfile's artifacts, as JSON, from the recorder's records file $1:
# a file entry { url, hash } for each line with a URL, and an inline entry
# { path, contents } for each line the recorder marked "inline" (its third
# field is the contents in Base64); one entry per path, in the order of
# the paths. Nix derives the path and the mirrors of a file entry from its
# URL.
artifacts_json() {
  jq -R -s 'split("\n") | map(select(length > 0) | split("\t"))
    | sort_by(.[0], (if .[1] == "inline" then 0 else 1 end))
    | unique_by(.[0])
    | map(if .[1] == "inline" then {path: .[0], contents: (.[2] | @base64d)}
          else {url: .[1], hash: .[2]} end)' "$1"
}

# The Kotlin/Native name of this machine. A Kotlin/Native toolchain runs
# on the machine it was made for, so the lock records it.
konan_host() {
  case $(uname -m) in
    x86_64) echo linux_x64 ;;
    aarch64 | arm64) echo linux_arm64 ;;
    *) uname -m ;;
  esac
}

# The konanHost of the lockfile, as JSON: the Kotlin/Native name of this
# machine when the records file $1 holds a download of a Kotlin/Native
# toolchain, and null otherwise, so that the lock of a build without
# Kotlin/Native is the same on every machine.
lock_konan_host() {
  if grep -q -F 'https://download.jetbrains.com/kotlin/native/' "$1"; then
    printf '"%s"' "$(konan_host)"
  else
    printf 'null'
  fi
}

lock_command() {
  local relock='' java_major='' given='' input_args=() refresh_inputs=()
  while [ $# -gt 0 ]; do
    case $1 in
      --relock) relock=1; shift ;;
      --input)
        [ $# -ge 2 ] || die "--input takes a flake package"
        input_args+=(--input "$2")
        relock=1
        shift 2
        ;;
      --java)
        [ $# -ge 2 ] || die "--java takes a Java major version, such as 17"
        java_major=$2
        shift 2
        ;;
      --lockfile)
        [ $# -ge 2 ] || die "--lockfile takes a path"
        given=$2
        shift 2
        ;;
      --) shift; break ;;
      -h | --help) usage; exit 0 ;;
      *) die "lock takes --relock, --java <major>, --lockfile <path> and, after --, gradle arguments; got $1" ;;
    esac
  done
  local gradle_args=("$@")
  set_paths "$given"

  local args_json current
  args_json=$(jq -n -c '$ARGS.positional' --args -- "${gradle_args[@]}")
  current=$(inputs_hash "$args_json")
  if [ -z "$relock" ] && [ -f "$lockfile" ] \
    && jq -e --argjson version "$lock_version" --arg current "$current" \
      '.version == $version and .inputsHash == $current' "$lockfile" >/dev/null 2>&1 \
    && [ "$(jq -r '.contentHash' "$lockfile")" = "$(content_hash "$lockfile")" ]; then
    echo "gradle-purifier: $lockfile is current" >&2
    return 0
  fi

  mkdir -p "$state"
  pinned_listings >"$state/pinned.tsv"
  update_args=()
  run_lock "$(lock_java "$java_major")" "$state/pinned.tsv" '' "${gradle_args[@]}"
}

update_command() {
  local java_major='' given='' have_args='' packages=() gradle_args=() input_args=() refresh_inputs=()
  while [ $# -gt 0 ]; do
    case $1 in
      --input)
        [ $# -ge 2 ] || die "--input takes a flake package"
        input_args+=(--input "$2")
        shift 2
        ;;
      --java)
        [ $# -ge 2 ] || die "--java takes a Java major version, such as 17"
        java_major=$2
        shift 2
        ;;
      --lockfile)
        [ $# -ge 2 ] || die "--lockfile takes a path"
        given=$2
        shift 2
        ;;
      --)
        shift
        gradle_args=("$@")
        have_args=1
        break
        ;;
      -h | --help) usage; exit 0 ;;
      -*) die "update takes --java <major>, --lockfile <path>, packages and, after --, gradle arguments; got $1" ;;
      *)
        [[ $1 == *:* ]] || die "a package is group:artifact, or group:* for every artifact of a group; got $1"
        packages+=("$1")
        shift
        ;;
    esac
  done
  set_paths "$given"
  # The update keeps the gradle arguments of the lock unless it is given
  # others.
  if [ -z "$have_args" ] && [ -f "$lockfile" ]; then
    mapfile -t gradle_args < <(jq -r '.gradleArguments[]?' "$lockfile" 2>/dev/null || true)
  fi

  mkdir -p "$state"
  : >"$state/pinned.tsv"
  update_args=()
  if [ ${#packages[@]} -gt 0 ]; then
    pinned_listings "${packages[@]}" >"$state/pinned.tsv"
    if uses_dependency_locking; then
      update_args=(--update-locks "$(IFS=,; echo "${packages[*]}")")
    fi
  elif uses_dependency_locking; then
    update_args=(--write-locks)
  fi
  if [ ${#packages[@]} = 0 ]; then refresh_inputs=(--refresh); resource_refresh=1; fi
  run_lock "$(lock_java "$java_major")" "$state/pinned.tsv" 1 "${gradle_args[@]}"
}

# The Java major version a lock runs on: $1 when --java gave one, else the
# one of the existing lockfile, so that a relock keeps the Java of the
# lock it replaces; empty for a first lock, which runs on JAVA_HOME or the
# JDK of the command.
lock_java() {
  if [ -n "$1" ]; then
    echo "$1"
  elif [ -f "$lockfile" ]; then
    jq -r '.javaMajor // empty' "$lockfile" 2>/dev/null || true
  fi
}

# The version listings (maven-metadata files) of the lockfile, for the
# recorder to serve in place of the remote ones (GRADLE_PURIFIER_PINNED):
# a line of the path and the contents in Base64 for each listing, except
# the listings of the packages $@ (group:artifact, or group:* for every
# artifact of a group). A lockfile of another format version pins
# nothing, and a lockfile that is not JSON fails the command.
pinned_listings() {
  [ -f "$lockfile" ] || return 0
  jq -r --argjson version "$lock_version" --args '
    ($ARGS.positional | map(split(":") | {group: (.[0] | gsub("\\."; "/")), artifact: (.[1] // "*")})) as $updated
    | select(.version == $version)
    | .artifacts[]
    | select(.contents != null)
    | (.path | split("/")) as $segments
    | select(($segments | length) > 2)
    | select(
        ($segments[-2]) as $artifact
        | ($segments[:-2] | join("/")) as $groupDir
        | $updated
        | any(. as $u | ($groupDir | endswith("/" + $u.group)) and ($u.artifact == "*" or $u.artifact == $artifact))
        | not)
    | [.path, (.contents | @base64)]
    | @tsv' "$@" <"$lockfile" || die "$lockfile is not JSON; restore it from version control, or delete it and run 'gradle-purifier lock'"
}

# Whether the build uses Gradle's dependency locking: Gradle wrote a
# lockfile somewhere in it.
uses_dependency_locking() {
  [ -n "$(find . \( -name .git -o -name .gradle -o -name .gradle-purifier -o -name build -o -name node_modules \) -prune -o \
    -type f -name '*gradle.lockfile' -print -quit)" ]
}

# The gradle arguments of an update that writes Gradle's own lockfiles,
# which the lock does not record.
update_args=()

# The lock run: Gradle on the JDK of the Java major version $1 (or the
# default), with the recorder serving the pinned version listings of the
# file $2; when $3 is not empty, Gradle's own lockfiles are copied back
# from the lock run to the build. The gradle arguments follow.
run_lock() {
  local java_major=$1 pins=$2 copy_back=$3
  shift 3
  local gradle_args=("$@")
  local args_json
  args_json=$(jq -n -c '$ARGS.positional' --args -- "${gradle_args[@]}")

  local url
  url=$(distribution_url)
  echo "gradle-purifier: fetching $url" >&2
  local prefetch dist_hash dist_zip
  prefetch=$(nix --extra-experimental-features nix-command store prefetch-file --json --hash-type sha256 "$url")
  dist_hash=$(jq -r '.hash' <<<"$prefetch")
  dist_zip=$(jq -r '.storePath' <<<"$prefetch")
  local gradle_home
  gradle_home=$(unpack_distribution "$dist_zip")

  local work=$state/lock-run
  rm -rf "$work"
  mkdir -p "$work"
  copy_build "$work/build"
  local previous_args=()
  if [ -f "$lockfile" ]; then previous_args=(--previous "$lockfile"); fi
  python3 "$purifier_source/tools/project_inputs.py" pin "${previous_args[@]}" \
    "${input_args[@]}" "${refresh_inputs[@]}" --output "$work/inputs.json"
  if [ "$(jq length "$work/inputs.json")" -gt 0 ]; then
    python3 "$purifier_source/tools/project_inputs.py" maven --inputs "$work/inputs.json" \
      --destination "$work/provided" --metadata "$work/input-metadata" --roots "$work/inputs"
    export GRADLE_PURIFIER_PROVIDED_REPOSITORY="$work/provided"
    export GRADLE_PURIFIER_PROVIDED_CATALOG="$work/input-metadata/catalog.json"
  fi

  local records=$work/records.tsv
  printf '%s\t%s\t%s\n' "$(tree_path "$url")" "$url" "$dist_hash" >"$records"
  local java
  java=$(java_home "$java_major")
  # Empty jdk.http.auth.*.disabledSchemes let the recorder send the Basic
  # credentials of an https_proxy URL to the proxy, for HTTPS tunnels and
  # for plain HTTP alike.
  # The recorder is a purifier process with Java 17 bytecode/APIs. Keep it
  # on the bundled runtime while Gradle uses the project-selected JDK below.
  GRADLE_PURIFIER_PINNED=$pins \
    "$default_java/bin/java" -Djdk.http.auth.tunneling.disabledSchemes= -Djdk.http.auth.proxying.disabledSchemes= \
    -cp "$recorder" Recorder "$records" "$work/port" "${konan_hosts[@]}" &
  local recorder_pid=$!
  # shellcheck disable=SC2064 # the PID is known now
  trap "kill $recorder_pid 2>/dev/null || true" EXIT
  local waited=0
  while [ ! -f "$work/port" ]; do
    kill -0 "$recorder_pid" 2>/dev/null || die "the recorder did not start"
    [ $waited -lt 300 ] || die "the recorder did not start within 30 seconds"
    sleep 0.1
    waited=$((waited + 1))
  done
  local ports base tunnel_port
  read -r -a ports <"$work/port" || true
  base=http://127.0.0.1:${ports[0]}
  tunnel_port=${ports[1]}

  local gradle_flags=()
  GRADLE_PURIFIER_MODE='' JAVA_HOME=$java GRADLE_USER_HOME=$work/gradle-home \
    python3 "$lib/gradle-flags.py" capture "$work/gradle-help.txt" --gradle "$gradle_home/bin/gradle" --arguments-json "$args_json" >"$work/gradle-flags.txt" \
    || die "could not select native Gradle controls"
  mapfile -t gradle_flags <"$work/gradle-flags.txt"

  # Read the unpacked native distribution's version, not its URL's filename.
  GRADLE_PURIFIER_MODE='' JAVA_HOME=$java GRADLE_USER_HOME=$work/gradle-home \
    "$gradle_home/bin/gradle" --version >"$work/gradle-version.txt" \
    || die "could not read the native Gradle version"
  local native_gradle_version
  native_gradle_version=$(sed -n 's/^Gradle //p' "$work/gradle-version.txt")
  [[ $native_gradle_version =~ ^[0-9][0-9A-Za-z.+_-]*$ ]] \
    || die "the native distribution did not return one recognizable Gradle version"

  echo "gradle-purifier: running the lock build in $work/build" >&2
  local legacy_bindings='[]' legacy_metadata_capture=0
  if [ -f "$lockfile" ]; then
    legacy_bindings=$(jq -c '[.projects[] | .path as $path | .publications[] | select(.kind == "legacy-maven-upload") | {path:$path,publication:.}]' "$lockfile") \
      || die "could not read existing legacy publication bindings"
  fi
  if [ ! -f "$lockfile" ] || [ "${relock:-}" = 1 ]; then
    legacy_metadata_capture=1
  fi
  if [ "$native_gradle_version" = 5.6.4 ]; then
    # readLock identifies this exact basic profile from the locked distribution.
    # A renamed archive must not give the builder a different profile identity.
    [ "${url##*/}" = gradle-5.6.4-bin.zip ] \
      || die "the native Gradle5.6.4 basic profile requires its matching gradle-5.6.4-bin.zip distribution identity"
    legacy_metadata_capture=0
  fi
  if ! (
    cd "$work/build" || exit 1
    # Gradle's own HTTP client reaches only the recorder: a remote it
    # contacts directly goes to a proxy that refuses, so a download the
    # lock would miss fails here rather than in the sandbox. The HTTPS
    # proxy is the recorder's tunnel, which reaches Kotlin/Native's
    # download host and refuses every other.
    KONAN_DATA_DIR=$work/konan \
      GRADLE_USER_HOME=$work/gradle-home \
      GRADLE_PURIFIER_MODE=lock \
      GRADLE_PURIFIER_LEGACY_PUBLICATIONS=$legacy_bindings \
      GRADLE_PURIFIER_LEGACY_METADATA_CAPTURE=$legacy_metadata_capture \
      GRADLE_PURIFIER_BUILD_ROOT=$work/build \
      GRADLE_PURIFIER_REPOSITORY=$base \
      GRADLE_PURIFIER_OUTPUT=$work/output \
      GRADLE_PURIFIER_RESOURCES=$lockfile \
      GRADLE_PURIFIER_RESOURCE_REFRESH=${resource_refresh:-} \
      JAVA_HOME=$java \
      "$gradle_home/bin/gradle" \
      --init-script "$lib/purifier.init.gradle" \
      --init-script "$lib/task-jdks.init.gradle" \
      "-Dorg.gradle.internal.plugins.portal.url.override=$base/$portal" \
      "-Djava.io.tmpdir=${TMPDIR:-/tmp}" \
      -Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=9 \
      -Dhttps.proxyHost=127.0.0.1 "-Dhttps.proxyPort=$tunnel_port" \
      '-Dhttp.nonProxyHosts=localhost|127.0.0.1' \
      -Porg.gradle.java.installations.auto-download=false \
      -Pkotlin.compiler.execution.strategy=in-process \
      "${gradle_args[@]}" "${update_args[@]}" "${gradle_flags[@]}" \
      purifierLock </dev/null 2>&1 | tee "$work/gradle.log"
  ); then
    # The lock serves the version listings of the lockfile, so a dynamic
    # version resolves as it did; a build that now asks for a version the
    # listing does not have needs an update of that package.
    if grep -q -e 'Could not find any version that matches' -e 'Could not find any matches for' "$work/gradle.log"; then
      die "the lock build failed: a dynamic version asks for a version that the version listings of the lockfile do not have; run 'gradle-purifier update <group>:<artifact>' for that package, or 'gradle-purifier update' for every package"
    fi
    die "the lock build failed (Gradle names the cause above)"
  fi
  kill "$recorder_pid" 2>/dev/null || true
  wait "$recorder_pid" 2>/dev/null || true
  trap - EXIT
  record_konan_dependencies "$work/gradle.log" "$records"
  if [ -n "$copy_back" ]; then
    rsync -a --prune-empty-dirs --exclude=.gradle/ --exclude=.gradle-purifier/ \
      --include='*/' --include='*gradle.lockfile' --exclude='*' "$work/build/" "$root/"
  fi
  # The hash of the build definition after the run, which an update may
  # change through Gradle's own lockfiles.
  local hash
  hash=$(inputs_hash "$args_json")

  [ -f "$work/output/projects.json" ] || die "the lock build wrote no projects.json"
  # A large build locks more than fits in one argument, so the artifacts
  # go to jq as a file.
  artifacts_json "$records" >"$work/artifacts.json"
  python3 "$lib/capture-project-jdks.py" "$work/output/task-sdks.json" "$sdk_descriptor" "$work/project-jdks.json"
  if [ -d "$work/output/external-resources" ]; then
    jq -s 'add | sort_by(.build, .task, .linkUrl)' "$work/output/external-resources/"*.json >"$work/resources.json"
  else
    echo '[]' >"$work/resources.json"
  fi
  jq -n \
    --argjson version "$lock_version" \
    --slurpfile projectInputs "$work/inputs.json" \
    --slurpfile projectJdks "$work/project-jdks.json" \
    --slurpfile described "$work/output/projects.json" \
    --slurpfile artifacts "$work/artifacts.json" \
    --slurpfile resources "$work/resources.json" \
    --arg distribution "$(tree_path "$url")" \
    --arg inputsHash "$hash" \
    --argjson konanHost "$(lock_konan_host "$records")" \
    --argjson gradleArguments "$args_json" \
    '{
      version: $version,
      contentHash: "",
      inputsHash: $inputsHash,
      gradleArguments: $gradleArguments,
      javaMajor: $described[0].javaMajor,
      distribution: $distribution,
      rootProjectName: $described[0].rootProjectName,
      konanHost: $konanHost,
      projects: $described[0].projects,
      artifacts: $artifacts[0]
    } | if ($projectJdks[0] | length) > 0 then . + {projectJdks: $projectJdks[0]} else . end
      | if ($projectInputs[0] | length) > 0 then . + {projectInputs: $projectInputs[0]} else . end
      | if ($resources[0] | length) > 0 then . + {resources: $resources[0]} else . end' >"$work/lock.json"
  mkdir -p "$(dirname "$lockfile")"
  jq --arg contentHash "$(content_hash "$work/lock.json")" '.contentHash = $contentHash' \
    "$work/lock.json" >"$lockfile.tmp"
  mv "$lockfile.tmp" "$lockfile"
  echo "gradle-purifier: wrote $lockfile ($(jq '.artifacts | length' "$lockfile") downloads)" >&2
}

# Builds the attribute $1 of the build (nix/lib/command.nix, which applies
# buildGradleProject to the build root and the lockfile), with a GC root
# beside the lockfile, and prints its store path. The lockfile's problems
# (stale, tampered, malformed, incomplete) fail here, as at Nix evaluation
# of the flake.
build_attr() {
  mkdir -p "$state"
  local out
  if ! out=$(nix-build --quiet "$lib/command.nix" \
    --argstr nixpkgs "$nixpkgs" --argstr purifierSource "$purifier_source" \
    --argstr src "$root" --argstr lockFile "$lockfile" \
    -A "$1" --out-link "$state/$1"); then
    die "Nix could not build the $1 of $lockfile (see Nix's error above). A hash mismatch means the lockfile does not match what a URL serves: restore the lockfile from version control, or run 'gradle-purifier lock --relock' if the remote changed the file."
  fi
  printf '%s' "$out" | tail -n 1
}

# Realize the same typed SDK closure used by the Nix builder.
realise_sdk_context() {
  sdk_context=$(build_attr sdkContext)
  if jq -e 'type == "object"' "$sdk_context" >/dev/null; then
    export GRADLE_PURIFIER_JDK_CONTEXT=$sdk_context
  else
    unset GRADLE_PURIFIER_JDK_CONTEXT
  fi
}

prefetch_command() {
  local given=''
  while [ $# -gt 0 ]; do
    case $1 in
      --lockfile)
        [ $# -ge 2 ] || die "--lockfile takes a path"
        given=$2
        shift 2
        ;;
      -h | --help) usage; exit 0 ;;
      *) die "prefetch takes --lockfile <path>; got $1" ;;
    esac
  done
  set_paths "$given"
  [ -f "$lockfile" ] || die "the build has no $lockfile; run 'gradle-purifier lock'"

  local tree jdk distribution
  tree=$(build_attr repository)
  jdk=$(build_attr jdk)
  distribution=$(build_attr distribution)
  realise_sdk_context
  if [ "$(jq '.projectInputs // [] | length' "$lockfile")" -gt 0 ]; then
    jq '.projectInputs' "$lockfile" > "$state/inputs.json"
    rm -rf "$state/provided" "$state/input-metadata"
    python3 "$purifier_source/tools/project_inputs.py" maven --inputs "$state/inputs.json" \
      --destination "$state/provided" --metadata "$state/input-metadata" --roots "$state/inputs"
  fi

  # ./gradlew finds the distribution installed where it looks for it.
  local user_home=${GRADLE_USER_HOME:-$HOME/.gradle}
  local wrapper_jar paths dist_dir zip
  wrapper_jar=$(find "$distribution/lib" -maxdepth 1 -name 'gradle-wrapper-shared-*.jar' | head -n 1)
  if [ -n "$wrapper_jar" ] && [ -f gradle/wrapper/gradle-wrapper.properties ]; then
    mapfile -t paths < <("$(jdk_home "$jdk")/bin/java" -cp "$wrapper_jar" "$lib/WrapperDistribution.java" "$user_home" "$root")
    dist_dir=${paths[0]}
    zip=${paths[1]}
    mkdir -p "$dist_dir"
    ln -sfn "$distribution" "$dist_dir/$(basename "$zip" | sed 's/-\(bin\|all\)\.zip$//')"
    touch "$zip.ok"
  fi

  local properties=$root/.gradle/purifier/prefetch.properties
  mkdir -p "$(dirname "$properties")"
  {
    echo "# Written by gradle-purifier prefetch, for the init script in $user_home/init.d."
    echo "lockFile=$lockfile"
    echo "lockSha256=$(sha256sum "$lockfile" | cut -d ' ' -f 1)"
    echo "initScript=$lib/purifier.init.gradle"
    if [ -n "${GRADLE_PURIFIER_JDK_CONTEXT:-}" ]; then
      echo "gradle.purifier.jdk.context=$sdk_context"
    fi
    echo "portal=file://$tree/$portal"
    echo "gradle.purifier.repository=file://$tree"
    echo "gradle.purifier.resources=$lockfile"
    if [ "$(jq '.projectInputs // [] | length' "$lockfile")" -gt 0 ]; then
      echo "gradle.purifier.provided.repository=$state/provided"
      echo "gradle.purifier.provided.catalog=$state/input-metadata/catalog.json"
    fi
  } >"$properties"

  if [ "$(jq '[.artifacts[] | select(.url // "" | startswith("https://download.jetbrains.com/kotlin/native/"))] | length' "$lockfile")" -gt 0 ]; then
    local toolchain konan_dir host
    toolchain=$(build_attr konan)
    # The data directory of Kotlin/Native, beside the GC root of the
    # toolchain ($state/konan), whose files it links.
    konan_dir=$state/konan-data
    rm -rf "$konan_dir/dependencies"
    mkdir -p "$konan_dir/dependencies"
    local dependency
    for dependency in "$toolchain"/*; do
      ln -s "$dependency" "$konan_dir/dependencies/"
    done
    cp "$toolchain/.extracted" "$konan_dir/dependencies/.extracted"
    host=$(konan_host)
    {
      echo "gradle.purifier.konan.data.dir=$konan_dir"
      echo "gradle.purifier.konan.properties=dynamicLinker.$host=$dynamic_linker"
    } >>"$properties"
  fi

  # The init scripts run from the store; this root keeps them there.
  nix-store --add-root "$state/lib" --realise "$lib" >/dev/null
  mkdir -p "$user_home/init.d"
  install -m 0644 "$lib/user.init.gradle" "$user_home/init.d/gradle-purifier.gradle"

  echo "gradle-purifier: prefetched $lockfile. ./gradlew, a Gradle on the PATH and an IDE now build $root from the locked downloads, offline; the init script is $user_home/init.d/gradle-purifier.gradle. Run 'gradle-purifier prefetch' again after each lock." >&2
  local major
  major=$(jq -r '.javaMajor' "$lockfile")
  if [ -n "${JAVA_HOME:-}" ] && ! "$JAVA_HOME/bin/java" -version 2>&1 | grep -q "version \"${major}[.\"]"; then
    echo "gradle-purifier: the lock was made on Java $major and JAVA_HOME is another; for the same builds, export JAVA_HOME=$(jdk_home "$jdk")" >&2
  fi
}

# Builds the release with Nix, and runs the build's publish tasks
# (`publish`, or the gradle arguments given) with the files Nix built in
# place of the ones Gradle would make.
publish_command() {
  local given=''
  if [ "${1:-}" = --lockfile ]; then
    [ $# -ge 2 ] || die "--lockfile takes a path"
    given=$2
    shift 2
  fi
  set_paths "$given"
  [ -f "$lockfile" ] || die "the build has no $lockfile; run 'gradle-purifier lock'"
  local release tree jdk distribution publication
  release=$(build_attr release)
  # Gradle5.6 basic locks deliberately have no strict native metadata capability.
  # Reject before projection construction, including archive-only old locks.
  case "$(jq -r '.distribution' "$lockfile")" in
    */gradle-5.6.4-bin.zip|gradle-5.6.4-bin.zip)
      python3 "$lib/validate-legacy-native-metadata.py" --release-lock "$release" "$lockfile" \
        || die "Gradle5.6.4 strict legacy Maven publish is unsupported"
      ;;
  esac
  # Building release validates the lock before this publication check. Old
  # archive-only locks remain buildable and opt into publication by relocking.
  if ! jq -e '[.projects[].publications[]] | length > 0' "$lockfile" >/dev/null; then
    die "the lock declares no Maven publications; run 'gradle-purifier lock --relock' before publishing"
  fi
  publication=$(build_attr publication)
  tree=$(build_attr repository)
  jdk=$(build_attr jdk)
  distribution=$(build_attr distribution)
  realise_sdk_context
  if [ "$(jq '.projectInputs // [] | length' "$lockfile")" -gt 0 ]; then
    jq '.projectInputs' "$lockfile" > "$state/inputs.json"
    rm -rf "$state/provided" "$state/input-metadata"
    python3 "$purifier_source/tools/project_inputs.py" maven --inputs "$state/inputs.json" \
      --destination "$state/provided" --metadata "$state/input-metadata" --roots "$state/inputs"
  fi
  local artifacts='{}' package
  for package in "$release"/*; do
    [ -e "$package" ] || continue
    artifacts=$(jq --arg id "$(basename "$package")" --arg out "$(readlink -f "$package")" '. + {($id): $out}' <<<"$artifacts")
  done
  python3 "$lib/validate-legacy-native-metadata.py" --release-lock "$release" "$lockfile" \
    || die "required native legacy metadata is unavailable or invalid"
  [ $# -gt 0 ] || set -- publish
  # The init script disables the task that generates each publication's
  # Gradle module metadata, so Gradle does not publish its output, and
  # publishes the module metadata Nix built instead. Gradle still runs the
  # dependencies of a disabled task, which compile and package the
  # project; excluding the task with -x leaves them out.
  local excluded=() locked_args=()
  mapfile -t excluded < <(jq -r '.projects[] | (if .path == ":" then "" else .path end) as $p
    | .publications[] | select((.kind // "") != "legacy-maven-upload") | "-x", "\($p):generateMetadataFileFor\(.name[0:1] | ascii_upcase)\(.name[1:])Publication"' "$lockfile")
  mapfile -t locked_args < <(jq -r '.gradleArguments[]' "$lockfile")
  local gradle_flags=() publish_args_json
  publish_args_json=$(jq -n -c '$ARGS.positional' --args -- "${locked_args[@]}" "$@")
  GRADLE_PURIFIER_MODE='' JAVA_HOME=$(jdk_home "$jdk") \
    python3 "$lib/gradle-flags.py" publish "$state/publish-gradle-help.txt" --gradle "$distribution/bin/gradle" --arguments-json "$publish_args_json" >"$state/publish-gradle-flags.txt" \
    || die "could not select native Gradle controls"
  mapfile -t gradle_flags <"$state/publish-gradle-flags.txt"
  JAVA_HOME=$(jdk_home "$jdk") \
    GRADLE_PURIFIER_MODE=publish \
    GRADLE_PURIFIER_BUILD_ROOT=$root \
    GRADLE_PURIFIER_REPOSITORY=file://$tree \
    GRADLE_PURIFIER_ARTIFACTS=$artifacts \
    GRADLE_PURIFIER_LEGACY_PUBLICATIONS=$(jq -c '[.projects[] | .path as $path | .publications[] | select(.kind == "legacy-maven-upload") | {path:$path,publication:.}]' "$lockfile") \
    GRADLE_PURIFIER_PUBLICATION_REPOSITORY=$publication/repository \
    GRADLE_PURIFIER_PUBLICATION_CATALOG=$publication/catalog.json \
    exec "$distribution/bin/gradle" \
    --init-script "$lib/purifier.init.gradle" \
    --init-script "$lib/task-jdks.init.gradle" \
    "-Dorg.gradle.internal.plugins.portal.url.override=file://$tree/$portal" \
    "${locked_args[@]}" "${excluded[@]}" "$@" --offline "${gradle_flags[@]}"
}

# The unit tests (tests/cli-unit.sh) source the command for its functions.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case ${1:-} in
    lock) shift; lock_command "$@" ;;
    update) shift; update_command "$@" ;;
    prefetch) shift; prefetch_command "$@" ;;
    publish) shift; publish_command "$@" ;;
    -h | --help | help) usage ;;
    '') usage; exit 1 ;;
    *) usage >&2; die "the commands are lock, update, prefetch and publish; for the build itself, run ./gradlew $* after 'gradle-purifier prefetch'" ;;
  esac
fi
