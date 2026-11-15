# Installed runtime regression

These controls exercise installed, non-store runtimes. Their tested versions
are evidence, not a supported-version list. The fixture uses Gradle 8.14.3
and JUnit 5, so its ordinary upstream runtime and target requirements apply.

```sh
python3 -B tests/local-runtime/native.py \
  --cli /path/to/gradle-purifier \
  --output /new/private/results \
  --manager-jdk /installed/manager \
  --task-jdk /installed/compiler-sdk \
  --via-path --via-path-shim --unused-sdk-declaration
```

The control locks dependencies, prefetches, runs a clean offline build with
three tests, replaces the manager runtime without changing the lock and checks sandbox-binding errors, and requires
byte-identical relock and unchanged project inputs. The included build retains its native SDK selection. The optional declaration names a deliberately
unresolvable attribute, proving that local adoption does not resolve it.

For a manager that is a JRE without `javac` or `jdk.compiler`, add
`--included-build-task-sdk` so both Java projects use the separate installed
compiler. The manager remains the JRE. The precompiled wrapper-path helper
does not need a compiler in that manager. Its bytecode requirement belongs
to the helper; older project JVMs can use the separate implementation JVM
for path computation without replacing the runtime of a project build.

To also test an explicit immutable binding, supply matching Nix package
attribute paths through `--pure-manager-attribute` and
`--pure-task-attribute`. This adds a sandboxed test build; a local host path
alone is never a sandbox SDK input.

The genuine missing-compiler control runs ordinary Gradle with a fresh user
home and no purifier init script:

```sh
python3 -B tests/local-runtime/missing-compiler.py \
  --gradle /path/to/gradle --jre /installed/jre --output /new/private/negative
```

It requires the installed JRE to appear in Gradle's native toolchain report
and the Java compiler requirement to fail. Both drivers own process groups
and deadlines, use private temporary directories, and retain command logs.
Runtime observations remain optional per-host diagnostics and never enter
the portable dependency lock or its admission rules.
