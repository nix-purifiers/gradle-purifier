# The Kotlin/Native toolchain of a lockfile, as the `dependencies`
# directory of a Kotlin/Native data directory (KONAN_DATA_DIR): every
# Kotlin/Native archive of the lock unpacked into a directory of its name,
# and `.extracted` naming them, which tells Kotlin/Native that they need
# no download.
#
# Kotlin/Native's archives hold prebuilt programs (LLVM's clang and lld,
# a GCC toolchain) that expect a conventional dynamic loader and system
# libraries. autoPatchelf points them at nixpkgs' loader and libraries, so
# they run in the sandbox. The GCC toolchain also holds the sysroot of
# the target: a glibc 2.19 that the programs Kotlin/Native links are
# linked against. autoPatchelf resolves libraries from every file it
# patches, so it patches everything but the sysroot, which stays as it is
# and is not where the toolchain's own libraries come from.
#
# It is a package for callPackage, with the archives among its arguments
# (buildGradleProject fetches them).
{
  lib,
  stdenv,
  autoPatchelfHook,
  zlib,
  ncurses,
  libxml2,
  # The archives, each as { name, src }: the name Kotlin/Native gives the
  # extracted directory, and the fetched archive.
  archives,
}:
let
  nameOf = archive: archive.name;
in
stdenv.mkDerivation {
  name = "kotlin-native-dependencies";
  srcs = map (archive: archive.src) archives;
  dontUnpack = true;
  nativeBuildInputs = [ autoPatchelfHook ];
  buildInputs = [
    stdenv.cc.cc.lib
    zlib
    ncurses
    libxml2
  ];
  # Some libraries of the toolchain, such as lldb's Python support, need
  # what no build uses.
  autoPatchelfIgnoreMissingDeps = true;
  # The fixup leaves the archives as they are (the target sysroot's
  # libraries and objects are link inputs); postFixup patches the
  # toolchain's own programs.
  dontAutoPatchelf = true;
  dontStrip = true;
  dontPatchELF = true;
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    for archive in $srcs; do
      tar -xzf "$archive" -C "$out"
    done
    printf '%s\n' ${lib.escapeShellArgs (map nameOf archives)} > "$out/.extracted"
    runHook postInstall
  '';
  postFixup = ''
    mapfile -t programs < <(find "$out" -name sysroot -prune -o -type f \( -perm -u+x -o -name '*.so*' \) -print)
    autoPatchelf -- "''${programs[@]}"
  '';
  passthru.names = map nameOf archives;
}
