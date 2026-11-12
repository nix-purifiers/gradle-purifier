# Reconstruct explicit project-owned SDK sources with live Nix contexts.
{ lib, purifier }:
{
  declaration,
  declarationHash,
  system,
}:
let
  problem = message: throw "gradle-purifier: project SDK sources ${message}";
  keysAre =
    value: keys:
    builtins.isAttrs value && builtins.attrNames value == builtins.sort builtins.lessThan keys;
  selections = declaration.sdks;
  selectionIds = map (selection: selection.id) selections;
  resolved = map (selection: purifier.jvmSdkResolve { inherit selection system; }) selections;
  records = lib.imap0 (index: selected: {
    id = (builtins.elemAt selections index).id;
    resolver = "immutable-flake-attribute-v1";
    inherit (selected)
      source
      system
      selectionKind
      attribute
      ;
    inherit (selected.identity.payload) output home;
    identityVersion = selected.identity.payload.version;
    identityHash = selected.identity.identityHash;
    flakeLock = selected.sourceLock;
  }) resolved;
  managerSelected = lib.findFirst (
    selected: selected.id == declaration.manager
  ) (problem "manager is unavailable") resolved;
  managerIdentity = managerSelected.identity.managerIdentity;
  manager = {
    version = managerIdentity.payload.version;
    sdk = managerSelected.identity.identityHash;
    inherit (managerIdentity.payload) executable runtimeHome;
    identityHash = managerIdentity.identityHash;
  };
  homes = map (selected: selected.home) resolved;
  identities = map (record: record.identityHash) records;
  context = {
    version = "jvm-sdk-context-v2";
    inherit declarationHash manager;
    sdks = records;
    roles = [ ];
  };
  sourceInputs = lib.unique (lib.concatMap (selected: selected.sourceInputs) resolved);
  closureInputs = lib.concatMap (selected: selected.closureInputs) resolved;
in
assert
  keysAre declaration [
    "version"
    "system"
    "sdks"
    "manager"
  ]
  || problem "declaration has unknown or missing fields";
assert builtins.isList selections || problem "SDK selections must be a list";
assert
  builtins.all (id: builtins.isString id && builtins.match "[A-Za-z0-9_-]+" id != null) selectionIds
  || problem "SDK ids are invalid";
assert
  builtins.length selectionIds == builtins.length (lib.unique selectionIds)
  || problem "SDK ids are ambiguous";
assert builtins.isString declaration.manager || problem "manager must select one SDK id";
assert
  builtins.isString declarationHash && builtins.match "[0-9a-f]{64}" declarationHash != null
  || problem "declaration hash is invalid";
assert
  builtins.isInt declaration.version && declaration.version == 1
  || problem "declaration version differs";
assert
  declaration.system == system && system == "x86_64-linux"
  || problem "execution system is unsupported";
assert builtins.length selections > 0 || problem "declaration has no SDKs";
assert
  builtins.length identities == builtins.length (lib.unique identities)
  || problem "declaration has ambiguous SDK identities";
assert
  builtins.length homes == builtins.length (lib.unique homes)
  || problem "declaration has ambiguous SDK homes";
assert managerIdentity != null || problem "manager needs a source-owned adapter";
{
  inherit
    context
    resolved
    sourceInputs
    closureInputs
    ;
  jdks = map (selected: selected.sdk) resolved;
  managerSdk = managerSelected.sdk;
  managerSdkHome = managerSelected.home;
  runtimeContext = context // {
    inherit sourceInputs closureInputs;
    paths = homes;
    managerHome = manager.runtimeHome;
    launchHome = managerSelected.home;
  };
}
