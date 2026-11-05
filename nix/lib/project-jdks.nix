# Reconstruct typed Nix SDK inputs; JSON paths alone never become build inputs.
{ lib, purifier }:
{ contexts, system }:
let
  problem =
    message:
    throw "gradle-purifier: ${message}; run 'gradle-purifier lock --relock' with the project's native tasks and declared Nix SDKs.";
  context = contexts.${system} or (problem "the project SDK lock has no context for ${system}");
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
{
  inherit jdks roles;
}
