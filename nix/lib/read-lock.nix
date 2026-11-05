# readLock: a Gradle build's lock in memory, as the nix-purifiers contract
# describes it (CONTRACT.md of the purifier library):
#
#   { purifier = "gradle-purifier"; entries; outputs; problems; }
#
# plus `gradle`, what buildGradleProject needs beyond that shape.
#
# Gradle's own files record no download URLs, so everything comes from
# gradle-purifier.lock.json, which `gradle-purifier lock` writes. It records
# only what Gradle's files cannot: each download by URL and hash, the
# outputs of each project, the Gradle arguments of the lock, the Java major
# version, the host of a Kotlin/Native toolchain and the Gradle
# distribution. This file derives the rest: the path of each download in
# the tree of locked downloads (its URL without "://"), its mirrors, the
# name of each project, the key of each published artifact, and the Gradle
# version.
#
# `problems` holds a message, naming the command that fixes it, for a
# lockfile of another format version, with a missing field or a malformed
# entry, whose content does not match its contentHash, or that is older
# than the build definition. The last needs the build root, so it is
# checked when `src` is a path. A lockfile that is not valid JSON fails
# evaluation at once, with the same advice.
{ lib }:
{
  src,
  lockFile ? src + "/gradle-purifier.lock.json",
}:
let
  inherit (builtins)
    elemAt
    filter
    hashString
    isAttrs
    isInt
    isList
    isString
    match
    toJSON
    ;

  fix = "run 'gradle-purifier lock --relock', or restore the lockfile from version control";
  problem = text: "gradle-purifier: ${toString lockFile} ${text}; ${fix}.";

  raw = builtins.addErrorContext "while reading ${toString lockFile} as a gradle-purifier lockfile; if it is not valid JSON, ${fix}" (
    builtins.fromJSON (builtins.readFile lockFile)
  );

  supportedVersion = import ./lock-version.nix;

  # The hash of a lockfile's content: SHA-256 of its JSON without the
  # contentHash field, with sorted keys and no spaces, as builtins.toJSON
  # writes it. `gradle-purifier lock` computes the same with jq.
  contentHashOf = l: "sha256:" + hashString "sha256" (toJSON (removeAttrs l [ "contentHash" ]));

  isArtifact =
    a:
    isAttrs a
    && (
      (isString (a.url or null) && isString (a.hash or null))
      || (isString (a.path or null) && isString (a.contents or null))
    );

  isSdkSource =
    source:
    isAttrs source
    && source.type or null == "github"
    && source.owner or null == "NixOS"
    && source.repo or null == "nixpkgs"
    && isString (source.rev or null)
    && match "[0-9a-f]{40}" source.rev != null
    && isString (source.narHash or null);
  isSdk =
    sdk:
    isAttrs sdk
    && isSdkSource (sdk.source or null)
    && isList (sdk.attribute or null)
    && sdk.attribute != [ ]
    && lib.all isString sdk.attribute
    && isString (sdk.output or null)
    && isString (sdk.home or null)
    && isString (sdk.identityHash or null);
  isSdkRole =
    role:
    isAttrs role
    && isString (role.task or null)
    && builtins.elem (role.role or null) [
      "compiler"
      "launcher"
      "javadoc"
    ]
    && isString (role.sdk or null)
    && role.provenance or null == "resolved-selected-provider";
  isSdkContext =
    context:
    isAttrs context
    && isList (context.sdks or null)
    && lib.all isSdk context.sdks
    && isList (context.roles or null)
    && lib.all isSdkRole context.roles;
  validProjectJdks =
    !(raw ? projectJdks)
    || (isAttrs raw.projectJdks && lib.all isSdkContext (builtins.attrValues raw.projectJdks));

  # The first problem of the lockfile's structure, or null.
  structureProblem =
    if !isAttrs raw then
      "is not a JSON object"
    else if (raw.version or null) != supportedVersion then
      "has lock format version ${toJSON (raw.version or null)}, and gradle-purifier reads version ${toString supportedVersion}"
    else if
      !(
        isString (raw.contentHash or null)
        && isString (raw.inputsHash or null)
        && isString (raw.distribution or null)
        && isString (raw.rootProjectName or null)
        && raw ? konanHost
        && (raw.konanHost == null || isString raw.konanHost)
        && isInt (raw.javaMajor or null)
        && isList (raw.gradleArguments or null)
        && isList (raw.projects or null)
        && isList (raw.artifacts or null)
      )
    then
      "lacks a field of a gradle-purifier lockfile"
    else if !(lib.all isArtifact raw.artifacts) then
      "has an artifact that is neither { url, hash } nor { path, contents }"
    else if !validProjectJdks then
      "has malformed project SDK contexts"
    else if contentHashOf raw != raw.contentHash then
      "does not match its contentHash, so it was edited"
    else
      null;

  usable = structureProblem == null;

  stale =
    usable
    && builtins.isPath src
    &&
      import ./inputs.nix {
        inherit lib src;
        inherit (raw) gradleArguments;
      } != raw.inputsHash;

  problems =
    if !usable then
      [ (problem structureProblem) ]
    else if stale then
      [
        "gradle-purifier: the build definition at ${toString src} changed since ${toString lockFile} was made; run 'gradle-purifier lock'."
      ]
    else
      [ ];

  # The path of a download in the tree of locked downloads.
  treePath =
    url:
    let
      m = match "([a-z]+)://(.*)" url;
    in
    if m == null then
      throw (problem "has the URL ${url}, which is not http or https")
    else
      "${elemAt m 0}/${elemAt m 1}";

  # Google's mirror of Maven Central serves every file of Maven Central,
  # under either of its names.
  centralPrefixes = [
    "https://repo.maven.apache.org/maven2/"
    "https://repo1.maven.org/maven2/"
  ];
  centralMirror = "https://maven-central.storage-download.googleapis.com/maven2/";
  mirrorsOf =
    url:
    lib.concatMap (
      prefix: lib.optional (lib.hasPrefix prefix url) (centralMirror + lib.removePrefix prefix url)
    ) centralPrefixes;

  # Kotlin/Native downloads its toolchain from here. The toolchain runs on
  # the machine it was made for, so its entries are needed there only.
  # konanHost names that machine, and is null in the lock of a build
  # without Kotlin/Native.
  konanPrefix = "https://download.jetbrains.com/kotlin/native/";
  konanSystem =
    if raw.konanHost == null then
      throw (problem "holds a Kotlin/Native toolchain and no konanHost")
    else
      {
        linux_x64 = "x86_64-linux";
        linux_arm64 = "aarch64-linux";
      }
      .${raw.konanHost} or raw.konanHost;

  # The entries of the purifier library: a file entry for each download,
  # and the inline entries as they are.
  entries = map (
    a:
    if a ? contents then
      { inherit (a) path contents; }
    else
      {
        path = treePath a.url;
        inherit (a) url hash;
        mirrors = mirrorsOf a.url;
      }
      // lib.optionalAttrs (lib.hasPrefix konanPrefix a.url) { systems = [ konanSystem ]; }
  ) raw.artifacts;

  idOf =
    path:
    if path == ":" then
      raw.rootProjectName
    else
      lib.replaceStrings [ ":" ] [ "-" ] (lib.removePrefix ":" path);

  # The file name of a published artifact without the project's version.
  keyOf =
    publication: a:
    publication.artifactId
    + lib.optionalString (a.classifier != null) "-${a.classifier}"
    + ".${a.extension}";

  outputs = builtins.listToAttrs (
    map (p: {
      name = idOf p.path;
      value = p // {
        id = idOf p.path;
        publications = map (
          publication:
          publication // { artifacts = map (a: a // { key = keyOf publication a; }) publication.artifacts; }
        ) p.publications;
      };
    }) raw.projects
  );

  gradleVersion =
    let
      m = match "gradle-(.*)-(bin|all)\\.zip" (baseNameOf raw.distribution);
    in
    if m == null then "unknown" else elemAt m 0;
in
{
  purifier = "gradle-purifier";
  entries = if usable then entries else [ ];
  outputs = if usable then outputs else { };
  inherit problems;
  gradle = lib.optionalAttrs usable {
    inherit (raw)
      javaMajor
      distribution
      gradleArguments
      konanHost
      rootProjectName
      ;
    inherit gradleVersion;
    projectJdks = raw.projectJdks or null;
    konanEntries = filter (e: e ? url && lib.hasPrefix konanPrefix e.url) entries;
  };
}
