# The hash of a Gradle build's definition: the files whose change means
# that its lock must be made again. `gradle-purifier lock` records it as
# the lockfile's inputsHash, and buildGradleProject compares the two, so a
# lockfile older than the build fails evaluation. Both evaluate this file,
# so they agree by construction.
#
# The definition is every settings and build script, gradle.properties,
# every version catalog, the wrapper properties, every init script and
# every lockfile of Gradle's dependency locking,
# anywhere outside Gradle's state, build outputs and version control; and
# every file of buildSrc and of each included build (a directory below the
# root with a settings script), outside their own state and build
# outputs. The Gradle arguments of the lock count too.
#
# The hash is SHA-256 of the arguments, one per line, then a line
# "<path>\t<SHA-256 of the file>" for each file of the definition, in
# byte order of the paths.
{
  lib,
  # The root of the Gradle build, as a path.
  src,
  gradleArguments,
}:
let
  inherit (builtins)
    attrNames
    concatStringsSep
    hashFile
    hashString
    pathExists
    readDir
    ;

  # Version control, the state of Gradle, of Kotlin, of IntelliJ and of
  # gradle-purifier (whose lock run holds a copy of the build), build
  # outputs and npm packages.
  pruned = [
    ".git"
    ".gradle"
    ".gradle-purifier"
    ".kotlin"
    ".idea"
    "build"
    "node_modules"
  ];
  prunedInBuilds = [
    ".gradle"
    ".gradle-purifier"
    ".kotlin"
    "build"
  ];
  definitionNames = [
    "settings.gradle"
    "settings.gradle.kts"
    "build.gradle"
    "build.gradle.kts"
    "gradle.properties"
    "gradle-wrapper.properties"
    "gradle-purifier.sdks.json"
  ];
  # The lockfiles of Gradle's dependency locking are gradle.lockfile, and
  # <name>-gradle.lockfile for the settings, the build scripts and the
  # lock files a build names.
  isDefinition =
    name:
    lib.elem name definitionNames
    || lib.hasSuffix ".versions.toml" name
    || lib.hasSuffix ".init.gradle" name
    || lib.hasSuffix "gradle.lockfile" name;

  # The files below `rel` (a path relative to src, "" for src itself) that
  # `keep` keeps, skipping directories named in `skip`.
  walk =
    skip: keep: rel:
    let
      dir = if rel == "" then src else src + "/${rel}";
      children = readDir dir;
    in
    lib.concatMap (
      name:
      let
        type = children.${name};
        path = if rel == "" then name else "${rel}/${name}";
      in
      if type == "directory" then
        if lib.elem name skip then [ ] else walk skip keep path
      else if keep name then
        [ path ]
      else
        [ ]
    ) (attrNames children);

  definitionFiles = walk pruned isDefinition "";

  # buildSrc, and the directories below the root with a settings script.
  builds =
    lib.optional (pathExists (src + "/buildSrc")) "buildSrc"
    ++ lib.unique (
      map dirOf (
        lib.filter (
          path:
          path != "settings.gradle"
          && path != "settings.gradle.kts"
          && lib.elem (baseNameOf path) [
            "settings.gradle"
            "settings.gradle.kts"
          ]
        ) definitionFiles
      )
    );
  buildFiles = lib.concatMap (walk prunedInBuilds (_: true)) builds;

  files = lib.sort (a: b: a < b) (lib.unique (definitionFiles ++ buildFiles));
in
hashString "sha256" (
  concatStringsSep "" (map (a: "${a}\n") gradleArguments)
  + concatStringsSep "" (map (path: "${path}\t${hashFile "sha256" (src + "/${path}")}\n") files)
)
