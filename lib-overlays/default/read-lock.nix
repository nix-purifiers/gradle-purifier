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

  isIdentityHash = value: isString value && match "[0-9a-f]{64}" value != null;
  isSdkSource =
    source:
    isAttrs source
    && source.type or null == "github"
    && source.owner or null == "NixOS"
    && source.repo or null == "nixpkgs"
    && isString (source.rev or null)
    && match "[0-9a-f]{40}" source.rev != null
    && isString (source.narHash or null)
    && match "sha256-[A-Za-z0-9+/]{43}=" source.narHash != null;
  isSdk =
    sdk:
    isAttrs sdk
    && !(sdk ? resolver || sdk ? selectionKind || sdk ? identityVersion || sdk ? flakeLock)
    && isSdkSource (sdk.source or null)
    && isList (sdk.attribute or null)
    && sdk.attribute != [ ]
    && lib.all isString sdk.attribute
    && isString (sdk.output or null)
    && isString (sdk.home or null)
    && isIdentityHash (sdk.identityHash or null);
  isSdkRole =
    role:
    isAttrs role
    && isString (role.task or null)
    && builtins.elem (role.role or null) [
      "compiler"
      "launcher"
      "javadoc"
    ]
    && isIdentityHash (role.sdk or null)
    && (
      role.provenance or null == "resolved-selected-provider"
      || (
        role.provenance or null == "gradle-5.6.4-configured-javadoc-executable-v1"
        && role.role == "javadoc"
        && baseNameOf (raw.distribution or "") == "gradle-5.6.4-bin.zip"
      )
      || (
        role.provenance or null == "configured-native-executable"
        && builtins.elem role.role [
          "compiler"
          "launcher"
        ]
      )
      || (role.provenance or null == "native-effective-fork-options" && role.role == "launcher")
    );
  keysAre =
    value: keys: isAttrs value && builtins.attrNames value == builtins.sort builtins.lessThan keys;
  isExplicitSdkSource =
    source:
    keysAre source [
      "type"
      "owner"
      "repo"
      "rev"
      "narHash"
    ]
    && source.type == "github"
    &&
      lib.all
        (
          key:
          isString source.${key}
          && match "[A-Za-z0-9_.-]+" source.${key} != null
          && !(builtins.elem source.${key} [
            "."
            ".."
          ])
        )
        [
          "owner"
          "repo"
        ]
    && isString source.rev
    && match "[0-9a-f]{40}" source.rev != null
    && isString source.narHash
    && match "sha256-[A-Za-z0-9+/]{43}=" source.narHash != null;
  isSourceLock =
    value:
    isAttrs value
    && (
      (keysAre value [ "state" ] && value.state == "absent-no-external-inputs")
      || (
        keysAre value [
          "state"
          "sha256"
        ]
        && value.state == "present"
        && isIdentityHash value.sha256
      )
    );
  isExplicitSdk =
    sdk:
    keysAre sdk [
      "id"
      "resolver"
      "source"
      "system"
      "selectionKind"
      "attribute"
      "output"
      "home"
      "identityVersion"
      "identityHash"
      "flakeLock"
    ]
    && isString sdk.id
    && match "[A-Za-z0-9_-]+" sdk.id != null
    && sdk.resolver == "immutable-flake-attribute-v1"
    && isExplicitSdkSource sdk.source
    && sdk.system == "x86_64-linux"
    && builtins.elem sdk.selectionKind [
      "derivation-v1"
      "jvm-sdk-adapter-v1"
    ]
    && isList sdk.attribute
    && sdk.attribute != [ ]
    && lib.all (x: isString x && x != "") sdk.attribute
    && isString sdk.output
    && lib.hasPrefix "/nix/store/" sdk.output
    && isString sdk.home
    && lib.hasPrefix "/nix/store/" sdk.home
    && sdk.identityVersion == "sdk-output-home-context-v1"
    && isIdentityHash sdk.identityHash
    && isSourceLock sdk.flakeLock;
  isExplicitManager =
    manager:
    keysAre manager [
      "version"
      "sdk"
      "executable"
      "runtimeHome"
      "identityHash"
    ]
    && manager.version == "jvm-manager-executable-runtime-home-v1"
    && isIdentityHash manager.sdk
    && isIdentityHash manager.identityHash
    && isString manager.executable
    && lib.hasPrefix "/nix/store/" manager.executable
    && isString manager.runtimeHome
    && lib.hasPrefix "/nix/store/" manager.runtimeHome;
  isExplicitSdkRole =
    role:
    keysAre role [
      "task"
      "role"
      "sdk"
      "provenance"
    ]
    && isSdkRole role;
  isExplicitSdkContext =
    context:
    keysAre context [
      "version"
      "declarationHash"
      "sdks"
      "manager"
      "roles"
    ]
    && context.version == "jvm-sdk-context-v2"
    && isIdentityHash context.declarationHash
    && isList context.sdks
    && context.sdks != [ ]
    && lib.all isExplicitSdk context.sdks
    && isExplicitManager context.manager
    && isList context.roles
    && lib.all isExplicitSdkRole context.roles;
  isSdkContext =
    context:
    isAttrs context
    && (
      if context ? version then
        isExplicitSdkContext context
      else
        !(context ? manager || context ? declarationHash)
        && isList (context.sdks or null)
        && lib.all isSdk context.sdks
        && isList (context.roles or null)
        && lib.all isSdkRole context.roles
    );
  sdkContextProblem =
    context:
    if !isSdkContext context then
      "has malformed project SDK contexts"
    else
      let
        ids = map (sdk: sdk.identityHash) context.sdks;
        roleKeys = map (
          role:
          toJSON [
            role.task
            role.role
          ]
        ) context.roles;
      in
      if builtins.length ids != builtins.length (lib.unique ids) then
        "has duplicate project SDK identities"
      else if builtins.length roleKeys != builtins.length (lib.unique roleKeys) then
        "has duplicate project SDK task roles"
      else if
        context ? version
        && (
          !(builtins.elem context.manager.sdk ids)
          ||
            builtins.length (map (sdk: sdk.id) context.sdks)
            != builtins.length (lib.unique (map (sdk: sdk.id) context.sdks))
          ||
            builtins.length (map (sdk: sdk.home) context.sdks)
            != builtins.length (lib.unique (map (sdk: sdk.home) context.sdks))
        )
      then
        "has an absent manager or ambiguous declared SDKs"
      else if !(lib.all (role: builtins.elem role.sdk ids) context.roles) then
        "has a project SDK role referencing an unknown SDK identity"
      else
        null;
  hasExplicitSdkContext =
    isAttrs (raw.projectJdks or null)
    && lib.any (context: isAttrs context && (context.version or null) == "jvm-sdk-context-v2") (
      builtins.attrValues raw.projectJdks
    );
  projectJdkProblem =
    if !(raw ? projectJdks) then
      null
    else if !isAttrs raw.projectJdks then
      "has malformed project SDK contexts"
    else if hasExplicitSdkContext && builtins.attrNames raw.projectJdks != [ "x86_64-linux" ] then
      "has unsupported or mixed explicit SDK execution contexts"
    else
      lib.findFirst (message: message != null) null (
        map sdkContextProblem (builtins.attrValues raw.projectJdks)
      );

  isSha256Sri = value: isString value && match "sha256-[A-Za-z0-9+/]{43}=" value != null;
  validRelativeBuild =
    value:
    isString value
    && (
      value == "."
      || (
        value != ""
        && !(lib.hasPrefix "/" value)
        && !(lib.hasInfix "\\" value)
        && match ".*[[:cntrl:]].*" value == null
        && lib.all (
          segment:
          !(builtins.elem segment [
            ""
            "."
            ".."
          ])
        ) (lib.splitString "/" value)
      )
    );
  validTask =
    value:
    isString value
    && lib.hasPrefix ":" value
    && value != ":"
    && !(lib.hasSuffix ":" value)
    && !(lib.hasInfix "::" value)
    && !(lib.hasInfix "/" value)
    && !(lib.hasInfix "\\" value)
    && match ".*[[:cntrl:]].*" value == null;
  validLinkUrl =
    value:
    if !isString value then
      false
    else
      let
        parts = match "[Hh][Tt][Tt][Pp]([Ss])?://([^/?#]+)(/[^?#]*)?" value;
        authority = if parts == null then "" else elemAt parts 1;
        hostPort = match "([A-Za-z0-9.-]+)(:([0-9]+))?" authority;
        port = if hostPort == null then null else elemAt hostPort 2;
        path = if parts == null || elemAt parts 2 == null then "" else elemAt parts 2;
        decoded =
          builtins.replaceStrings [ "%2e" "%2E" "%2f" "%2F" "%5c" "%5C" ] [ "." "." "/" "/" "\\" "\\" ]
            path;
      in
      parts != null
      && hostPort != null
      && match ".*[[:cntrl:][:space:]].*" value == null
      && !(lib.hasInfix "\\" decoded)
      && !(lib.hasInfix "//" decoded)
      && lib.all (
        segment:
        !(builtins.elem segment [
          "."
          ".."
        ])
      ) (lib.splitString "/" decoded)
      && match ".*%([01][0-9a-f]|7f|c2%[89][0-9a-f]).*" (lib.toLower path) == null
      && (
        port == null
        || (
          builtins.stringLength port <= 5
          && match "(0|[1-9][0-9]*)" port != null
          && builtins.fromJSON port <= 65535
        )
      );
  isResource =
    resource:
    isAttrs resource
    &&
      builtins.attrNames resource == [
        "artifactUrl"
        "build"
        "hash"
        "kind"
        "linkUrl"
        "task"
      ]
    && (resource.kind or null) == "javadoc-link-metadata"
    && validRelativeBuild resource.build
    && validTask resource.task
    && validLinkUrl resource.linkUrl
    && isString resource.artifactUrl
    && builtins.elem resource.artifactUrl (
      map
        (
          filename:
          resource.linkUrl + lib.optionalString (!(lib.hasSuffix "/" resource.linkUrl)) "/" + filename
        )
        [
          "element-list"
          "package-list"
        ]
    )
    && isSha256Sri resource.hash;
  resourceProblem =
    if !(isList (raw.resources or [ ]) && lib.all isResource (raw.resources or [ ])) then
      "has an invalid external resource role"
    else
      let
        keys = map (
          resource:
          toJSON [
            resource.build
            resource.task
            resource.linkUrl
          ]
        ) (raw.resources or [ ]);
      in
      if builtins.length keys != builtins.length (lib.unique keys) then
        "has duplicate external resource roles"
      else
        null;

  isMavenComponent = value: isString value && match "[A-Za-z0-9_][A-Za-z0-9_.-]*" value != null;
  validProjectPath =
    value:
    isString value
    && (
      value == ":"
      || (
        validTask value
        && lib.all (
          segment:
          !(builtins.elem segment [
            "."
            ".."
          ])
        ) (lib.splitString ":" (lib.removePrefix ":" value))
      )
    );
  isLegacyPublication =
    project: publication:
    isAttrs publication
    && validProjectPath project.path
    &&
      builtins.attrNames (builtins.removeAttrs publication [ "nativeMetadata" ]) == [
        "artifactId"
        "artifacts"
        "configuration"
        "groupId"
        "kind"
        "name"
        "uploadTask"
        "version"
      ]
    && (
      !(publication ? nativeMetadata)
      || (
        isAttrs publication.nativeMetadata
        &&
          builtins.attrNames publication.nativeMetadata == [
            "kind"
            "version"
          ]
        && publication.nativeMetadata.kind == "gradle-java-component"
        && isInt publication.nativeMetadata.version
        && publication.nativeMetadata.version == 1
      )
    )
    && publication.kind == "legacy-maven-upload"
    && publication.name == "legacyUploadArchives"
    && publication.configuration == "archives"
    &&
      publication.uploadTask
      == (if project.path == ":" then ":uploadArchives" else "${project.path}:uploadArchives")
    && lib.all isMavenComponent [
      publication.groupId
      publication.artifactId
      publication.version
    ]
    &&
      publication.artifacts == [
        {
          classifier = null;
          extension = "jar";
        }
      ];
  publicationProblem =
    if
      !(
        isList (raw.projects or null)
        && lib.all (
          project:
          isAttrs project
          && isString (project.path or null)
          && isList (project.publications or null)
          && lib.all (
            publication:
            isAttrs publication
            && (
              !(
                publication ? kind
                || publication ? uploadTask
                || publication ? configuration
                || publication ? nativeMetadata
              )
              || isLegacyPublication project publication
            )
          ) project.publications
        ) raw.projects
      )
    then
      "has an invalid typed publication binding"
    else
      let
        legacy = lib.concatMap (
          project:
          map (publication: {
            path = project.path;
            inherit publication;
          }) (filter (publication: publication.kind or null == "legacy-maven-upload") project.publications)
        ) raw.projects;
        keys = map (binding: binding.publication.uploadTask) legacy;
        coordinates = map (
          binding:
          toJSON [
            binding.publication.groupId
            binding.publication.artifactId
            binding.publication.version
          ]
        ) legacy;
        distribution = baseNameOf (raw.distribution or "");
        paths = map (project: project.path) raw.projects;
        ids = map idOf paths;
      in
      if
        builtins.length keys != builtins.length (lib.unique keys)
        || builtins.length coordinates != builtins.length (lib.unique coordinates)
        || (
          legacy != [ ]
          && (
            !(builtins.elem distribution [
              "gradle-5.6.4-bin.zip"
              "gradle-6.9.4-bin.zip"
            ])
            || (
              distribution == "gradle-5.6.4-bin.zip"
              && lib.any (binding: binding.publication ? nativeMetadata) legacy
            )
            || !(lib.all validProjectPath paths)
            || !(validProjectPath ":${raw.rootProjectName}")
            || builtins.length paths != builtins.length (lib.unique paths)
            || builtins.length ids != builtins.length (lib.unique ids)
          )
        )
      then
        "has duplicate or unsupported legacy publication bindings"
      else
        null;

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
    else if publicationProblem != null then
      publicationProblem
    else if projectJdkProblem != null then
      projectJdkProblem
    else if resourceProblem != null then
      resourceProblem
    else if contentHashOf raw != raw.contentHash then
      "does not match its contentHash, so it was edited"
    else
      null;

  usable = structureProblem == null;

  stale =
    usable
    && (builtins.isPath src || hasExplicitSdkContext)
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
    resources = raw.resources or [ ];
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
