# Bounded SDK closure regression

These probes reuse Gradle 8.14.3 with manager Java 21/project Java 8 and its
same-SDK Java 21 control. They add no SDK release matrix. The baseline CLI is
from `f7ec9a358e25567231b58100d196c40557a6d638`.

After building the candidate CLI and running `tests/sdk-telemetry/probe.py`, use
fresh evidence directories:

```sh
PYTHONDONTWRITEBYTECODE=1 python tests/sdk-closure/lifecycle.py \
  --cli /path/to/candidate/bin/gradle-purifier \
  --baseline-cli /path/to/f7ec9a3/bin/gradle-purifier \
  --sdk-receipt /path/to/telemetry/context.json --output /path/to/new-lifecycle
PYTHONDONTWRITEBYTECODE=1 python tests/sdk-closure/negative.py \
  --cli /path/to/candidate/bin/gradle-purifier \
  --project /path/to/new-lifecycle/split --output /path/to/new-negatives
```

Capture needs network access; sandbox replay uses only fixed-output downloads.
The driver sets both `HOME` and `GRADLE_USER_HOME`: changing `HOME` alone does
not guarantee that Gradle loads the prefetched user init script. Opt-in
`GRADLE_PURIFIER_SDK_TRACE=1` produces selected-SDK validation markers, which
the positive offline check asserts. These markers are not task execution claims.

The lifecycle covers native capture, byte-identical relock, prefetch, actual
up-to-date task outcomes with unchanged SDK selection, installed-init replay,
sandbox tests without an explicit JDK argument, CLI publication and ordinary
Java 8 compilation/execution. It asserts class major 52, rejects a previously
unobserved SDK task role and an effective SDK change, and preserves original
project sources. The same-SDK control compares old/new lock bytes through cold
and warm captures. Diagnostic state changes must leave the pure producer
derivation unchanged.

Negative tests distinguish ambient paths, an alias before and after retarget,
identical release metadata from a different Nix output, lock-content and Nix
identity tampering, wrong platform context, and a foreign SDK absent from a
legacy lock. The different-output fixture contains release metadata and empty
placeholder binaries: it is not a runnable SDK and proves no native substitution
behavior. The old explicit `toolchainJdks` API has a separate positive check;
Nix-context-bearing values with an ambient home are rejected.

Locks record stable selected root-task roles and immutable SDK identities,
not executed/skipped/up-to-date states. Those raw outcomes stay in diagnostic
sidecars. A skipped `Test` provider can be selected without executing tests;
Javadoc absent from capture remains unobserved. Automatic capture does not
claim all custom or nested SDK roles. Explicit typed SDK inputs retain their
existing builder semantics.
