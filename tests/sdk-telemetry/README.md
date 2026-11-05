# Native SDK selection telemetry

This test-only probe uses the existing Gradle 8.14.3 fixture with Java 21 as
manager and Java 8 as project compiler and launcher. It adds no SDK selection
flag, production code, lock schema or compatibility matrix.

Build the existing CLI, then run the probe with a fresh output directory:

```sh
nix build .#default --out-link telemetry-cli
PYTHONDONTWRITEBYTECODE=1 python tests/sdk-telemetry/probe.py \
  --cli telemetry-cli/bin/gradle-purifier --output /path/to/new-output
```

The pinned Java 8 and Java 21 SDKs must already be realized. The native CLI
fetches its pinned Gradle distribution, so capture needs network access.

The init script resolves providers only for the selected task graph, records
both providers and explicit fork/task executable overrides, and retains raw
executed/skipped/up-to-date/no-source outcomes. A selected provider is not
proof of process execution. In particular, Gradle can report `executed=true`
and `skipped=true` together. Javadoc outside this graph is explicitly
unobserved; its provider is not resolved. Listing unobserved tasks realizes
task objects for this diagnostic, and is not a production capture design.

The probe validates the actual Java 8 compiler/launcher paths in native process
logs, class major 52, unchanged project sources, and exact matching Nix SDK
output identities. It retains source hashes, CLI command, distribution pins,
SDK derivations/string contexts, task graph and raw receipts. It does not
certify another Gradle release, Javadoc execution, or custom task coverage.

The observed Gradle 8.14.3 provider honors this fixture's explicit Java 8
compiler/launcher overrides. Consumers must retain both forms of evidence and
follow native precedence; they must not reject a legitimate override simply
because an unused provider defaults to the manager JDK.
