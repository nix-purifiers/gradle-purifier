# Gradle epoch probes

These isolated probes separate an upstream native Gradle control from the
unchanged purifier. They do not select a production SDK, modify the builder,
or advertise supported ranges. Run them on x86 Linux. The legacy Java packages
are headless probe inputs, not general GUI SDK packages.

`profiles.json` pins official Gradle distribution URLs and publisher SHA256s.
The initial inventory spans 0.7, 0.9.2, 1.12, 2.14.1, 3.5.1, 4.10.3, 5.6.4,
6.9.4, 7.6.6, 8.14.3, 9.8.2 and the active 9.9.0-rc-1. Java 6/7 binary SDKs
are pinned separately in `sdks.nix`; Java 8/11/17/21 use this flake's own locked
package set. Manager runtime, project compiler and project runtime are separate
profile selections, including Java 21 running Gradle with Java 8 compiling and
executing the project.

The native fixture compiles a library, executes a real asserting Java check,
publishes a Maven JAR and compiles/runs an ordinary consumer of that JAR.
It deliberately uses an asserting JavaExec/Ant check rather than JUnit; the
JUnit task is disabled. Gradle 0.7 uses its upstream era's plugin/task API and
Ant Java invocation, taken from upstream tag v0.7 (`eca7497b6f5e456a7d51094899a6307e0df1aa8c`).
Historical launcher scripts run through an explicit host Bash because their
hardcoded `/bin/bash` does not exist on NixOS. Distribution bytes remain
checksum-verified.

Build and describe the probe SDKs, using an external output directory:

```sh
export EPOCH_SOURCE="$PWD"
nix build --impure --no-link --expr 'let
  f = builtins.getFlake ("path:" + builtins.getEnv "EPOCH_SOURCE");
  p = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
in import (/. + builtins.getEnv "EPOCH_SOURCE" + "/tests/epoch-probes/sdks.nix") { pkgs = p; }'
nix eval --impure --json --expr 'let
  f = builtins.getFlake ("path:" + builtins.getEnv "EPOCH_SOURCE");
  p = f.inputs.nixpkgs.legacyPackages.x86_64-linux;
  sdks = import (/. + builtins.getEnv "EPOCH_SOURCE" + "/tests/epoch-probes/sdks.nix") { pkgs = p; };
in builtins.mapAttrs (_: sdk: {
  path = toString sdk; home = sdk.home or (toString sdk); version = sdk.version;
}) sdks' > /path/to/sdk-info.json
nix build '.#default' --out-link /path/to/cli
python3 tests/epoch-probes/probe.py --sdks /path/to/sdk-info.json \
  --cache /path/to/distributions --output /path/to/observations \
  --cli /path/to/cli/bin/gradle-purifier
```

Use repeatable `--profile ID` arguments for selected profiles. Add `--lifecycle`
to continue successful capture through prefetch, explicit offline replay,
sandbox tests with the independently selected compiler supplied through
`toolchainJdks`, and the unchanged CLI's publication path. Commands have bounded
execution time (default 240 seconds). The diagnostic runner writes every
outcome, including failures, and exits normally after collecting them; process
exit zero is not a compatibility pass. Inspect `report.json` and stage logs.

## Initial local observations

[The observation record](observations/2026-11-05.json) and its relative logs
record source `759d96470f4857e90fffcba17ab532efda572449`. These are local
observations, not landed native CI or catalog support claims.

- Twelve native controls complete compile/check/publication/ordinary-consumer
  execution across the twelve selected Gradle versions. Gradle 1.12 fails its
  launcher with the Java 6 and Java 7 hosts (`PosixFileFunctions.symlink` JNI
  linkage), but its Java 8 host with Java 6 project compiler/runtime completes
  the native control. This is not a minimum-version conclusion.
- The unchanged purifier's Java 17 recorder bytecode fails on selected Java
  6/8/11 manager runtimes before Gradle capture can run.
- Gradle 7.6.6 on Java 17 cannot compile the init script's static flow API
  references (`FlowScope`, `FlowProviders` and related classes).
- Gradle 8.14.3, 9.8.2 and 9.9.0-rc-1 on Java 21 with Java 8 project compiler
  and runtime pass capture, prefetch, offline replay and sandbox tests when
  the compiler is supplied explicitly through `toolchainJdks`.
- Those same profiles fail the unchanged CLI publication path: its independent
  sandbox cannot find the configured Java 8 `javac`. The direct builder API
  with `toolchainJdks` succeeds. This identifies compiler propagation work;
  it does not establish publication support. Original fixture sources remain
  unchanged in all three deeper probes.

Production recorder/init-script/selector/CLI changes belong to a separate
review. Do not turn native-only controls or these observed failures into
catalog compatibility points before the required production paths and landed
native CI prove them.

The separate CLI-only snapshots in `tests/gradle-flags-help` were collected from
actual Gradle 2.14.1, 8.14.3 and 9.9.0-rc-1 distributions with their selected
SDKs. Collection checked native option parsing and used an init-script trap to
confirm that `--help` did not initialize projects or execute tasks. The unit check
replays these snapshots offline; it is not a new native compatibility matrix.

`gradle-9.9.0-rc-1-rejected-v3.txt` retains the exact input rejected by the former
flat-only parser and is deliberately byte-identical to the ordinary 9.9 RC
snapshot. The explicit categorized signature now accepts that input only with its
complete section sequence and footer. Missing categories or footer, duplicate
usage output and unknown sections still fail closed.

Inventory sources: [Gradle versions](https://services.gradle.org/versions/all),
[Gradle compatibility reference](https://docs.gradle.org/current/userguide/compatibility.html),
[Azul Java 6 metadata](https://api.azul.com/metadata/v1/zulu/packages/6dc1d984-dd4c-11ea-a8fe-fa163ed8e833),
[Azul Java 7 metadata](https://api.azul.com/metadata/v1/zulu/packages/f436b3cb-0115-4814-b7fa-e180747bd68f).
