# Native legacy Maven publication controls

These controls target the Gradle 6.9.4 `maven` plugin and its original
`uploadArchives` task, with manager Java 11 and compiler/asserting runtime Java 8.
They do not establish a Gradle version range or a general JVM compatibility claim.
The existing epoch fixture's disabled JUnit task is not credited as execution.

`lifecycle.py` retains the unchanged fixture through native publication, lock and
relocation, cold network-isolated replay, Nix tests, and original-task deployment
with compilation forbidden. `metadata.py` adds native POM callbacks and a real
loopback dependency origin, stops it after prefetch, and compares every deployed
producer and dependency JAR/POM with the authoritative Nix projection. Fresh
Gradle and Maven consumers resolve only the deployment for project dependencies.
Maven may fetch its build plugins; consumer network isolation is not claimed.
A direct competing dependency proves that a downloaded literal version remains
a soft Maven request rather than becoming an invented strict constraint. `provided.py` uses the real
existing Nix composition producer through `--input`, preserves its exact hard
POM reference and full projected closure, and compares competing-version
Gradle/Maven behavior with direct consumers of the immutable projection. It
does not invent strict Gradle module metadata.

The expected publication is independently built with the exact immutable
`command.nix` packaged in the candidate CLI. Changing the live companion directory after building the CLI changes its
producer derivation identity, so mixed-revision comparisons are invalid. The
separate flake-builder tests and companion byte checks remain required controls.
The immutable companion directory has a canonical store name, and `companion.py`
requires exact command-versus-flake producer derivation identity.

`native.py` checks native default/sentinel/explicit coordinates and snapshot POM
byte parity. `parent.py` checks an unpublished parent and its published child.
`schema.py`, `negative.py`, and `companion.py` check public owner bindings,
collisions, actual task/deployer guards, and immutable companion identity.

The adapter accepts the proven release-only, unclassified JAR/POM closure and
preserves its projected bytes and original repository objects. SNAPSHOT routing,
custom Upload behavior, custom deployment transports, unmapped descriptors,
classifiers, and additional Gradle metadata fail explicitly. Native mapping of
those inputs requires further evidence. An arbitrary late Gradle listener can
execute user code before a task guard; atomic deployment is not claimed.

The `legacy-maven` workflow job runs these controls and preserves raw reports,
source identities, SDK paths, consumer definitions and logs on failure as well
as success. No catalog support point follows from a local pass alone.
