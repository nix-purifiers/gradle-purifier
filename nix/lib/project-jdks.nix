# Reconstruct typed Nix SDK inputs; JSON paths alone never become build inputs.
{ lib, purifier }:
{ contexts, system }:
let
  problem =
    message:
    throw "gradle-purifier: ${message}; run 'gradle-purifier lock --relock' with the project's native tasks and declared Nix SDKs.";
  context = contexts.${system} or (problem "the project SDK lock has no context for ${system}");
  ids = map (sdk: sdk.identityHash) context.sdks;
  roleKeys = map (
    role:
    builtins.toJSON [
      role.task
      role.role
    ]
  ) context.roles;
  resolve =
    record:
    let
      source = builtins.fetchTree record.source;
      pkgs = import source.outPath {
        inherit system;
        config = { };
        overlays = [ ];
      };
      sdk =
        lib.attrByPath record.attribute (problem "the recorded project SDK attribute is unavailable")
          pkgs;
      home = sdk.home or "${sdk}/lib/openjdk";
      identity = purifier.inputIdentity sdk;
    in
    assert source.narHash == record.source.narHash || problem "the project SDK source hash differs";
    assert
      toString sdk == record.output
      || problem "the project SDK output differs from its recorded identity";
    assert home == record.home || problem "the project SDK home differs from its recorded identity";
    assert identity.hash == record.identityHash || problem "the project SDK Nix identity differs";
    sdk;
  jdks = map resolve context.sdks;
  byIdentity = builtins.listToAttrs (
    lib.imap0 (index: record: {
      name = record.identityHash;
      value = builtins.elemAt jdks index;
    }) context.sdks
  );
  roles = map (
    role:
    let
      sdk = byIdentity.${role.sdk} or (problem "a project SDK task names an unknown SDK");
    in
    {
      inherit (role) task role provenance;
      home = sdk.home or "${sdk}/lib/openjdk";
    }
  ) context.roles;
in
assert
  builtins.length ids == builtins.length (lib.unique ids)
  || problem "duplicate project SDK identities";
assert
  builtins.length roleKeys == builtins.length (lib.unique roleKeys)
  || problem "duplicate project SDK task roles";
assert
  lib.all (role: builtins.elem role.sdk ids) context.roles
  || problem "project SDK role references an unknown identity";
{
  inherit jdks roles;
}
